"""Regression tests for stable item-search and stash-sort ordering."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


UI_DIR = str(Path(__file__).resolve().parents[1])
if UI_DIR not in sys.path:
    sys.path.insert(0, UI_DIR)

from src.models.item import Item  # noqa: E402
from src.models.point import Point  # noqa: E402
from src.models.search_sort import search_result_sort_key  # noqa: E402
from src.models.stash_manager import StashManager  # noqa: E402
import src.models.stash_manager as stash_manager_module  # noqa: E402


def _result(name, rarity, nickname, stash_id, slot_id):
    return {
        "nickname": nickname,
        "id": nickname.lower().replace(" ", "-"),
        "stash_id": stash_id,
        "slotId": slot_id,
        "item": {
            "name": name,
            "rarity": rarity,
            "pp": [],
            "sp": [],
        },
    }


def test_search_results_are_stable_and_not_capture_time_order():
    # Deliberately use the opposite of the expected display order. Character
    # cache order follows file mtimes and must not leak into API result order.
    results = [
        _result("Zebra Weapon", "Common", "Newest Capture", "20", 8),
        _result("Alpha Weapon", "Poor", "Newest Capture", "4", 9),
        _result("Alpha Weapon", "Epic", "Older Capture", "2", 1),
    ]

    results.sort(key=search_result_sort_key)

    assert [
        (entry["item"]["name"], entry["item"]["rarity"], entry["nickname"])
        for entry in results
    ] == [
        ("Alpha Weapon", "Epic", "Older Capture"),
        ("Alpha Weapon", "Poor", "Newest Capture"),
        ("Zebra Weapon", "Common", "Newest Capture"),
    ]


def test_search_result_tiebreaks_numeric_stash_and_slot_ids():
    results = [
        _result("Potion", "Common", "Hero", "20", "12"),
        _result("Potion", "Common", "Hero", "4", "9"),
        _result("Potion", "Common", "Hero", "4", "2"),
    ]

    results.sort(key=search_result_sort_key)

    assert [(entry["stash_id"], entry["slotId"]) for entry in results] == [
        ("4", "2"),
        ("4", "9"),
        ("20", "12"),
    ]


def test_equal_sort_fields_do_not_use_process_memory_as_tiebreaker():
    """Equivalent items preserve stable input order across app restarts."""
    first = Item("potion", "Potion", 2, Point(0, 0), 1, 1, None)
    second = Item("potion", "Potion", 2, Point(5, 5), 1, 1, None)

    assert Item.compare_items(first, second) == 0
    assert Item.compare_items(second, first) == 0


def test_structured_rarity_filter_is_exact_but_free_text_remains_substring(monkeypatch):
    """The rarity dropdown must not turn Common into a match for Uncommon."""

    metadata = {
        "common-sword": {"name": "Training Sword", "rarity": "Common"},
        "uncommon-sword": {"name": "Training Sword", "rarity": "Uncommon"},
        "legendary-sword": {"name": "Training Sword", "rarity": "Legendary"},
    }

    class FakeItemDataManager:
        @staticmethod
        def get_item_id_from_design_str(design_id):
            return design_id

        @staticmethod
        def get_item_data(item_id):
            return metadata[item_id]

        @staticmethod
        def format_design_id_as_name(item_id):
            return item_id

    monkeypatch.setattr(stash_manager_module, "item_data_manager", FakeItemDataManager())

    manager = object.__new__(StashManager)
    manager._is_loaded = True
    manager._cache_lock = threading.Lock()
    manager.get_characters = lambda: [{
        "id": "fighter-1",
        "nickname": "Fighter",
        "class": "Fighter",
        "level": 20,
        "stashes": {
            "4": [
                {"itemId": "uncommon-sword", "itemCount": 1, "slotId": 2, "data": {}},
                {"itemId": "common-sword", "itemCount": 1, "slotId": 1, "data": {}},
                {"itemId": "legendary-sword", "itemCount": 1, "slotId": 3, "data": {}},
            ]
        },
    }]

    # Preserve the historic free-text behavior: typed terms are substrings.
    assert [entry["item"]["rarity"] for entry in manager.search_items("common")] == [
        "Uncommon",
        "Common",
    ]

    # The dropdown is a structured, case-insensitive exact-match filter.
    common_results = manager.search_items("", rarity="  cOmMoN ")
    assert [entry["item"]["rarity"] for entry in common_results] == ["Common"]

    uncommon_results = manager.search_items("training", rarity="Uncommon")
    assert [entry["item"]["rarity"] for entry in uncommon_results] == ["Uncommon"]

    # The existing UI spells this option "Legend" while item assets use
    # "Legendary"; the structured filter retains that compatibility alias.
    legendary_results = manager.search_items("training", rarity="Legend")
    assert [entry["item"]["rarity"] for entry in legendary_results] == ["Legendary"]


def test_search_without_text_or_structured_filter_returns_no_results():
    manager = object.__new__(StashManager)
    manager._is_loaded = True
    manager.get_characters = lambda: [{"stashes": {}}]

    assert manager.search_items("") == []
    assert manager.search_items("  ", rarity="  ") == []


def test_concurrent_initial_load_publishes_one_complete_snapshot(tmp_path, monkeypatch):
    for index in range(2):
        (tmp_path / f"character-{index}.json").write_text(json.dumps({
            "characterDataBase": {
                "characterId": f"character-{index}",
                "characterClass": "DesignDataPlayerCharacter:Id_PlayerCharacter_Fighter",
                "nickName": {"originalNickName": f"Hero {index}"},
            }
        }), encoding="utf-8")

    manager = object.__new__(StashManager)
    manager.data_dir = str(tmp_path)
    manager.characters_cache = {"old": {"nickname": "Previous complete snapshot"}}
    manager._is_loaded = False
    manager._cache_lock = threading.Lock()
    manager._load_lock = threading.RLock()
    manager.load_stats = {}
    manager._precompute_priority_stashes = lambda _char: None

    parse_started = threading.Event()
    allow_parse = threading.Event()
    count_lock = threading.Lock()
    parse_count = 0

    def blocking_parse(_packet):
        nonlocal parse_count
        with count_lock:
            parse_count += 1
            parse_started.set()
        assert allow_parse.wait(timeout=5)
        return {}

    monkeypatch.setattr(stash_manager_module, "parse_stashes", blocking_parse)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager._load_data)
        assert parse_started.wait(timeout=5)
        second = pool.submit(manager._load_data)

        # No reader can observe a half-populated replacement cache.
        with manager._cache_lock:
            assert manager.characters_cache == {
                "old": {"nickname": "Previous complete snapshot"}
            }
        allow_parse.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert parse_count == 2
    assert set(manager.characters_cache) == {"character-0", "character-1"}
    assert manager._is_loaded is True


def test_deferred_character_deep_link_getters_wait_for_initial_load():
    manager = object.__new__(StashManager)
    manager.characters_cache = {}
    manager._is_loaded = False
    manager._cache_lock = threading.Lock()
    manager._load_lock = threading.RLock()
    load_calls = []

    def fake_load(force=False):
        load_calls.append(force)
        with manager._cache_lock:
            manager.characters_cache = {
                "42": {
                    "id": "42",
                    "nickname": "Packet Hero",
                    "class": "Fighter",
                    "level": 20,
                    "lastUpdate": "now",
                    "stashes": {"3": [{"itemId": "Sword"}]},
                    "rank": {"name": "Pathfinder"},
                    "streamingModeName": "",
                }
            }
            manager._is_loaded = True

    manager._load_data = fake_load

    # These endpoints are used directly by character deep links, before the
    # index route has necessarily caused the deferred manager to load.
    assert manager.get_character_stashes(42) == {"3": [{"itemId": "Sword"}]}
    assert manager.get_character_details("42")["nickname"] == "Packet Hero"
    assert load_calls == [False]


def test_initial_load_skips_snapshot_without_character_id(tmp_path, monkeypatch):
    (tmp_path / "missing-id.json").write_text(json.dumps({
        "characterDataBase": {
            "characterClass": "DesignDataPlayerCharacter:Id_PlayerCharacter_Fighter",
            "nickName": {"originalNickName": "Nameless"},
        }
    }), encoding="utf-8")

    manager = object.__new__(StashManager)
    manager.data_dir = str(tmp_path)
    manager.characters_cache = {}
    manager._is_loaded = False
    manager._cache_lock = threading.Lock()
    manager._load_lock = threading.RLock()
    manager.load_stats = {}
    manager._precompute_priority_stashes = lambda _char: None
    monkeypatch.setattr(stash_manager_module, "parse_stashes", lambda _packet: {})

    manager._load_data_snapshot()

    assert manager.characters_cache == {}
    assert manager._is_loaded is True


def test_sort_allows_an_existing_empty_target_stash(tmp_path, monkeypatch):
    (tmp_path / "hero.json").write_text("{}", encoding="utf-8")

    manager = object.__new__(StashManager)
    manager.data_dir = str(tmp_path)
    manager.characters_cache = {
        "hero": {
            "id": "hero",
            "nickname": "Hero",
            "class": "Fighter",
            "level": 1,
            "lastUpdate": "now",
            "stashes": {"4": []},
            "rank": {},
            "streamingModeName": "",
        }
    }
    manager._is_loaded = True
    manager._cache_lock = threading.Lock()
    manager._load_lock = threading.RLock()
    manager._reset_modifier_state = lambda _session: None
    manager._generate_previews = lambda _character_id: None

    class FakeStorage:
        def __init__(self, _stash_type, _items):
            self.pq = []

    class FakeSorter:
        def __init__(self, *_args, pack_mode=False, stack_mode=False, **_kwargs):
            self.pack_mode = pack_mode
            self.stack_mode = stack_mode
            self._failure_reason = None

        def sort(self, *_args, **_kwargs):
            return True

        def get_feedback_summary(self):
            return None

        def mark_items_for_transfer(self, _items):
            return 0

    class FakeWindow:
        title = "Dark and Darker  "
        def activate(self):
            pass

    class FakeSession:
        def update_status(self, *_args, **_kwargs):
            pass
        def add_log(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(stash_manager_module, "parse_stashes", lambda _packet: {2: []})
    monkeypatch.setattr(stash_manager_module, "Storage", FakeStorage)
    monkeypatch.setattr(stash_manager_module, "StashSorter", FakeSorter)
    monkeypatch.setattr(stash_manager_module.gw, "getAllWindows", lambda: [FakeWindow()])
    monkeypatch.setattr(stash_manager_module.macros, "get_game_window_mode", lambda: 1, raising=False)
    monkeypatch.setattr(stash_manager_module.macros, "has_calibration_saved", lambda: False, raising=False)
    monkeypatch.setattr(
        stash_manager_module.macros,
        "settings_manager",
        types.SimpleNamespace(get=lambda key, default=None: False if key == "autoStashSelection" else default),
        raising=False,
    )

    success, error, _summary = manager.sort_stash(
        "hero",
        "4",
        include_inventory=True,
        overlay_session=FakeSession(),
    )

    assert success is True
    assert error is None


def test_parse_stashes_assigns_distinct_slots_when_packet_omits_slot_ids():
    # conftest stubs stash_preview for sort tests, so run the real parser in a
    # clean interpreter instead of replacing the shared test harness module.
    harness = r"""
