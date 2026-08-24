import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import src.models.appdirs as appdirs


# The shared test harness replaces appdirs with a minimal stub.
if not hasattr(appdirs, "get_quests_dir"):
    appdirs.get_quests_dir = lambda: "."

import src.quest_service as quest_service_module

from src.quest_service import (
    DARKERDB_API_VERSION,
    QuestPayloadError,
    QuestService,
    fetch_darkerdb_quests,
    get_darkerdb_api_key,
    normalize_darkerdb_v2_quest,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


def v2_row(quest_id="id.quest.alchemist_01", title="Marks of Malice"):
    return {
        "id": quest_id,
        "title": title,
        "description": "Bring supplies.",
        "chapter_id": "id.quest.tavern_master_01",
        "chapter_title": "Is it you?",
        "objectives": [
            {
                "id": "id.quest_content.fetch_bandages_01",
                "content_type": "fetch",
                "content_count": 1,
                "target_kind": "item",
                "target_archetype": "id.item.bandage",
                "dungeon_tags": ["id.dungeon.crypts"],
                "icon_url": "https://cdn.example.test/bandage.webp",
            }
        ],
        "rewards": [
            {
                "entries": [
                    {"type": "item", "count": 30, "item_id": "id.item.gold_coins"},
                    {"type": "experience", "count": 25},
                    {
                        "type": "random",
                        "count": 1,
                        "random_reward_id": "id.random_reward.quest_armor_uncommon_01",
                    },
                ]
            }
        ],
    }


def test_normalizes_v2_consolidated_quest_contract():
    result = normalize_darkerdb_v2_quest(v2_row())

    assert result["id"] == "Alchemist_01"
    assert result["merchant"] == "Alchemist"
    assert result["prerequisite"] == "TavernMaster_01"
    assert result["dungeons"] == ["Crypts"]
    assert result["objectives"] == [{
        "type": "Fetch",
        "count": 1,
        "item_id": "Bandage",
        "icon_url": "https://cdn.example.test/bandage.webp",
    }]
    assert result["rewards"][0] == {"type": "Item", "count": 30, "item_id": "GoldCoins"}
    assert result["rewards"][1] == {"type": "Experience", "count": 25}
    assert result["rewards"][2]["item_type"] == "Armor"
    assert result["rewards"][2]["rarity"] == "Uncommon"


def test_fetch_uses_pinned_header_opaque_cursor_and_deduplicates():
    first = FakeResponse(
        {
            "build": "build-1",
            "patch": 130,
            "body": [v2_row()],
            "pagination": {"next": "opaque-cursor"},
        }
    )
    second = FakeResponse(
        {
            "body": [
                v2_row(),
                v2_row("id.quest.armourer_01", "An Armourer's Task"),
            ],
            "pagination": {"next": None},
        }
    )
    session = FakeSession(first, second)

    quests, metadata = fetch_darkerdb_quests(api_key="secret", session=session)

    assert [quest["id"] for quest in quests] == ["Alchemist_01", "Armourer_01"]
    assert metadata["build"] == "build-1"
    assert metadata["patch"] == 130
    assert session.calls[0][1]["headers"]["X-API-Version"] == DARKERDB_API_VERSION
    assert session.calls[0][1]["headers"]["X-Api-Key"] == "secret"
    assert "key" not in session.calls[0][1]["params"]
    assert "cursor" not in session.calls[0][1]["params"]
    assert session.calls[1][1]["params"]["cursor"] == "opaque-cursor"


def test_fetch_rejects_repeated_pagination_cursor():
    session = FakeSession(
        FakeResponse({"body": [v2_row()], "pagination": {"next": "same"}}),
        FakeResponse({"body": [v2_row()], "pagination": {"next": "same"}}),
    )

    with pytest.raises(QuestPayloadError, match="repeated pagination cursor"):
        fetch_darkerdb_quests(api_key="secret", session=session)


def test_force_refresh_without_key_returns_deduped_stale_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("DARKERDB_API_KEY", raising=False)
    monkeypatch.delenv("DNDTOOLS_DARKERDB_API_KEY", raising=False)
    cache_payload = {
        "version": 1,
        "timestamp": 1,
        "quests": [
            {"id": "Old_01", "title": "Old", "objectives": []},
            {"id": "Old_01", "title": "Old", "objectives": []},
        ],
    }
    (tmp_path / "quests_cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")

    service = QuestService(logging.getLogger("test"), data_dir=tmp_path)
    service._bundled_snapshot_file = tmp_path / "missing-quests.json"
    quests = service.fetch_quests(force=True)
    status = service.get_fetch_status()

    assert len(quests) == 1
    assert status["source"] == "disk-cache"
    assert status["cached"] is True
    assert status["stale"] is True
    assert "DARKERDB_API_KEY" in status["warning"]


def test_no_key_client_uses_recent_distributed_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("DARKERDB_API_KEY", raising=False)
    monkeypatch.delenv("DNDTOOLS_DARKERDB_API_KEY", raising=False)
    snapshot = tmp_path / "quests.json"
    snapshot.write_text(
        json.dumps(
            {
                "version": 2,
                "source": "darkerdb-v2",
                "api_version": DARKERDB_API_VERSION,
                "timestamp": time.time(),
                "build": "current",
                "patch": 130,
                "quests": [{"id": "Alchemist_01", "title": "Current", "objectives": []}],
            }
        ),
        encoding="utf-8",
    )
    session = FakeSession()
    service = QuestService(logging.getLogger("test"), data_dir=tmp_path / "cache", session=session)
    service._bundled_snapshot_file = snapshot

    quests = service.fetch_quests()
    status = service.get_fetch_status()

    assert quests[0]["title"] == "Current"
    assert session.calls == []
    assert status["source"] == "asset-snapshot"
    assert status["stale"] is False
    assert "distributed quest snapshot" in status["warning"]


def test_generic_api_key_is_never_sent_to_darkerdb(monkeypatch):
    monkeypatch.delenv("DARKERDB_API_KEY", raising=False)
    monkeypatch.delenv("DNDTOOLS_DARKERDB_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "unrelated-service-secret")

    assert get_darkerdb_api_key() == ""


def test_item_index_maps_archetype_to_all_concrete_grades(tmp_path, monkeypatch):
    items_path = tmp_path / "items.json"
    items_path.write_text(
        json.dumps({
            "Bandage_4001": {
                "name": "Bandage",
                "rarity": "Rare",
                "type": "Utility",
                "archetype": "Bandage",
                "iconPath": "icons/Bandage_4001.webp",
            },
            "Bandage_1001": {
                "name": "Bandage",
                "rarity": "Poor",
                "type": "Utility",
                "archetype": "Bandage",
                "iconPath": "icons/Bandage_1001.webp",
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(quest_service_module, "resource_path", lambda _name: str(items_path))
    service = QuestService(logging.getLogger("test"), data_dir=tmp_path / "cache")

    family = service.load_items_index()["Bandage"]

    assert family["item_id"] == "Bandage"
    assert family["name"] == "Bandage"
    assert family["rarity"] == "Any"
    assert family["representative_item_id"] == "Bandage_1001"
    assert family["concrete_item_ids"] == ["Bandage_1001", "Bandage_4001"]
    assert service.get_concrete_item_ids("Bandage") == ["Bandage_1001", "Bandage_4001"]


def test_item_index_bridges_v2_archetype_names_with_legacy_snapshot(tmp_path, monkeypatch):
    items_path = tmp_path / "items.json"
    items_path.write_text(
        json.dumps({
            "ShiningPearl": {"name": "Shining Pearl", "rarity": "Rare", "type": "Misc"},
            "BlemishedPearl": {"name": "Blemished Pearl", "rarity": "Poor", "type": "Misc"},
            "LuckPotion_3001": {"name": "Potion of Luck", "rarity": "Uncommon", "type": "Utility"},
            "LuckPotion_5001": {"name": "Potion of Luck", "rarity": "Epic", "type": "Utility"},
            "Brimstone": {"name": "Brimstone", "rarity": "Unique", "type": "Misc"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(quest_service_module, "resource_path", lambda _name: str(items_path))
    service = QuestService(logging.getLogger("test"), data_dir=tmp_path / "cache")

    assert service.get_concrete_item_ids("PearlShining") == ["ShiningPearl"]
    assert service.get_concrete_item_ids("PearlBlemished") == ["BlemishedPearl"]
    assert service.get_concrete_item_ids("LuckPotionSmall") == ["LuckPotion_3001"]
    assert service.get_concrete_item_ids("LuckPotionLarge") == ["LuckPotion_5001"]
    assert service.get_concrete_item_ids("BrimstoneOres") == ["Brimstone"]
    assert service.load_items_index()["PearlShining"]["name"] == "Shining Pearl"


def test_native_v2_archetype_replaces_legacy_family_fallback(tmp_path, monkeypatch):
    items_path = tmp_path / "items.json"
    items_path.write_text(
        json.dumps({
            "Brimstone": {"name": "Brimstone", "rarity": "Unique", "type": "Misc"},
            "BrimstoneOres_5001": {
                "name": "Brimstone Ore",
                "rarity": "Epic",
                "type": "Misc",
                "darkerdb_archetype": "id.item.brimstone_ores",
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(quest_service_module, "resource_path", lambda _name: str(items_path))
    service = QuestService(logging.getLogger("test"), data_dir=tmp_path / "cache")

    assert service.get_concrete_item_ids("BrimstoneOres") == ["BrimstoneOres_5001"]


def test_item_family_holdings_merge_grades_per_character():
    holdings = {
        "Bandage_1001": [{
            "character_id": "one",
            "character_name": "Cleric",
            "total": 2,
            "stashes": [{"stash_id": "0", "count": 2}],
        }],
        "Bandage_4001": [{
            "character_id": "one",
            "character_name": "Cleric",
            "total": 3,
            "stashes": [{"stash_id": "1", "count": 3}],
        }],
    }

    merged = QuestService.merge_item_family_holdings(
        holdings,
        ["Bandage_1001", "Bandage_4001"],
    )

    assert len(merged) == 1
    assert merged[0]["total"] == 5
    assert {stash["item_id"] for stash in merged[0]["stashes"]} == {
        "Bandage_1001",
        "Bandage_4001",
    }


def test_item_payload_uses_remote_icon_when_local_item_is_missing():
    service = QuestService(logging.getLogger("test"), data_dir=".")
    payload = service.build_item_payload({
        "item_id": "LuckPotion_4001",
        "name": "Luck Potion",
        "rarity": "Rare",
        "type": "Utility",
        "icon_url": "https://cdn.example.test/luck-potion.webp",
    })

    assert payload["icon"] == "https://cdn.example.test/luck-potion.webp"


def test_progress_and_active_merchants_are_transactional_under_concurrent_saves(tmp_path):
    service = QuestService(logging.getLogger("test"), data_dir=tmp_path)

    for revision in range(1, 41):
        progress = {
            "objectives": {
                "quest:objective": {
                    "submitted": revision,
                    "completed": False,
                }
            },
            "items": {},
        }
        merchants = [f"merchant-{revision}"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            progress_future = pool.submit(service.save_progress, progress, None, revision)
            merchant_future = pool.submit(service.save_active_merchants, merchants)
            assert progress_future.result() is True
            merchant_future.result()

        loaded_progress, _ = service.load_progress()
        assert loaded_progress["objectives"]["quest:objective"]["submitted"] == revision
        assert service.load_active_merchants() == merchants

    assert not list(tmp_path.glob("*.tmp"))


def test_older_progress_revision_cannot_overwrite_newer_state(tmp_path):
    service = QuestService(logging.getLogger("test"), data_dir=tmp_path)
    newer = {"objectives": {}, "items": {"Bandage": 9}}
    older = {"objectives": {}, "items": {"Bandage": 2}}

    assert service.save_progress(newer, ["Alchemist"], revision=200) is True
    assert service.save_progress(older, ["Armourer"], revision=100) is False

    progress, _ = service.load_progress()
    assert progress["items"]["Bandage"] == 9
    assert service.load_active_merchants() == ["Alchemist"]


def test_progress_sync_snapshot_includes_revision_and_merchants(tmp_path):
    service = QuestService(logging.getLogger("test"), data_dir=tmp_path)
    progress = {"objectives": {}, "items": {"Bandage": 4}}

    assert service.save_progress(progress, ["Alchemist", "Alchemist", "  Armourer  "], revision=321)

    loaded, timestamp, revision, merchants = service.load_progress_sync_state()
    assert loaded["items"]["Bandage"] == 4
    assert timestamp is not None
    assert revision == 321
    assert merchants == ["Alchemist", "Armourer"]
