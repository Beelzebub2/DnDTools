"""
Test infrastructure for the sort system.

Mocks platform-specific modules (keyboard, pygetwindow, etc.) so that
sort components can be imported and tested without a game running.
Provides MockStorage and item factories for unit tests.
"""

import sys
import types
from pathlib import Path
from typing import Optional, Set, Tuple

# ── Patch heavy / platform modules BEFORE any src.models import ──────────

_STUB_MODULES = [
    "keyboard",
    "pygetwindow",
    "pyautogui",
    "win32gui",
    "win32con",
    "win32api",
    "ctypes",
    "ctypes.wintypes",
]

for _mod_name in _STUB_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# Ensure keyboard stub has add_hotkey
_kb = sys.modules["keyboard"]
if not hasattr(_kb, "add_hotkey"):
    _kb.add_hotkey = lambda *a, **kw: None

# Ensure pygetwindow stub has getAllWindows
_gw = sys.modules["pygetwindow"]
if not hasattr(_gw, "getAllWindows"):
    _gw.getAllWindows = lambda: []

# ── Add UI/ to sys.path so `from src.models…` resolves ──────────────────

_UI_DIR = str(Path(__file__).resolve().parent.parent)
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

# ── Mock macros module so it doesn't try to open windows ─────────────────

_macros_mod = sys.modules.get("src.models.macros")
if _macros_mod is None:
    _macros_mod = types.ModuleType("src.models.macros")
    sys.modules["src.models.macros"] = _macros_mod

# Provide minimal stubs used by sort.py and storage.py
if not hasattr(_macros_mod, "move_from_to_reliable"):
    _macros_mod.move_from_to_reliable = lambda *a, **kw: None
if not hasattr(_macros_mod, "get_screen_positions"):
    _macros_mod.get_screen_positions = lambda: {"stash": (0, 0), "inv": (0, 0)}
if not hasattr(_macros_mod, "push_cancel_event"):
    _macros_mod.push_cancel_event = lambda *a, **kw: None
if not hasattr(_macros_mod, "pop_cancel_event"):
    _macros_mod.pop_cancel_event = lambda *a, **kw: None
if not hasattr(_macros_mod, "MacroCancelled"):
    class _MacroCancelled(Exception):
        pass
    _macros_mod.MacroCancelled = _MacroCancelled
if not hasattr(_macros_mod, "nudge_cursor"):
    _macros_mod.nudge_cursor = lambda dx=15, dy=0: None
if not hasattr(_macros_mod, "jump"):
    _macros_mod.jump = 40.5
if not hasattr(_macros_mod, "click_stash_tab"):
    _macros_mod.click_stash_tab = lambda *a, **kw: True
if not hasattr(_macros_mod, "STASH_TYPE_TO_TAB_INDEX"):
    _macros_mod.STASH_TYPE_TO_TAB_INDEX = {
        4: 0, 20: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 30: 7,
    }
if not hasattr(_macros_mod, "STASH_TAB_LABELS"):
    _macros_mod.STASH_TAB_LABELS = [
        'Storage', 'Shared Stash', 'Purchased 1', 'Purchased 2',
        'Purchased 3', 'Purchased 4', 'Purchased 5', 'Shared Seasonal',
    ]
if not hasattr(_macros_mod, "DEFAULT_STASH_TAB_MAPPING"):
    _macros_mod.DEFAULT_STASH_TAB_MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]
if not hasattr(_macros_mod, "STASH_TYPE_NAMES"):
    _macros_mod.STASH_TYPE_NAMES = {
        4: 'Storage', 5: 'Purchased 1', 6: 'Purchased 2',
        7: 'Purchased 3', 8: 'Purchased 4', 9: 'Purchased 5',
        20: 'Shared Stash', 30: 'Shared Seasonal',
    }
if not hasattr(_macros_mod, "load_tab_mapping"):
    _macros_mod.load_tab_mapping = lambda: None

# ── Mock game_overlay ────────────────────────────────────────────────────

_overlay_mod = sys.modules.get("src.models.game_overlay")
if _overlay_mod is None:
    _overlay_mod = types.ModuleType("src.models.game_overlay")
    sys.modules["src.models.game_overlay"] = _overlay_mod

if not hasattr(_overlay_mod, "OverlayChip"):
    from dataclasses import dataclass as _dataclass, field as _field

    @_dataclass
    class _OverlayChip:
        label: str
        value: str
        detail: str = ""
        status: str = "info"
    _overlay_mod.OverlayChip = _OverlayChip

