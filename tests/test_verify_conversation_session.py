from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_conversation_session import (
    CheckResult,
    PUBLIC_INGRESS_ROUTES,
    build_evidence,
    check_public_ingress_contract,
    normalize_engine_url,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_engine_url_is_origin_only_and_never_accepts_credentials() -> None:
    assert normalize_engine_url("https://gateway.example.test:8443/") == (
        "https://gateway.example.test:8443"
    )

    for unsafe in (
        "https://user:secret@gateway.example.test",
        "https://gateway.example.test/public/v1",
        "https://gateway.example.test?token=secret",
        "file:///tmp/engine.sock",
    ):
        with pytest.raises(ValueError):
            normalize_engine_url(unsafe)


def test_gateway_exposes_exactly_the_five_approved_public_operations() -> None:
    result = check_public_ingress_contract(REPO_ROOT)

    assert result.status == "PASS"
    assert PUBLIC_INGRESS_ROUTES == frozenset(
        {
            ("POST", "/public/v1/conversation-sessions/claim"),
            ("GET", "/public/v1/conversation-sessions/:id/live"),
            ("POST", "/public/v1/conversation-sessions/:id/live"),
            ("GET", "/public/v1/conversation-sessions/:id/turns"),
            ("POST", "/public/v1/conversation-sessions/:id/turns"),
        }
    )


def test_evidence_never_serializes_check_details_or_sensitive_values() -> None:
    sensitive = "private transcript invite-token user-123 device-456 token-digest"
    evidence = build_evidence(
        automated=[CheckResult("P3.3 deterministic suite", "PASS", sensitive)],
        generated_at="2026-07-20T00:00:00+00:00",
        commit="0123456789abcdef",
    )

    assert sensitive not in evidence
    assert "invite-token" not in evidence
    assert "user-123" not in evidence
    assert "device-456" not in evidence
    assert "0123456789abcdef" in evidence
    assert "| P3.3 deterministic suite | PASS |" in evidence
    assert evidence.count("| PENDING |") >= 8
    assert "Do not promote a physical row without named-device evidence" in evidence
