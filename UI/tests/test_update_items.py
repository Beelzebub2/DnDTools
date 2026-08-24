import pytest
import requests

import scripts.update_items as update_items_module

from scripts.update_items import (
    API_VERSION,
    IconProcessResult,
    IconRefreshSummary,
    _existing_item_identity,
    canonical_item_to_game_id,
    fetch_items_catalog,
    get_darkerdb_api_key,
    item_has_remote_icon,
    normalize_v2_item,
    process_icon,
    quarantine_unreferenced_icons,
    refresh_icons,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


class FakeIconResponse:
    def __init__(self, status_code=200, content=b"image", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


def item_row(item_id="id.item.adventurer_boots_1001"):
    return {
        "id": item_id,
        "archetype": "id.item.adventurer_boots",
        "name": "Adventurer Boots",
        "flavor": "Hardy boots.",
        "icon": "remote-hash",
        "icon_url": "https://cdn.example/icon",
        "rarity": "poor",
        "item_type": "armor",
        "slot_type": "foot",
        "armor_type": "leather",
        "hand_type": None,
        "max_stack_size": 1,
        "inventory_width": 2,
        "inventory_height": 2,
        "gear_score": 1,
        "vendor_price": 0,
        "wearing_delay_time": 1,
        "required_class": ["fighter", "ranger"],
        "num_primary_attributes": 3,
        "num_secondary_attributes": 0,
    }


@pytest.mark.parametrize(
    ("canonical", "expected"),
    [
        ("id.item.adventurer_boots_1001", "AdventurerBoots_1001"),
        ("id.item.gold_bangle2_h_4001", "GoldBangle2H_4001"),
        ("id.item.gold_coins", "GoldCoins"),
    ],
)
def test_canonical_item_id_preserves_game_lookup_contract(canonical, expected):
    assert canonical_item_to_game_id(canonical) == expected


def test_normalize_v2_item_replaces_remote_fields_and_preserves_local_metadata():
    existing = {
        "AdventurerBoots_1001": {
            "id": "AdventurerBoots_1001",
            "name": "Old name",
            "rarity": "Poor",
            "primary_min_armor_rating": 5,
            "removed_remote_field": "stale",
            "iconPath": "icons/Armor/AdventurerBoots_1001.webp",
            "iconHash": "local-webp-hash",
            "iconETag": "local-etag",
        }
    }
    by_canonical, by_name_rarity = _existing_item_identity(existing)
    remote = item_row()
    remote["primary_min_armor_rating"] = 11
    remote["new_remote_payload"] = {"nested": [1, 2, 3]}
    remote["iconPath"] = "../remote-controlled-path.webp"
    remote["iconHash"] = "remote-local-collision"

    item_id, result = normalize_v2_item(
        remote,
        existing,
        by_canonical=by_canonical,
        by_name_rarity=by_name_rarity,
    )

    assert item_id == "AdventurerBoots_1001"
    assert result["name"] == "Adventurer Boots"
    assert result["description"] == "Hardy boots."
    assert result["type"] == "Armor"
    assert result["slot_type"] == "Foot"
    assert result["required_class"] == 1 | 8
    assert result["primary_min_armor_rating"] == 11
    assert result["new_remote_payload"] == {"nested": [1, 2, 3]}
    assert "removed_remote_field" not in result
    assert result["iconHash"] == "local-webp-hash"
    assert result["iconETag"] == "local-etag"
    assert result["darkerdb_id"] == "id.item.adventurer_boots_1001"


def test_normalize_v2_item_does_not_retain_missing_or_null_remote_values():
    existing = {
        "AdventurerBoots_1001": {
            "id": "AdventurerBoots_1001",
            "description": "Old description",
            "vendor_price": 999,
            "tag": "old-v1-tag",
            "iconPath": "icons/Armor/AdventurerBoots_1001.webp",
        }
    }
    remote = item_row()
    remote["flavor"] = None
    remote["vendor_price"] = None

    _, result = normalize_v2_item(remote, existing)

    assert "description" not in result
    assert "vendor_price" not in result
    assert "tag" not in result
    assert result["iconPath"] == "icons/Armor/AdventurerBoots_1001.webp"


def test_normalize_explicitly_iconless_item_clears_fabricated_and_stale_icon_metadata():
    raw = item_row("id.item.bare_hands")
    raw["icon"] = None
    raw["icon_url"] = None
    existing = {
        "BareHands": {
            "id": "BareHands",
            "name": "Bare Hands",
            "iconPath": "icons/Weapon/BareHands.webp",
            "iconHash": "stale",
            "iconETag": "stale-etag",
            "iconLastModified": "yesterday",
        }
    }

    item_id, result = normalize_v2_item(raw, existing)

    assert item_id == "BareHands"
    assert item_has_remote_icon(raw) is False
    assert not any(field in result for field in (
        "iconPath",
        "iconHash",
        "iconETag",
        "iconLastModified",
        "darkerdbIconHash",
        "iconUrl",
    ))


def test_fetch_items_uses_v2_header_and_opaque_cursor_and_deduplicates():
    session = FakeSession(
        FakeResponse({"build": "b", "body": [item_row()], "pagination": {"next": "opaque"}}),
        FakeResponse(
            {
                "body": [item_row(), item_row("id.item.adventurer_boots_2001")],
                "pagination": {"next": None},
            }
        ),
    )

    rows, metadata = fetch_items_catalog(api_key="secret", session=session)

    assert len(rows) == 2
    assert metadata["build"] == "b"
    assert session.calls[0][0].endswith("/v2/items")
    assert session.calls[0][1]["headers"]["X-API-Version"] == API_VERSION
    assert session.calls[0][1]["headers"]["X-Api-Key"] == "secret"
    assert "key" not in session.calls[0][1]["params"]
    assert session.calls[1][1]["params"]["cursor"] == "opaque"


def test_fetch_items_rejects_repeated_cursor():
    session = FakeSession(
        FakeResponse({"body": [item_row()], "pagination": {"next": "same"}}),
        FakeResponse({"body": [item_row()], "pagination": {"next": "same"}}),
    )

    with pytest.raises(ValueError, match="repeated item pagination cursor"):
        fetch_items_catalog(api_key="secret", session=session)


def test_item_updater_ignores_unrelated_generic_api_key(monkeypatch):
    monkeypatch.delenv("DARKERDB_API_KEY", raising=False)
    monkeypatch.delenv("DNDTOOLS_DARKERDB_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "unrelated-service-secret")

    assert get_darkerdb_api_key() == ""


def _missing_icon_record():
    return {"iconPath": "icons/Armor/NewItem_1001.webp"}


@pytest.mark.parametrize("icon_path", ["../outside.webp", "icons/../../outside.webp"])
def test_icon_target_rejects_paths_outside_assets(tmp_path, monkeypatch, icon_path):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)

    with pytest.raises(ValueError, match="Unsafe icon path"):
        update_items_module._icon_path_and_target(
            "Unsafe_1001",
            item_row(),
            {"iconPath": icon_path},
        )


def test_missing_icon_request_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)

    def fail_request(*_args, **_kwargs):
        raise requests.RequestException("offline")

    monkeypatch.setattr(update_items_module.requests, "get", fail_request)

    result = process_icon("NewItem_1001", item_row(), _missing_icon_record(), {}, False)

    assert result.fatal is True
    assert "offline" in result.error


def test_missing_icon_http_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        update_items_module.requests,
        "get",
        lambda *_args, **_kwargs: FakeIconResponse(status_code=503),
    )

    result = process_icon("NewItem_1001", item_row(), _missing_icon_record(), {}, False)

    assert result.fatal is True
    assert "HTTP 503" in result.error