if not hasattr(_overlay_mod, "OverlayState"):
    @_dataclass
    class _OverlayState:
        visible: bool = False
        heading: str = ""
        subtitle: str = ""
        logs: list = _field(default_factory=list)
        status: str = "info"
        chips: list = _field(default_factory=list)
        progress_current: int = 0
        progress_total: int = 0
    _overlay_mod.OverlayState = _OverlayState

if not hasattr(_overlay_mod, "NullOverlaySession"):
    class _NullOverlaySession:
        finished = True
        def wait_for_countdown(self): return True
        def update_status(self, *a, **kw): pass
        def add_log(self, *a, **kw): pass
        def update_progress(self, *a, **kw): pass
        def update_sort_overview(self, **kw): pass
        def finish(self, *a, **kw): pass
        def force_close(self): pass
        def set_chip(self, *a, **kw): pass
    _overlay_mod.NullOverlaySession = _NullOverlaySession

if not hasattr(_overlay_mod, "SortOverlaySession"):
    import threading as _threading

    class _SortOverlaySession:
        def __init__(self, manager, countdown_seconds=0.0, context=None):
            self._manager = manager
            self.countdown_seconds = max(0.0, float(countdown_seconds or 0.0))
            self.context = context or {}
            self.heading = self._build_heading()
            self.subtitle = "Preparing sort overlay..."
            self.status = "warning" if self.countdown_seconds > 0 else "info"
            self.logs = []
            self.chips = []
            self.max_logs = 6
            self._finished = False
            self._progress_current = 0
            self._progress_total = 0
            self._last_log_message = None
            self._last_log_count = 0
            self._chip_store = {}
            self._chip_order = []

        def _build_heading(self):
            ctx = self.context
            if ctx.get("character"):
                name = ctx["character"]
                cls = ctx.get("character_class", "")
                stash = ctx.get("stash", "")
                return f"{name} ({cls}) — Stash {stash}"
            return "Character stash"

        @property
        def finished(self):
            return self._finished

        def wait_for_countdown(self):
            if self.countdown_seconds <= 0:
                self.status = "info"
                return True
            self.status = "info"
            return True

        def update_status(self, subtitle, status="info"):
            if self._finished:
                return
            self.subtitle = subtitle
            self.status = status

        def add_log(self, message):
            if self._finished:
                return
            if not message or not message.strip():
                return
            if len(message) > 160:
                message = message[:160] + "..."
            if message == self._last_log_message:
                self._last_log_count += 1
                self.logs[-1] = f"{message} (x{self._last_log_count})"
            else:
                self._last_log_message = message
                self._last_log_count = 1
                self.logs.append(message)
                if len(self.logs) > self.max_logs:
                    self.logs = self.logs[-self.max_logs:]

        def finish(self, success=True, message=None):
            if self._finished:
                return
            self._finished = True
            self.status = "success" if success else "error"
            if message is not None:
                self.subtitle = message

        def force_close(self):
            self._finished = True

        def set_chip(self, key, *, label, value, detail="", status="info", refresh=True):
            if self._finished:
                return
            if len(value) > 32:
                value = value[:32]
            chip = _overlay_mod.OverlayChip(label=label, value=value, detail=detail, status=status)
            if key in self._chip_store:
                self._chip_store[key] = chip
            else:
                self._chip_store[key] = chip
                self._chip_order.append(key)
            self.chips = [self._chip_store[k] for k in self._chip_order if k in self._chip_store]

        def update_progress(self, processed, total):
            if self._finished:
                return
            self._progress_current = min(processed, total)
            self._progress_total = total

        def update_sort_overview(self, **kw):
            if self._finished:
                return
            self.set_chip("items", label="Items", value=str(kw.get("total_items", 0)))
            mode_parts = []
            if kw.get("pack_mode"):
                mode_parts.append("Pack")
            if kw.get("stack_mode"):
                mode_parts.append("Stack")
            self.set_chip("mode", label="Mode", value=", ".join(mode_parts) if mode_parts else "Sort")
            if kw.get("workspace_free") is not None:
                self.set_chip("workspace", label="Workspace",
                              value=f"{kw['workspace_free']}/{kw.get('workspace_target', '?')}")
            self.set_chip("difficulty", label="Difficulty",
                          value=kw.get("difficulty_label", "?"),
                          status=kw.get("difficulty_status", "info"))
            if kw.get("ml_placement_active"):
                self.set_chip("ml", label="ML", value="Active")

    _overlay_mod.SortOverlaySession = _SortOverlaySession

