"""Deterministic mock ASR / sample-meeting data (CLAUDE_BUILD_INSTRUCTIONS §11).

This module is the "killer workflow" seed for the VOXAGENT mobile companion:
a fixed, multilingual (Chinese / Japanese / English) mock meeting that proves
the full loop end to end — capture session -> transcript segments -> memory
nodes -> search -> agent-queryable (CLAUDE_BUILD_INSTRUCTIONS §12).

Everything here is plain data plus one small driver function
(`seed_workflow`) so it can be exercised identically from:

  * pytest via `fastapi.testclient.TestClient` (see
    tests/test_voice_memory_contract_flow.py::TestKillerWorkflow), and
  * a real running engine via `httpx.Client` (see scripts/seed_mobile_mvp.py).

`seed_workflow` only requires `.post(path, json=...)` / `.get(path, params=...)`
methods returning an object with `.status_code` and `.json()` — both
`TestClient` and `httpx.Client` satisfy that surface.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 1. The capture session
# ---------------------------------------------------------------------------

MOCK_MEETING_SESSION: dict[str, Any] = {
    "workspaceId": "ws_demo",
    "ownerUserId": "user_demo",
    "source": "MOBILE",
    "mode": "MEETING",
    "languageHints": ["zh", "ja", "en"],
    "privacyLevel": "TEAM",
    "consentState": "GRANTED",
    "deviceIds": [],
    "localOnlyUntilSync": False,
    "title": "Japan launch sync",
}

# ---------------------------------------------------------------------------
# 2. Transcript segments — mixed zh/ja/en, two speakers, increasing timestamps
# ---------------------------------------------------------------------------

MOCK_TRANSCRIPT_SEGMENTS: list[dict[str, Any]] = [
    {
        "speakerId": "spk_pm",
        "speakerLabel": "Alex (PM)",
        "startMs": 0,
        "endMs": 4200,
        "language": "zh",
        "textRaw": "我们决定七月启动日本发布",
        "textNormalized": "我们决定七月启动日本发布。",
        "textTranslated": "We have decided to launch in Japan in July.",
        "translationTargetLanguage": "en",
        "confidence": 0.95,
        "model": "mock-asr",
    },
    {
        "speakerId": "spk_jp_lead",
        "speakerLabel": "Yuki (JP Lead)",
        "startMs": 4200,
        "endMs": 9100,
        "language": "ja",
        "textRaw": "7月に日本ローンチを開始します",
        "textNormalized": "7月に日本ローンチを開始します。",
        "textTranslated": "We will begin the Japan launch in July.",
        "translationTargetLanguage": "en",
        "confidence": 0.93,
        "model": "mock-asr",
    },
    {
        "speakerId": "spk_eng_lead",
        "speakerLabel": "Sam (Eng Lead)",
        "startMs": 9100,
        "endMs": 14800,
        "language": "en",
        "textRaw": "we will follow up with the tokyo partner next week",
        "textNormalized": "We will follow up with the Tokyo partner next week.",
        "textTranslated": None,
        "translationTargetLanguage": None,
        "confidence": 0.97,
        "model": "mock-asr",
    },
    {
        "speakerId": "spk_pm",
        "speakerLabel": "Alex (PM)",
        "startMs": 14800,
        "endMs": 20100,
        "language": "zh",
        "textRaw": "市场团队需要在七月底前完成本地化",
        "textNormalized": "市场团队需要在七月底前完成本地化。",
        "textTranslated": "The marketing team needs to finish localization by end of July.",
        "translationTargetLanguage": "en",
        "confidence": 0.91,
        "model": "mock-asr",
    },
    {
        "speakerId": "spk_jp_lead",
        "speakerLabel": "Yuki (JP Lead)",
        "startMs": 20100,
        "endMs": 25600,
        "language": "ja",
        "textRaw": "東京のパートナーとの契約を来週までに確認します",
        "textNormalized": "東京のパートナーとの契約を来週までに確認します。",
        "textTranslated": "I will confirm the contract with the Tokyo partner by next week.",
        "translationTargetLanguage": "en",
        "confidence": 0.9,
        "model": "mock-asr",
    },
    {
        "speakerId": "spk_eng_lead",
        "speakerLabel": "Sam (Eng Lead)",
        "startMs": 25600,
        "endMs": 31200,
        "language": "en",
        "textRaw": "i will prepare the japan launch checklist and share it with the team",
        "textNormalized": "I will prepare the Japan launch checklist and share it with the team.",
        "textTranslated": None,
        "translationTargetLanguage": None,
        "confidence": 0.96,
        "model": "mock-asr",
    },
    {
        "speakerId": "spk_pm",
        "speakerLabel": "Alex (PM)",
        "startMs": 31200,
        "endMs": 36000,
        "language": "zh",
        "textRaw": "好的 我们下周同一时间再同步进展",
        "textNormalized": "好的，我们下周同一时间再同步进展。",
        "textTranslated": "Great, let's sync again next week at the same time.",
        "translationTargetLanguage": "en",
        "confidence": 0.94,
        "model": "mock-asr",
    },
]

# ---------------------------------------------------------------------------
# 3. Memory nodes — 3 SUMMARY (zh/ja/en minutes), 1 DECISION, 3 ACTION_ITEM
# ---------------------------------------------------------------------------

_SUMMARY_BODY_ZH = (
    "正式会议纪要\n"
    "出席人员：Alex（产品经理）、Yuki（日本负责人）、Sam（工程负责人）\n"
    "议题：日本市场发布计划\n"
    "决定：七月启动日本发布。\n"
    "行动项：\n"
    "1. Sam 下周跟进东京合作伙伴（follow up with Tokyo partner）。\n"
    "2. Yuki 下周前确认与东京合作伙伴的合同。\n"
    "3. 市场团队在七月底前完成本地化。"
)

_SUMMARY_BODY_JA = (
    "議事録\n"
    "出席者：Alex（プロダクトマネージャー）、Yuki（日本担当リーダー）、Sam（エンジニアリングリーダー）\n"
    "議題：日本ローンチ計画\n"
    "決定事項：7月に日本ローンチを開始する。\n"
    "アクションアイテム：\n"
    "1. Sam が来週、東京のパートナーとフォローアップする（follow up with Tokyo partner）。\n"
    "2. Yuki が来週までに東京のパートナーとの契約を確認する。\n"
    "3. マーケティングチームが7月末までにローカライズを完了する。"
)

_SUMMARY_BODY_EN = (
    "Formal meeting minutes\n"
    "Attendees: Alex (PM), Yuki (JP Lead), Sam (Eng Lead)\n"
    "Topics: Japan market launch plan.\n"
    "Decisions: Launch in Japan in July.\n"
    "Action items:\n"
    "1. Sam will follow up with the Tokyo partner next week.\n"
    "2. Yuki will confirm the contract with the Tokyo partner by next week.\n"
    "3. Marketing team will finish localization by end of July."
)

MOCK_MEMORY_NODES: list[dict[str, Any]] = [
    {
        "type": "SUMMARY",
        "title": "正式会议纪要 — Japan launch sync",
        "body": _SUMMARY_BODY_ZH,
        "language": "zh",
        "tags": ["meeting-minutes", "japan-launch", "zh"],
        "confidence": 0.92,
        "workspaceId": "ws_demo",
    },
    {
        "type": "SUMMARY",
        "title": "議事録 — Japan launch sync",
        "body": _SUMMARY_BODY_JA,
        "language": "ja",
        "tags": ["meeting-minutes", "japan-launch", "ja"],
        "confidence": 0.92,
        "workspaceId": "ws_demo",
    },
    {
        "type": "SUMMARY",
        "title": "Formal meeting minutes — Japan launch sync",
        "body": _SUMMARY_BODY_EN,
        "language": "en",
        "tags": ["meeting-minutes", "japan-launch", "en"],
        "confidence": 0.92,
        "workspaceId": "ws_demo",
    },
    {
        "type": "DECISION",
        "title": "Launch in Japan in July",
        "body": "The team decided on the Japan launch: we will launch in Japan in July.",
        "language": "en",
        "tags": ["decision", "japan-launch"],
        "confidence": 0.95,
        "workspaceId": "ws_demo",
    },
    {
        "type": "ACTION_ITEM",
        "title": "Follow up with Tokyo partner",
        "body": "Sam will follow up with Tokyo partner next week to confirm launch logistics.",
        "language": "en",
        "tags": ["action-item", "japan-launch"],
        "confidence": 0.9,
        "workspaceId": "ws_demo",
        "metadata": {"status": "OPEN", "owner": "Sam (Eng Lead)"},
    },
    {
        "type": "ACTION_ITEM",
        "title": "Confirm Tokyo partner contract",
        "body": "Yuki will confirm the contract with the Tokyo partner by next week.",
        "language": "en",
        "tags": ["action-item", "japan-launch"],
        "confidence": 0.88,
        "workspaceId": "ws_demo",
        "metadata": {"status": "OPEN", "owner": "Yuki (JP Lead)"},
    },
    {
        "type": "ACTION_ITEM",
        "title": "Finish Japan localization",
        "body": "Marketing team will finish localization for the Japan launch by end of July.",
        "language": "en",
        "tags": ["action-item", "japan-launch"],
        "confidence": 0.85,
        "workspaceId": "ws_demo",
        "metadata": {"status": "OPEN", "owner": "Marketing team"},
    },
]


def _post(client: Any, path: str, json: dict) -> dict:
    r = client.post(path, json=json)
    if r.status_code != 200:
        raise RuntimeError(f"POST {path} failed ({r.status_code}): {r.text if hasattr(r, 'text') else r.json()}")
    return r.json()


def _patch(client: Any, path: str, json: dict) -> dict:
    r = client.patch(path, json=json)
    if r.status_code != 200:
        raise RuntimeError(f"PATCH {path} failed ({r.status_code}): {r.text if hasattr(r, 'text') else r.json()}")
    return r.json()


def seed_workflow(client: Any, base_path: str = "") -> dict:
    """Drive the killer-workflow seed against any HTTP-ish client.

    Works with both `fastapi.testclient.TestClient` and `httpx.Client` — both
    expose `.post(path, json=...)` / `.patch(path, json=...)` returning a
    response object with `.status_code` and `.json()`.

    Steps:
      1. Create the capture session (`MOCK_MEETING_SESSION`).
      2. PATCH it to READY with detectedLanguages ["zh", "ja", "en"].
      3. POST every entry of `MOCK_TRANSCRIPT_SEGMENTS` to the session.
      4. POST every entry of `MOCK_MEMORY_NODES`, with `sourceSessionId` set
         to the created session's id.

    Returns `{"sessionId": ..., "segmentIds": [...], "nodeIds": [...]}`.
    Raises `RuntimeError` (with the response body) on any non-200 response.
    """
    session = _post(client, f"{base_path}/v1/capture/sessions", MOCK_MEETING_SESSION)
    session_id = session["id"]

    _patch(
        client,
        f"{base_path}/v1/capture/sessions/{session_id}",
        {"status": "READY", "detectedLanguages": ["zh", "ja", "en"]},
    )

    segment_ids = []
    for segment_payload in MOCK_TRANSCRIPT_SEGMENTS:
        segment = _post(
            client,
            f"{base_path}/v1/capture/sessions/{session_id}/transcript-segments",
            segment_payload,
        )
        segment_ids.append(segment["id"])

    node_ids = []
    for node_payload in MOCK_MEMORY_NODES:
        payload = dict(node_payload)
        payload["sourceSessionId"] = session_id
        node = _post(client, f"{base_path}/v1/memory/nodes", payload)
        node_ids.append(node["id"])

    return {"sessionId": session_id, "segmentIds": segment_ids, "nodeIds": node_ids}