import sys

sys.path.insert(0, sys.argv[1])
from src.models import stash_preview

stash_preview.item_data_manager.get_item_id_from_design_str = lambda value: value
stash_preview.item_data_manager.get_item_name_from_id = lambda value: value
stash_preview.item_data_manager.get_item_data = lambda _value: {}

packet = {
    "characterDataBase": {
        "CharacterStorageInfos": [{
            "inventoryId": 4,
            "CharacterStorageItemList": [
                {"slotId": 0, "itemId": "existing"},
                {"itemId": "missing-a"},
                {"itemId": "missing-b"},
            ],
        }],
        "CharacterItemList": [
            {"inventoryId": 2, "slotId": 0, "itemId": "bag-existing"},
            {"inventoryId": 2, "itemId": "bag-missing-a"},
            {"inventoryId": 2, "itemId": "bag-missing-b"},
        ],
    }
}

stashes = stash_preview.parse_stashes(packet)
assert [item["slotId"] for item in stashes[4]] == [0, 1, 2]
assert [item["slotId"] for item in stashes[2]] == [0, 1, 2]
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_equal_items_have_equal_hashes():
    position = Point(3, 4)
    first = Item("design-a", "Potion", 2, position, 1, 1, None)
    second = Item("design-b", "Potion", 2, position, 1, 1, None)

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


