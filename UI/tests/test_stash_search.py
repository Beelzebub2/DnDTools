"""Regression tests for stable item-search and stash-sort ordering."""

from __future__ import annotations

import json
import sys
import threading
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
