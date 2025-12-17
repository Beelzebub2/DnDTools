import logging
import math
import time
import heapq
import keyboard
import os
from collections import defaultdict
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

import pygetwindow as gw

from src.models import macros
from src.models.game_overlay import NullOverlaySession, SortOverlaySession
from src.models.item import Item
from src.models.point import Point
from src.models.stash_preview import parse_stashes
from src.models.storage import Storage, StashType

logger = logging.getLogger(__name__)

def intersects(pos1, width1, height1, pos2, width2, height2):
    if pos1.x + width1 <= pos2.x or pos2.x + width2 <= pos1.x:
        return False
    if pos1.y + height1 <= pos2.y or pos2.y + height2 <= pos1.y:
        return False
    return True


class LayoutPlanError(Exception):
    """Raised when a deterministic plan cannot be created for the stash."""


@dataclass
class PlanEntry:
    item: Item
    target: Point


@dataclass
class LayoutPlan:
    positions: Dict[int, Point]
    order: List[PlanEntry]


class LayoutPlanner:
    """Determines the final resting position for every item before any moves happen."""

    def __init__(self, width: int, height: int, prefer_dense: bool = False):
        self.width = width
        self.height = height
        self.prefer_dense = prefer_dense
        self._reset()

    def _reset(self) -> None:
        self.occupancy = [[False for _ in range(self.height)] for _ in range(self.width)]

    def build(self, items: List[Item], comparator: Optional[Callable[[Item, Item], int]] = None) -> LayoutPlan:
        self._reset()
        if comparator:
            ordered_items = sorted(items, key=cmp_to_key(comparator))
        else:
            ordered_items = sorted(items, key=self._priority_key)
        positions: Dict[int, Point] = {}

        for itm in ordered_items:
            slot = self._find_slot_for(itm)
            if slot is None:
                raise LayoutPlanError(f"Unable to place item '{itm}' within stash bounds")
            self._mark(slot, itm)
            positions[id(itm)] = slot

        execution_order = sorted(
            [PlanEntry(item=itm, target=positions[id(itm)]) for itm in items],
            key=lambda entry: (
                entry.target.y,
                entry.target.x,
                -(entry.item.width * entry.item.height),
            ),
        )
        return LayoutPlan(positions=positions, order=execution_order)

    def _priority_key(self, item: Item) -> Tuple[int, int, int, int, str]:
        area = item.width * item.height
        rarity = getattr(item, "rarity", 0) or 0
        return (-area, -max(item.width, item.height), -rarity, -item.height, item.name or "")

    def _find_slot_for(self, item: Item) -> Optional[Point]:
        max_x = self.width - item.width
        max_y = self.height - item.height
        best_score = None
        best_point: Optional[Point] = None

        for y in range(0, max_y + 1):
            for x in range(0, max_x + 1):
                if not self._fits(item, x, y):
                    continue
                adjacency = self._adjacency_score(item, x, y)
                score = (y, x, -adjacency if self.prefer_dense else adjacency)
                if best_score is None or score < best_score:
                    best_score = score
                    best_point = Point(x, y)
        return best_point

    def _fits(self, item: Item, origin_x: int, origin_y: int) -> bool:
        for dx in range(item.width):
            for dy in range(item.height):
                x = origin_x + dx
                y = origin_y + dy
                if x >= self.width or y >= self.height or self.occupancy[x][y]:
                    return False
        return True

    def _adjacency_score(self, item: Item, origin_x: int, origin_y: int) -> int:
        score = 0
        for dx in range(item.width):
            for dy in range(item.height):
                x = origin_x + dx
                y = origin_y + dy
                neighbors = [
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                ]
                for nx, ny in neighbors:
                    if 0 <= nx < self.width and 0 <= ny < self.height and self.occupancy[nx][ny]:
                        score += 1
        return score

    def _mark(self, point: Point, item: Item) -> None:
        for dx in range(item.width):
            for dy in range(item.height):
                self.occupancy[point.x + dx][point.y + dy] = True

