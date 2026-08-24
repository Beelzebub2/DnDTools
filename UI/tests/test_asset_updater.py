import hashlib
import os
import shutil
from pathlib import Path

import pytest

from utils.asset_updater import (
    AssetUpdater,
    PUBLISHED_ASSET_FILENAMES,
    PUBLISHED_ASSET_MAX_BYTES,
)


def _manifest_entry(name, **overrides):
    entry = {
        "name": name,
        "url": f"https://assets.example/{name}",
        "sha256": "0" * 64,
        "size": 1,
    }
    entry.update(overrides)
    return entry


class _DownloadResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        yield from self._chunks


class _DownloadSession:
    def __init__(self, *chunks):
        self.headers = {}
        self._chunks = chunks

    def get(self, *_args, **_kwargs):
        return _DownloadResponse(self._chunks)


@pytest.mark.parametrize(
    "manifest_path",
    [
        "../outside.json",
        "..\\outside.json",
        "nested/items.json",
        "nested\\items.json",
        "/tmp/items.json",
        "C:\\tmp\\items.json",
        "\\\\server\\share\\items.json",
        "./items.json",
        "items.json/",
        "unknown.json",
    ],
)
def test_manifest_rejects_paths_outside_published_asset_allowlist(tmp_path, manifest_path):
    updater = AssetUpdater(tmp_path / "assets")

    with pytest.raises(RuntimeError):
        updater._extract_manifest_files({
            "files": [{"path": manifest_path, "url": "https://assets.example/file"}],
        })


def test_manifest_accepts_each_known_root_level_asset(tmp_path):
    updater = AssetUpdater(tmp_path / "assets")
    manifest = {
        "files": [
            _manifest_entry(name)
            for name in sorted(PUBLISHED_ASSET_FILENAMES)
        ],
    }

    files = updater._extract_manifest_files(manifest)

    assert [entry["path"] for entry in files] == sorted(PUBLISHED_ASSET_FILENAMES)


def test_manifest_rejects_duplicate_destination(tmp_path):
    updater = AssetUpdater(tmp_path / "assets")

    with pytest.raises(RuntimeError, match="duplicate asset path"):
        updater._extract_manifest_files({
            "files": [
                _manifest_entry("items.json", path="items.json", url="https://assets.example/one"),
                _manifest_entry("items.json", url="https://assets.example/two"),
            ],
        })


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": ""},
        {"sha256": "not-a-sha256"},
        {"size": 0},
        {"size": -1},
        {"size": "not-a-number"},
        {"size": PUBLISHED_ASSET_MAX_BYTES["items.json"] + 1},
    ],
)
def test_manifest_rejects_unverifiable_or_oversized_asset(tmp_path, overrides):
    updater = AssetUpdater(tmp_path / "assets")

    with pytest.raises(RuntimeError):
        updater._extract_manifest_files({
            "files": [_manifest_entry("items.json", **overrides)],
        })


