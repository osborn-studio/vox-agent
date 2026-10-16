#!/usr/bin/env python3
"""Run cargo-audit against the repository's pinned, expiring policy."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA = "voxagent.rust-audit-policy"
SCHEMA_VERSION = 1
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ADVISORY_RE = re.compile(r"^RUSTSEC-\d{4}-\d{4}$")
_REPOSITORY_URL = "https://github.com/RustSec/advisory-db.git"


class PolicyError(ValueError):
    """The audit policy is ambiguous, stale, or unsafe."""


@dataclass(frozen=True)
class AuditException:
    advisory_id: str
    reason: str
    owner: str
    tracking_url: str
    expires_on: date


@dataclass(frozen=True)
class AuditPolicy:
    cargo_audit_version: str
    artifact_target: str
    artifact_url: str
    artifact_sha256: str
    advisory_database_url: str
    advisory_database_commit: str
    advisory_database_published_at: date
    max_database_age_days: int
    lockfiles: tuple[str, ...]
    exceptions: tuple[AuditException, ...]


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{name} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise PolicyError(f"{name} keys invalid; missing={missing}, unknown={unknown}")


def _parse_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise PolicyError(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyError(f"{name} must be an ISO date") from exc


def load_policy(path: Path, *, today: date | None = None) -> AuditPolicy:
    try:
        root = _object(json.loads(path.read_text(encoding="utf-8")), "policy")
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"could not read policy: {exc}") from exc
    _exact_keys(
        root,
        {
            "schema",
            "schemaVersion",
            "cargoAuditVersion",
            "ciArtifact",
            "advisoryDatabase",
            "maxDatabaseAgeDays",
            "lockfiles",
            "exceptions",
        },
        "policy",
    )
    if root["schema"] != SCHEMA or root["schemaVersion"] != SCHEMA_VERSION:
        raise PolicyError("unsupported Rust audit policy schema")

    version = root["cargoAuditVersion"]
    if not isinstance(version, str) or not _SEMVER_RE.fullmatch(version):
        raise PolicyError("cargoAuditVersion must be an exact x.y.z version")

    artifact = _object(root["ciArtifact"], "ciArtifact")
    _exact_keys(artifact, {"target", "url", "sha256"}, "ciArtifact")
    target = artifact["target"]
    expected_url = (
        "https://github.com/rustsec/rustsec/releases/download/"
        f"cargo-audit/v{version}/cargo-audit-{target}-v{version}.tgz"
    )
    if not isinstance(target, str) or not target:
        raise PolicyError("ciArtifact.target must be non-empty")
    if artifact["url"] != expected_url:
        raise PolicyError("ciArtifact.url must match the pinned version and target")
    if not isinstance(artifact["sha256"], str) or not _SHA256_RE.fullmatch(
        artifact["sha256"]
    ):
        raise PolicyError("ciArtifact.sha256 must be a lowercase SHA-256 digest")

    database = _object(root["advisoryDatabase"], "advisoryDatabase")
    _exact_keys(database, {"url", "commit", "publishedAt"}, "advisoryDatabase")
    if database["url"] != _REPOSITORY_URL:
        raise PolicyError(f"advisoryDatabase.url must be {_REPOSITORY_URL}")
    commit = database["commit"]
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise PolicyError("advisoryDatabase.commit must be a 40-character Git commit")
    published_at = _parse_date(database["publishedAt"], "advisoryDatabase.publishedAt")

    max_age = root["maxDatabaseAgeDays"]
    if isinstance(max_age, bool) or not isinstance(max_age, int) or not 1 <= max_age <= 30:
        raise PolicyError("maxDatabaseAgeDays must be an integer from 1 through 30")
    effective_today = today or date.today()
    age_days = (effective_today - published_at).days
    if age_days < 0:
        raise PolicyError("advisoryDatabase.publishedAt cannot be in the future")
    if age_days > max_age:
        raise PolicyError(f"advisory database pin is older than {max_age} days")

    raw_lockfiles = root["lockfiles"]
    if not isinstance(raw_lockfiles, list) or not raw_lockfiles:
        raise PolicyError("lockfiles must be a non-empty list")
    lockfiles: list[str] = []
    for value in raw_lockfiles:
        if not isinstance(value, str):
            raise PolicyError("each lockfile must be a repository-relative path")
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.name != "Cargo.lock":
            raise PolicyError("each lockfile must be a safe repository Cargo.lock path")
        if value in lockfiles:
            raise PolicyError(f"duplicate lockfile: {value}")
        lockfiles.append(value)

    raw_exceptions = root["exceptions"]
    if not isinstance(raw_exceptions, list):
        raise PolicyError("exceptions must be a list")
    exceptions: list[AuditException] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_exceptions):
        item = _object(raw, f"exceptions[{index}]")
        _exact_keys(
            item,
            {"id", "reason", "owner", "trackingUrl", "expiresOn"},
            f"exceptions[{index}]",
        )
        advisory_id = item["id"]
        if not isinstance(advisory_id, str) or not _ADVISORY_RE.fullmatch(advisory_id):
            raise PolicyError(f"exceptions[{index}].id must be a RustSec advisory ID")
        if advisory_id in seen_ids:
            raise PolicyError(f"duplicate exception: {advisory_id}")
        seen_ids.add(advisory_id)
        reason = item["reason"]
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            raise PolicyError(f"exceptions[{index}].reason must document risk and reachability")
        owner = item["owner"]
        if not isinstance(owner, str) or not owner.strip():
            raise PolicyError(f"exceptions[{index}].owner is required")
        tracking_url = item["trackingUrl"]
        if not isinstance(tracking_url, str) or not tracking_url.startswith("https://"):
            raise PolicyError(f"exceptions[{index}].trackingUrl must be an HTTPS URL")
        expires_on = _parse_date(item["expiresOn"], f"exceptions[{index}].expiresOn")
        if expires_on < effective_today:
            raise PolicyError(f"exception {advisory_id} expired on {expires_on}")
        if (expires_on - effective_today).days > 30:
            raise PolicyError(f"exception {advisory_id} may not exceed 30 days")
        exceptions.append(
            AuditException(
                advisory_id=advisory_id,
                reason=reason.strip(),
                owner=owner.strip(),
                tracking_url=tracking_url,
                expires_on=expires_on,
            )
        )

    return AuditPolicy(
        cargo_audit_version=version,
        artifact_target=target,
        artifact_url=artifact["url"],
        artifact_sha256=artifact["sha256"],
        advisory_database_url=database["url"],
        advisory_database_commit=commit,
        advisory_database_published_at=published_at,
        max_database_age_days=max_age,
        lockfiles=tuple(lockfiles),
        exceptions=tuple(exceptions),
    )


def validate_cargo_audit_version(expected: str, output: str) -> None:
    if output.strip() != f"cargo-audit {expected}":
        raise PolicyError(
            f"cargo-audit version drift: expected {expected}, got {output.strip()!r}"
        )


def build_cargo_audit_command(
    policy: AuditPolicy,
    *,
    lockfile: Path,
    database: Path,
) -> list[str]:
    command = [
        "cargo",
        "audit",
        "--db",
        str(database),
        "--no-fetch",
        "--no-yanked",
        "--file",
        str(lockfile),
    ]
    for exception in policy.exceptions:
        command.extend(["--ignore", exception.advisory_id])
    return command


def _run_output(command: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PolicyError(f"command failed: {' '.join(command)}") from exc
    return result.stdout.strip()


def _verify_database(policy: AuditPolicy, database: Path) -> None:
    if not database.is_dir():
        raise PolicyError(f"advisory database directory not found: {database}")
    revision = _run_output(["git", "rev-parse", "HEAD"], cwd=database)
    if revision != policy.advisory_database_commit:
        raise PolicyError(
            "advisory database revision drift: "
            f"expected {policy.advisory_database_commit}, got {revision}"
        )
    origin = _run_output(["git", "remote", "get-url", "origin"], cwd=database)
    if origin != policy.advisory_database_url:
        raise PolicyError(f"advisory database origin drift: {origin}")
    dirty = _run_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=database
    )
    if dirty:
        raise PolicyError("advisory database checkout is modified")


def run_gate(policy_path: Path, database: Path, repo_root: Path) -> int:
    policy = load_policy(policy_path)
    version = _run_output(["cargo-audit", "--version"])
    validate_cargo_audit_version(policy.cargo_audit_version, version)
    _verify_database(policy, database)

    resolved_root = repo_root.resolve()
    for relative in policy.lockfiles:
        lockfile = (resolved_root / relative).resolve()
        try:
            lockfile.relative_to(resolved_root)
        except ValueError as exc:
            raise PolicyError(f"lockfile escapes repository: {relative}") from exc
        if not lockfile.is_file():
            raise PolicyError(f"lockfile not found: {relative}")
        command = build_cargo_audit_command(
            policy,
            lockfile=Path(relative),
            database=database.resolve(),
        )
        print(
            f"auditing {relative} with cargo-audit {policy.cargo_audit_version} "
            f"and RustSec {policy.advisory_database_commit[:12]}"
        )
        result = subprocess.run(command, cwd=resolved_root, check=False)
        if result.returncode != 0:
            return result.returncode
    print(f"rust_advisory_audit=pass lockfiles={len(policy.lockfiles)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("security/rust-audit-policy.json"),
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        return run_gate(args.policy, args.database, args.repo_root)
    except PolicyError as exc:
        print(f"rust_advisory_audit=fail reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