def test_item_compare_parses_string_directives_inside_a_list():
    common = Item("common", "Blade", 2, Point(0, 0), 1, 1, None)
    epic = Item("epic", "Blade", 5, Point(1, 0), 1, 1, None)

    assert Item.compare_items(common, epic, ["rarity:asc"]) < 0


def test_item_data_reload_preserves_last_good_cache_on_invalid_refresh(tmp_path):
    harness = r"""
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from src.models.game_data import ItemDataManager

items_file = Path(sys.argv[2]) / "items.json"
items_file.write_text('{"sword": {"name": "Old Sword"}}', encoding="utf-8")

manager = ItemDataManager()
manager._file_path = items_file
assert manager.get_item_name_from_id("sword") == "Old Sword"

items_file.write_text("{broken-json", encoding="utf-8")
manager.reload()

assert manager.get_item_name_from_id("sword") == "Old Sword"
assert manager._loaded is True

items_file.write_text("[]", encoding="utf-8")
manager.reload()
assert manager.get_item_name_from_id("sword") == "Old Sword"
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_sort_model_remote_apply_replaces_live_estimator_and_heads_train_independently():
    harness = r"""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from src.models.sort_model import ITEM_FEATURE_NAMES, RISK_FEATURE_NAMES, SortAdaptiveModel