def test_download_verifies_declared_size_and_sha256(tmp_path):
    payload = b'{"items": []}'
    session = _DownloadSession(payload[:4], payload[4:])
    updater = AssetUpdater(tmp_path / "assets", session=session)
    entry = _manifest_entry(
        "items.json",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    relative_path, staged = updater._download_asset(entry)

    assert relative_path == "items.json"
    assert staged.read_bytes() == payload
    staged.unlink()


@pytest.mark.parametrize(
    "entry_overrides",
    [
        {"size": 2},
        {"size": 20},
        {"size": 3, "sha256": "f" * 64},
    ],
)
def test_download_removes_partial_file_on_size_or_hash_failure(tmp_path, entry_overrides):
    payload = b"bad"
    session = _DownloadSession(payload)
    assets_dir = tmp_path / "assets"
    updater = AssetUpdater(assets_dir, session=session)
    entry = _manifest_entry("items.json", **entry_overrides)

    with pytest.raises(RuntimeError, match="mismatch"):
        updater._download_asset(entry)

    assert not list(assets_dir.glob("dnd-asset-*.tmp"))


def test_apply_revalidates_download_destination(tmp_path):
    assets_dir = tmp_path / "assets"
    updater = AssetUpdater(assets_dir)
    staged = tmp_path / "download.tmp"
    staged.write_text("payload", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Unsafe asset path"):
        updater._apply_assets([("../outside.json", staged)])

    assert staged.exists()
    assert not (tmp_path / "outside.json").exists()


def test_apply_installs_allowed_asset_inside_assets_directory(tmp_path):
    assets_dir = tmp_path / "assets"
    updater = AssetUpdater(assets_dir)
    staged = tmp_path / "download.tmp"
    staged.write_text("{}", encoding="utf-8")

    updater._apply_assets([("quests.json", staged)])

    assert (assets_dir / "quests.json").read_text(encoding="utf-8") == "{}"
    assert not staged.exists()


def test_apply_restores_backup_when_replacement_fails(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    target = assets_dir / "items.json"
    target.write_text("old catalog", encoding="utf-8")
    staged = tmp_path / "download.tmp"
    staged.write_text("new catalog", encoding="utf-8")
    updater = AssetUpdater(assets_dir)

    def fail_after_partial_write(_staged: Path, destination: Path):
        destination.write_text("partial catalog", encoding="utf-8")
        raise OSError("injected replacement failure")

    monkeypatch.setattr(updater, "_replace_file", fail_after_partial_write)

    with pytest.raises(RuntimeError, match="injected replacement failure"):
        updater._apply_assets([("items.json", staged)])

    assert target.read_text(encoding="utf-8") == "old catalog"
    assert not target.with_suffix(".bak").exists()
    assert staged.read_text(encoding="utf-8") == "new catalog"


def test_apply_removes_partial_new_asset_when_replacement_fails(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    updater = AssetUpdater(assets_dir)
    staged = tmp_path / "download.tmp"
    staged.write_text("new snapshot", encoding="utf-8")

    def fail_after_partial_write(_staged: Path, destination: Path):
        destination.write_text("partial snapshot", encoding="utf-8")
        raise OSError("injected new-file failure")

    monkeypatch.setattr(updater, "_replace_file", fail_after_partial_write)

    with pytest.raises(RuntimeError, match="injected new-file failure"):
        updater._apply_assets([("quests.json", staged)])

    assert not (assets_dir / "quests.json").exists()


def test_apply_preserves_existing_target_when_backup_creation_fails(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    target = assets_dir / "items.json"
    target.write_text("original catalog", encoding="utf-8")
    staged = tmp_path / "download.tmp"
    staged.write_text("new catalog", encoding="utf-8")
    updater = AssetUpdater(assets_dir)
    replacement_attempted = False

    original_copy = shutil.copy2

    def fail_backup_copy(source: Path, destination: Path):
        if Path(source) == target:
            raise OSError("injected backup failure")
        return original_copy(source, destination)

    def fail_if_replacement_attempted(_staged: Path, _destination: Path):
        nonlocal replacement_attempted
        replacement_attempted = True
        raise OSError("replacement should not run without rollback")

    monkeypatch.setattr("utils.asset_updater.shutil.copy2", fail_backup_copy)
    monkeypatch.setattr(updater, "_replace_file", fail_if_replacement_attempted)

    with pytest.raises(RuntimeError, match="injected backup failure"):
        updater._apply_assets([("items.json", staged)])

    assert replacement_attempted is False
    assert target.read_text(encoding="utf-8") == "original catalog"
    assert staged.read_text(encoding="utf-8") == "new catalog"


def test_apply_rolls_back_prior_assets_when_later_replacement_fails(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    items = assets_dir / "items.json"
    quests = assets_dir / "quests.json"
    items.write_text("old items", encoding="utf-8")
    quests.write_text("old quests", encoding="utf-8")
    staged_items = tmp_path / "items.tmp"
    staged_quests = tmp_path / "quests.tmp"
    staged_items.write_text("new items", encoding="utf-8")
    staged_quests.write_text("new quests", encoding="utf-8")
    updater = AssetUpdater(assets_dir)
    original_replace = updater._replace_file

    def fail_second_replacement(staged: Path, destination: Path):
        if destination.name == "quests.json":
            destination.write_text("partial quests", encoding="utf-8")
            raise OSError("injected second replacement failure")
        original_replace(staged, destination)

    monkeypatch.setattr(updater, "_replace_file", fail_second_replacement)

    with pytest.raises(RuntimeError, match="injected second replacement failure"):
        updater._apply_assets([
            ("items.json", staged_items),
            ("quests.json", staged_quests),
        ])

    assert items.read_text(encoding="utf-8") == "old items"
    assert quests.read_text(encoding="utf-8") == "old quests"
    assert not list(assets_dir.glob(".*.dndtools-*.bak"))


def test_apply_rolls_back_prior_assets_when_later_backup_creation_fails(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    items = assets_dir / "items.json"
    quests = assets_dir / "quests.json"
    items.write_text("old items", encoding="utf-8")
    quests.write_text("old quests", encoding="utf-8")
    staged_items = tmp_path / "items.tmp"
    staged_quests = tmp_path / "quests.tmp"
    staged_items.write_text("new items", encoding="utf-8")
    staged_quests.write_text("new quests", encoding="utf-8")
    updater = AssetUpdater(assets_dir)
    original_copy = shutil.copy2

    def fail_quest_backup(source: Path, destination: Path):
        if Path(source) == quests:
            raise OSError("injected second backup failure")
        return original_copy(source, destination)

    monkeypatch.setattr("utils.asset_updater.shutil.copy2", fail_quest_backup)

    with pytest.raises(RuntimeError, match="injected second backup failure"):
        updater._apply_assets([
            ("items.json", staged_items),
            ("quests.json", staged_quests),
        ])

    assert items.read_text(encoding="utf-8") == "old items"
    assert quests.read_text(encoding="utf-8") == "old quests"


def test_apply_keeps_canonical_asset_present_until_atomic_promotion(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    target = assets_dir / "items.json"
    target.write_text("old items", encoding="utf-8")
    staged = tmp_path / "items.tmp"
    staged.write_text("new items", encoding="utf-8")
    updater = AssetUpdater(assets_dir)
    original_replace = updater._replace_file

    def assert_canonical_then_replace(source: Path, destination: Path):
        assert destination.exists()
        assert destination.read_text(encoding="utf-8") == "old items"
        original_replace(source, destination)

    monkeypatch.setattr(updater, "_replace_file", assert_canonical_then_replace)

    updater._apply_assets([("items.json", staged)])

    assert target.read_text(encoding="utf-8") == "new items"
    assert not list(assets_dir.glob(".*.dndtools-*.bak"))


def test_cross_volume_fallback_stages_locally_before_atomic_promotion(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    target = assets_dir / "items.json"
    target.write_text("old items", encoding="utf-8")
    staged = tmp_path / "external" / "items.tmp"
    staged.parent.mkdir()
    staged.write_text("new items", encoding="utf-8")
    updater = AssetUpdater(assets_dir)
    real_replace = os.replace

    def reject_external_source(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path == staged:
            assert target.read_text(encoding="utf-8") == "old items"
            raise OSError("injected cross-volume rename")
        assert source_path.parent == target.parent
        assert destination_path == target
        assert target.read_text(encoding="utf-8") == "old items"
        return real_replace(source, destination)

    monkeypatch.setattr("utils.asset_updater.os.replace", reject_external_source)

    updater._replace_file(staged, target)

    assert target.read_text(encoding="utf-8") == "new items"
    assert not staged.exists()
    assert not list(assets_dir.glob(".items.json.dndtools-*.tmp"))


def test_apply_rejects_duplicate_batch_before_mutating_any_asset(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    target = assets_dir / "items.json"
    target.write_text("old items", encoding="utf-8")
    first = tmp_path / "first.tmp"
    second = tmp_path / "second.tmp"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    updater = AssetUpdater(assets_dir)

    with pytest.raises(RuntimeError, match="duplicate path"):
        updater._apply_assets([
            ("items.json", first),
            ("items.json", second),
        ])

    assert target.read_text(encoding="utf-8") == "old items"
    assert first.exists()
    assert second.exists()