if not hasattr(_overlay_mod, "GameOverlayManager"):
    class _StubOverlayManager:
        enabled = False
        max_logs = 6
        def __init__(self):
            self._active_session = None
        def hide(self, session=None): pass
        def temporarily_hide_for_capture(self): pass
        def restore_after_capture(self): pass
        def begin_sort_session(self, *a, **kw):
            return _overlay_mod.SortOverlaySession(self, *a, **kw)
        def end_session(self, session): pass
        def schedule_hide(self, session, delay): pass
        def show_message(self, session, **kw): pass
    _overlay_mod.GameOverlayManager = _StubOverlayManager
    _overlay_mod.overlay_manager = _StubOverlayManager()

# ── Mock sort_safety ─────────────────────────────────────────────────────

_safety_mod = sys.modules.get("src.models.sort_safety")
if _safety_mod is None:
    _safety_mod = types.ModuleType("src.models.sort_safety")
    sys.modules["src.models.sort_safety"] = _safety_mod

if not hasattr(_safety_mod, "SortSafetyMonitor"):
    class _SortSafetyMonitor:
        def __init__(self, *a, **kw):
            self.reason = None
        def start(self): pass
        def stop(self): pass
        def checkpoint(self): return True
        def snapshot_position(self): pass
    _safety_mod.SortSafetyMonitor = _SortSafetyMonitor

# ── Mock sort_feedback ───────────────────────────────────────────────────

_fb_mod = sys.modules.get("src.models.sort_feedback")
if _fb_mod is None:
    _fb_mod = types.ModuleType("src.models.sort_feedback")
    sys.modules["src.models.sort_feedback"] = _fb_mod

if not hasattr(_fb_mod, "get_sort_feedback_manager"):
    _fb_mod.get_sort_feedback_manager = lambda: None

# ── Mock sort_learning ───────────────────────────────────────────────────

_learn_mod = sys.modules.get("src.models.sort_learning")
if _learn_mod is None:
    _learn_mod = types.ModuleType("src.models.sort_learning")
    sys.modules["src.models.sort_learning"] = _learn_mod

if not hasattr(_learn_mod, "get_sort_learning_manager"):
    _learn_mod.get_sort_learning_manager = lambda: None
if not hasattr(_learn_mod, "SortLearningManager"):
    _learn_mod.SortLearningManager = type("SortLearningManager", (), {})

# ── Mock stash_preview ───────────────────────────────────────────────────

_sp_mod = sys.modules.get("src.models.stash_preview")
if _sp_mod is None:
    _sp_mod = types.ModuleType("src.models.stash_preview")
    sys.modules["src.models.stash_preview"] = _sp_mod

if not hasattr(_sp_mod, "parse_stashes"):
    _sp_mod.parse_stashes = lambda *a, **kw: {}
if not hasattr(_sp_mod, "StashPreviewGenerator"):
    _sp_mod.StashPreviewGenerator = type("StashPreviewGenerator", (), {})
if not hasattr(_sp_mod, "ItemInfo"):
    _sp_mod.ItemInfo = type("ItemInfo", (), {})

# ── Mock appdirs ─────────────────────────────────────────────────────────

_appdirs_mod = sys.modules.get("src.models.appdirs")
if _appdirs_mod is None:
    _appdirs_mod = types.ModuleType("src.models.appdirs")
    sys.modules["src.models.appdirs"] = _appdirs_mod

if not hasattr(_appdirs_mod, "get_characters_dir"):
    _appdirs_mod.get_characters_dir = lambda: "."
if not hasattr(_appdirs_mod, "resource_path"):
    _appdirs_mod.resource_path = lambda p: p
if not hasattr(_appdirs_mod, "get_output_dir"):
    _appdirs_mod.get_output_dir = lambda: "."

# ── Mock game_data ───────────────────────────────────────────────────────

_gd_mod = sys.modules.get("src.models.game_data")
if _gd_mod is None:
    _gd_mod = types.ModuleType("src.models.game_data")
    sys.modules["src.models.game_data"] = _gd_mod

if not hasattr(_gd_mod, "item_data_manager"):
    class _StubItemDataManager:
        def get_item_id_from_design_str(self, s): return s
        def get_item_dimensions_from_id(self, i): return (1, 1)
        def get_item_rarity_from_id(self, i): return "common"
        def get_item_name_from_id(self, i): return i
        def rarity_to_id(self, r): return 2
        def format_design_id_as_name(self, i): return i
        def get_item_vendor_price(self, i): return 0
        def get_item_max_stack_size(self, i): return 1
    _gd_mod.item_data_manager = _StubItemDataManager()

# ── Mock sort_model ──────────────────────────────────────────────────────

_sm_mod = sys.modules.get("src.models.sort_model")
if _sm_mod is None:
    _sm_mod = types.ModuleType("src.models.sort_model")
    sys.modules["src.models.sort_model"] = _sm_mod

