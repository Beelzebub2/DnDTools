from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "update-items.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _release_step(text: str) -> str:
    return text.split("    - name: Publish assets-latest release (create or update)", 1)[1].split(
        "    - name: Notify website of asset update", 1
    )[0]


def test_asset_workflow_serializes_release_and_push_mutations():
    text = _workflow_text()

    assert "concurrency:" in text
    assert "group: darkerdb-assets-${{ github.repository }}" in text
    assert "cancel-in-progress: false" in text
    assert "permissions:\n  contents: write" in text
    assert text.count("ref: main") == 2
    assert text.count("fetch-depth: 0") == 2


def test_release_upload_stages_complete_batch_before_canonical_promotion():
    step = _release_step(_workflow_text())

    stage_position = step.index('echo "Staging $name as $staged_name"')
    promotion_position = step.index('rollback_name="${name}.rollback.${RUN_TOKEN}"')

    assert stage_position < promotion_position
    assert "curl -fsS" in step
    assert "Staged upload for $name failed name/size/digest verification" in step
    assert "rollback_promotions" in step
    assert "prior assets were restored" in step
    assert "Final canonical release verification failed" in step
    assert 'rollback_prefix="${name}.rollback."' in step
    assert '"${name}.staged."' in step
    assert "Required release asset $path is missing or empty" in step
    assert 'curl -s -X POST' not in step


def test_item_failure_cannot_reach_icon_pack_or_suppress_quest_path():
    text = _workflow_text()
    rebuild = text.split("    - name: Rebuild icon pack", 1)[1].split(
        "    - name: Verify icon pack manifest", 1
    )[0]

    assert "steps.update_items.outcome == 'success'" in rebuild
    assert "continue-on-error: true" in text.split(
        "    - name: Update items data", 1
    )[1].split("    - name: Rebuild icon pack", 1)[0]
    assert "Discard incomplete item and icon refresh" in text
    assert "steps.update_quests.outcome == 'success'" in text


def test_patch_detection_uses_authenticated_current_patch_and_force_inputs():
    text = _workflow_text()
    detect = text.split("    - name: Detect DarkerDB build changes", 1)[1].split(
        "    - name: Log up-to-date status", 1
    )[0]

    assert "https://api.darkerdb.com/v2/patches/current" in detect
    assert '"X-API-Version": "2026-08-03"' in detect
    assert '"X-Api-Key": api_key' in detect
    assert "build = payload.get('build')" in detect
    assert "patch = payload.get('patch')" in detect
    assert "force_refresh:" in text
    assert "FORCE_REFRESH: ${{ github.event.inputs.force_refresh }}" in detect
    assert "changed = True" in detect
    assert "print(api_key)" not in detect


def test_checksums_fail_closed_and_commit_stages_only_allowed_assets():
    text = _workflow_text()
    checksum_step = text.split("    - name: Compute asset checksums", 1)[1].split(
        "    - name: Verify jq", 1
    )[0]
    persist_step = text.split("    - name: Commit and push checked asset sources", 1)[1].split(
        "    - name: Publish assets-latest release", 1
    )[0]

    assert "Required release assets are missing or empty" in checksum_step
    assert "Checksum set does not match the required release asset set" in checksum_step
    assert "git add -A --" in persist_step
    assert "git add -A\n" not in persist_step
    assert "Refusing to commit unexpected staged paths" in persist_step
    assert "git push origin HEAD:main" in persist_step


def test_webhook_failure_is_reported_as_workflow_failure():
    text = _workflow_text()
    webhook = text.split("    - name: Notify website of asset update", 1)[1].split(
        "    - name: Report item refresh failure", 1
    )[0]

    assert "RELEASE_WEBHOOK_SECRET is not configured" in webhook
    assert "--retry-all-errors" in webhook
    assert '[[ ! "$HTTP_CODE" =~ ^2[0-9][0-9]$ ]]' in webhook
    assert "Website notification failed" in webhook
    assert "exit 1" in webhook
    assert "Warning: Failed to notify" not in webhook
