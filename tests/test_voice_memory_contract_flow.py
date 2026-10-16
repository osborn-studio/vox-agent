"""Contract flow tests for VOXAGENT mobile companion endpoints.

Covers Task 5 (capture sessions / memory nodes / token ledger) and Task 6
(simulated device protocol). Endpoints are pure in-memory (no lifespan
dependency), so we use TestClient(app) WITHOUT the `with` context manager —
lifespan never runs, and each test gets fresh repos via the fixture below.
"""

import io
import wave
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import engine.server as server
from engine.app_context import create_app_context
from engine.mock_asr import (
    MOCK_MEMORY_NODES,
    MOCK_MEETING_SESSION,
    MOCK_TRANSCRIPT_SEGMENTS,
    seed_workflow,
)
from voxagent_mcp.voxagent_server import get_action_items, get_session, search_memory


@pytest.fixture(params=["memory", "sqlite"])
def client(request, tmp_path):
    """Fresh repos per test, parametrized over both persistence backends."""
    voice_db = None if request.param == "memory" else tmp_path / "voice_memory.db"
    context = create_app_context(
        data_dir=tmp_path / "app",
        voice_memory_db=voice_db,
    )
    yield TestClient(server.create_app(context))
    context.close()


def _context(client):
    return client.app.state.context


# ---------------------------------------------------------------------------
# Gateway-contract guards — required camelCase keys per packages/contracts/src
# ---------------------------------------------------------------------------

REQUIRED_SESSION_KEYS = {
    "id", "workspaceId", "ownerUserId", "source", "mode", "startedAt",
    "endedAt", "languageHints", "privacyLevel", "consentState", "deviceIds",
    "localOnlyUntilSync", "status",
}

REQUIRED_SEGMENT_KEYS = {
    "id", "sessionId", "speakerId", "speakerLabel", "startMs", "endMs",
    "language", "textRaw", "textNormalized", "textTranslated",
    "translationTargetLanguage", "confidence", "model", "isUserMarked",
    "embeddingId",
}

REQUIRED_NODE_KEYS = {
    "id", "workspaceId", "sourceSessionId", "type", "title", "body", "tags",
    "confidence", "createdAt", "updatedAt",
}

REQUIRED_DEVICE_KEYS = {
    "id", "workspaceId", "ownerUserId", "kind", "displayName", "status",
    "batteryPercent", "firmwareVersion", "pairedAt", "lastSeenAt",
}

REQUIRED_PAIRING_KEYS = {"code", "qrPayload", "expiresAt"}

REQUIRED_EVENT_KEYS = {"id", "deviceId", "type", "occurredAt", "payload", "signature"}

REQUIRED_TOOL_KEYS = {"id", "deviceId", "tool", "args", "requestedAt", "status"}


SESSION_PAYLOAD = {
    "workspaceId": "ws_1",
    "ownerUserId": "user_1",
    "source": "MOBILE",
    "mode": "MEETING",
    "languageHints": ["en"],
    "privacyLevel": "PERSONAL",
    "consentState": "UNKNOWN",
    "deviceIds": [],
    "localOnlyUntilSync": False,
}


# ---------------------------------------------------------------------------
# 1. Capture session lifecycle
# ---------------------------------------------------------------------------