class StashSorter:
    def __init__(self, stash: Storage, inv: Storage, pack_mode: bool = False, stack_mode: bool = False):
        self.stash = stash
        self.inv = inv
        self.cancel_event = None
        self.pack_mode = bool(pack_mode)
        self.stack_mode = bool(stack_mode)
        self._stack_instructions = []
        self.overlay_session: Optional[Union[SortOverlaySession, NullOverlaySession]] = None
        if self.stack_mode:
            self._prepare_stack_plan()
        self._buffered_inventory = {}
        self._reserved_slots = {}
        self._blocker_move_counts = defaultdict(int)
        self._plan_positions: Dict[int, Point] = {}
        self._plan_order: List[PlanEntry] = []
        self._cell_plan_rank: Dict[Tuple[int, int], int] = {}
        self._rank_lookup: Dict[int, int] = {}
        self._current_plan_index = -1

    def sort(
        self,
        cancel_event=None,
        overlay_session: Optional[Union[SortOverlaySession, NullOverlaySession]] = None,
    ):
        self.cancel_event = cancel_event
        self.overlay_session = overlay_session or NullOverlaySession()
        if cancel_event:
            macros.push_cancel_event(cancel_event)

        try:
            if self.stack_mode and self._stack_instructions:
                self._overlay_update("Merging stackable items before sorting...", status="info")

            if self.stack_mode:
                if not self._perform_stacking_phase():
                    self._overlay_log("Stacking phase failed; aborting sort.")
                    return False

            self._overlay_update("Preparing workspace...", status="info")
            self._ensure_initial_workspace()
            self._overlay_update("Analyzing stash contents...", status="info")

            unique_items: Dict[int, Item] = {}
            for itm in self.stash.pq:
                if itm.stash is self.stash or getattr(itm, "_buffered_by_sort", False):
                    unique_items[id(itm)] = itm
            for itm in self._buffered_inventory.values():
                unique_items[id(itm)] = itm
            active_items = list(unique_items.values())
            total_items = len(active_items)
            if not total_items and not self._buffered_inventory:
                self._overlay_log("No items detected in stash; nothing to sort.")
                return True

            self._overlay_update("Calculating deterministic layout...", status="info")
            plan = self._build_sort_plan(active_items)
            if plan is None:
                self._overlay_update("Unable to calculate layout plan.", status="error")
                self._overlay_log("Unable to create deterministic layout; aborting sort.")
                return False

            plan_size = len(plan.order)
            mode_label = "dense pack" if self.pack_mode else "scanline"
            stack_label = "stacking on" if self.stack_mode else "stacking off"
            self._overlay_log(
                f"Layout plan ready for {plan_size} items ({mode_label}, {stack_label})."
            )
            self._overlay_update(
                f"Executing layout plan for {plan_size} items...",
                status="info",
            )
            result = self._execute_plan(plan, total_items)
            return result
        except macros.MacroCancelled:
            logger.info("Sort operation cancelled during macro execution")
            self._overlay_update(
                "Sort cancelled. Refresh your character data to resynchronize.",
                status="warning",
            )
            self._overlay_log(
                "Sort cancelled. Macro actions stopped immediately. Refresh your character data to resynchronize."
            )
            return False
        finally:
            if cancel_event:
                macros.pop_cancel_event(cancel_event)

    # ------------------------------------------------------------------ overlay helpers
    def _overlay_update(self, subtitle: str, status: str = "info") -> None:
        if not self.overlay_session:
            return
        try:
            self.overlay_session.update_status(subtitle, status=status)
        except Exception:
            pass

    def _overlay_log(self, message: str) -> None:
        if not self.overlay_session:
            return
        try:
            self.overlay_session.add_log(message)
        except Exception:
            pass

    def _build_sort_plan(self, items: List[Item]) -> Optional[LayoutPlan]:
        if not items:
            self._plan_positions.clear()
            self._plan_order.clear()
            self._cell_plan_rank.clear()
            self._rank_lookup.clear()
            return LayoutPlan(positions={}, order=[])

        comparators: List[Optional[Callable[[Item, Item], int]]] = []
        if Item.sort_order:
            comparators.append(Item.build_sort_comparator(Item.sort_order))
        comparators.append(None)

        plan: Optional[LayoutPlan] = None
        fallback_used = False
        for comparator in comparators:
            planner = LayoutPlanner(self.stash.width, self.stash.height, prefer_dense=self.pack_mode)
            try:
                plan = planner.build(items, comparator=comparator)
                if fallback_used and comparator is None:
                    self._overlay_log(
                        "Custom ordering could not be perfectly applied; using default layout heuristics instead."
                    )
                break
            except LayoutPlanError as exc:
                if comparator is None:
                    logger.error("Layout planner failed: %s", exc)
                    return None
                fallback_used = True
                logger.warning("Custom layout ordering failed; retrying with default priority: %s", exc)
                continue

        if plan is None:
            return None

        self._plan_positions = plan.positions
        self._plan_order = plan.order
        self._cell_plan_rank = {}
        self._rank_lookup = {}
        for index, entry in enumerate(plan.order):
            self._rank_lookup[id(entry.item)] = index
            for dx in range(entry.item.width):
                for dy in range(entry.item.height):
                    key = (entry.target.x + dx, entry.target.y + dy)
                    self._cell_plan_rank[key] = index
        return plan

    def _execute_plan(self, plan: LayoutPlan, total_items: int) -> bool:
        processed = 0
        for index, entry in enumerate(plan.order):
            self._current_plan_index = index
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("Sort operation cancelled by user input")
                self._overlay_log("Sort cancelled by user.")
                return False

            if entry.item.position == entry.target:
                logger.debug("%s already at %s", entry.item, entry.target)
                continue

            if not self._move_item_to_target(entry.item, entry.target, set()):
                logger.error("Failed to relocate %s to %s", entry.item, entry.target)
                self._overlay_log("Unable to place an item in its planned slot; aborting sort.")
                return False

            processed += 1
            if total_items and (processed == total_items or processed == 1 or processed % 5 == 0):
                self._overlay_update(
                    f"Sorting stash items... ({processed}/{total_items})",
                    status="info",
                )

        self._overlay_update("Sorting complete.", status="success")
        return True

    def _move_item_to_target(self, item: Item, target_point: Point, lineage: Set[int]) -> bool:
        token = id(item)
        if token in lineage:
            logger.debug("Detected cycle while moving %s; attempting to park it", item)
            return self._park_blocker(item)

        if item.position == target_point:
            return True

        lineage.add(token)
        blockers = self._collect_blocking_items(item, target_point)
        for blocker in list(blockers):
            blocker_token = id(blocker)
            if blocker_token in lineage:
                if not self._park_blocker(blocker):
                    lineage.discard(token)
                    return False
                continue

            desired = self._plan_positions.get(blocker_token)
            if desired is None:
                if not self._park_blocker(blocker):
                    lineage.discard(token)
                    return False
                continue

            if blocker.position != desired:
                if not self._move_item_to_target(blocker, desired, lineage):
                    if not self._park_blocker(blocker):
                        lineage.discard(token)
                        return False
            elif intersects(desired, blocker.width, blocker.height, target_point, item.width, item.height):
                if not self._park_blocker(blocker):
                    lineage.discard(token)
                    return False

        if not self._ensure_area_available(item, target_point):
            lineage.discard(token)
            return False

        item.stash.move(item, target_point, self.stash)
        self._unmark_buffered_inventory(item)
        lineage.discard(token)
        logger.debug("Placed %s at %s", item, target_point)
        return True

    def _park_blocker(self, blocker: Item) -> bool:
        if blocker is None:
            return True

        if self.inv:
            inv_slot = self.inv.find_empty_slot(blocker)
            if inv_slot is None and self._rebalance_inventory(blocker):
                inv_slot = self.inv.find_empty_slot(blocker)
            if inv_slot:
                logger.debug("Parking %s in inventory slot %s", blocker, inv_slot)
                blocker.stash.move(blocker, inv_slot, self.inv)
                self._mark_buffered_inventory(blocker)
                return True

        future_slot = self._find_future_safe_slot(blocker)
        if future_slot:
            logger.debug("Parking %s in future stash slot %s", blocker, future_slot)
            blocker.stash.move(blocker, future_slot, self.stash)
            self._unmark_buffered_inventory(blocker)
            return True

        if self._create_workspace_for(None, blocker):
            logger.debug("Workspace creation succeeded for %s", blocker)
            return True

        logger.error("Unable to park blocker %s; out of workspace", blocker)
        self._overlay_log("Unable to secure temporary space for a blocking item; aborting sort.")
        return False

    def _find_future_safe_slot(self, item: Item) -> Optional[Point]:
        min_rank = max(
            self._current_plan_index + 1,
            self._rank_lookup.get(id(item), self._current_plan_index + 1),
        )
        return self._find_slot_with_min_rank(item, min_rank)

    def _find_slot_with_min_rank(self, item: Item, min_rank: int) -> Optional[Point]:
        if not self.stash:
            return None

        max_x = self.stash.width - item.width
        max_y = self.stash.height - item.height
        if max_x < 0 or max_y < 0:
            return None

        for y in range(max_y, -1, -1):
            for x in range(max_x, -1, -1):
                fits = True
                for dx in range(item.width):
                    for dy in range(item.height):
                        grid_x = x + dx
                        grid_y = y + dy
                        cell_rank = self._cell_plan_rank.get((grid_x, grid_y))
                        if cell_rank is not None and cell_rank < min_rank:
                            fits = False
                            break
                        occupant = self.stash.grid[grid_x][grid_y]
                        if occupant not in (0, item):
                            fits = False
                            break
                    if not fits:
                        break
                if fits:
                    return Point(x, y)
        return None

    def _ensure_initial_workspace(self, min_free_cells: int = 6, max_buffer_moves: int = 8):
        if not self.inv:
            return

        free_cells = self._count_empty_cells(self.stash)
        if free_cells >= min_free_cells:
            return

        logger.info("Preparing workspace: current free cells %s, target %s", free_cells, min_free_cells)

        candidates = [
            itm for itm in list(self.stash.pq)
            if itm.stash is self.stash and not getattr(itm, "stacked", False)
        ]

        candidates.sort(key=lambda itm: (itm.width * itm.height, getattr(itm, "rarity", 0)))

        moves = 0
        for candidate in candidates:
            if free_cells >= min_free_cells or moves >= max_buffer_moves:
                break

            inv_slot = self.inv.find_empty_slot(candidate)
            if inv_slot is None:
                continue

            logger.debug("Buffering item %s to inventory slot %s to create workspace", candidate, inv_slot)
            candidate.stash.move(candidate, inv_slot, self.inv)
            self._mark_buffered_inventory(candidate)
            moves += 1
            free_cells += candidate.width * candidate.height

        logger.info("Workspace preparation complete. Free cells: %s, items buffered: %s", free_cells, moves)

    def _prepare_stack_plan(self):
        grouped = {}
        for item in list(self.stash.pq):
            max_stack = getattr(item, "max_stack_size", 1) or 1
            if max_stack <= 1:
                continue
            key = (getattr(item, "item_id", None), item.rarity)
            grouped.setdefault(key, []).append(item)

        removal_set = set()
        instructions = []

        for key, items in grouped.items():
            if len(items) <= 1:
                continue

            stackables = sorted(items, key=lambda itm: getattr(itm, "quantity", 1), reverse=True)
            max_stack = max(1, getattr(stackables[0], "max_stack_size", 1) or 1)
            total_qty = sum(max(1, getattr(itm, "quantity", 1)) for itm in stackables)
            required_stacks = min(len(stackables), math.ceil(total_qty / max_stack))

            targets = stackables[:required_stacks]
            remaining_items = stackables[required_stacks:]

            target_capacity = {
                target: max(0, max_stack - min(max_stack, getattr(target, "quantity", 1)))
                for target in targets
            }

            for extra in remaining_items:
                if extra in removal_set:
                    continue

                extra_qty = max(1, getattr(extra, "quantity", 1))
                chosen_target = None
                for target, capacity in target_capacity.items():
                    if capacity >= extra_qty:
                        chosen_target = target
                        break

                if chosen_target:
                    instructions.append((extra, chosen_target))
                    target_capacity[chosen_target] -= extra_qty
                    new_quantity = min(
                        getattr(chosen_target, "max_stack_size", max_stack),
                        getattr(chosen_target, "quantity", 1) + extra_qty,
                    )
                    chosen_target.quantity = new_quantity
                    removal_set.add(extra)
                else:
                    targets.append(extra)
                    target_capacity[extra] = max(0, max_stack - min(max_stack, getattr(extra, "quantity", 1)))
                    extra.quantity = min(max_stack, max(1, getattr(extra, "quantity", 1)))

        if instructions:
            self._stack_instructions = instructions
            remaining_items = [item for item in self.stash.pq if item not in removal_set]
            heapq.heapify(remaining_items)
            self.stash.pq = remaining_items

    def _perform_stacking_phase(self):
        if not self._stack_instructions:
            return True

        for item, target in self._stack_instructions:
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("Sort operation cancelled during stacking phase")
                return False
            if not self._stack_item(item, target):
                logger.error("Failed to stack items; aborting sort")
                return False
        return True

    def _stack_item(self, item: Item, target: Item) -> bool:
        if not item or not target:
            return False

        start_stash = item.stash
        target_stash = target.stash
        if not start_stash or not target_stash:
            return False

        start_pos = Point(item.position.x, item.position.y)
        target_pos = Point(target.position.x, target.position.y)

        try:
            macros.move_from_to_reliable(
                start_stash,
                start_pos,
                target_stash,
                target_pos,
                item.width,
                item.height,
                target.width,
                target.height,
            )
        except Exception as exc:
            logger.error("Stacking move failed: %s", exc)
            return False

        for dx in range(item.width):
            for dy in range(item.height):
                x = start_pos.x + dx
                y = start_pos.y + dy
                if 0 <= x < start_stash.width and 0 <= y < start_stash.height:
                    start_stash.grid[x][y] = 0

        item.stacked = True
        item.stash = target_stash
        item.position = Point(target_pos.x, target_pos.y)
        return True

    def _ensure_area_available(self, item: Item, target_point: Point) -> bool:
        moved_items = set()
        for dx in range(item.width):
            for dy in range(item.height):
                x = target_point.x + dx
                y = target_point.y + dy

                if x >= self.stash.width or y >= self.stash.height:
                    logger.warning("Target position out of bounds")
                    return False

                occupying_item = self.stash.grid[x][y]
                if occupying_item == 0 or occupying_item == item or occupying_item in moved_items:
                    continue

                if self.cancel_event and self.cancel_event.is_set():
                    logger.info("Sort operation cancelled during area clearing")
                    return False

                blocker_id = id(occupying_item)
                inv_slot = self.inv.find_empty_slot(occupying_item) if self.inv else None
                if inv_slot:
                    logger.debug("Temporarily storing %s in inventory slot %s", occupying_item, inv_slot)
                    self.stash.move(occupying_item, inv_slot, self.inv)
                    self._mark_buffered_inventory(occupying_item)
                    self._blocker_move_counts.pop(blocker_id, None)
                    moved_items.add(occupying_item)
                    continue

                new_pos = self.stash.find_empty_slot(occupying_item)
                if new_pos:
                    if self.inv and self._blocker_move_counts[blocker_id] >= 1:
                        inv_slot_retry = self.inv.find_empty_slot(occupying_item)
                        if inv_slot_retry:
                            logger.debug(
                                "Blocker %s moved previously; relocating to inventory slot %s to avoid loops",
                                occupying_item,
                                inv_slot_retry,
                            )
                            self.stash.move(occupying_item, inv_slot_retry, self.inv)
                            self._mark_buffered_inventory(occupying_item)
                            self._blocker_move_counts.pop(blocker_id, None)
                            moved_items.add(occupying_item)
                            continue

                    if self.inv and not self.inv.find_empty_slot(occupying_item):
                        if self._rebalance_inventory(occupying_item):
                            inv_slot_after_rebalance = self.inv.find_empty_slot(occupying_item)
                            if inv_slot_after_rebalance:
                                logger.debug(
                                    "Rebalanced inventory; moving blocker %s into freed slot %s",
                                    occupying_item,
                                    inv_slot_after_rebalance,
                                )
                                self.stash.move(occupying_item, inv_slot_after_rebalance, self.inv)
                                self._mark_buffered_inventory(occupying_item)
                                self._blocker_move_counts.pop(blocker_id, None)
                                moved_items.add(occupying_item)
                                continue

                    logger.debug("Moving %s to empty slot in stash", occupying_item)
                    self.stash.move(occupying_item, new_pos, self.stash)
                    self._unmark_buffered_inventory(occupying_item)
                    self._blocker_move_counts[blocker_id] += 1
                else:
                    logger.info("No immediate positions found; attempting to create workspace")
                    if self._create_workspace_for(item, occupying_item):
                        return self._ensure_area_available(item, target_point)
                    logger.error("Workspace creation failed; aborting")
                    return False

                moved_items.add(occupying_item)

        return True


    def _create_workspace_for(self, target_item: Item | None, blocking_item: Item) -> bool:
        if not self.inv:
            return False

        # Prefer moving the smallest items first to minimize disruption
        workspace_candidates = [
            candidate for candidate in self.stash.pq
            if candidate.stash is self.stash
            and not getattr(candidate, "stacked", False)
        ]

        if target_item is not None:
            workspace_candidates = [c for c in workspace_candidates if c not in {target_item, blocking_item}]
        else:
            workspace_candidates = [c for c in workspace_candidates if c is not blocking_item]

        if not workspace_candidates:
            return False

        workspace_candidates.sort(key=lambda itm: (itm.width * itm.height, getattr(itm, "rarity", 0)))

        moves_attempted = 0
        max_moves = 10

        for candidate in workspace_candidates:
            if moves_attempted >= max_moves:
                break

            inv_slot = self.inv.find_empty_slot(candidate)
            if not inv_slot:
                continue

            logger.debug("Creating workspace: moving %s to inventory slot %s", candidate, inv_slot)
            candidate.stash.move(candidate, inv_slot, self.inv)
            self._mark_buffered_inventory(candidate)
            moves_attempted += 1

            reassigned_slot = self.stash.find_empty_slot(blocking_item)
            if reassigned_slot:
                logger.debug("Relocating blocking item %s to %s", blocking_item, reassigned_slot)
                blocking_item.stash.move(blocking_item, reassigned_slot, self.stash)
                self._unmark_buffered_inventory(blocking_item)
                return True

        return False

    def _count_empty_cells(self, storage: Storage) -> int:
        empty = 0
        for x in range(storage.width):
            for y in range(storage.height):
                if storage.grid[x][y] == 0:
                    empty += 1
        return empty

    def _collect_blocking_items(self, item: Item, point: Point):
        blockers = set()

        if item is None or point is None:
            return blockers

        for dx in range(item.width):
            for dy in range(item.height):
                x = point.x + dx
                y = point.y + dy
                if x >= self.stash.width or y >= self.stash.height:
                    continue

                occupant = self.stash.grid[x][y]
                if occupant not in (0, item) and occupant is not None:
                    blockers.add(occupant)

        return blockers

    def _force_clear_blockers(self, target_item: Item, blockers, target_point: Point) -> bool:
        if not blockers:
            return True

        for blocker in list(blockers):
            if blocker is None:
                continue

            if blocker.stash is self.stash:
                forbidden_width = target_item.width if target_item else 0
                forbidden_height = target_item.height if target_item else 0
                new_slot = self._find_safe_slot(
                    blocker,
                    target_point,
                    forbidden_width,
                    forbidden_height,
                )
                if new_slot:
                    logger.debug("Relocating blocker %s to %s", blocker, new_slot)
                    self.stash.move(blocker, new_slot, self.stash)
                    self._unmark_buffered_inventory(blocker)
                    continue

                inv_slot = self.inv.find_empty_slot(blocker) if self.inv else None
                if inv_slot:
                    logger.debug("Relocating blocker %s to inventory slot %s", blocker, inv_slot)
                    self.stash.move(blocker, inv_slot, self.inv)
                    self._mark_buffered_inventory(blocker)
                    continue

                logger.warning("Blocker %s has no immediate relocation; attempting workspace creation", blocker)
                if not self._create_workspace_for(target_item, blocker):
                    return False
            elif blocker.stash is self.inv:
                continue
            else:
                continue

        remaining = self._collect_blocking_items(target_item, target_point)
        return len(remaining) == 0

    def _find_safe_slot(self, item: Item, forbidden_origin: Point, forbidden_width: int, forbidden_height: int):
        if item is None:
            return None

        max_x = self.stash.width - item.width
        max_y = self.stash.height - item.height

        if max_x < 0 or max_y < 0:
            return None

        for y in range(max_y, -1, -1):
            for x in range(max_x, -1, -1):
                candidate = Point(x, y)

                if forbidden_origin and forbidden_width and forbidden_height:
                    if intersects(
                        candidate,
                        item.width,
                        item.height,
                        forbidden_origin,
                        forbidden_width,
                        forbidden_height,
                    ):
                        continue

                if candidate == item.position:
                    continue

                fits = True
                for dx in range(item.width):
                    for dy in range(item.height):
                        grid_x = x + dx
                        grid_y = y + dy
                        occupant = self.stash.grid[grid_x][grid_y]
                        if occupant not in (0, item):
                            fits = False
                            break
                    if not fits:
                        break

                if fits:
                    return candidate

        return None

    def _mark_buffered_inventory(self, item: Item):
        if item is None:
            return
        setattr(item, "_buffered_by_sort", True)
        self._buffered_inventory[id(item)] = item
        self._blocker_move_counts.pop(id(item), None)
        self._reserve_inventory_slots(item)

    def _unmark_buffered_inventory(self, item: Item):
        if item is None:
            return
        self._buffered_inventory.pop(id(item), None)
        if getattr(item, "_buffered_by_sort", False):
            setattr(item, "_buffered_by_sort", False)
        self._blocker_move_counts.pop(id(item), None)
        self._release_reserved_slots(item)

    def _reserve_inventory_slots(self, item: Item) -> None:
        if not item or item.stash is not self.inv:
            return

        slots = set()
        for dx in range(item.width):
            for dy in range(item.height):
                slot = (item.position.x + dx, item.position.y + dy)
                item.stash._reserved_slots.add(slot)
                slots.add(slot)

        if slots:
            self._reserved_slots[id(item)] = (item.stash, slots)

    def _release_reserved_slots(self, item: Item) -> None:
        record = self._reserved_slots.pop(id(item), None)
        if not record:
            return
        storage, slots = record
        for slot in slots:
            storage._reserved_slots.discard(slot)

    def _rebalance_inventory(self, blocker: Item, max_attempts: int = 5) -> bool:
        if not self.inv or not self._buffered_inventory:
            return False

        attempts = 0
        buffered_items = list(self._buffered_inventory.values())

        for candidate in buffered_items:
            if attempts >= max_attempts:
                break
            if candidate is blocker or candidate.stash is not self.inv:
                continue

            stash_slot = self._find_safe_slot(candidate, None, 0, 0)
            if not stash_slot:
                continue

            logger.debug(
                "Rebalancing inventory: returning %s from inventory to stash slot %s",
                candidate,
                stash_slot,
            )
            self.inv.move(candidate, stash_slot, self.stash)
            self._unmark_buffered_inventory(candidate)
            attempts += 1

            if self.inv.find_empty_slot(blocker):
                return True

        return self.inv.find_empty_slot(blocker) is not None


def main():
    def force_exit():
        logger.info("F7 pressed. Exiting...")
        os._exit(0)
    keyboard.add_hotkey('F7', force_exit)

    # Focus the 'Dark and Darker' window before sorting
    windows = [w for w in gw.getAllWindows() if w.title == "Dark and Darker"]
    if windows:
        try:
            windows[0].activate()
            logger.info("Focused window: Dark and Darker")
        except Exception as e:
            logger.error("Error focusing window: %s", e)
    else:
        logger.warning("No window with exact title 'Dark and Darker' found.")

    time.sleep(2)

    # not working
    stashes = parse_stashes({})

    stash = Storage(StashType.STORAGE.value, stashes[StashType.STORAGE.value])
    bag = stashes.get(StashType.BAG.value, [])
    inv = Storage(StashType.BAG.value, bag)

    sorter = StashSorter(stash, inv)

    logger.debug("%s", stash)
    logger.debug("%s", inv)
    #exit()
    sorter.sort()

if __name__ == "__main__":
    main()
