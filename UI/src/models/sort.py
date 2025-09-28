from src.models.stash_preview import parse_stashes
import time
from src.models.storage import Storage, StashType
from src.models import macros
import heapq
import keyboard
import os
from src.models.point import Point
from src.models.item import Item
import pygetwindow as gw
import math

def intersects(pos1, width1, height1, pos2, width2, height2):
    if pos1.x + width1 <= pos2.x or pos2.x + width2 <= pos1.x:
        return False
    if pos1.y + height1 <= pos2.y or pos2.y + height2 <= pos1.y:
        return False
    return True

class StashSorter:
    def __init__(self, stash: Storage, inv: Storage, pack_mode: bool = False, stack_mode: bool = False):
        self.stash = stash
        self.inv = inv
        self.cur_x = 0
        self.cur_y = 0
        self.cur_height = 0
        self.cancel_event = None
        self.pack_mode = bool(pack_mode)
        self.stack_mode = bool(stack_mode)
        self._stack_instructions = []
        if self.stack_mode:
            self._prepare_stack_plan()
        self.pack_positions = {}
        if self.pack_mode:
            self.pack_positions = self._compute_pack_plan()
            if not self.pack_positions:
                print("Pack mode plan could not be generated; falling back to sequential layout.")
                self.pack_mode = False

    def sort(self, cancel_event=None):
        self.cancel_event = cancel_event

        if self.stack_mode:
            if not self._perform_stacking_phase():
                return False

        self._ensure_initial_workspace()

        while self.stash.pq:
            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled")
                return False

            item = heapq.heappop(self.stash.pq)
            print("Processing item: ", item)

            planned_point = self.pack_positions.get(id(item)) if self.pack_mode else None
            target_point = planned_point

            if target_point is not None and not self._is_within_bounds(item, target_point):
                print("Planned target position out of bounds; recalculating")
                if self.pack_mode:
                    self.pack_positions.pop(id(item), None)
                target_point = None

            if target_point is None:
                sequential_point = self._compute_next_sequential_position(item)
                if sequential_point is not None and self._is_within_bounds(item, sequential_point):
                    target_point = sequential_point
                else:
                    if sequential_point is None:
                        print("Sequential planner could not provide a position; searching alternatives")
                    else:
                        print("Sequential planner returned out-of-bounds position; searching alternatives")

                    target_point = self._find_next_fittable_slot(item)
                    if target_point is None:
                        target_point = self._find_direct_empty_slot(item)

            if target_point is None or not self._is_within_bounds(item, target_point):
                print("No valid target position found after adjustments; aborting sort")
                return False

            if self.pack_mode:
                self.pack_positions[id(item)] = target_point

            print(f"Target position: {target_point}, Current position: {item.position}")
            if target_point == item.position:
                print("Item already in correct position")
                continue

            if not self._ensure_area_available(item, target_point):
                print("Failed to clear target area; attempting fallback placement")
                fallback_point = self._find_direct_empty_slot(item)
                if fallback_point and fallback_point != target_point:
                    print(f"Fallback slot located at {fallback_point}")
                    if self.pack_mode:
                        self.pack_positions[id(item)] = fallback_point
                    if not self._ensure_area_available(item, fallback_point):
                        print("Fallback slot blocked; aborting sort")
                        return False
                    target_point = fallback_point
                else:
                    print("No suitable fallback slot available; aborting sort")
                    return False
            else:
                blockers = self._collect_blocking_items(item, target_point)
                if blockers:
                    print(f"Target area still obstructed after clearing by {[repr(b) for b in blockers]}")
                    if not self._force_clear_blockers(item, blockers, target_point):
                        print("Unable to relocate remaining blockers; aborting sort")
                        return False

            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled before final placement")
                return False

            item.stash.move(item, target_point, self.stash)
            print(f"Current stash state:\n{self.stash}")

            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled after item placement")
                return False

        return True

    def _ensure_initial_workspace(self, min_free_cells: int = 6, max_buffer_moves: int = 8):
        if not self.inv:
            return

        free_cells = self._count_empty_cells(self.stash)
        if free_cells >= min_free_cells:
            return

        print(f"Preparing workspace: current free cells {free_cells}, target {min_free_cells}")

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

            print(f"Buffering item {candidate} to inventory slot {inv_slot} to create workspace")
            candidate.stash.move(candidate, inv_slot, self.inv)
            moves += 1
            free_cells += candidate.width * candidate.height

        print(f"Workspace preparation complete. Free cells: {free_cells}, items buffered: {moves}")

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
                print("Sort operation cancelled during stacking phase")
                return False
            if not self._stack_item(item, target):
                print("Failed to stack items; aborting sort")
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
            print(f"Stacking move failed: {exc}")
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
                    print("Target position out of bounds")
                    return False

                occupying_item = self.stash.grid[x][y]
                if occupying_item == 0 or occupying_item == item or occupying_item in moved_items:
                    continue

                if self.cancel_event and self.cancel_event.is_set():
                    print("Sort operation cancelled during area clearing")
                    return False

                new_pos = self.stash.find_empty_slot(occupying_item)
                if new_pos:
                    print(f"Moving {occupying_item} to empty slot in stash")
                    self.stash.move(occupying_item, new_pos, self.stash)
                else:
                    new_pos = self.inv.find_empty_slot(occupying_item)
                    if new_pos:
                        print(f"Moving {occupying_item} to inventory")
                        self.stash.move(occupying_item, new_pos, self.inv)
                    else:
                        print("No immediate positions found; attempting to create workspace")
                        if self._create_workspace_for(item, occupying_item):
                            return self._ensure_area_available(item, target_point)
                        print("Workspace creation failed; aborting")
                        return False

                moved_items.add(occupying_item)

        return True

    def _compute_next_sequential_position(self, item: Item):
        if self.cur_height == 0:
            self.cur_height = item.height

        if self.cur_x + item.width > self.stash.width:
            self.cur_y += self.cur_height
            self.cur_x = 0
            self.cur_height = item.height

        if self.cur_y + item.height > self.stash.height:
            print("Sequential layout ran out of space")
            return None

        target_point = Point(self.cur_x, self.cur_y)
        self.cur_x += item.width
        self.cur_height = max(self.cur_height, item.height)
        return target_point

    def _find_direct_empty_slot(self, item: Item):
        max_x = self.stash.width - item.width
        max_y = self.stash.height - item.height
        for y in range(max_y + 1):
            for x in range(max_x + 1):
                fits = True
                for dx in range(item.width):
                    for dy in range(item.height):
                        if self.stash.grid[x + dx][y + dy] != 0:
                            fits = False
                            break
                    if not fits:
                        break
                if fits:
                    return Point(x, y)
        return None

    def _compute_pack_plan(self):
        plan = {}
        occupancy = [[False for _ in range(self.stash.width)] for _ in range(self.stash.height)]
        temp_heap = list(self.stash.pq)
        heapq.heapify(temp_heap)

        while temp_heap:
            item = heapq.heappop(temp_heap)
            placed = False
            for y in range(0, self.stash.height - item.height + 1):
                for x in range(0, self.stash.width - item.width + 1):
                    fits = True
                    for dy in range(item.height):
                        for dx in range(item.width):
                            if occupancy[y + dy][x + dx]:
                                fits = False
                                break
                        if not fits:
                            break
                    if fits:
                        plan[id(item)] = Point(x, y)
                        for dy in range(item.height):
                            for dx in range(item.width):
                                occupancy[y + dy][x + dx] = True
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                print(f"Unable to find pack position for item {item}; aborting pack plan")
                return {}
        return plan

    def _is_within_bounds(self, item: Item, point: Point) -> bool:
        if item is None or point is None:
            return False
        return (
            0 <= point.x and
            0 <= point.y and
            point.x + item.width <= self.stash.width and
            point.y + item.height <= self.stash.height
        )

    def _find_next_fittable_slot(self, item: Item):
        if item is None:
            return None

        max_x = self.stash.width - item.width
        max_y = self.stash.height - item.height

        if max_x < 0 or max_y < 0:
            return None

        start_y = min(self.cur_y, max_y)
        for y in range(start_y, max_y + 1):
            start_x = self.cur_x if (y == self.cur_y and self.cur_y <= max_y) else 0
            start_x = min(start_x, max_x)
            for x in range(start_x, max_x + 1):
                candidate = Point(x, y)
                if self._is_within_bounds(item, candidate):
                    return candidate

        if start_y > 0:
            for y in range(0, start_y):
                for x in range(0, max_x + 1):
                    candidate = Point(x, y)
                    if self._is_within_bounds(item, candidate):
                        return candidate

        return None

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

            print(f"Creating workspace: moving {candidate} to inventory slot {inv_slot}")
            candidate.stash.move(candidate, inv_slot, self.inv)
            moves_attempted += 1

            reassigned_slot = self.stash.find_empty_slot(blocking_item)
            if reassigned_slot:
                print(f"Relocating blocking item {blocking_item} to {reassigned_slot}")
                blocking_item.stash.move(blocking_item, reassigned_slot, self.stash)
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
                    print(f"Relocating blocker {blocker} to {new_slot}")
                    self.stash.move(blocker, new_slot, self.stash)
                    if self.pack_mode:
                        self.pack_positions[id(blocker)] = new_slot
                    continue

                inv_slot = self.inv.find_empty_slot(blocker) if self.inv else None
                if inv_slot:
                    print(f"Relocating blocker {blocker} to inventory slot {inv_slot}")
                    self.stash.move(blocker, inv_slot, self.inv)
                    if self.pack_mode:
                        self.pack_positions.pop(id(blocker), None)
                    continue

                print(f"Blocker {blocker} has no immediate relocation; attempting workspace creation")
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


def main():
    def force_exit():
        print("F7 pressed. Exiting...")
        os._exit(0)
    keyboard.add_hotkey('F7', force_exit)

    # Focus the 'Dark and Darker' window before sorting
    windows = [w for w in gw.getAllWindows() if w.title == "Dark and Darker"]
    if windows:
        try:
            windows[0].activate()
            print("Focused window: Dark and Darker")
        except Exception as e:
            print(f"Error focusing window: {e}")
    else:
        print("No window with exact title 'Dark and Darker' found.")

    time.sleep(2)

    # not working
    stashes = parse_stashes({})

    stash = Storage(StashType.STORAGE.value, stashes[StashType.STORAGE.value])
    bag = stashes.get(StashType.BAG.value, [])
    inv = Storage(StashType.BAG.value, bag)

    sorter = StashSorter(stash, inv)

    print(stash)
    print(inv)
    #exit()
    sorter.sort()

if __name__ == "__main__":
    main()