class TestCaptureSessions:
    def test_create_session(self, client):
        r = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD)
        assert r.status_code == 200
        body = r.json()
        assert body["id"].startswith("cap_")
        assert body["status"] == "RECORDING"
        assert REQUIRED_SESSION_KEYS <= body.keys()

    def test_get_session(self, client):
        created = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        r = client.get(f"/v1/capture/sessions/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_patch_session_status(self, client):
        created = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        r = client.patch(f"/v1/capture/sessions/{created['id']}", json={"status": "READY"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "READY"
        assert body["updatedAt"]


# ---------------------------------------------------------------------------
# 2. Transcript segments
# ---------------------------------------------------------------------------

class TestTranscriptSegments:
    def test_add_and_list_segment(self, client):
        session = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        segment_payload = {
            "speakerId": "spk_1",
            "startMs": 0,
            "endMs": 2400,
            "textRaw": "hello world",
            "textNormalized": "Hello world.",
            "confidence": 0.92,
            "model": "mock-asr",
        }
        r = client.post(
            f"/v1/capture/sessions/{session['id']}/transcript-segments",
            json=segment_payload,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"].startswith("seg_")
        assert REQUIRED_SEGMENT_KEYS <= body.keys()

        r2 = client.get(f"/v1/capture/sessions/{session['id']}/transcript-segments")
        assert r2.status_code == 200
        assert len(r2.json()) == 1


# ---------------------------------------------------------------------------
# 3. Memory nodes
# ---------------------------------------------------------------------------

class TestMemoryNodes:
    def test_add_and_get_node(self, client):
        session = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        node_payload = {
            "type": "DECISION",
            "title": "Ship the mobile companion",
            "body": "We decided to ship the mobile companion feature this quarter.",
            "tags": ["roadmap"],
            "confidence": 0.9,
            "workspaceId": "ws_1",
            "sourceSessionId": session["id"],
        }
        r = client.post("/v1/memory/nodes", json=node_payload)
        assert r.status_code == 200
        node = r.json()
        assert node["id"].startswith("mem_")
        assert REQUIRED_NODE_KEYS <= node.keys()

        r2 = client.get(f"/v1/memory/nodes/{node['id']}")
        assert r2.status_code == 200
        assert r2.json()["id"] == node["id"]

    def test_search_memory(self, client):
        node_payload = {
            "type": "DECISION",
            "title": "Ship the mobile companion",
            "body": "Decided to ship it.",
            "tags": ["roadmap"],
            "confidence": 0.9,
            "workspaceId": "ws_1",
            "sourceSessionId": None,
        }
        client.post("/v1/memory/nodes", json=node_payload)
        r = client.get("/v1/memory/search", params={"q": "mobile"})
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["node"]["title"] == "Ship the mobile companion"

    def test_search_is_word_order_independent_and_ranked(self, client):
        for title, body in [
            ("Launch in Japan in July", "Tokyo first."),
            ("Berlin office update", "unrelated to launch"),
            ("正式会议纪要", "会议决定七月在日本发布"),
        ]:
            client.post("/v1/memory/nodes", json={
                "type": "DECISION", "title": title, "body": body, "tags": [],
                "confidence": 0.9, "workspaceId": "ws_1", "sourceSessionId": None,
            })
        # word-order-independent: "japan launch" matches "Launch in Japan in July"
        results = client.get("/v1/memory/search", params={"q": "japan launch"}).json()["results"]
        titles = [r["node"]["title"] for r in results]
        assert "Launch in Japan in July" in titles
        # ranked: the full 2-token match outranks any partial match; scores real
        assert results[0]["node"]["title"] == "Launch in Japan in July"
        assert all(r["score"] > 0 for r in results)
        # CJK phrase still matches as a single token
        cjk = client.get("/v1/memory/search", params={"q": "正式会议纪要"}).json()["results"]
        assert any(r["node"]["title"] == "正式会议纪要" for r in cjk)

    def test_rare_term_outranks_common_term(self, client):
        # "launch" is in every node (common → low IDF); "berlin" is in one
        # (rare → high IDF). A query mixing both must rank the Berlin node top.
        for title in ["Launch in Tokyo", "Launch in Osaka", "Launch in Berlin"]:
            client.post("/v1/memory/nodes", json={
                "type": "DECISION", "title": title, "body": "", "tags": [],
                "confidence": 0.9, "workspaceId": "ws_1", "sourceSessionId": None,
            })
        results = client.get("/v1/memory/search", params={"q": "launch berlin"}).json()["results"]
        assert results[0]["node"]["title"] == "Launch in Berlin"
        # a lone ubiquitous term still returns matches but at a low score
        common = client.get("/v1/memory/search", params={"q": "launch"}).json()["results"]
        assert len(common) == 3  # all three matched
        assert results[0]["score"] > common[0]["score"]  # rare+common beats common-only

    def test_recent_nodes(self, client):
        node_payload = {
            "type": "DECISION",
            "title": "Recent node",
            "body": "body",
            "tags": [],
            "confidence": 0.5,
            "workspaceId": "ws_1",
            "sourceSessionId": None,
        }
        node = client.post("/v1/memory/nodes", json=node_payload).json()
        r = client.get("/v1/memory/recent")
        assert r.status_code == 200
        ids = [n["id"] for n in r.json()]
        assert node["id"] in ids

    def test_action_items(self, client):
        r = client.get("/v1/memory/action-items")
        assert r.status_code == 200
        assert r.json() == []

        action_payload = {
            "type": "ACTION_ITEM",
            "title": "Follow up with customer",
            "body": "body",
            "tags": [],
            "confidence": 0.8,
            "workspaceId": "ws_1",
            "sourceSessionId": None,
        }
        node = client.post("/v1/memory/nodes", json=action_payload).json()
        r2 = client.get("/v1/memory/action-items")
        ids = [n["id"] for n in r2.json()]
        assert node["id"] in ids

    def test_list_nodes_for_session(self, client):
        session = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        other = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        repo = _context(client).voice_memory_repo
        a = repo.add_memory_node(
            {"workspaceId": "ws_test", "sourceSessionId": session["id"],
             "type": "SUMMARY", "title": "s", "body": "b", "tags": [], "confidence": 0.9})
        repo.add_memory_node(
            {"workspaceId": "ws_test", "sourceSessionId": other["id"],
             "type": "SUMMARY", "title": "s2", "body": "b2", "tags": [], "confidence": 0.9})
        b = repo.add_memory_node(
            {"workspaceId": "ws_test", "sourceSessionId": session["id"],
             "type": "ACTION_ITEM", "title": "a", "body": "b", "tags": [], "confidence": 0.9})

        nodes = repo.list_nodes_for_session(session["id"])
        assert {n["id"] for n in nodes} == {a["id"], b["id"]}
        summaries = repo.list_nodes_for_session(session["id"], node_type="SUMMARY")
        assert [n["id"] for n in summaries] == [a["id"]]


# ---------------------------------------------------------------------------
# 4. Token ledger
# ---------------------------------------------------------------------------

class TestTokens:
    def test_balance(self, client):
        r = client.get("/v1/tokens/balance")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["balance"], int)
        assert body["balance"] > 0
        assert body["currency"] == "TOKENS"

    def test_ledger(self, client):
        r = client.get("/v1/tokens/ledger")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# 5. 404 checks
# ---------------------------------------------------------------------------

class TestNotFound:
    def test_get_unknown_session(self, client):
        assert client.get("/v1/capture/sessions/unknown").status_code == 404

    def test_patch_unknown_session(self, client):
        r = client.patch("/v1/capture/sessions/unknown", json={"status": "READY"})
        assert r.status_code == 404

    def test_get_segments_unknown_session(self, client):
        assert client.get("/v1/capture/sessions/unknown/transcript-segments").status_code == 404

    def test_add_segment_unknown_session(self, client):
        r = client.post(
            "/v1/capture/sessions/unknown/transcript-segments",
            json={
                "speakerId": "spk_1", "startMs": 0, "endMs": 100,
                "textRaw": "x", "textNormalized": "x",
                "confidence": 0.5, "model": "mock-asr",
            },
        )
        assert r.status_code == 404

    def test_get_unknown_node(self, client):
        assert client.get("/v1/memory/nodes/unknown").status_code == 404


# ---------------------------------------------------------------------------
# 6. Simulated device protocol (Task 6)
# ---------------------------------------------------------------------------

class TestDevicePairing:
    def test_create_pairing_code(self, client):
        r = client.post("/v1/devices/pairing-codes")
        assert r.status_code == 200
        body = r.json()
        assert len(body["code"]) >= 6
        assert body["code"] in body["qrPayload"]
        assert REQUIRED_PAIRING_KEYS <= body.keys()

    def test_pair_success(self, client):
        pairing = client.post("/v1/devices/pairing-codes").json()
        r = client.post("/v1/devices/pair", json={
            "code": pairing["code"],
            "ownerUserId": "user_1",
            "workspaceId": "ws_1",
        })
        assert r.status_code == 200
        device = r.json()
        assert device["status"] == "CONNECTED"
        assert device["batteryPercent"] == 100
        assert device["firmwareVersion"]
        assert REQUIRED_DEVICE_KEYS <= device.keys()

    def test_pair_unknown_code(self, client):
        r = client.post("/v1/devices/pair", json={
            "code": "NOPECODE",
            "ownerUserId": "user_1",
            "workspaceId": "ws_1",
        })
        assert r.status_code == 404

    def test_pair_same_code_twice(self, client):
        pairing = client.post("/v1/devices/pairing-codes").json()
        body = {
            "code": pairing["code"],
            "ownerUserId": "user_1",
            "workspaceId": "ws_1",
        }
        r1 = client.post("/v1/devices/pair", json=body)
        assert r1.status_code == 200
        r2 = client.post("/v1/devices/pair", json=body)
        assert r2.status_code == 404

    def test_pair_expired_code(self, client):
        pairing = client.post("/v1/devices/pairing-codes").json()
        expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        _context(client).device_simulator_repo.pairing_codes[pairing["code"]][
            "expiresAt"
        ] = expired_at

        r = client.post("/v1/devices/pair", json={
            "code": pairing["code"],
            "ownerUserId": "user_1",
            "workspaceId": "ws_1",
        })
        assert r.status_code == 404


class TestDeviceList:
    def test_list_and_get_device(self, client):
        pairing = client.post("/v1/devices/pairing-codes").json()
        device = client.post("/v1/devices/pair", json={
            "code": pairing["code"], "ownerUserId": "user_1", "workspaceId": "ws_1",
        }).json()

        r = client.get("/v1/devices")
        assert r.status_code == 200
        assert len(r.json()) == 1

        r2 = client.get(f"/v1/devices/{device['id']}")
        assert r2.status_code == 200
        assert r2.json()["id"] == device["id"]

    def test_get_unknown_device(self, client):
        assert client.get("/v1/devices/unknown").status_code == 404


class TestDeviceEvents:
    def _paired_device(self, client):
        pairing = client.post("/v1/devices/pairing-codes").json()
        return client.post("/v1/devices/pair", json={
            "code": pairing["code"], "ownerUserId": "user_1", "workspaceId": "ws_1",
        }).json()

    def test_record_button_pressed_creates_session(self, client):
        device = self._paired_device(client)
        r = client.post(f"/v1/devices/{device['id']}/events", json={
            "type": "RECORD_BUTTON_PRESSED",
            "occurredAt": "2026-07-03T00:00:00+00:00",
            "payload": {},
        })
        assert r.status_code == 200
        event = r.json()
        assert REQUIRED_EVENT_KEYS <= event.keys()
        assert event["sessionId"]

        session = client.get(f"/v1/capture/sessions/{event['sessionId']}").json()
        assert session["source"] == "HARDWARE"
        assert device["id"] in session["deviceIds"]

    def test_event_unknown_device(self, client):
        r = client.post("/v1/devices/unknown/events", json={
            "type": "RECORD_BUTTON_PRESSED", "occurredAt": "2026-07-03T00:00:00+00:00", "payload": {},
        })
        assert r.status_code == 404


class TestDeviceTools:
    def _paired_device(self, client):
        pairing = client.post("/v1/devices/pairing-codes").json()
        return client.post("/v1/devices/pair", json={
            "code": pairing["code"], "ownerUserId": "user_1", "workspaceId": "ws_1",
        }).json()

    def test_invoke_tool(self, client):
        device = self._paired_device(client)
        r = client.post(f"/v1/devices/{device['id']}/tools", json={
            "tool": "DEVICE_VIBRATE",
            "args": {"pattern": "short"},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ACKNOWLEDGED"
        assert REQUIRED_TOOL_KEYS <= body.keys()

    def test_tool_unknown_device(self, client):
        r = client.post("/v1/devices/unknown/tools", json={"tool": "DEVICE_VIBRATE", "args": {}})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7. Killer workflow (CLAUDE_BUILD_INSTRUCTIONS §12) — the deterministic
#    multilingual mock meeting proving capture -> transcript -> memory nodes
#    -> search -> agent-queryable, end to end.
# ---------------------------------------------------------------------------

class _TestClientMcpAdapter:
    """Wraps a sync `TestClient` with the async method surface
    `voxagent_mcp.voxagent_server.VoxAgentApiClient` exposes, so the MCP pure
    tool functions (`search_memory`, `get_session`, `get_action_items`) can be
    exercised directly against the in-memory engine without real HTTP.
    """

    def __init__(self, client):
        self._client = client

    async def search_memory(self, query: str, limit: int = 10) -> dict:
        r = self._client.get("/v1/memory/search", params={"q": query, "limit": limit})
        r.raise_for_status()
        return r.json()

    async def get_session(self, session_id: str) -> dict:
        r = self._client.get(f"/v1/capture/sessions/{session_id}")
        r.raise_for_status()
        session = r.json()
        r2 = self._client.get(f"/v1/capture/sessions/{session_id}/transcript-segments")
        r2.raise_for_status()
        return {"session": session, "segments": r2.json()}

    async def get_action_items(self, status: str | None = None) -> list:
        params = {"status": status} if status else {}
        r = self._client.get("/v1/memory/action-items", params=params)
        r.raise_for_status()
        return r.json()


class TestKillerWorkflow:
    def test_seed_workflow_end_to_end(self, client):
        result = seed_workflow(client)

        assert result["sessionId"].startswith("cap_")
        assert len(result["segmentIds"]) == len(MOCK_TRANSCRIPT_SEGMENTS)
        assert len(result["segmentIds"]) >= 6
        assert len(result["nodeIds"]) == len(MOCK_MEMORY_NODES)

        # 1 session, GET-able, READY with detectedLanguages.
        session = client.get(f"/v1/capture/sessions/{result['sessionId']}").json()
        assert session["status"] == "READY"
        assert session["detectedLanguages"] == ["zh", "ja", "en"]
        assert session["title"] == MOCK_MEETING_SESSION["title"]

        # >= 6 segments listed.
        segments = client.get(f"/v1/capture/sessions/{result['sessionId']}/transcript-segments").json()
        assert len(segments) >= 6
        languages = {seg["language"] for seg in segments}
        assert {"zh", "ja", "en"} <= languages

        # Search coverage required by §12.
        for query in ("Japan launch", "follow up", "正式会议纪要"):
            r = client.get("/v1/memory/search", params={"q": query})
            assert r.status_code == 200
            results = r.json()["results"]
            assert len(results) >= 1, f'expected >=1 result for "{query}"'

        # Exactly 3 action items, each OPEN with an owner in metadata.
        action_items = client.get("/v1/memory/action-items").json()
        assert len(action_items) == 3
        for item in action_items:
            assert item["metadata"]["status"] == "OPEN"
            assert item["metadata"].get("owner")

        # Recent contains the SUMMARY/DECISION nodes.
        recent = client.get("/v1/memory/recent", params={"limit": 50}).json()
        recent_ids = {n["id"] for n in recent}
        assert set(result["nodeIds"]) <= recent_ids
        recent_by_id = {n["id"]: n for n in recent}
        summary_count = sum(1 for nid in result["nodeIds"] if recent_by_id[nid]["type"] == "SUMMARY")
        decision_count = sum(1 for nid in result["nodeIds"] if recent_by_id[nid]["type"] == "DECISION")
        assert summary_count == 3
        assert decision_count == 1

        # Token balance responds with an int balance.
        balance = client.get("/v1/tokens/balance").json()
        assert isinstance(balance["balance"], int)

    async def test_mcp_compatible_query_proof(self, client):
        """Same seed, but proven queryable through the MCP pure functions
        (voxagent_mcp/voxagent_server.py) via a TestClient-backed adapter —
        no real HTTP, no MCP SDK required.
        """
        result = seed_workflow(client)
        mcp_client = _TestClientMcpAdapter(client)

        search_result = await search_memory(mcp_client, "Japan launch", limit=10)
        titles = {item["title"] for item in search_result["results"]}
        assert any("Launch in Japan in July" == t for t in titles) or any(
            "Japan launch" in (t or "") for t in titles
        )
        types = {item["type"] for item in search_result["results"]}
        assert "DECISION" in types

        summary_search = await search_memory(mcp_client, "正式会议纪要", limit=10)
        assert summary_search["count"] >= 1
        assert any(item["type"] == "SUMMARY" for item in summary_search["results"])

        session_view = await get_session(mcp_client, result["sessionId"])
        assert session_view["id"] == result["sessionId"]
        assert session_view["segmentCount"] >= 6
        # Bounded: no full transcript, only a small preview list.
        assert "segments" not in session_view
        assert len(session_view["segmentPreviews"]) <= 3

        action_items = await get_action_items(mcp_client)
        assert action_items["count"] == 3


# ---------------------------------------------------------------------------
# 8. Process + summary endpoints (mock ASR adapter -> deterministic summary)
# ---------------------------------------------------------------------------

class TestProcessAndSummary:
    def _create_session(self, client):
        return client.post("/v1/capture/sessions", json={
            "workspaceId": "ws_test", "ownerUserId": "user_test",
            "source": "MOBILE", "mode": "MEETING",
            "languageHints": ["zh", "ja", "en"], "privacyLevel": "PERSONAL",
            "consentState": "GRANTED", "deviceIds": [],
            "localOnlyUntilSync": False,
        }).json()

    def test_process_transcribes_and_summarizes(self, client):
        session = self._create_session(client)
        r = client.post(f"/v1/capture/sessions/{session['id']}/process", json={})
        assert r.status_code == 200
        processed = r.json()
        assert processed["status"] == "READY"
        assert set(processed["detectedLanguages"]) == {"zh", "ja", "en"}

        segments = client.get(f"/v1/capture/sessions/{session['id']}/transcript-segments").json()
        assert len(segments) >= 6
        assert all(seg["sessionId"] == session["id"] for seg in segments)

        summary = client.get(f"/v1/capture/sessions/{session['id']}/summary").json()
        assert summary["sessionId"] == session["id"]
        assert summary["status"] == "READY"
        assert len(summary["summaryNodes"]) == 1
        node = summary["summaryNodes"][0]
        assert node["type"] == "SUMMARY"
        assert node["sourceSessionId"] == session["id"]
        assert node["body"]  # non-empty deterministic summary

    def test_process_is_idempotent_for_segments(self, client):
        session = self._create_session(client)
        client.post(f"/v1/capture/sessions/{session['id']}/process", json={})
        first = client.get(f"/v1/capture/sessions/{session['id']}/transcript-segments").json()
        client.post(f"/v1/capture/sessions/{session['id']}/process", json={})
        second = client.get(f"/v1/capture/sessions/{session['id']}/transcript-segments").json()
        assert len(second) == len(first)
        summary = client.get(f"/v1/capture/sessions/{session['id']}/summary").json()
        assert len(summary["summaryNodes"]) == 1  # no duplicate summaries either

    def test_process_respects_existing_segments(self, client):
        session = self._create_session(client)
        client.post(f"/v1/capture/sessions/{session['id']}/transcript-segments", json={
            "speakerId": "spk_1", "startMs": 0, "endMs": 1000,
            "textRaw": "manual", "textNormalized": "Manual.",
            "confidence": 0.9, "model": "manual", "language": "en",
        })
        client.post(f"/v1/capture/sessions/{session['id']}/process", json={})
        segments = client.get(f"/v1/capture/sessions/{session['id']}/transcript-segments").json()
        assert len(segments) == 1  # adapter did not run over existing transcript

    def test_process_unknown_session_404(self, client):
        assert client.post("/v1/capture/sessions/cap_missing/process", json={}).status_code == 404

    def test_summary_unknown_session_404(self, client):
        assert client.get("/v1/capture/sessions/cap_missing/summary").status_code == 404

    def test_summary_before_process_is_empty(self, client):
        session = self._create_session(client)
        summary = client.get(f"/v1/capture/sessions/{session['id']}/summary").json()
        assert summary["summaryNodes"] == []
        assert summary["actionItems"] == []
        assert summary["decisions"] == []


# ---------------------------------------------------------------------------
# 9. Audio path store (both backends) — dedicated per-repo store, not on the
#    session dict, so the wire contract stays pure.
# ---------------------------------------------------------------------------

class TestAudioPathStore:
    def _session(self, client):
        return client.post("/v1/capture/sessions", json={
            "workspaceId": "ws_test", "ownerUserId": "user_test",
            "source": "MOBILE", "mode": "MEETING", "languageHints": [],
            "privacyLevel": "PERSONAL", "consentState": "GRANTED",
            "deviceIds": [], "localOnlyUntilSync": False,
        }).json()

    def test_set_and_get_audio_path(self, client):
        s = self._session(client)
        repo = _context(client).voice_memory_repo
        assert repo.get_audio_path(s["id"]) is None
        repo.set_audio_path(s["id"], "/tmp/a.wav")
        assert repo.get_audio_path(s["id"]) == "/tmp/a.wav"

    def test_set_audio_path_unknown_session_raises(self, client):
        import pytest
        with pytest.raises(KeyError):
            _context(client).voice_memory_repo.set_audio_path(
                "cap_missing", "/tmp/a.wav"
            )

    def test_get_audio_path_unknown_session_returns_none(self, client):
        assert _context(client).voice_memory_repo.get_audio_path("cap_missing") is None


# ---------------------------------------------------------------------------
# 10. Capture audio upload — POST .../audio saves the file and records its
#     path; `process` falls back to it when the body omits `audioPath`.
# ---------------------------------------------------------------------------

def _tiny_wav_bytes(seconds=0.2, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class TestAudioUpload:
    def _session(self, client):
        return client.post("/v1/capture/sessions", json={
            "workspaceId": "ws_test", "ownerUserId": "user_test",
            "source": "MOBILE", "mode": "MEETING", "languageHints": [],
            "privacyLevel": "PERSONAL", "consentState": "GRANTED",
            "deviceIds": [], "localOnlyUntilSync": False,
        }).json()

    def test_upload_saves_file_and_records_path(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("VOXAGENT_AUDIO_DIR", str(tmp_path))
        s = self._session(client)
        r = client.post(
            f"/v1/capture/sessions/{s['id']}/audio",
            files={"file": ("clip.wav", _tiny_wav_bytes(), "audio/wav")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["sessionId"] == s["id"]
        assert body["bytes"] > 0
        from pathlib import Path
        assert Path(body["audioPath"]).exists()
        assert (
            _context(client).voice_memory_repo.get_audio_path(s["id"])
            == body["audioPath"]
        )

    def test_upload_unknown_session_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("VOXAGENT_AUDIO_DIR", str(tmp_path))
        r = client.post(
            "/v1/capture/sessions/cap_missing/audio",
            files={"file": ("clip.wav", _tiny_wav_bytes(), "audio/wav")},
        )
        assert r.status_code == 404

    def test_process_uses_uploaded_audio_when_body_omits_path(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("VOXAGENT_AUDIO_DIR", str(tmp_path))
        # Register a recording adapter that captures the audio_path it receives.
        import engine.asr as asr
        captured = {}

        class _RecordingAdapter:
            name = "record"

            def transcribe(self, session_id, audio_path, language_hints):
                captured["audio_path"] = audio_path
                return [{
                    "speakerId": "s", "startMs": 0, "endMs": 100,
                    "textRaw": "x", "textNormalized": "X", "confidence": 0.9,
                    "model": "record", "language": "en",
                }]

        monkeypatch.setitem(asr._ADAPTER_FACTORIES, "record", lambda: _RecordingAdapter())
        s = self._session(client)
        upload = client.post(
            f"/v1/capture/sessions/{s['id']}/audio",
            files={"file": ("clip.wav", _tiny_wav_bytes(), "audio/wav")},
        ).json()
        # process WITHOUT audioPath in the body — must fall back to the uploaded file
        client.post(f"/v1/capture/sessions/{s['id']}/process", json={"asrAdapter": "record"})
        assert captured["audio_path"] == upload["audioPath"]


class TestListSessions:
    def _mk(self, client, title):
        return client.post("/v1/capture/sessions", json={
            "workspaceId": "ws_test", "ownerUserId": "user_test",
            "source": "MOBILE", "mode": "MEETING", "title": title,
            "languageHints": [], "privacyLevel": "PERSONAL",
            "consentState": "GRANTED", "deviceIds": [], "localOnlyUntilSync": False,
        }).json()

    def test_list_sessions_empty(self, client):
        assert client.get("/v1/capture/sessions").json() == []

    def test_list_sessions_returns_created_sessions(self, client):
        a = self._mk(client, "A")
        b = self._mk(client, "B")
        listed = client.get("/v1/capture/sessions").json()
        assert {s["id"] for s in listed} == {a["id"], b["id"]}
        # newest first
        assert listed[0]["id"] == b["id"]
        # each item has the capture-session required keys
        for s in listed:
            for key in ("id", "workspaceId", "ownerUserId", "source", "mode",
                        "status", "startedAt", "createdAt"):
                assert key in s

    def test_list_sessions_limit(self, client):
        for i in range(3):
            self._mk(client, f"S{i}")
        listed = client.get("/v1/capture/sessions?limit=2").json()
        assert len(listed) == 2


class TestAgentQuery:
    def _node(self, client, title, body, ntype="DECISION"):
        return client.post("/v1/memory/nodes", json={
            "type": ntype, "title": title, "body": body, "tags": [],
            "confidence": 0.9, "workspaceId": "ws_test", "sourceSessionId": None,
        }).json()

    def test_query_returns_grounded_cited_answer(self, client):
        # node_matches (engine.voice_memory) does literal-substring matching,
        # so the query must appear verbatim (case-insensitive) in the node's
        # title/body/tags — mirror that here with "launch in japan".
        node = self._node(client, "Launch in Japan", "We will launch in July.")
        r = client.post("/v1/agents/query", json={"query": "launch in japan"})
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "launch in japan"
        assert body["answerer"] == "mock"
        assert node["id"] in body["contextNodeIds"]
        assert any(c["id"] == node["id"] for c in body["citations"])
        assert body["answer"]  # non-empty grounded answer

    def test_query_with_explicit_node_ids(self, client):
        a = self._node(client, "Alpha decision", "alpha body")
        self._node(client, "Beta decision", "beta body")
        r = client.post("/v1/agents/query", json={"query": "anything", "nodeIds": [a["id"]]})
        body = r.json()
        assert body["contextNodeIds"] == [a["id"]]

    def test_query_empty_query_400(self, client):
        assert client.post("/v1/agents/query", json={"query": "   "}).status_code == 400

    def test_query_unknown_answerer_400(self, client):
        r = client.post("/v1/agents/query", json={"query": "x", "answerer": "nope"})
        assert r.status_code == 400

    def test_query_unconfigured_llm_answerer_503(self, client, monkeypatch):
        # The `llm` answerer selected but VOXAGENT_LLM_* not set → 503, not 500.
        for var in ("VOXAGENT_LLM_BASE_URL", "VOXAGENT_LLM_API_KEY", "VOXAGENT_LLM_MODEL"):
            monkeypatch.delenv(var, raising=False)
        self._node(client, "A decision", "some body")  # ensure non-empty context
        r = client.post("/v1/agents/query", json={"query": "a decision", "answerer": "llm"})
        assert r.status_code == 503

    def test_query_no_matches_returns_empty_context(self, client):
        r = client.post("/v1/agents/query", json={"query": "zzz-nomatch"})
        body = r.json()
        assert body["contextNodeIds"] == []
        assert body["answer"]  # still a (no-memory) answer, not a crash

    def test_recent_context(self, client):
        node = self._node(client, "Recent decision", "body")
        r = client.get("/v1/agents/context/recent")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        assert any(n["id"] == node["id"] for n in body["nodes"])
        # bounded: nodes carry id/title/type/createdAt only, not full body
        for n in body["nodes"]:
            assert set(n.keys()) == {"id", "title", "type", "createdAt"}


# ---------------------------------------------------------------------------
# 6. Admin audit log (master brief §18 / §11.6)
# ---------------------------------------------------------------------------

class TestAuditRecorder:
    def test_record_and_list_newest_first(self):
        from engine.voice_audit import VoiceAuditLog
        log = VoiceAuditLog()
        a = log.record("capture.session.created", actor_id="u", workspace_id="ws", target_id="cap_1")
        b = log.record("memory.node.created", target_id="mem_1", metadata={"type": "DECISION"})
        assert a["id"].startswith("aud_")
        assert set(a.keys()) >= {"id", "action", "actorId", "workspaceId", "targetId", "metadata", "createdAt"}
        events = log.list_events()
        assert [e["action"] for e in events] == ["memory.node.created", "capture.session.created"]  # newest first
        assert events[0]["metadata"] == {"type": "DECISION"}

    def test_list_limit(self):
        from engine.voice_audit import VoiceAuditLog
        log = VoiceAuditLog()
        for i in range(5):
            log.record("x", target_id=str(i))
        assert len(log.list_events(limit=2)) == 2


class TestAuditEndpoint:
    def test_actions_are_audited(self, client):
        session = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        client.post("/v1/memory/nodes", json={
            "type": "DECISION", "title": "T", "body": "b", "tags": [],
            "confidence": 0.9, "workspaceId": "ws_1", "sourceSessionId": None,
        })
        client.post("/v1/agents/query", json={"query": "t"})
        events = client.get("/v1/admin/audit-events").json()
        actions = [e["action"] for e in events]
        assert "capture.session.created" in actions
        assert "memory.node.created" in actions
        assert "agent.query" in actions
        # newest-first: the agent query (last action) is at or near the top
        assert events[0]["action"] == "agent.query"
        # bounded event shape — no raw transcript / unbounded text
        for e in events:
            assert set(e.keys()) == {"id", "action", "actorId", "workspaceId", "targetId", "metadata", "createdAt"}

    def test_audit_events_limit(self, client):
        for _ in range(4):
            client.post("/v1/capture/sessions", json=SESSION_PAYLOAD)
        assert len(client.get("/v1/admin/audit-events?limit=2").json()) == 2

    def test_capture_session_audit_targets_the_session(self, client):
        session = client.post("/v1/capture/sessions", json=SESSION_PAYLOAD).json()
        events = client.get("/v1/admin/audit-events").json()
        created = [e for e in events if e["action"] == "capture.session.created"]
        assert created and created[0]["targetId"] == session["id"]
        assert created[0]["workspaceId"] == session["workspaceId"]


class TestModelPolicy:
    def test_default_effective_policy(self, client, monkeypatch):
        monkeypatch.delenv("VOXAGENT_ANSWERER", raising=False)
        monkeypatch.delenv("VOXAGENT_ASR_ADAPTER", raising=False)
        for var in ("VOXAGENT_LLM_BASE_URL", "VOXAGENT_LLM_API_KEY", "VOXAGENT_LLM_MODEL"):
            monkeypatch.delenv(var, raising=False)
        body = client.get("/v1/admin/model-policy").json()
        assert body["answerer"] == "mock"
        assert body["asrAdapter"] == "mock"
        assert body["answererOverride"] is None
        assert body["llmConfigured"] is False
        assert "mock" in body["availableAnswerers"]
        assert "sherpa" in body["availableAsrAdapters"]

    def test_patch_sets_override_and_affects_effective(self, client):
        r = client.patch("/v1/admin/model-policy", json={"answerer": "llm"})
        assert r.status_code == 200
        body = r.json()
        assert body["answererOverride"] == "llm"
        assert body["answerer"] == "llm"
        # persists on subsequent GET
        assert client.get("/v1/admin/model-policy").json()["answerer"] == "llm"

    def test_patch_clear_override(self, client):
        client.patch("/v1/admin/model-policy", json={"answerer": "llm"})
        client.patch("/v1/admin/model-policy", json={"answerer": None})
        assert client.get("/v1/admin/model-policy").json()["answererOverride"] is None

    def test_patch_unknown_answerer_400(self, client):
        assert client.patch("/v1/admin/model-policy", json={"answerer": "nope"}).status_code == 400

    def test_patch_unknown_asr_400(self, client):
        assert client.patch("/v1/admin/model-policy", json={"asrAdapter": "nope"}).status_code == 400

    def test_policy_answerer_used_when_body_omits_it(self, client):
        # override the default answerer to a bad name → agent_query resolves it
        # via the policy and returns 400 (proving the policy default is applied).
        client.patch("/v1/admin/model-policy", json={"answerer": "llm"})
        self._node(client, "Launch", "body")
        # llm answerer selected via policy but unconfigured → 503 (not mock 200)
        for var in ("VOXAGENT_LLM_BASE_URL", "VOXAGENT_LLM_API_KEY", "VOXAGENT_LLM_MODEL"):
            import os
            os.environ.pop(var, None)
        r = client.post("/v1/agents/query", json={"query": "launch"})
        assert r.status_code == 503  # policy's llm default took effect

    def _node(self, client, title, body):
        return client.post("/v1/memory/nodes", json={
            "type": "DECISION", "title": title, "body": body, "tags": [],
            "confidence": 0.9, "workspaceId": "ws_1", "sourceSessionId": None,
        }).json()


class TestRetentionPolicy:
    def test_default_effective_policy(self, client, monkeypatch):
        for var in (
            "VOXAGENT_AUDIO_RETENTION_DAYS",
            "VOXAGENT_TRANSCRIPT_RETENTION_DAYS",
            "VOXAGENT_MEMORY_RETENTION_DAYS",
            "VOXAGENT_AUTO_DELETE",
        ):
            monkeypatch.delenv(var, raising=False)
        body = client.get("/v1/admin/retention-policy").json()
        assert body["audioRetentionDays"] == 30
        assert body["transcriptRetentionDays"] == 90
        assert body["memoryRetentionDays"] == 365
        assert body["autoDeleteEnabled"] is False
        assert body["audioRetentionDaysOverride"] is None
        assert body["autoDeleteOverride"] is None

    def test_env_default_used_when_no_override(self, client, monkeypatch):
        monkeypatch.setenv("VOXAGENT_AUDIO_RETENTION_DAYS", "7")
        monkeypatch.setenv("VOXAGENT_AUTO_DELETE", "true")
        body = client.get("/v1/admin/retention-policy").json()
        assert body["audioRetentionDays"] == 7
        assert body["audioRetentionDaysOverride"] is None
        assert body["autoDeleteEnabled"] is True

    def test_patch_sets_override_and_affects_effective(self, client):
        r = client.patch("/v1/admin/retention-policy", json={"audioRetentionDays": 14})
        assert r.status_code == 200
        body = r.json()
        assert body["audioRetentionDays"] == 14
        assert body["audioRetentionDaysOverride"] == 14
        # persists on subsequent GET (real server-side storage)
        assert client.get("/v1/admin/retention-policy").json()["audioRetentionDays"] == 14

    def test_patch_auto_delete_toggle(self, client):
        r = client.patch("/v1/admin/retention-policy", json={"autoDeleteEnabled": True})
        assert r.status_code == 200
        assert r.json()["autoDeleteEnabled"] is True
        assert r.json()["autoDeleteOverride"] is True

    def test_patch_clear_override(self, client):
        client.patch("/v1/admin/retention-policy", json={"memoryRetentionDays": 30})
        client.patch("/v1/admin/retention-policy", json={"memoryRetentionDays": None})
        body = client.get("/v1/admin/retention-policy").json()
        assert body["memoryRetentionDaysOverride"] is None
        assert body["memoryRetentionDays"] == 365

    def test_patch_rejects_zero_days(self, client):
        assert client.patch("/v1/admin/retention-policy", json={"audioRetentionDays": 0}).status_code == 400

    def test_patch_rejects_non_int_days(self, client):
        assert client.patch("/v1/admin/retention-policy", json={"transcriptRetentionDays": "soon"}).status_code == 400

    def test_patch_rejects_bool_days(self, client):
        # bool is a subclass of int; must not be accepted as a day count.
        assert client.patch("/v1/admin/retention-policy", json={"audioRetentionDays": True}).status_code == 400

    def test_patch_rejects_non_bool_auto_delete(self, client):
        assert client.patch("/v1/admin/retention-policy", json={"autoDeleteEnabled": "yes"}).status_code == 400