if not hasattr(_sm_mod, "SortAdaptiveModel"):
    class _StubModel:
        base_dir = "."
        def score_item_slot(self, f): return None
        def predict_risk(self, f): return 0.0
        def recommended_workspace_cells(self, **kw): return 6
        def apply_remote_item_model(self, p): return False
        def apply_remote_risk_model(self, p): return False
        def get_item_version(self): return None
        def get_risk_version(self): return None
        def get_item_model_payload(self): return None
        def train_items(self, *a, **kw): pass
        def train_risk(self, *a, **kw): pass
    _sm_mod.SortAdaptiveModel = _StubModel
    _sm_mod.get_sort_adaptive_model = lambda: _StubModel()
    _sm_mod.ITEM_FEATURE_NAMES = []
    _sm_mod.RISK_FEATURE_NAMES = []

# ── Mock sort_event_store ────────────────────────────────────────────────

_se_mod = sys.modules.get("src.models.sort_event_store")
if _se_mod is None:
    _se_mod = types.ModuleType("src.models.sort_event_store")
    sys.modules["src.models.sort_event_store"] = _se_mod

if not hasattr(_se_mod, "SortEventStore"):
    class _StubEventStore:
        def record_item_placement(self, **kw): pass
        def record_user_correction(self, **kw): pass
        def record_sort_started(self, **kw): pass
        def record_sort_completed(self, **kw): pass
        def record_move_outcome(self, *a, **kw): return 0
        def get_item_training_data(self, **kw): return []
        def get_risk_training_data(self, **kw): return []
    _se_mod.SortEventStore = _StubEventStore
    _se_mod.get_event_store = lambda: _StubEventStore()

# ═════════════════════════════════════════════════════════════════════════
# Now safe to import actual project modules
# ═════════════════════════════════════════════════════════════════════════

from src.models.point import Point
from src.models.item import Item

# ── MockStorage ──────────────────────────────────────────────────────────


class MockStorage:
    """
    Lightweight stand-in for Storage that operates entirely in-memory.

    Mirrors the subset of the Storage interface used by the extracted
    sort components: grid, pq, _reserved_slots, width, height, move(),
    and find_empty_slot().
    """

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.stash_type = 4
        self.size = width * height
        self.grid = [[0 for _ in range(height)] for _ in range(width)]
        self.pq = []
        self._reserved_slots: Set[Tuple[int, int]] = set()
        self.base_screen_pos = (0, 0)

    def move(self, item, end_pos, end_stash):
        """In-memory move: update grids and item metadata. No macros."""
        # Clear old location
        for dx in range(item.width):
            for dy in range(item.height):
                self.grid[item.position.x + dx][item.position.y + dy] = 0

        # Place in new location
        for dx in range(item.width):
            for dy in range(item.height):
                end_stash.grid[end_pos.x + dx][end_pos.y + dy] = item

        item.stash = end_stash
        item.position = end_pos

    def find_empty_slot(self, item) -> Optional[Point]:
        """Bottom-right to top-left scan, matching real Storage behaviour."""
        for y in range(self.height - item.height, -1, -1):
            for x in range(self.width - item.width, -1, -1):
                fits = True
                for dx in range(item.width):
                    for dy in range(item.height):
                        slot = (x + dx, y + dy)
                        if slot in self._reserved_slots or self.grid[slot[0]][slot[1]] != 0:
                            fits = False
                            break
                    if not fits:
                        break
                if fits:
                    return Point(x, y)
        return None

    def place_item(self, item):
        """Helper: place an item on the grid at its current position and add to pq."""
        for dx in range(item.width):
            for dy in range(item.height):
                self.grid[item.position.x + dx][item.position.y + dy] = item
        self.pq.append(item)


# ── Factories ────────────────────────────────────────────────────────────


def make_item(
    name: str = "Sword",
    rarity: int = 2,
    x: int = 0,
    y: int = 0,
    w: int = 1,
    h: int = 1,
    stash: Optional[MockStorage] = None,
    item_id: str = "item_001",
    quantity: int = 1,
    max_stack_size: int = 1,
) -> Item:
    """Create an Item wired to the given (or new) MockStorage."""
    if stash is None:
        stash = MockStorage(12, 20)
    pos = Point(x, y)
    item = Item(
        item_id=item_id,
        name=name,
        rarity=rarity,
        position=pos,
        width=w,
        height=h,
        stash=stash,
        quantity=quantity,
        max_stack_size=max_stack_size,
    )
    return item


def make_stash(width: int = 12, height: int = 20) -> MockStorage:
    """Create a blank MockStorage."""
    return MockStorage(width, height)