def test_existing_icon_http_failure_is_nonfatal_and_preserves_local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    record = {"iconPath": "icons/Misc/WorkshopScraps_3001.webp"}
    target = tmp_path / "assets" / record["iconPath"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"known-good-webp")
    monkeypatch.setattr(
        update_items_module.requests,
        "get",
        lambda *_args, **_kwargs: FakeIconResponse(status_code=404),
    )

    result = process_icon(
        "WorkshopScraps_3001",
        item_row("id.item.workshop_scraps_3001"),
        record,
        {},
        False,
    )

    assert result.fatal is False
    assert "HTTP 404" in result.error
    assert target.read_bytes() == b"known-good-webp"


def test_missing_icon_not_modified_response_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        update_items_module.requests,
        "get",
        lambda *_args, **_kwargs: FakeIconResponse(status_code=304),
    )

    result = process_icon("NewItem_1001", item_row(), _missing_icon_record(), {}, False)

    assert result.fatal is True
    assert "304 for missing icon" in result.error


def test_missing_icon_decode_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        update_items_module.requests,
        "get",
        lambda *_args, **_kwargs: FakeIconResponse(content=b"not an image"),
    )

    result = process_icon("NewItem_1001", item_row(), _missing_icon_record(), {}, False)

    assert result.fatal is True
    assert "decode/convert" in result.error


def test_missing_icon_write_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        update_items_module.requests,
        "get",
        lambda *_args, **_kwargs: FakeIconResponse(content=b"source"),
    )
    monkeypatch.setattr(update_items_module, "convert_icon_to_webp_bytes", lambda _data: b"webp")

    def fail_write(_target, _payload):
        raise OSError("disk full")

    monkeypatch.setattr(update_items_module, "_write_icon_bytes", fail_write)

    result = process_icon("NewItem_1001", item_row(), _missing_icon_record(), {}, False)

    assert result.fatal is True
    assert "disk full" in result.error


def test_required_icon_worker_exception_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)

    def fail_worker(*_args, **_kwargs):
        raise RuntimeError("worker crashed")

    monkeypatch.setattr(update_items_module, "process_icon", fail_worker)
    item_id = "NewItem_1001"
    summary = refresh_icons(
        {item_id: _missing_icon_record()},
        {item_id: item_row()},
        {},
        required_item_ids={item_id},
    )

    assert len(summary.fatal_failures) == 1
    assert "worker crashed" in summary.fatal_failures[0]


