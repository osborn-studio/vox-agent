"""Generate the Windows Context cloud-contract fixture from the real service (T03).

Every request/response pair is produced by the real `create_backup_app`,
`AuthService` and `BackupStore` in a temporary directory, and every status
sequence by the store's own claim/finish/defer/recover transitions (the same
methods the Feishu worker calls). Nothing is hand-written, so the fixture
cannot drift from the server silently: tests/test_context_wire_fixture_v2.py
regenerates it and compares it with the committed copy.

Synthetic data only. No network, tokens, timestamps or local paths are
recorded.

    python scripts/context_wire_fixture.py --check   # exit 1 if stale
    python scripts/context_wire_fixture.py --write   # refresh the committed copy
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE_PATH = "apps/desktop-tauri/test/fixtures/context-backup-wire-v2.json"
SENDER = "Synthetic Sender"
NOW = 1_800_000_000.0  # fixed clock for worker transitions


def _response(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code == 422 and isinstance(body, dict) and isinstance(body.get("detail"), list):
        # Pydantic validation detail echoes input; keep only its shape.
        body = {"detail": [{"type": item.get("type"), "loc": item.get("loc")} for item in body["detail"]]}
    return {"status": response.status_code, "body": body,
            "cache_control": response.headers.get("cache-control")}


def _case(name, method, path, response, request=None, auth="owner", note=None):
    case = {"name": name, "request": {"method": method, "path": path, "auth": auth}, "response": _response(response)}
    if request is not None:
        case["request"]["json"] = request
    if note:
        case["note"] = note
    return case


def _body(event="event-1", **changes):
    return {"eventId": event, "targetId": "team", "text": "synthetic context", **changes}


def _limits(model, rendered_max_bytes: int, retry_window: int, max_ascii: int) -> dict:
    """Field limits read from the real request model's JSON schema."""
    properties = model.model_json_schema()["properties"]

    def field(name):
        spec = properties[name]
        spec = next((option for option in spec.get("anyOf", []) if option.get("type") == "string"), spec)
        return {key: value for key, value in (("max_chars", spec.get("maxLength")),
                                               ("min_chars", spec.get("minLength")),
                                               ("pattern", spec.get("pattern"))) if value is not None}

    text = field("text")
    text.update(rendered_max_utf8_bytes=rendered_max_bytes,
                max_ascii_chars_accepted_for_sender={"sender": SENDER, "chars": max_ascii})
    return {"eventId": field("eventId"), "targetId": field("targetId"), "text": text,
            "projectId": field("projectId"), "repository": field("repository"),
            "worker_retry_window_seconds": retry_window}


