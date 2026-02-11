import logging
import math
import time
import heapq
import keyboard
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING, Union

import pygetwindow as gw

from src.models import macros
from src.models.game_overlay import NullOverlaySession, SortOverlaySession
from src.models.item import Item
from src.models.point import Point
from src.models.stash_preview import parse_stashes
from src.models.storage import Storage, StashType
from src.models.sort_feedback import get_sort_feedback_manager
from src.models.sort_learning import get_sort_learning_manager
from src.models.sort_safety import SortSafetyMonitor

if TYPE_CHECKING:
    from src.models.sort_learning import SortLearningManager

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
    learning: Dict[int, Dict[str, Any]] = field(default_factory=dict)


class LayoutPlanner:
    """Determines the final resting position for every item before any moves happen."""

    def __init__(
        self,
        width: int,
        height: int,
        prefer_dense: bool = False,
        *,
        stash: Optional[Storage] = None,
        stack_mode: bool = False,
        learning_manager: Optional["SortLearningManager"] = None,
        learning_session_id: Optional[str] = None,
    ):
        self.width = width
        self.height = height
        self.prefer_dense = prefer_dense
        self.stash_ref = stash
        self.stack_mode = bool(stack_mode)
        self.learning_manager = learning_manager
        self.learning_session_id = learning_session_id
        self._learning_cache: Dict[int, Dict[str, Any]] = {}
        self._initial_free_ratio = self._compute_free_ratio(stash)
        self._reset()

    def _reset(self) -> None:
        self.occupancy = [[False for _ in range(self.height)] for _ in range(self.width)]

    def build(self, items: List[Item], comparator: Optional[Callable[[Item, Item], int]] = None) -> LayoutPlan:
        self._reset()
        self._learning_cache = {}
        if comparator:
            ordered_items = sorted(items, key=cmp_to_key(comparator))
            for itm in ordered_items:
                self._ensure_learning_cache(itm)
        else:
            ordered_items = sorted(items, key=self._learning_sort_key)
        positions: Dict[int, Point] = {}
        learning_payload: Dict[int, Dict[str, Any]] = {}

        for itm in ordered_items:
            slot = self._find_slot_for(itm)
            if slot is None:
                raise LayoutPlanError(f"Unable to place item '{itm}' within stash bounds")
            self._mark(slot, itm)
            positions[id(itm)] = slot
            record = self._record_learning_assignment(itm, slot, comparator_used=bool(comparator))
            if record:
                learning_payload[id(itm)] = record

        execution_order = sorted(
            [PlanEntry(item=itm, target=positions[id(itm)]) for itm in items],
            key=lambda entry: (
                entry.target.y,
                entry.target.x,
                -(entry.item.width * entry.item.height),
            ),
        )
        return LayoutPlan(positions=positions, order=execution_order, learning=learning_payload)

    def _priority_key(self, item: Item) -> Tuple[int, int, int, int, str]:
        area = item.width * item.height
        rarity = getattr(item, "rarity", 0) or 0
        return (-area, -max(item.width, item.height), -rarity, -item.height, item.name or "")

    def _learning_sort_key(self, item: Item) -> Tuple[Any, ...]:
        fallback = self._priority_key(item)
        cache = self._ensure_learning_cache(item)
        score = cache.get("score")
        if score is None:
            return (1, 0.0) + fallback
        return (0, -float(score)) + fallback

    def _ensure_learning_cache(self, item: Item) -> Dict[str, Any]:
        token = id(item)
        cached = self._learning_cache.get(token)
        if cached is not None:
            return cached
        features = self._build_learning_features(item)
        score = None
        if self.learning_manager and features:
            score = self.learning_manager.score_item(features)
        record: Dict[str, Any] = {"features": features}
        if score is not None:
            record["score"] = score
        self._learning_cache[token] = record
        return record

    def _build_learning_features(self, item: Item) -> Dict[str, float]:
        pos = item.position or Point(0, 0)
        width = float(getattr(item, "width", 0) or 0)
        height = float(getattr(item, "height", 0) or 0)
        longest = max(width, height)
        rarity = float(getattr(item, "rarity", 0) or 0)
        slot_x = float(getattr(pos, "x", 0) or 0)
        slot_y = float(getattr(pos, "y", 0) or 0)
        norm_x = slot_x / float(max(1, self.width - 1))
        norm_y = slot_y / float(max(1, self.height - 1))
        return {
            "width": width,
            "height": height,
            "area": width * height,
            "max_side": longest,
            "rarity": rarity,
            "slot_x": slot_x,
            "slot_y": slot_y,
            "slot_x_norm": norm_x,
            "slot_y_norm": norm_y,
            "pack_mode": 1.0 if self.prefer_dense else 0.0,
            "stack_mode": 1.0 if self.stack_mode else 0.0,
            "free_ratio": self._initial_free_ratio,
        }

    def _record_learning_assignment(
        self,
        item: Item,
        slot: Point,
        *,
        comparator_used: bool,
    ) -> Optional[Dict[str, Any]]:
        if not self.learning_session_id and not self.learning_manager:
            return None
        cache = self._learning_cache.get(id(item))
        if not cache:
            return None
        features = cache.get("features")
        if not isinstance(features, dict):
            return None
        adjacency = self._adjacency_score(item, slot.x, slot.y)
        record = {
            "features": features,
            "score": cache.get("score"),
            "target": {"x": slot.x, "y": slot.y},
            "adjacency": adjacency,
            "comparatorUsed": bool(comparator_used),
        }
        if self.learning_manager:
            try:
                self.learning_manager.record_priority_sample(
                    session_id=self.learning_session_id,
                    item=item,
                    features=features,
                    metadata={
                        "target": record["target"],
                        "adjacency": adjacency,
                        "comparatorUsed": bool(comparator_used),
                        "stashWidth": self.width,
                        "stashHeight": self.height,
                    },
                )
            except Exception:
                pass
        return record

    def _compute_free_ratio(self, stash: Optional[Storage]) -> float:
        if not stash:
            return 0.0
        total = max(1, stash.width * stash.height)
        empty = 0
        try:
            for x in range(stash.width):
                for y in range(stash.height):
                    if stash.grid[x][y] == 0:
                        empty += 1
        except Exception:
            return 0.0
        return empty / float(total)

    def _build_features_at(self, item: Item, x: int, y: int) -> Dict[str, float]:
        width = float(getattr(item, "width", 0) or 0)
        height = float(getattr(item, "height", 0) or 0)
        longest = max(width, height)
        rarity = float(getattr(item, "rarity", 0) or 0)
        slot_x = float(x)
        slot_y = float(y)
        norm_x = slot_x / float(max(1, self.width - 1))
        norm_y = slot_y / float(max(1, self.height - 1))
        return {
            "width": width,
            "height": height,
            "area": width * height,
            "max_side": longest,
            "rarity": rarity,
            "slot_x": slot_x,
            "slot_y": slot_y,
            "slot_x_norm": norm_x,
            "slot_y_norm": norm_y,
            "pack_mode": 1.0 if self.prefer_dense else 0.0,
            "stack_mode": 1.0 if self.stack_mode else 0.0,
            "free_ratio": self._initial_free_ratio,
        }

    def _find_slot_for(self, item: Item) -> Optional[Point]:
        max_x = self.width - item.width
        max_y = self.height - item.height
        best_score = None
        best_point: Optional[Point] = None

        # Pre-check: probe whether the ML model actually returns scores
        # to avoid hundreds of wasted score_item calls when no model is loaded.
        use_ml = bool(self.learning_manager)
        if use_ml and max_x >= 0 and max_y >= 0:
            probe = self._build_features_at(item, 0, 0)
            if self.learning_manager.score_item(probe) is None:
                use_ml = False

        for y in range(0, max_y + 1):
            for x in range(0, max_x + 1):
                if not self._fits(item, x, y):
                    continue
                adjacency = self._adjacency_score(item, x, y)
                
                ml_score = 0.0
                if use_ml:
                    # Construct features for this specific slot candidate
                    feats = self._build_features_at(item, x, y)
                    val = self.learning_manager.score_item(feats)
                    if val is not None:
                        ml_score = val

                # Priority:
                # 1. High ML Score (negated so smaller is better in tuple sort)
                # 2. Top (y)
                # 3. Left (x)
                # 4. Density/Adjacency
                
                # Weight ML score heavily (x100) so a 0.01 difference matters more than 1 row
                # But ensure we don't break fallback.
                score = (-ml_score * 100.0, y, x, -adjacency if self.prefer_dense else adjacency)
                
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
    def __init__(
        self,
        stash: Storage,
        inv: Storage,
        pack_mode: bool = False,
        stack_mode: bool = False,
        *,
        character_id: Optional[str] = None,
        stash_id: Optional[int] = None,
        feedback_manager=None,
    ):
        self.stash = stash
        self.inv = inv
        self.cancel_event = None
        self.pack_mode = bool(pack_mode)
        self.stack_mode = bool(stack_mode)
        self.character_id = character_id
        self.stash_id = stash_id
        self._stack_instructions = []
        self.overlay_session: Optional[Union[SortOverlaySession, NullOverlaySession]] = None
        if self.stack_mode:
            self._prepare_stack_plan()
        self._buffered_inventory = {}
        self._reserved_slots = {}
        self._blocker_move_counts = defaultdict(int)
        self._plan_positions: Dict[int, Point] = {}
        self._plan_order: List[PlanEntry] = []
        self._plan_required_moves: int = 0
        self._move_progress_total: int = 0
        self._move_progress_done: int = 0
        self._pending_move_tokens: Set[int] = set()
        self._completed_move_tokens: Set[int] = set()
        self._cell_plan_rank: Dict[Tuple[int, int], int] = {}
        self._rank_lookup: Dict[int, int] = {}
        self._current_plan_index = -1
        self._failure_reason: Optional[str] = None
        self._session_summary: Optional[Dict[str, Any]] = None
        self._safety_monitor: Optional[SortSafetyMonitor] = None
        self._workspace_min_free_cells = 6
        self.learning_manager = get_sort_learning_manager()
        self._learning_session_id: Optional[str] = None
        self._plan_learning_records: Dict[int, Dict[str, Any]] = {}
        self.feedback_handle = None
        manager = feedback_manager or get_sort_feedback_manager()
        try:
            total_cells = self.stash.width * self.stash.height
            occupied_cells = total_cells - self._count_empty_cells(self.stash)
            inventory_total = (self.inv.width * self.inv.height) if self.inv else 0
            inventory_free = self._count_empty_cells(self.inv) if self.inv else 0
            largest_item_area = 0
            for candidate in list(self.stash.pq):
                if not candidate:
                    continue
                largest_item_area = max(largest_item_area, candidate.width * candidate.height)
            base_features = {
                "stash_total_cells": float(total_cells),
                "stash_occupied_cells": float(max(0, occupied_cells)),
                "inventory_total_cells": float(max(0, inventory_total)),
                "inventory_free_cells": float(max(0, inventory_free)),
            }
            if largest_item_area:
                base_features["largest_item_area"] = float(largest_item_area)
            if manager:
                self.feedback_handle = manager.begin_session(
                    character_id=self.character_id,
                    stash_id=self.stash_id,
                    pack_mode=self.pack_mode,
                    stack_mode=self.stack_mode,
                    features=base_features,
                )
        except Exception as exc:
            logger.debug("Unable to initialize feedback session: %s", exc, exc_info=True)
            self.feedback_handle = None

        if self.feedback_handle:
            try:
                self._workspace_min_free_cells = self.feedback_handle.recommended_workspace_cells()
                if not self._learning_session_id:
                    self._learning_session_id = self.feedback_handle.record.get("session_id")
            except Exception:
                self._workspace_min_free_cells = 6

        if not self._learning_session_id:
            self._learning_session_id = f"learning-{uuid.uuid4().hex}"

    def _metric_increment(self, key: str, amount: float = 1.0) -> None:
        if not self.feedback_handle:
            return
        try:
            self.feedback_handle.increment(key, amount)
        except Exception:
            pass

    def _metric_set(self, key: str, value: float) -> None:
        if not self.feedback_handle:
            return
        try:
            self.feedback_handle.set_metric(key, value)
        except Exception:
            pass

    def _feature_set(self, key: str, value: float) -> None:
        if not self.feedback_handle:
            return
        try:
            self.feedback_handle.set_feature(key, value)
        except Exception:
            pass

    # ── Transfer support ────────────────────────────────────────────

    def mark_items_for_transfer(self, items: list) -> int:
        """Mark inventory items as transfer targets to be placed in the stash.

        Call this *after* construction and *before* ``sort()``.  Each item
        must currently reside in ``self.inv`` (the inventory Storage).  The
        items will be included in the layout plan so the sort pipeline
        physically moves them from the inventory grid to their planned
        stash positions.

        Returns the number of items successfully marked.
        """
        marked = 0
        for item in items:
            if item is None:
                continue
            if item.stash is not self.inv:
                logger.debug(
                    "Skipping transfer mark for %s – not in inventory", item
                )
                continue
            self._mark_buffered_inventory(item)
            marked += 1
        if marked:
            logger.info(
                "Transfer mode: %d inventory items marked for placement in stash",
                marked,
            )
            self._metric_set("transfer_items_marked", float(marked))
            # Pre-stack transfer items so the layout planner sees fewer items
            self._prepare_transfer_stack_plan()
        return marked

    def _prepare_transfer_stack_plan(self):
        """Merge stackable items among buffered inventory (transfer) items.

        Groups transfer items by ``(item_id, rarity)`` and collapses
        multiple partial stacks into fewer full stacks.  Generates
        stacking instructions that will execute **in-game** (via macros)
        before the main sort, and removes consumed items from the
        buffered-inventory bookkeeping so the layout planner sees a
        realistic item count.
        """
        buffered = list(self._buffered_inventory.values())
        if not buffered:
            return

        # ── Phase 1: merge inventory items with EACH OTHER ──────────
        grouped_inv: Dict[tuple, list] = {}
        for item in buffered:
            max_stack = getattr(item, "max_stack_size", 1) or 1
            if max_stack <= 1:
                continue
            key = (getattr(item, "item_id", None), item.rarity)
            grouped_inv.setdefault(key, []).append(item)

        removal_ids: Set[int] = set()
        new_instructions: list = []

        for key, inv_items in grouped_inv.items():
            if len(inv_items) <= 1:
                continue

            stackables = sorted(
                inv_items,
                key=lambda itm: getattr(itm, "quantity", 1),
                reverse=True,
            )
            max_stack = max(1, getattr(stackables[0], "max_stack_size", 1) or 1)
            total_qty = sum(max(1, getattr(itm, "quantity", 1)) for itm in stackables)
            required_stacks = min(len(stackables), math.ceil(total_qty / max_stack))

            targets = stackables[:required_stacks]
            extras = stackables[required_stacks:]

            target_capacity = {
                t: max(0, max_stack - min(max_stack, getattr(t, "quantity", 1)))
                for t in targets
            }

            for extra in extras:
                if id(extra) in removal_ids:
                    continue
                extra_qty = max(1, getattr(extra, "quantity", 1))
                chosen = None
                for t, cap in target_capacity.items():
                    if cap >= extra_qty:
                        chosen = t
                        break
                if chosen:
                    new_instructions.append((extra, chosen))
                    target_capacity[chosen] -= extra_qty
                    chosen.quantity = min(
                        max_stack,
                        getattr(chosen, "quantity", 1) + extra_qty,
                    )
                    removal_ids.add(id(extra))
                else:
                    targets.append(extra)
                    target_capacity[extra] = max(
                        0, max_stack - min(max_stack, getattr(extra, "quantity", 1))
                    )

        # ── Phase 2: merge inventory items into existing STASH stacks ──
        # Re-group what remains after phase 1
        remaining_inv: Dict[tuple, list] = {}
        for item in buffered:
            if id(item) in removal_ids:
                continue
            max_stack = getattr(item, "max_stack_size", 1) or 1
            if max_stack <= 1:
                continue
            key = (getattr(item, "item_id", None), item.rarity)
            remaining_inv.setdefault(key, []).append(item)

        for stash_item in list(self.stash.pq):
            max_stack = getattr(stash_item, "max_stack_size", 1) or 1
            if max_stack <= 1:
                continue
            capacity = max(0, max_stack - min(max_stack, getattr(stash_item, "quantity", 1)))
            if capacity <= 0:
                continue
            key = (getattr(stash_item, "item_id", None), stash_item.rarity)
            candidates = remaining_inv.get(key, [])
            for inv_item in list(candidates):
                if id(inv_item) in removal_ids:
                    continue
                inv_qty = max(1, getattr(inv_item, "quantity", 1))
                if inv_qty <= capacity:
                    new_instructions.append((inv_item, stash_item))
                    stash_item.quantity = min(
                        max_stack,
                        getattr(stash_item, "quantity", 1) + inv_qty,
                    )
                    capacity -= inv_qty
                    removal_ids.add(id(inv_item))
                    if capacity <= 0:
                        break

        # ── Housekeeping ────────────────────────────────────────────
        for item in buffered:
            if id(item) in removal_ids:
                self._buffered_inventory.pop(id(item), None)
                self._release_reserved_slots(item)

        # Prepend so transfer stacking runs before any stash-internal stacking
        if new_instructions:
            self._stack_instructions = new_instructions + self._stack_instructions
            logger.info(
                "Transfer pre-stacking: %d merge instruction(s), "
                "%d redundant item(s) removed",
                len(new_instructions),
                len(removal_ids),
            )

    def _record_failure_reason(self, reason: str) -> None:
        if not self._failure_reason:
            self._failure_reason = reason

    def _finalize_feedback(self, success: bool, failure_reason: Optional[str], cancelled: bool) -> None:
        if not self.feedback_handle:
            return
        try:
            summary = self.feedback_handle.finalize(
                success=bool(success),
                cancelled=bool(cancelled),
                failure_reason=failure_reason or self._failure_reason,
            )
            self._session_summary = summary
        except Exception as exc:
            logger.debug("Failed to finalize sort feedback: %s", exc, exc_info=True)

    def get_feedback_summary(self) -> Optional[Dict[str, Any]]:
        return self._session_summary

    def sort(
        self,
        cancel_event=None,
        overlay_session: Optional[Union[SortOverlaySession, NullOverlaySession]] = None,
    ):
        success = False
        failure_reason: Optional[str] = None
        self.cancel_event = cancel_event
        self.overlay_session = overlay_session or NullOverlaySession()
        self._reset_move_tracking()
        if cancel_event:
            macros.push_cancel_event(cancel_event)

        try:
            # ── Safety monitor: abort sort on focus loss or mouse interference
            self._safety_monitor = SortSafetyMonitor(cancel_event)
            self._safety_monitor.start()

            if self._stack_instructions:
                self._overlay_update("Merging stackable items before sorting...", status="info")
                if not self._perform_stacking_phase():
                    failure_reason = failure_reason or "stacking_failed"
                    self._overlay_log("Stacking phase failed; aborting sort.")
                    return False

            self._overlay_update("Preparing workspace...", status="info")
            self._ensure_initial_workspace(self._workspace_min_free_cells)
            self._safety_snapshot()
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

            if plan and self.learning_manager and self._learning_session_id:
                try:
                    plan_map = {}
                    feature_map = {}
                    for entry in plan.order:
                        itm = entry.item
                        item_id = str(getattr(itm, "item_id", "") or "")
                        if item_id:
                            plan_map[item_id] = (entry.target.x, entry.target.y)
                        
                        # LayoutPlanner stores `learning_payload[id(itm)] = record`
                        if plan.learning:
                            record = plan.learning.get(id(itm))
                            if record and "features" in record:
                                feature_map[item_id] = record["features"]

                    self.learning_manager.register_pending_plan(
                        self._learning_session_id,
                        plan_map,
                        feature_map
                    )
                except Exception as exc:
                    logger.warning("Failed to register sort plan for learning: %s", exc)

            if plan is None:
                failure_reason = failure_reason or "layout_plan_failed"
                self._overlay_update("Unable to calculate layout plan.", status="error")
                self._overlay_log("Unable to create deterministic layout; aborting sort.")
                return False

            plan_size = len(plan.order)
            self._plan_required_moves = self._count_required_moves(plan)
            self._move_progress_total = self._plan_required_moves
            self._overlay_update_overview(total_items, plan_size, self._plan_required_moves)
            mode_label = "dense pack" if self.pack_mode else "scanline"
            stack_label = "stacking on" if self.stack_mode else "stacking off"
            self._overlay_log(
                f"Layout plan ready for {plan_size} items ({mode_label}, {stack_label})."
            )
            self._overlay_update(
                f"Executing layout plan for {plan_size} items...",
                status="info",
            )
            success = self._execute_plan(plan, total_items)
            if not success and failure_reason is None:
                failure_reason = self._failure_reason or "execution_failed"
            return success
        except macros.MacroCancelled:
            safety_reason = self._safety_monitor.reason if self._safety_monitor else None
            if safety_reason == "game_window_unfocused":
                failure_reason = failure_reason or "safety_focus_lost"
                logger.info("Sort operation stopped by safety monitor: game window lost focus")
                self._overlay_update(
                    "Sort stopped — game window lost focus.",
                    status="warning",
                )
                self._overlay_log(
                    "Sort stopped because the game window lost focus. "
                    "Keep the game in the foreground during sorting. "
                    "Refresh your character data to resynchronize."
                )
            elif safety_reason == "mouse_interference":
                failure_reason = failure_reason or "safety_mouse_interference"
                logger.info("Sort operation stopped by safety monitor: mouse interference detected")
                self._overlay_update(
                    "Sort stopped — mouse movement detected.",
                    status="warning",
                )
                self._overlay_log(
                    "Sort stopped because manual mouse movement was detected. "
                    "Avoid moving the mouse while sorting is in progress. "
                    "Refresh your character data to resynchronize."
                )
            else:
                failure_reason = failure_reason or "macro_cancelled"
                logger.info("Sort operation cancelled during macro execution")
                self._overlay_update(
                    "Sort cancelled. Refresh your character data to resynchronize.",
                    status="warning",
                )
                self._overlay_log(
                    "Sort cancelled. Macro actions stopped immediately. "
                    "Refresh your character data to resynchronize."
                )
            return False
        finally:
            if self._safety_monitor:
                self._safety_monitor.stop()
                self._safety_monitor = None
            cancelled_flag = bool(cancel_event and cancel_event.is_set()) or failure_reason == "macro_cancelled"
            self._finalize_feedback(success, failure_reason, cancelled_flag)
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

    # --------------------------------------------------------- safety helpers
    def _safety_check(self) -> bool:
        """Check cancel_event and (if active) the safety monitor.

        Returns ``True`` when the sort should continue.  When it returns
        ``False`` an appropriate log/overlay message has already been
        emitted and the caller should abort the current phase.
        """
        if self.cancel_event and self.cancel_event.is_set():
            safety_reason = self._safety_monitor.reason if self._safety_monitor else None
            if safety_reason == "game_window_unfocused":
                self._record_failure_reason("safety_focus_lost")
                self._overlay_log("Sort stopped — the game window lost focus.")
            elif safety_reason == "mouse_interference":
                self._record_failure_reason("safety_mouse_interference")
                self._overlay_log("Sort stopped — mouse interference detected.")
            else:
                self._record_failure_reason("cancelled")
                self._overlay_log("Sort cancelled by user.")
            logger.info("Sort safety check failed (reason=%s)", safety_reason or "user_cancel")
            return False

        if self._safety_monitor and not self._safety_monitor.checkpoint():
            # checkpoint() already set the cancel_event
            safety_reason = self._safety_monitor.reason
            if safety_reason == "mouse_interference":
                self._record_failure_reason("safety_mouse_interference")
                self._overlay_log("Sort stopped — mouse interference detected.")
            else:
                self._record_failure_reason("safety_unknown")
                self._overlay_log("Sort stopped by safety monitor.")
            logger.info("Sort safety checkpoint triggered cancellation (reason=%s)", safety_reason)
            return False

        return True

    def _safety_snapshot(self) -> None:
        """Record the current cursor position as the expected baseline."""
        if self._safety_monitor:
            self._safety_monitor.snapshot_position()

    def _reset_move_tracking(self) -> None:
        self._plan_required_moves = 0
        self._move_progress_total = 0
        self._move_progress_done = 0
        self._pending_move_tokens.clear()
        self._completed_move_tokens.clear()

    def _overlay_progress(self, processed: int, total: int) -> None:
        session = self.overlay_session
        if not session or not hasattr(session, "update_progress"):
            return
        baseline = total or self._move_progress_total or self._plan_required_moves
        if baseline <= 0:
            baseline = 1
        try:
            session.update_progress(processed, baseline)
        except Exception:
            pass

    def _overlay_update_overview(self, total_items: int, plan_size: int, move_budget: Optional[int]) -> None:
        session = self.overlay_session
        if not session or not hasattr(session, "update_sort_overview"):
            return
        try:
            workspace_free = self._count_empty_cells(self.stash) if self.stash else None
        except Exception:
            workspace_free = None
        buffered_items = len(self._buffered_inventory)
        difficulty = self._estimate_sort_difficulty(
            total_items=total_items,
            plan_size=plan_size,
            workspace_free=workspace_free,
        )
        try:
            session.update_sort_overview(
                total_items=total_items,
                plan_moves=plan_size,
                move_budget=move_budget,
                pack_mode=self.pack_mode,
                stack_mode=self.stack_mode,
                workspace_free=workspace_free,
                workspace_target=self._workspace_min_free_cells,
                buffered_items=buffered_items,
                difficulty_label=difficulty["label"],
                difficulty_score=difficulty["score"],
                difficulty_reason=difficulty["reason"],
                difficulty_status=difficulty["status"],
            )
            if move_budget is not None and move_budget <= 0:
                session.update_progress(1, 1)
            else:
                baseline_total = max(1, (move_budget if move_budget is not None else plan_size) or total_items or 1)
                session.update_progress(0, baseline_total)
        except Exception:
            pass

    def _count_required_moves(self, plan: LayoutPlan) -> int:
        required = 0
        self._pending_move_tokens = set()
        self._completed_move_tokens = set()
        for entry in plan.order:
            if entry.item.position != entry.target or entry.item.stash is not self.stash:
                required += 1
                self._pending_move_tokens.add(id(entry.item))
        self._move_progress_done = 0
        return required

    def _record_item_finalized(self, item: Item) -> None:
        self._log_learning_outcome(item, True, None)
        if self._move_progress_total <= 0:
            return
        token = id(item)
        if not self._pending_move_tokens or token not in self._pending_move_tokens:
            return
        if token in self._completed_move_tokens:
            return
        self._completed_move_tokens.add(token)
        self._pending_move_tokens.discard(token)
        self._move_progress_done += 1
        self._overlay_progress(self._move_progress_done, self._move_progress_total)
        if (
            self._move_progress_done == 1
            or self._move_progress_done == self._move_progress_total
            or self._move_progress_done % 5 == 0
        ):
            self._overlay_update(
                f"Sorting stash items... ({self._move_progress_done}/{self._move_progress_total} moves)",
                status="info",
            )

    def _log_learning_outcome(self, item: Item, success: bool, reason: Optional[str]) -> None:
        if not self.learning_manager:
            return
        token = id(item)
        record = self._plan_learning_records.get(token)
        if not isinstance(record, dict):
            return
        metadata = dict(record)
        if reason:
            metadata.setdefault("resultReason", reason)
        try:
            self.learning_manager.record_priority_outcome(
                session_id=self._learning_session_id,
                item=item,
                success=success,
                reason=reason,
                metadata=metadata,
            )
        except Exception:
            return
        self._plan_learning_records.pop(token, None)

    def _estimate_sort_difficulty(
        self,
        *,
        total_items: int,
        plan_size: int,
        workspace_free: Optional[int],
    ) -> Dict[str, Any]:
        capacity = max(1, (self.stash.width * self.stash.height) if self.stash else 1)
        fill_ratio = min(1.0, total_items / float(capacity)) if capacity else 0.0
        move_reference = max(1, total_items) if total_items else max(1, plan_size, capacity)
        move_density = min(1.0, plan_size / float(move_reference))
        workspace_value = max(0, workspace_free) if workspace_free is not None else 0
        workspace_pressure = 1.0 - min(1.0, workspace_value / float(capacity))
        score = 0.2 + (fill_ratio * 0.4) + (move_density * 0.25) + (workspace_pressure * 0.15)
        if self.pack_mode:
            score += 0.1
        if not self.stack_mode and fill_ratio > 0.6:
            score += 0.05
        score = max(0.0, min(score, 1.0))

        if score < 0.35:
            label = "Easy"
            status = "success"
        elif score < 0.7:
            label = "Standard"
            status = "info"
        else:
            label = "Challenging"
            status = "warning"

        reasons: List[str] = []
        if fill_ratio > 0.85:
            reasons.append("Stash nearly full")
        elif fill_ratio > 0.7:
            reasons.append("High fill level")
        if move_density > 0.75:
            reasons.append("Large move set")
        if workspace_pressure > 0.5:
            reasons.append("Tight workspace")
        if self.pack_mode:
            reasons.append("Dense pack enabled")
        if not self.stack_mode and fill_ratio > 0.6:
            reasons.append("Stacking disabled")
        if not reasons:
            reasons.append("Routine layout")

        return {
            "label": label,
            "status": status,
            "score": score,
            "reason": " · ".join(reasons),
        }

    def _build_sort_plan(self, items: List[Item]) -> Optional[LayoutPlan]:
        if not items:
            self._plan_positions.clear()
            self._plan_order.clear()
            self._cell_plan_rank.clear()
            self._rank_lookup.clear()
            self._plan_learning_records = {}
            return LayoutPlan(positions={}, order=[], learning={})

        comparators: List[Optional[Callable[[Item, Item], int]]] = []
        if Item.sort_order:
            comparators.append(Item.build_sort_comparator(Item.sort_order))
        comparators.append(None)

        self._plan_learning_records = {}
        plan: Optional[LayoutPlan] = None
        fallback_used = False
        for comparator in comparators:
            planner = LayoutPlanner(
                self.stash.width,
                self.stash.height,
                prefer_dense=self.pack_mode,
                stash=self.stash,
                stack_mode=self.stack_mode,
                learning_manager=self.learning_manager,
                learning_session_id=self._learning_session_id,
            )
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
        self._plan_learning_records = plan.learning or {}
        self._cell_plan_rank = {}
        self._rank_lookup = {}
        for index, entry in enumerate(plan.order):
            self._rank_lookup[id(entry.item)] = index
            for dx in range(entry.item.width):
                for dy in range(entry.item.height):
                    key = (entry.target.x + dx, entry.target.y + dy)
                    self._cell_plan_rank[key] = index
        try:
            self._metric_set("plan_size", float(len(plan.order)))
            if plan.order:
                largest_area = max(entry.item.width * entry.item.height for entry in plan.order)
                self._metric_set("largest_item_area", float(largest_area))
        except Exception:
            pass
        return plan

    def _execute_plan(self, plan: LayoutPlan, _total_items: int) -> bool:
        for index, entry in enumerate(plan.order):
            self._current_plan_index = index

            # ── Safety & cancellation gate ──────────────────────────
            if not self._safety_check():
                return False

            if entry.item.position == entry.target and entry.item.stash is self.stash:
                logger.debug("%s already at %s", entry.item, entry.target)
                self._log_learning_outcome(entry.item, True, "already_aligned")
                continue

            if not self._move_item_to_target(entry.item, entry.target, set()):
                logger.error("Failed to relocate %s to %s", entry.item, entry.target)
                self._overlay_log("Unable to place an item in its planned slot; aborting sort.")
                self._log_learning_outcome(entry.item, False, "move_failed")
                return False

            self._safety_snapshot()

        self._overlay_update("Sorting complete.", status="success")
        return True

    def _move_item_to_target(self, item: Item, target_point: Point, lineage: Set[int]) -> bool:
        token = id(item)
        if token in lineage:
            logger.debug("Detected cycle while moving %s; attempting to park it", item)
            return self._park_blocker(item)

        if item.position == target_point and item.stash is self.stash:
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
        planned_target = self._plan_positions.get(token)
        if planned_target and planned_target == target_point:
            self._record_item_finalized(item)
        return True

    def _park_blocker(self, blocker: Item) -> bool:
        if blocker is None:
            return True

        self._metric_increment("park_attempts")

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
        self._metric_increment("park_failures")
        self._record_failure_reason("park_blocker_failed")
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

    def _ensure_initial_workspace(self, min_free_cells: Optional[int] = None, max_buffer_moves: int = 8):
        if not self.inv:
            return

        target_free = min_free_cells if min_free_cells is not None else self._workspace_min_free_cells
        if target_free is None:
            target_free = 6
        free_cells = self._count_empty_cells(self.stash)
        self._feature_set("initial_free_cells", float(free_cells))
        self._feature_set("workspace_min_free_cells", float(target_free))
        if free_cells >= target_free:
            return

        logger.info("Preparing workspace: current free cells %s, target %s", free_cells, target_free)

        candidates = [
            itm for itm in list(self.stash.pq)
            if itm.stash is self.stash and not getattr(itm, "stacked", False)
        ]

        candidates.sort(key=lambda itm: (itm.width * itm.height, getattr(itm, "rarity", 0)))

        moves = 0
        for candidate in candidates:
            if free_cells >= target_free or moves >= max_buffer_moves:
                break

            inv_slot = self.inv.find_empty_slot(candidate)
            if inv_slot is None:
                continue

            logger.debug("Buffering item %s to inventory slot %s to create workspace", candidate, inv_slot)
            candidate.stash.move(candidate, inv_slot, self.inv)
            self._mark_buffered_inventory(candidate)
            moves += 1
            free_cells += candidate.width * candidate.height

        self._metric_set("workspace_preparation_moves", float(moves))
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
            if not self._safety_check():
                return False
            if not self._stack_item(item, target):
                logger.error("Failed to stack items; aborting sort")
                return False
            self._safety_snapshot()
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
                    self._record_failure_reason("workspace_creation_failed")
                    return False

                moved_items.add(occupying_item)

        return True


    def _create_workspace_for(self, target_item: Item | None, blocking_item: Item) -> bool:
        if not self.inv:
            return False

        self._metric_increment("workspace_creation_attempts")

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
                self._metric_increment("workspace_creation_successes")
                return True

        self._metric_increment("workspace_creation_failures")
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
        already_buffered = id(item) in self._buffered_inventory
        setattr(item, "_buffered_by_sort", True)
        self._buffered_inventory[id(item)] = item
        self._blocker_move_counts.pop(id(item), None)
        self._reserve_inventory_slots(item)
        if not already_buffered:
            self._metric_increment("buffered_items")

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
