"""T03: the Windows client's cloud contract fixture is generated from the real
backup app, store and worker transitions; the committed copy must match."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_wire_fixture import FIXTURE_PATH, generate  # noqa: E402


def test_committed_fixture_matches_the_real_service():
    committed = json.loads((ROOT / FIXTURE_PATH).read_text(encoding="utf-8"))
    assert committed == generate(), "run: python scripts/context_wire_fixture.py --write"


def test_generation_is_deterministic():
    assert generate() == generate()


def test_fixture_covers_every_server_status_and_error_code():
    fixture = generate()
    statuses = {step["receipt"]["status"] for seq in fixture["status_sequences"] for step in seq["steps"] if step.get("receipt")}
    assert statuses == {"pending", "sending", "unknown", "delivered", "blocked", "rejected"}
    def code(body):
        detail = body.get("detail") if isinstance(body, dict) else None
        # Backup routes use {"detail": "CODE"}; auth uses {"detail": {"code", "message"}}.
        return detail.get("code") if isinstance(detail, dict) else detail

    codes = {(case["response"]["status"], code(case["response"]["body"])) for case in fixture["http_cases"]}
    assert any(status == 401 and value for status, value in codes), codes
    for expected in [(202, None), (401, "AUTH_REQUIRED"), (401, "INVALID_CREDENTIAL"), (403, "TARGET_DENIED"),
                     (403, "PROJECT_DENIED"), (404, "BACKUP_NOT_FOUND"), (409, "EVENT_PAYLOAD_CONFLICT"),
                     (413, "RENDERED_MESSAGE_TOO_LARGE"), (422, "INVALID_PROJECT_ATTRIBUTION"),
                     (422, "INVALID_BACKUP_INPUT"), (503, "BACKUP_DISABLED")]:
        assert expected in codes, expected


def test_fixture_never_contains_tokens_or_local_paths():
    import re

    text = json.dumps(generate())
    # The auth error message mentions "Bearer credential"; a token value would follow "Bearer ".
    assert not re.search(r"Bearer\s+(?!credential)[A-Za-z0-9._~+/=-]{12,}", text)
    for forbidden in ("/tmp", "/private/", "/var/folders", "bootstrap"):
        assert forbidden not in text


def test_retry_window_boundary_is_recorded_from_the_real_store():
    from engine.input_backup import RETRY_WINDOW_SECONDS

    fixture = generate()
    assert fixture["limits"]["worker_retry_window_seconds"] == RETRY_WINDOW_SECONDS
    claims = {step["action"]: step["claimed"] for seq in fixture["status_sequences"]
              for step in seq["steps"] if step["action"].startswith("worker_claim+")}
    assert claims[f"worker_claim+{RETRY_WINDOW_SECONDS - 1}s"] is True
    assert claims[f"worker_claim+{RETRY_WINDOW_SECONDS}s"] is False


def test_field_limits_are_read_from_the_real_request_model():
    from engine.input_backup import RENDERED_MAX_BYTES, BackupInput

    schema = BackupInput.model_json_schema()["properties"]
    limits = generate()["limits"]
    assert limits["eventId"]["max_chars"] == schema["eventId"]["maxLength"]
    assert limits["eventId"]["pattern"] == schema["eventId"]["pattern"]
    assert limits["text"]["max_chars"] == schema["text"]["maxLength"]
    assert limits["text"]["rendered_max_utf8_bytes"] == RENDERED_MAX_BYTES


def test_operator_recovery_sequences_show_blocked_is_not_final():
    sequences = {seq["name"]: seq for seq in generate()["status_sequences"]}
    blocked = [step["receipt"]["status"] for step in sequences["blocked_requeued_by_operator"]["steps"]]
    assert blocked == ["pending", "sending", "blocked", "pending", "sending", "delivered"]
    unknown = [step["receipt"]["status"] for step in sequences["unknown_requeued_after_attestation"]["steps"]]
    assert unknown == ["pending", "sending", "unknown", "pending", "sending"]