def generate() -> dict:
    from fastapi.testclient import TestClient

    from engine.auth import AuthService
    from engine.backup_app import BackupAppContext, create_backup_app
    from engine.context_projects import ContextProject
    from engine.input_backup import RENDERED_MAX_BYTES, RETRY_WINDOW_SECONDS, BackupInput, BackupStore, Target
    from engine.org_store import OrgStore
    from engine.voice_audit import VoiceAuditLog

    with tempfile.TemporaryDirectory() as temp:
        tmp = Path(temp)
        org = OrgStore(tmp / "identity")
        audit = VoiceAuditLog()
        auth = AuthService(org_store=org, audit_log=audit, edition="enterprise",
                           bootstrap_admin_token="synthetic-bootstrap-credential-long-enough")
        owner = auth.create_user(org_id="default-org", email="owner@example.test", display_name=SENDER)
        other = auth.create_user(org_id="default-org", email="other@example.test", display_name="Other")
        _, owner_token = auth.issue_credential(user_id=owner["id"], name="synthetic")
        _, other_token = auth.issue_credential(user_id=other["id"], name="synthetic")
        headers = {"owner": {"Authorization": f"Bearer {owner_token}"},
                   "other": {"Authorization": f"Bearer {other_token}"}, "none": {}}
        store = BackupStore(tmp / "backup.sqlite")
        target = Target("team", 1, "default-org", "oc_synthetic", frozenset({owner["id"], other["id"]}))
        context = BackupAppContext(auth, store, (target,), enabled=True)
        context.projects = (
            ContextProject(id="p1", org_id="default-org", name="Project One",
                           repository_id="studio/repo", allowed_users=[owner["id"]]),
            ContextProject(id="p-other", org_id="default-org", name="Not Shared",
                           repository_id="studio/other", allowed_users=[other["id"]]),
        )
        cases, sequences = [], []
        try:
            with TestClient(create_backup_app(context)) as client:
                def call(name, method, path, request=None, auth="owner", note=None):
                    response = client.request(method, path, headers=headers[auth], json=request)
                    cases.append(_case(name, method, path, response, request, auth, note))
                    return response

                # --- submission and replay ---
                call("submit_accepted", "POST", "/v1/input-backups", _body())
                call("submit_replay_same_payload", "POST", "/v1/input-backups", _body(),
                     note="Same org/actor/eventId and canonical payload returns the stored receipt.")
                call("submit_conflict_different_payload", "POST", "/v1/input-backups",
                     _body(text="different synthetic context"))
                call("submit_conflict_project_added_same_event", "POST", "/v1/input-backups",
                     _body(targetId="team", text="synthetic context", projectId="p1", repoSource="manual"),
                     note="Idempotency namespace is (org, actor, eventId); target/project are part of the payload.")
                call("submit_same_event_other_actor_is_independent", "POST", "/v1/input-backups",
                     _body(), auth="other")
                call("submit_project_attributed", "POST", "/v1/input-backups",
                     _body("event-p1", projectId="p1", repoSource="manual"))
                call("submit_project_with_matching_repository", "POST", "/v1/input-backups",
                     _body("event-p1-repo", projectId="p1", repoSource="manual", repository="studio/repo"))
                # --- refusals ---
                call("submit_no_auth", "POST", "/v1/input-backups", _body("event-noauth"), auth="none")
                call("submit_target_denied", "POST", "/v1/input-backups", _body("event-t", targetId="unknown"))
                call("submit_project_denied_not_member", "POST", "/v1/input-backups",
                     _body("event-pd", projectId="p-other", repoSource="manual"))
                call("submit_project_denied_unknown", "POST", "/v1/input-backups",
                     _body("event-pu", projectId="missing", repoSource="manual"))
                call("submit_attribution_repo_source_without_project", "POST", "/v1/input-backups",
                     _body("event-a1", repoSource="manual"))
                call("submit_attribution_project_without_repo_source", "POST", "/v1/input-backups",
                     _body("event-a2", projectId="p1"))
                call("submit_attribution_repository_mismatch", "POST", "/v1/input-backups",
                     _body("event-a3", projectId="p1", repoSource="manual", repository="studio/other"))
                call("submit_schema_unknown_field", "POST", "/v1/input-backups", _body("event-s1", actorId="forged"))
                call("submit_schema_bad_event_id", "POST", "/v1/input-backups", _body("event with spaces"))
                call("submit_schema_text_over_8192_chars", "POST", "/v1/input-backups", _body("event-s2", text="a" * 8193))
                call("submit_rendered_too_large_cjk", "POST", "/v1/input-backups", _body("event-big", text="字" * 3000),
                     note="Passes the 8192-character schema limit but the rendered UTF-8 message exceeds 8192 bytes.")
                # --- account / service state ---
                context.enabled = False
                call("submit_backup_disabled", "POST", "/v1/input-backups", _body("event-off"))
                context.enabled = True
                auth.org_store.update_user(other["id"], status="disabled")
                call("submit_account_disabled", "POST", "/v1/input-backups", _body("event-dis"), auth="other",
                     note="A disabled account is rejected by authentication (401), before the route's 403 "
                          "ACCOUNT_DISABLED check, which is only reachable if the account is disabled mid-request.")
                auth.org_store.update_user(other["id"], status="active")
                # --- readback ---
                call("status_owner", "GET", "/v1/input-backups/event-1")
                call("status_project_attributed", "GET", "/v1/input-backups/event-p1")
                call("status_unknown_event", "GET", "/v1/input-backups/never-submitted")
                call("status_no_auth", "GET", "/v1/input-backups/event-1", auth="none")
                call("projects_catalog", "GET", "/v1/context-projects")
                call("projects_no_auth", "GET", "/v1/context-projects", auth="none")

                # --- rendered byte boundary for this sender name ---
                low, high = 1, RENDERED_MAX_BYTES
                while low < high:
                    mid = (low + high + 1) // 2
                    response = client.post("/v1/input-backups", headers=headers["owner"],
                                           json=_body(f"bound-{mid}", text="a" * mid))
                    low, high = (mid, high) if response.status_code == 202 else (low, mid - 1)
                max_ascii = low

                # --- status sequences driven by the store's worker transitions ---
                def get(event, auth="owner"):
                    response = client.get(f"/v1/input-backups/{event}", headers=headers[auth])
                    return response.json() if response.status_code == 200 else {"http_status": response.status_code}

                def submit(event):
                    client.post("/v1/input-backups", headers=headers["owner"], json=_body(event))
                    record_id = store.db.execute(
                        "SELECT r.id FROM backup_records r WHERE r.event=? AND r.actor=?",
                        (event, owner["id"])).fetchone()[0]
                    store.db.execute("UPDATE backup_outbox SET next_attempt=0 WHERE id=?", (record_id,))
                    store.db.commit()
                    return record_id

                def drain_others(keep):
                    with store.lock, store.db:
                        store.db.execute("UPDATE backup_outbox SET next_attempt=? WHERE id<>?", (NOW * 10, keep))

                def sequence(name, event, actions, note):
                    record_id = submit(event)
                    drain_others(record_id)
                    steps = [{"action": "submit", "receipt": get(event)}]
                    for action in actions:
                        kind, arg = action if isinstance(action, tuple) else (action, None)
                        if kind == "claim":
                            job = store.claim(now=NOW if arg is None else NOW + arg)
                            steps.append({"action": "worker_claim" + ("" if arg is None else f"+{arg}s"),
                                          "claimed": bool(job and job["id"] == record_id), "receipt": get(event)})
                            last_job[0] = job
                        elif kind == "finish":
                            status, message = arg
                            if status == "blocked":
                                # The worker records an authorization denial through block().
                                store.block(last_job[0], now=NOW)
                            else:
                                store.finish(record_id, status, message_id=message, now=NOW)
                            steps.append({"action": f"worker_finish_{status}", "receipt": get(event)})
                        elif kind == "defer":
                            store.defer(last_job[0], now=NOW)
                            steps.append({"action": "worker_defer_without_send", "receipt": get(event)})
                        elif kind == "recover":
                            store.recover()
                            steps.append({"action": "worker_restart_recover", "receipt": get(event)})
                        elif kind == "operator_requeue":
                            from engine.backup_delivery_recovery import apply_recovery

                            apply_recovery(tmp / "backup.sqlite", "default-org", owner["id"], [event],
                                           operator="synthetic-operator", reason="synthetic recovery",
                                           attest_not_delivered=arg, now=NOW + RETRY_WINDOW_SECONDS + 1)
                            with store.lock, store.db:
                                store.db.execute("UPDATE backup_outbox SET next_attempt=0 WHERE id=?", (record_id,))
                            steps.append({"action": "operator_requeue" + ("_attested" if arg else ""),
                                          "receipt": get(event)})
                    sequences.append({"name": name, "note": note, "steps": steps})

                last_job = [None]
                sequence("delivered", "seq-delivered", ["claim", ("finish", ("delivered", "om_synthetic"))],
                         "Only a delivered receipt carries a messageId.")
                sequence("crash_while_sending_becomes_unknown", "seq-crash", ["claim", "recover"],
                         "A worker restart turns an in-flight send into unknown; it is retried inside the window.")
                window = RETRY_WINDOW_SECONDS
                sequence("unknown_retried_inside_window", "seq-retry",
                         ["claim", ("finish", ("unknown", None)), ("claim", window - 1)],
                         "unknown is reclaimed while fewer than the window's seconds have passed since the first attempt.")
                sequence("unknown_not_retried_at_window", "seq-window",
                         ["claim", ("finish", ("unknown", None)), ("claim", window)],
                         "From the window boundary on, the server never retries; GET keeps returning unknown.")
                sequence("blocked_is_terminal_for_the_worker", "seq-blocked", ["claim", ("finish", ("blocked", None)), ("claim", 61)],
                         "blocked is never reclaimed automatically; recovery needs an operator path (T03d).")
                sequence("rejected", "seq-rejected", ["claim", ("finish", ("rejected", None))],
                         "rejected is terminal.")
                sequence("deferred_without_send_returns_to_pending", "seq-defer", ["claim", "defer"],
                         "No POST was attempted, so the record goes back to pending, not unknown.")
                sequence("blocked_requeued_by_operator", "seq-op-blocked",
                         ["claim", ("finish", ("blocked", None)), ("operator_requeue", False),
                          ("claim", window + 2), ("finish", ("delivered", "om_synthetic_recovered"))],
                         "An operator can requeue blocked (never sent) after fixing access; clients must keep "
                         "blocked readable and re-check it, not treat it as final.")
                sequence("unknown_requeued_after_attestation", "seq-op-unknown",
                         ["claim", ("finish", ("unknown", None)), ("operator_requeue", True), ("claim", window + 2)],
                         "Past the window an operator may requeue unknown only after attesting the message is "
                         "absent from the group (docs/decisions/2026-10-08-backup-delivery-recovery-v1.md).")
        finally:
            store.close()
            org.close()
            audit.close()

    return {
        "version": 2,
        "generated_by": "scripts/context_wire_fixture.py",
        "source_contract": ["engine.backup_app.create_backup_app", "engine.input_backup.BackupStore",
                            "engine.auth.require_principal"],
        "idempotency": {
            "namespace": ["org", "actor", "eventId"],
            "payload_fingerprint": "sha256 of canonical BackupInput (targetId, text, projectId, repoSource, repository)",
            "same_payload": "202 with the stored receipt",
            "different_payload": "409 EVENT_PAYLOAD_CONFLICT",
            "retention": "records are never deleted by service code (ADR 2026-10-08-backup-idempotency-retention-v1)",
        },
        "error_envelopes": {
            "backup_routes": {"detail": "CODE"},
            "authentication": {"detail": {"code": "CODE", "message": "text"}},
            "validation": {"detail": "INVALID_BACKUP_INPUT"},
        },
        "limits": _limits(BackupInput, RENDERED_MAX_BYTES, RETRY_WINDOW_SECONDS, max_ascii),
        "visible_statuses": ["pending", "sending", "unknown", "delivered", "blocked", "rejected"],
        "http_cases": cases,
        "status_sequences": sequences,
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
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if current != generated:
        print(f"{FIXTURE_PATH} is stale; run --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
