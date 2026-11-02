"""Guards for the manual Windows installer workflow (release-windows.yml)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release-windows.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_token_is_read_only_and_reads_no_secrets():
    text = _workflow()
    assert "permissions:\n  contents: read\n" in text
    assert "contents: write" not in text
    assert "secrets." not in text
    assert "self-hosted" not in text


def test_triggers_are_dispatch_and_packaging_pull_requests_only():
    text = _workflow()
    on = text[text.index("\non:\n"):text.index("\npermissions:")]
    assert "workflow_dispatch:" in on
    assert "pull_request:\n    paths:" in on
    for trigger in ("push:", "release:", "tags:", "schedule:", "pull_request_target"):
        assert trigger not in on


def test_runs_on_github_hosted_windows():
    assert "runs-on: windows-latest" in _workflow()


def test_engine_is_frozen_and_checked_before_packaging_and_upload():
    text = _workflow()
    order = [
        "--stage source --stage host",
        "scripts/build_windows_engine.py --target x86_64-pc-windows-msvc --verify",
        "--sku full",
        "npx tauri build --config src-tauri/tauri.release-engine.conf.json --bundles nsis msi",
        "sha256sum",
        "uses: actions/upload-artifact",
    ]
    positions = [text.index(step) for step in order]
    assert positions == sorted(positions)


def test_release_config_bundles_the_engine():
    config = json.loads(
        (ROOT / "apps/desktop-tauri/src-tauri/tauri.release-engine.conf.json").read_text(encoding="utf-8")
    )
    assert config["bundle"]["externalBin"] == ["binaries/voxagent-engine"]


def test_smartscreen_warning_is_documented_in_the_run_summary():
    text = _workflow()
    assert "SmartScreen" in text
    assert "Run anyway" in text
    assert "not a device pass" in text