with tempfile.TemporaryDirectory() as temp_dir:
    model = SortAdaptiveModel(Path(temp_dir))

    model._risk_estimator = object()
    payload = {
        "version": "remote-risk",
        "coefficients": [0.0] * len(RISK_FEATURE_NAMES),
        "intercept": -2.0,
        "feature_names": list(RISK_FEATURE_NAMES),
    }
    assert model.apply_remote_risk_model(payload) is True
    assert model._risk_estimator is None
    assert model.predict_risk({}) < 0.2

    invalid_payload = dict(payload)
    invalid_payload["coefficients"] = ["bad"] * len(RISK_FEATURE_NAMES)
    assert model.apply_remote_risk_model(invalid_payload) is False

    wrong_schema_payload = dict(payload)
    wrong_schema_payload["feature_names"] = []
    assert model.apply_remote_risk_model(wrong_schema_payload) is False

    malformed_schema_payload = dict(payload)
    malformed_schema_payload["feature_names"] = 5
    assert model.apply_remote_risk_model(malformed_schema_payload) is False

    model._item_estimator = object()
    item_payload = {
        "version": "remote-item",
        "coefficients": [0.0] * len(ITEM_FEATURE_NAMES),
        "intercept": 7.0,
        "feature_names": list(ITEM_FEATURE_NAMES),
    }
    assert model.apply_remote_item_model(item_payload) is True
    assert model._item_estimator is None
    assert model.score_item_slot({}) == 7.0

    risk_started = threading.Event()
    item_started = threading.Event()
    release = threading.Event()

    def train_risk(_events):
        risk_started.set()
        assert release.wait(3)

    def train_items(_events):
        item_started.set()
        assert release.wait(3)

    model._do_train_risk = train_risk
    model._do_train_items = train_items
    model._schedule_train("risk", [])
    assert risk_started.wait(2)
    model._schedule_train("items", [])
    assert item_started.wait(2), "item training was dropped while risk training was active"
    release.set()
    for thread in model._training_threads.values():
        thread.join(timeout=3)

