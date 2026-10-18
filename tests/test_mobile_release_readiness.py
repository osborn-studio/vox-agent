from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_mobile_device import (
    CheckResult,
    build_evidence,
    is_loopback_engine_url,
    normalize_engine_url,
)


def test_engine_url_normalization_rejects_secrets_and_paths():
    assert normalize_engine_url("http://192.168.1.2:18900/") == ("http://192.168.1.2:18900")

    for unsafe in (
        "http://user:secret@host:18900",
        "http://host:18900/path",
        "http://host:18900?token=secret",
        "file:///tmp/engine.sock",
    ):
        try:
            normalize_engine_url(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe engine URL accepted: {unsafe}")


def test_physical_device_loopback_detection():
    assert is_loopback_engine_url("http://127.0.0.1:18900")
    assert is_loopback_engine_url("http://localhost:18900")
    assert not is_loopback_engine_url("http://192.168.1.2:18900")


def test_evidence_keeps_hardware_only_work_visibly_pending():
    evidence = build_evidence(
        operator="QA",
        platform="iOS",
        device_model="iPhone",
        engine_url="http://192.168.1.2:18900",
        automated=[CheckResult("Engine health", "PASS", "HTTP 200")],
    )

    assert "Engine health | PASS" in evidence
    assert "Microphone capture and upload | PENDING" in evidence
    assert "Background and resume | PENDING" in evidence
    assert "Network failure and recovery | PENDING" in evidence
    assert "MCP hardware flow | PENDING" in evidence
    assert "Do not change `PENDING` to `PASS` without attached evidence" in evidence


def test_product_decisions_name_required_release_dimensions():
    org_adr = Path("docs/decisions/2026-07-17-org-collab-mainline.md")
    roadmap = Path("docs/product/2026-07-17-phase3-5-execution-roadmap.md")
    headings = ("Scope", "Compatibility", "Migration", "Security", "Exit criteria")

    org_content = org_adr.read_text(encoding="utf-8")
    for heading in headings:
        assert heading in org_content

    if not roadmap.exists():
        pytest.skip(f"{roadmap} is a private product plan omitted from the public source snapshot (SOURCE_SNAPSHOT.md)")
    roadmap_content = roadmap.read_text(encoding="utf-8")
    for heading in headings:
        assert heading in roadmap_content
