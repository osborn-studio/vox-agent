"""T03p: private inbox, own inputs / weekly draft and Feishu conversation
contracts are generated from the real service; the committed copy must match."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_extended_fixture import FIXTURE_PATH, generate  # noqa: E402


@pytest.fixture(scope="module")
def fixture():
    return generate()


def test_committed_fixture_matches_the_real_service(fixture):
    committed = json.loads((ROOT / FIXTURE_PATH).read_text(encoding="utf-8"))
    assert committed == fixture, "run: python scripts/context_extended_fixture.py --write"


def test_generation_is_deterministic(fixture):
    assert generate() == fixture


def codes(cases):
    def code(body):
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail.get("code") if isinstance(detail, dict) else detail

    return {(case["response"]["status"], code(case["response"]["body"])) for case in cases}


def test_private_contract_covers_mutations_readback_and_refusals(fixture):
    names = {case["name"] for case in fixture["private"]["http_cases"]}
    for required in ("authorization", "create", "create_replay", "create_conflict", "list", "get",
                     "get_other_owner", "event_readback", "event_readback_not_observed", "edit",
                     "edit_stale_revision", "edit_replay_request", "request_readback", "withdraw",
                     "reference_check_stale", "reference_check_current", "search", "no_auth",
                     "validation_unknown_field"):
        assert required in names, required
    assert any(status == 401 for status, _ in codes(fixture["private"]["http_cases"]))
    statuses = {case["response"]["status"] for case in fixture["private"]["http_cases"]}
    assert {200, 401, 404, 409, 422} <= statuses
    not_observed = next(c for c in fixture["private"]["http_cases"] if c["name"] == "event_readback_not_observed")
    assert not_observed["response"]["body"]["status"] == "not-observed"
    assert not_observed["response"]["body"]["meaning"] == "point-in-time-only-not-absence-proof"


def test_weekly_contract_records_week_rules_and_pagination(fixture):
    cases = {case["name"]: case for case in fixture["inputs_weekly"]["http_cases"]}
    assert cases["weekly_draft"]["response"]["body"]["timezone"] == "Asia/Shanghai"
    assert cases["weekly_not_monday"]["response"]["body"] == {"detail": "INVALID_CONTEXT_WEEK"}
    assert cases["inputs_page_1"]["response"]["body"]["hasMore"] is True
    assert cases["inputs_page_1"]["response"]["body"]["nextCursor"] == "<cursor>"
    assert cases["inputs_page_2"]["response"]["body"]["hasMore"] is False


def test_conversation_contract_records_its_own_status_vocabulary(fixture):
    conversation = fixture["conversations"]
    statuses = {step["receipt"]["status"] for step in conversation["status_sequence"] if step.get("receipt")}
    assert "sent" in statuses and "delivered" not in statuses
    send = {case["name"]: case for case in conversation["http_cases"]}
    assert send["send_accepted"]["response"]["status"] == 202
    assert send["send_conflict_other_conversation"]["response"]["status"] == 409
    assert send["send_forged_conversation"]["response"]["status"] == 403


def test_fixture_never_contains_tokens_paths_raw_ids_or_timestamps(fixture):
    text = json.dumps(fixture)
    assert not re.search(r"Bearer\s+(?!credential)[A-Za-z0-9._~+/=-]{12,}", text)
    for forbidden in ("/tmp", "/private/", "/var/folders", "bootstrap"):
        assert forbidden not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
    assert not re.search(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}", text)


def test_requests_record_their_query_so_cases_can_be_replayed(fixture):
    cases = {case["name"]: case["request"] for case in fixture["inputs_weekly"]["http_cases"]}
    assert cases["inputs_page_1"]["query"]["limit"] == "2" and "cursor" not in cases["inputs_page_1"]["query"]
    assert cases["inputs_page_2"]["query"]["cursor"] == "<cursor>"
    assert cases["inputs_bad_limit"]["query"]["limit"] == "51"
    assert cases["weekly_draft"]["query"]["week"] == "<week-monday>"
    assert cases["weekly_not_monday"]["query"]["week"] != "<week-monday>"
    assert "query" not in cases["weekly_missing_week"]


def test_normalizer_leaves_malformed_values_visible():
    from context_extended_fixture import Normalizer

    normalize = Normalizer()
    assert normalize("2026-10-08T01:02:03.456+00:00") == "<timestamp>"
    assert normalize("2026-10-08T01:02:03") == "2026-10-08T01:02:03"  # no timezone
    assert normalize("2026-10-08T09:02:03+08:00") == "2026-10-08T09:02:03+08:00"
    assert normalize({"week": "2026-10-05"}) == {"week": "<week-monday>"}
    assert normalize({"week": "2026-10-06"}) == {"week": "2026-10-06"}
    assert normalize({"week": "NOT_A_DATE"}) == {"week": "NOT_A_DATE"}
    assert normalize("fc_" + "a" * 60) == "fc_" + "a" * 60
    assert normalize("2026-99-99T25:61:61+00:00") == "2026-99-99T25:61:61+00:00"  # impossible time
    assert normalize("fc_" + "a" * 31 + "-") == "<id-1>"  # base64url may end in '-'
    assert normalize("fc_" + "a" * 32 + "-extra") == "fc_" + "a" * 32 + "-extra"
    uuid = "12345678-1234-4abc-8abc-123456789abc"
    assert normalize("x" + uuid) == "x" + uuid  # embedded in a longer token
    assert normalize("/v1/context-private/" + uuid) == "/v1/context-private/<id-2>"


def test_invalid_week_case_is_stable_across_week_rollover(monkeypatch, fixture):
    import context_extended_fixture as generator
    from datetime import datetime, timedelta

    class NextWeek(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.now(tz)
            return value + timedelta(days=7) if str(tz) == "Asia/Shanghai" else value

    monkeypatch.setattr(generator, "datetime", NextWeek)
    def invalid_case(value):
        return next(c for c in value["inputs_weekly"]["http_cases"] if c["name"] == "weekly_not_monday")
    assert invalid_case(generator.generate()) == invalid_case(fixture)