with tempfile.TemporaryDirectory() as temp_dir:
    model_dir = Path(temp_dir) / "sort_model"
    model_dir.mkdir(parents=True)
    (model_dir / "risk_model.json").write_text(json.dumps({
        "version": "invalid-cached-version",
        "coefficients": ["bad"] * len(RISK_FEATURE_NAMES),
        "intercept": 0.0,
        "feature_names": list(RISK_FEATURE_NAMES),
    }), encoding="utf-8")
    reloaded = SortAdaptiveModel(Path(temp_dir))
    assert reloaded.get_risk_version() is None
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_sort_learning_does_not_duplicate_or_guess_ambiguous_corrections():
    harness = r"""
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])
from src.models.sort_learning import SortLearningManager

class FakeModel:
    def score_item_slot(self, _features):
        return None

class FakeStore:
    def __init__(self):
        self.calls = []

    def record_user_correction(self, **kwargs):
        self.calls.append(kwargs)
        return [1, 2]

store = FakeStore()
manager = SortLearningManager(model=FakeModel(), event_store=store)
manager._schedule_item_training = lambda: None
manager.register_pending_plan(
    "session",
    {"potion": (1, 1)},
    {"potion": {"width": 1.0}},
)
item = SimpleNamespace(item_id="potion", position=SimpleNamespace(x=2, y=2))
assert manager.check_corrections([item]) == 1
assert len(store.calls) == 1
assert manager.check_corrections([item]) == 0
assert len(store.calls) == 1

ambiguous_store = FakeStore()
ambiguous = SortLearningManager(model=FakeModel(), event_store=ambiguous_store)
ambiguous._schedule_item_training = lambda: None
ambiguous.register_pending_plan(
    "session",
    {"potion": (1, 1)},
    {"potion": {"width": 1.0}},
)
items = [
    SimpleNamespace(item_id="potion", position=SimpleNamespace(x=1, y=1)),
    SimpleNamespace(item_id="potion", position=SimpleNamespace(x=5, y=5)),
]
assert ambiguous.check_corrections(items) == 0
assert ambiguous_store.calls == []

stale_store = FakeStore()
stale = SortLearningManager(model=FakeModel(), event_store=stale_store)
stale._schedule_item_training = lambda: None
stale.register_pending_plan(
    "session",
    {"potion": (1, 1)},
    {"potion": {"width": 1.0}},
)
stale._pending_plans["session"]["timestamp"] = time.time() - 7200
assert stale.check_corrections([item]) == 0
assert stale_store.calls == []
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_character_packet_fallback_filenames_do_not_overwrite_within_same_second(tmp_path):
    harness = r"""
import sys
from datetime import datetime as RealDatetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])
import src.models.character as character

character.data_dir = sys.argv[2]

class FixedDatetime:
    @classmethod
    def now(cls):
        return RealDatetime(2026, 9, 24, 12, 0, 0)

character.datetime = FixedDatetime
character.MessageToJson = lambda message: message.payload

first = SimpleNamespace(payload='{"packet": 1}')
second = SimpleNamespace(payload='{"packet": 2}')
assert character.save_packet_data(first) is False
assert character.save_packet_data(second) is False