def test_explicitly_iconless_item_skips_download_without_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    item_id = "BareHands"
    record = {
        "id": item_id,
        "iconPath": "icons/Weapon/BareHands.webp",
        "iconHash": "stale",
    }
    remote = item_row("id.item.bare_hands")
    remote["icon"] = None
    remote["icon_url"] = None

    def unexpected_worker(*_args, **_kwargs):
        raise AssertionError("iconless rows must not schedule a download")

    monkeypatch.setattr(update_items_module, "process_icon", unexpected_worker)

    summary = refresh_icons(
        {item_id: record},
        {item_id: remote},
        {},
        required_item_ids={item_id},
    )

    assert summary.failures == ()
    assert summary.fatal_failures == ()
    assert "iconPath" not in record
    assert "iconHash" not in record


def test_fatal_icon_batch_rolls_back_successful_new_icon(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    first = "NewItem_1001"
    second = "BrokenItem_1001"

    def fake_process(item_id, _api_item, record, *_args):
        _, target = update_items_module._icon_path_and_target(item_id, {}, record)
        if item_id == second:
            return IconProcessResult(error="decode failed", fatal=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"new webp")
        return IconProcessResult(
            updated_files=1,
            target_path=target,
            created_new=True,
        )

    monkeypatch.setattr(update_items_module, "process_icon", fake_process)
    records = {
        first: {"iconPath": f"icons/Armor/{first}.webp"},
        second: {"iconPath": f"icons/Armor/{second}.webp"},
    }
    fetched = {first: item_row(), second: item_row("id.item.broken_item_1001")}

    summary = refresh_icons(records, fetched, {}, required_item_ids={first, second})

    assert summary.fatal_failures == ("decode failed",)
    assert not (tmp_path / "assets" / records[first]["iconPath"]).exists()


def test_unreferenced_webp_icons_are_quarantined_and_can_be_rolled_back(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    referenced = tmp_path / "assets" / "icons" / "Armor" / "Keep.webp"
    obsolete = tmp_path / "assets" / "icons" / "Misc" / "Remove.webp"
    unrelated = tmp_path / "assets" / "icons" / "Misc" / "notes.txt"
    for path, payload in (
        (referenced, b"keep"),
        (obsolete, b"restore me"),
        (unrelated, b"not an icon"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    transaction = quarantine_unreferenced_icons({
        "Keep": {"iconPath": "icons/Armor/Keep.webp"},
    })

    assert transaction.pruned_files == 1
    assert referenced.read_bytes() == b"keep"
    assert not obsolete.exists()
    assert unrelated.exists()

    transaction.rollback()

    assert obsolete.read_bytes() == b"restore me"
    assert not transaction.quarantine_root.exists()


def test_unreferenced_webp_icon_quarantine_is_removed_on_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    obsolete = tmp_path / "assets" / "icons" / "Misc" / "Remove.webp"
    obsolete.parent.mkdir(parents=True)
    obsolete.write_bytes(b"obsolete")

    transaction = quarantine_unreferenced_icons({})
    quarantine_root = transaction.quarantine_root
    transaction.commit()

    assert not obsolete.exists()
    assert quarantine_root is not None
    assert not quarantine_root.exists()


def test_update_items_restores_pruned_icons_when_catalog_write_fails(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    icons_dir = assets_dir / "icons" / "Misc"
    icons_dir.mkdir(parents=True)
    stale_icon = icons_dir / "Stale.webp"
    stale_icon.write_bytes(b"old icon")
    items_file = assets_dir / "items.json"
    items_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(update_items_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(update_items_module, "ITEMS_FILE", items_file)
    monkeypatch.setattr(update_items_module, "API_KEY", "test-key")
    monkeypatch.setattr(
        update_items_module,
        "fetch_items_catalog",
        lambda **_kwargs: ([item_row()], {"build": "test"}),
    )
    monkeypatch.setattr(
        update_items_module,
        "refresh_icons",
        lambda *_args, **_kwargs: IconRefreshSummary(),
    )
    monkeypatch.setattr(
        update_items_module.json,
        "dump",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert update_items_module.update_items() is False
    assert stale_icon.read_bytes() == b"old icon"
    assert not list(assets_dir.glob(".icons-prune-*"))


def test_update_items_does_not_write_catalog_after_required_icon_failure(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    items_file = assets_dir / "items.json"
    original = {"Existing_1001": {"id": "Existing_1001", "name": "Existing"}}
    items_file.write_text(__import__("json").dumps(original), encoding="utf-8")

    monkeypatch.setattr(update_items_module, "API_KEY", "test-key")
    monkeypatch.setattr(update_items_module, "ITEMS_FILE", items_file)
    monkeypatch.setattr(
        update_items_module,
        "fetch_items_catalog",
        lambda **_kwargs: ([item_row("id.item.new_item_1001")], {"build": "test"}),
    )
    monkeypatch.setattr(
        update_items_module,
        "refresh_icons",
        lambda *_args, **_kwargs: IconRefreshSummary(
            failures=("missing new icon",),
            fatal_failures=("missing new icon",),
        ),
    )

    assert update_items_module.update_items() is False
    assert __import__("json").loads(items_file.read_text(encoding="utf-8")) == original
