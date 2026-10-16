"""T03c tripwire: receipts and /me carry no authorization revision yet.

docs/decisions/2026-10-09-authorization-revision-gap.md records this as a GAP
with a concrete proposal. If the server starts returning an authorization
field, this test fails until the decision, the Rust decoder and the client
copy are updated together, so the guarantee is never widened by accident.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_feishu_login import login, service  # noqa: F401  (pytest fixture)

ROOT = Path(__file__).resolve().parents[1]
DECISION = "docs/decisions/2026-10-09-authorization-revision-gap.md"
RECEIPT_FIELDS = {"eventId", "status", "messageId", "attribution"}


def _receipt_bodies():
    fixture = json.loads((ROOT / "apps/desktop-tauri/test/fixtures/context-backup-wire-v2.json").read_text())
    bodies = []

    def walk(node):
        if isinstance(node, dict):
            request, response = node.get("request"), node.get("response")
            if isinstance(request, dict) and isinstance(response, dict) and isinstance(response.get("body"), dict):
                if "/v1/input-backups" in request.get("path", "") and response.get("status") in (200, 202):
                    bodies.append(response["body"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(fixture)
    return bodies


def test_receipts_carry_no_authorization_revision():
    bodies = _receipt_bodies()
    assert bodies, "the backup fixture contains accepted and read-back receipts"
    for body in bodies:
        assert set(body) <= RECEIPT_FIELDS, f"new receipt fields {set(body) - RECEIPT_FIELDS}: see {DECISION}"


ME_FIELDS = {"expiresAt", "expiresIn", "profile", "targetIds"}
ME_PROFILE_FIELDS = {"appId", "userId", "orgId", "name", "tenantKey", "openId"}


def test_me_carries_no_authorization_revision(service):  # noqa: F811 (pytest fixture)
    """Calls the real service: any new field (whatever its name) trips this."""
    me = service.me(login(service)["token"])
    assert set(me) == ME_FIELDS, f"/me fields changed {set(me) ^ ME_FIELDS}: see {DECISION}"
    assert set(me["profile"]) == ME_PROFILE_FIELDS, f"/me profile fields changed: see {DECISION}"


def test_the_gap_is_recorded_and_disclosed():
    assert (ROOT / DECISION).is_file()
    gap = (ROOT / "docs/releases/4.2.9-GAP.md").read_text(encoding="utf-8")
    assert "T03c" in gap and "does not prove current permission" in gap