files = sorted(Path(character.data_dir).glob("*.json"))
assert len(files) == 2
assert {path.read_text(encoding="utf-8") for path in files} == {
    '{"packet": 1}',
    '{"packet": 2}',
}
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_character_snapshot_keeps_last_good_file_when_atomic_promotion_fails(tmp_path):
    harness = r"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])
import src.models.character as character

character.data_dir = sys.argv[2]
target = Path(character.data_dir) / "42.json"
target.write_text('{"old": true}', encoding="utf-8")

character.MessageToJson = lambda _message: (
    '{"result": 1, "characterDataBase": {"characterId": "42"}}'
)
message = SimpleNamespace(
    characterDataBase=SimpleNamespace(characterId=42),
)

def fail_replace(_source, _target):
    raise OSError("simulated promotion failure")

character.os.replace = fail_replace

try:
    character.save_packet_data(message)
except OSError as exc:
    assert "simulated promotion failure" in str(exc)
else:
    raise AssertionError("save unexpectedly succeeded")

assert target.read_text(encoding="utf-8") == '{"old": true}'
assert list(Path(character.data_dir).glob(".*.tmp")) == []
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_sort_sync_opt_out_controls_network_worker_and_reenable_triggers_sync(tmp_path):
    harness = r"""
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from src.models.sort_sync_service import SortSyncService

class Settings:
    def __init__(self):
        self.data = {
            "sortFeedbackSyncEnabled": False,
            "sortLearningAutoTrain": True,
        }

    def get(self, key, default=None):
        return self.data.get(key, default)

class Model:
    def __init__(self, base_dir):
        self.base_dir = base_dir

settings = Settings()
model_dir = Path(sys.argv[2]) / "model"
model_dir.mkdir()
service = SortSyncService(
    settings_manager=settings,
    app_version="test",
    model=Model(model_dir),
    event_store=object(),
    base_url="https://example.invalid/api",
)
assert service._enabled() is False

calls = []
service._ensure_worker = lambda: calls.append("worker")
service.trigger_sync = lambda immediate=False: calls.append(("sync", immediate))
settings.data["sortFeedbackSyncEnabled"] = True
service.apply_settings({"sortFeedbackSyncEnabled": True})
assert calls == ["worker", ("sync", True)]
assert service._enabled() is True

settings.data["sortFeedbackSyncEnabled"] = False
service.apply_settings({"sortFeedbackSyncEnabled": False})
assert service._enabled() is False
service._session.close()
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_manual_sort_feedback_updates_real_session_sample_and_requeues_sync(tmp_path):
    harness = r"""
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from src.models.sort_event_store import SortEventStore
from src.models.sort_feedback import SortObserver

class Model:
    def predict_risk(self, _features):
        return 0.0

    def recommended_workspace_cells(self, **_kwargs):
        return 6

store = SortEventStore(Path(sys.argv[2]))
event_id = store.record_sort_completed(
    "session-1",
    {"stash_fill_ratio": 0.75, "inventory_free_ratio": 0.25},
    success=True,
    metrics={"plan_size": 7},
)
store.mark_synced([event_id])

observer = SortObserver(model=Model(), event_store=store)
scheduled = []
observer._schedule_risk_training = lambda: scheduled.append(True)

assert observer.record_user_feedback("session-1", False, "items got stuck") is True
assert store.count_events() == 1

events = store.get_unsynced_events()
assert len(events) == 1
event = events[0]
assert event["id"] == event_id
assert event["features"]["stash_fill_ratio"] == 0.75
assert event["features"]["inventory_free_ratio"] == 0.25
assert event["label"] == 1
assert event["metadata"]["user_feedback"] is True
assert event["metadata"]["user_success"] is False
assert event["metadata"]["user_note"] == "items got stuck"
assert scheduled == [True]

assert observer.record_user_feedback("missing-session", True) is False
store.close()
"""

    result = subprocess.run(
        [sys.executable, "-c", harness, UI_DIR, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
