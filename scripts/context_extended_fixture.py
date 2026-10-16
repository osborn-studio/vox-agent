"""Generate the private inbox, weekly draft and Feishu conversation contract
fixture from the real service (T03p).

Like scripts/context_wire_fixture.py, every response comes from the real
FastAPI apps and stores in a temporary directory. Feishu is replaced by the
same offline MockTransport and login Provider the conversation tests use.
Random ids, wall-clock timestamps, cursors and the current week are replaced
by stable placeholders so the output is deterministic.
tests/test_context_extended_wire_fixture.py compares it with the committed copy.

    python scripts/context_extended_fixture.py --check
    python scripts/context_extended_fixture.py --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE_PATH = "apps/desktop-tauri/test/fixtures/context-extended-wire-v1.json"
# Only well-formed UTC timestamps are replaced; anything else stays raw so a
# format or timezone change shows up as a golden-file difference.
_TIMESTAMP = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?\+00:00$")
_WEEK = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")
# Server-generated identifiers, also when embedded in a path.
_ID_CHAR = r"[A-Za-z0-9_-]"
_VOLATILE = re.compile(
    rf"(?<!{_ID_CHAR})(?:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"  # record ids (UUID v4)
    r"|fc_[A-Za-z0-9_-]{32}"                                               # conversation ids (base64url)
    r"|feishu-[0-9a-f]{32}"                                                # Feishu-backed user ids
    rf")(?!{_ID_CHAR})"
)


class Normalizer:
    """Replace values that change between runs with stable placeholders."""

    def __init__(self):
        self.ids: dict[str, str] = {}

    def _id(self, match):
        return self.ids.setdefault(match.group(0), f"<id-{len(self.ids) + 1}>")

    def __call__(self, value, key=None):
        if isinstance(value, dict):
            return {k: self(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, str):
            if key in {"nextCursor", "cursor"}:
                return "<cursor>"
            if key in {"createdAt", "updatedAt"} and value.isdigit():
                return "<epoch-ms>"
            if key == "policyRevision":
                return "<revision>"
            if key == "week" and _WEEK.match(value):
                try:
                    is_monday = datetime.fromisoformat(value).weekday() == 0
                except ValueError:
                    is_monday = False
                return "<week-monday>" if is_monday else value
            if _TIMESTAMP.match(value):
                try:
                    parsed = datetime.fromisoformat(value)
                except ValueError:
                    return value  # impossible date/time stays visible
                if parsed.utcoffset() == timedelta(0):
                    return "<timestamp>"
                return value
            return _VOLATILE.sub(self._id, value)
        return value


def _record(normalize, name, method, path, response, request=None, auth="owner", note=None, params=None):
    try:
        body = response.json()
    except ValueError:
        body = None
    case = {"name": name, "request": {"method": method, "path": normalize(path), "auth": auth},
            "response": {"status": response.status_code, "body": normalize(body),
                         "cache_control": response.headers.get("cache-control")}}
    if request is not None:
        case["request"]["json"] = normalize(request)
    if params:
        query = dict(params)
        for key in ("start", "end"):
            if key in query:
                query[key] = "<window-" + key + ">"
        case["request"]["query"] = normalize(query)
    if note:
        case["note"] = note
    return case


def _private_and_weekly(normalize):
    from fastapi.testclient import TestClient

    from engine.auth import AuthService
    from engine.backup_app import BackupAppContext, create_backup_app
    from engine.input_backup import BackupStore, Target
    from engine.org_store import OrgStore
    from engine.voice_audit import VoiceAuditLog

    private_cases, weekly_cases = [], []
    with tempfile.TemporaryDirectory() as temp:
        tmp = Path(temp)
        org, audit = OrgStore(tmp / "identity"), VoiceAuditLog()
        auth = AuthService(org_store=org, audit_log=audit, edition="enterprise",
                           bootstrap_admin_token="synthetic-bootstrap-credential-long-enough")
        owner = auth.create_user(org_id="default-org", email="owner@example.test", display_name="Owner")
        other = auth.create_user(org_id="default-org", email="other@example.test", display_name="Other")
        _, owner_token = auth.issue_credential(user_id=owner["id"], name="synthetic")
        _, other_token = auth.issue_credential(user_id=other["id"], name="synthetic")
        headers = {"owner": {"Authorization": f"Bearer {owner_token}"},
                   "other": {"Authorization": f"Bearer {other_token}"}, "none": {}}
        store = BackupStore(tmp / "backup.sqlite")
        target = Target("team", 1, "default-org", "oc_synthetic", frozenset({owner["id"]}))
        context = replace(BackupAppContext(auth, store, (target,), enabled=True), private_context_enabled=True)
        now = datetime.now(timezone.utc)
        window = {"start": (now - timedelta(days=1)).isoformat(), "end": (now + timedelta(days=1)).isoformat()}
        try:
            with TestClient(create_backup_app(context)) as client:
                def call(cases, name, method, path, request=None, auth="owner", params=None, note=None):
                    response = client.request(method, path, headers=headers[auth], json=request, params=params)
                    cases.append(_record(normalize, name, method, path, response, request, auth, note, params))
                    return response

                p = private_cases
                call(p, "authorization", "GET", "/v1/context-private-authorization")
                created = call(p, "create", "POST", "/v1/context-private",
                               {"eventId": "personal-1", "text": "synthetic note", "tags": ["question"]}).json()
                record = created["id"]
                call(p, "create_replay", "POST", "/v1/context-private",
                     {"eventId": "personal-1", "text": "synthetic note", "tags": ["question"]},
                     note="Same eventId and payload returns the stored record.")
                call(p, "create_conflict", "POST", "/v1/context-private",
                     {"eventId": "personal-1", "text": "synthetic note", "tags": []})
                call(p, "validation_unknown_field", "POST", "/v1/context-private",
                     {"eventId": "personal-2", "text": "x", "visibility": "team"})
                call(p, "validation_snapshot_without_project", "POST", "/v1/context-private",
                     {"eventId": "personal-3", "text": "x", "expectedRepositoryId": "studio/repo"})
                call(p, "no_auth", "POST", "/v1/context-private", {"eventId": "personal-4", "text": "x"}, auth="none")
                call(p, "list", "GET", "/v1/context-private", params=window)
                call(p, "get", "GET", f"/v1/context-private/{record}")
                call(p, "get_other_owner", "GET", f"/v1/context-private/{record}", auth="other")
                call(p, "event_readback", "GET", "/v1/context-private-events/personal-1")
                call(p, "event_readback_not_observed", "GET", "/v1/context-private-events/never-created",
                     note="200 not-observed is a point-in-time answer, never proof the create did not happen.")
                call(p, "edit", "PUT", f"/v1/context-private/{record}",
                     {"requestId": "edit-1", "expectedRevision": 1, "text": "synthetic edit", "tags": []})
                call(p, "edit_replay_request", "PUT", f"/v1/context-private/{record}",
                     {"requestId": "edit-1", "expectedRevision": 1, "text": "synthetic edit", "tags": []})
                call(p, "edit_stale_revision", "PUT", f"/v1/context-private/{record}",
                     {"requestId": "edit-2", "expectedRevision": 1, "text": "another edit", "tags": []})
                call(p, "request_readback", "GET", "/v1/context-private-events/personal-1/requests/edit-1",
                     note="Reconstructs the revision accepted by that request, not the current record.")
                call(p, "reference_check_stale", "POST", "/v1/context-private-reference-check",
                     {"sources": [{"id": record, "revision": 1}]})
                call(p, "reference_check_current", "POST", "/v1/context-private-reference-check",
                     {"sources": [{"id": record, "revision": 2}]})
                call(p, "search", "POST", "/v1/context-private-search", {**window, "q": "edit"})
                call(p, "withdraw", "POST", f"/v1/context-private/{record}/withdraw",
                     {"requestId": "withdraw-1", "expectedRevision": 2})
                call(p, "get_after_withdraw", "GET", f"/v1/context-private/{record}")

                w = weekly_cases
                for index in range(3):
                    client.post("/v1/input-backups", headers=headers["owner"],
                                json={"eventId": f"team-{index}", "targetId": "team", "text": f"team note {index}"})
                first = call(w, "inputs_page_1", "GET", "/v1/context-inputs", params={**window, "limit": "2"})
                call(w, "inputs_page_2", "GET", "/v1/context-inputs",
                     params={**window, "limit": "2", "cursor": first.json()["nextCursor"]})
                call(w, "inputs_bad_limit", "GET", "/v1/context-inputs", params={**window, "limit": "51"})
                call(w, "inputs_unknown_parameter", "GET", "/v1/context-inputs", params={**window, "q": "x"})
                shanghai = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                monday = shanghai - timedelta(days=shanghai.weekday())
                call(w, "weekly_draft", "GET", "/v1/context-weekly-draft", params={"week": monday.isoformat()},
                     note="Week is the Monday of the current Asia/Shanghai week; private records are excluded.")
                call(w, "weekly_not_monday", "GET", "/v1/context-weekly-draft",
                     # A fixed Tuesday keeps the invalid-input golden case stable across weeks.
                     params={"week": "2026-10-06"})
                call(w, "weekly_missing_week", "GET", "/v1/context-weekly-draft")
        finally:
            store.close()
            org.close()
            audit.close()
    return private_cases, weekly_cases


def _conversations(normalize):
    import httpx
    from fastapi.testclient import TestClient

    from engine.backup_runtime import create_runtime_app
    from engine.feishu_conversation_worker import ConversationWorker, authorize_current
    from tests.test_backup_runtime import secret, settings
    from tests.test_feishu_conversations import chat, page
    from tests.test_feishu_login import Provider, login

    def handle(req):
        if req.url.path.endswith("/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "synthetic", "expire": 7200})
        if req.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_sent", "chat_id": "oc_one"}})
        if req.url.path.endswith("/chats"):
            return httpx.Response(200, json=page([chat()]))
        return httpx.Response(200, json=page([{"member_id": "ou_alice"}]))

    cases, sequence = [], []
    with tempfile.TemporaryDirectory() as temp:
        app = create_runtime_app(
            settings(Path(temp), conversations_enabled=True,
                     feishu_login={"public_base_url": "http://127.0.0.1:8766", "tenant_orgs": {"team": "default-org"}}),
            read_secret=secret, conversation_transport_factory=lambda: httpx.MockTransport(handle))
        with TestClient(app, base_url="http://127.0.0.1:8766") as http:
            app.state.conversation_runtime.close()  # step the worker manually
            service = app.state.feishu_login
            service.provider = Provider()
            headers = {"owner": {"Authorization": "Bearer " + login(service)["token"]}, "none": {}}

            def call(name, method, path, request=None, auth="owner", note=None, params=None):
                response = http.request(method, path, headers=headers[auth], json=request, params=params)
                cases.append(_record(normalize, name, method, path, response, request, auth, note, params))
                return response

            listing = call("list", "GET", "/v1/conversations").json()["conversations"]
            dm, group = [item["id"] for item in listing]
            call("list_no_auth", "GET", "/v1/conversations", auth="none")
            body = {"eventId": "conv-1", "text": "synthetic message"}
            call("send_accepted", "POST", f"/v1/conversations/{dm}/messages", body)
            call("send_replay", "POST", f"/v1/conversations/{dm}/messages", body)
            call("send_conflict_other_conversation", "POST", f"/v1/conversations/{group}/messages", body,
                 note="The eventId is bound to its first conversation.")
            call("send_conflict_text", "POST", f"/v1/conversations/{dm}/messages", {**body, "text": "changed"})
            call("send_forged_conversation", "POST", "/v1/conversations/fc_forged/messages", body)
            call("send_unknown_field", "POST", f"/v1/conversations/{dm}/messages", {**body, "target": "oc_one"})
            receipt = f"/v1/conversations/{dm}/receipts/conv-1"
            sequence.append({"action": "submit", "receipt": normalize(http.get(receipt, headers=headers["owner"]).json())})
            context = app.state.context
            with ConversationWorker(context.conversation_store, context.conversation_discovery.client,
                                    authorize=lambda job: authorize_current(context, job)) as worker:
                worker.run_once()
            sequence.append({"action": "worker_send", "receipt": normalize(http.get(receipt, headers=headers["owner"]).json())})
            call("receipt_other_conversation", "GET", f"/v1/conversations/{group}/receipts/conv-1")
            # History of a group without a shared-read policy: only this user's own submissions.
            for event in ("conv-2", "conv-3"):
                http.post(f"/v1/conversations/{group}/messages", headers=headers["owner"],
                          json={"eventId": event, "text": f"synthetic {event}"})
            messages = f"/v1/conversations/{group}/messages"
            first = call("history_own_first_page", "GET", messages, params={"pageSize": "1"},
                         note="Group without a shared-read policy: own submissions only, not group history.")
            cursor = first.json()["nextCursor"]
            call("history_own_next_page", "GET", messages, params={"pageSize": "1", "cursor": cursor})
            call("history_cursor_consumed", "GET", messages, params={"pageSize": "1", "cursor": cursor},
                 note="A cursor is single use; the client restarts from the first page.")
            call("history_forged_conversation", "GET", "/v1/conversations/fc_forged/messages")
    return cases, sequence


def generate() -> dict:
    normalize = Normalizer()
    private_cases, weekly_cases = _private_and_weekly(normalize)
    conversation_cases, conversation_sequence = _conversations(normalize)
    return {
        "version": 1,
        "generated_by": "scripts/context_extended_fixture.py",
        "placeholders": {"<id-N>": "server-generated identifier, stable within this fixture",
                         "<timestamp>": "server wall-clock ISO-8601 time",
                         "<cursor>": "opaque pagination cursor; pass back unchanged",
                         "<epoch-ms>": "server time in epoch milliseconds, as a decimal string",
                         "<revision>": "opaque authorization revision of a history traversal",
                         "<week-monday>": "Monday of the requested Asia/Shanghai week (YYYY-MM-DD)"},
        "private": {"source_contract": "engine.context_private (PrivateContextJournal routes)",
                    "http_cases": private_cases},
        "inputs_weekly": {"source_contract": "engine.context_inputs", "timezone": "Asia/Shanghai",
                          "http_cases": weekly_cases},
        "conversations": {"source_contract": "engine.feishu_conversations / feishu_conversation_store",
                          "note": "Conversation receipts use their own status vocabulary (e.g. sent), "
                                  "distinct from team backup receipts (delivered).",
                          "http_cases": conversation_cases, "status_sequence": conversation_sequence},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    generated = json.dumps(generate(), ensure_ascii=False, indent=2) + "\n"
    path = ROOT / FIXTURE_PATH
    if args.write:
        path.write_text(generated, encoding="utf-8")
        return 0
    if (path.read_text(encoding="utf-8") if path.exists() else "") != generated:
        print(f"{FIXTURE_PATH} is stale; run --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
