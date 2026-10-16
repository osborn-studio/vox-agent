"""Tests for the pinned RustSec advisory policy gate."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.rust_audit_gate import (
    PolicyError,
    build_cargo_audit_command,
    load_policy,
    validate_cargo_audit_version,
)


def valid_policy() -> dict:
    return {
        "schema": "voxagent.rust-audit-policy",
        "schemaVersion": 1,
        "cargoAuditVersion": "0.22.2",
        "ciArtifact": {
            "target": "x86_64-unknown-linux-gnu",
            "url": "https://github.com/rustsec/rustsec/releases/download/cargo-audit/v0.22.2/cargo-audit-x86_64-unknown-linux-gnu-v0.22.2.tgz",
            "sha256": "ab28a1bdb54db4d5d8ad5981cf1f959410370b3d28250dbd35f6a44248620e39",
        },
        "advisoryDatabase": {
            "url": "https://github.com/RustSec/advisory-db.git",
            "commit": "b5fc89b8be99e96f79194d8a6f11e9b4143b99f0",
            "publishedAt": "2026-07-17",
        },
        "maxDatabaseAgeDays": 14,
        "lockfiles": ["apps/desktop-tauri/src-tauri/Cargo.lock"],
        "exceptions": [],
    }


def write_policy(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_loads_exact_tool_database_and_lockfile_pins(tmp_path):
    policy = load_policy(
        write_policy(tmp_path / "policy.json", valid_policy()),
        today=date(2026, 7, 18),
    )

    assert policy.cargo_audit_version == "0.22.2"
    assert policy.advisory_database_commit == (
        "b5fc89b8be99e96f79194d8a6f11e9b4143b99f0"
    )
    assert policy.lockfiles == ("apps/desktop-tauri/src-tauri/Cargo.lock",)


def test_rejects_expired_or_unaccountable_exceptions(tmp_path):
    payload = valid_policy()
    payload["exceptions"] = [
        {
            "id": "RUSTSEC-2026-0001",
            "reason": "No patched release exists; affected API is unreachable.",
            "owner": "desktop-security",
            "trackingUrl": "https://github.com/osborn/vox-agents/issues/123",
            "expiresOn": "2026-07-17",
        }
    ]

    with pytest.raises(PolicyError, match="expired"):
        load_policy(
            write_policy(tmp_path / "expired.json", payload),
            today=date(2026, 7, 18),
        )

    payload["exceptions"][0]["expiresOn"] = "2026-07-25"
    payload["exceptions"][0]["trackingUrl"] = ""
    with pytest.raises(PolicyError, match="trackingUrl"):
        load_policy(
            write_policy(tmp_path / "untracked.json", payload),
            today=date(2026, 7, 18),
        )


def test_rejects_floating_tool_or_database_revisions(tmp_path):
    payload = valid_policy()
    payload["cargoAuditVersion"] = "latest"
    with pytest.raises(PolicyError, match="cargoAuditVersion"):
        load_policy(write_policy(tmp_path / "tool.json", payload))

    payload = valid_policy()
    payload["advisoryDatabase"]["commit"] = "main"
    with pytest.raises(PolicyError, match="40-character"):
        load_policy(write_policy(tmp_path / "database.json", payload))


def test_rejects_stale_advisory_database_pin(tmp_path):
    with pytest.raises(PolicyError, match="older than 14 days"):
        load_policy(
            write_policy(tmp_path / "stale.json", valid_policy()),
            today=date(2026, 8, 1),
        )


def test_builds_fail_closed_command_with_only_active_exceptions(tmp_path):
    payload = valid_policy()
    payload["exceptions"] = [
        {
            "id": "RUSTSEC-2026-0001",
            "reason": "No patched release exists; affected API is unreachable.",
            "owner": "desktop-security",
            "trackingUrl": "https://github.com/osborn/vox-agents/issues/123",
            "expiresOn": "2026-07-25",
        }
    ]
    policy = load_policy(
        write_policy(tmp_path / "policy.json", payload),
        today=date(2026, 7, 18),
    )

    command = build_cargo_audit_command(
        policy,
        lockfile=Path("apps/desktop-tauri/src-tauri/Cargo.lock"),
        database=Path("/tmp/advisory-db"),
    )

    assert command == [
        "cargo",
        "audit",
        "--db",
        "/tmp/advisory-db",
        "--no-fetch",
        "--no-yanked",
        "--file",
        "apps/desktop-tauri/src-tauri/Cargo.lock",
        "--ignore",
        "RUSTSEC-2026-0001",
    ]


def test_rejects_installed_cargo_audit_version_drift():
    validate_cargo_audit_version("0.22.2", "cargo-audit 0.22.2")
    with pytest.raises(PolicyError, match="expected 0.22.2"):
        validate_cargo_audit_version("0.22.2", "cargo-audit 0.23.0")
