"""PCM-to-ASR bridge tests for authenticated streaming speech."""

from __future__ import annotations

import json
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from engine.app_context import create_app_context
from engine.server import create_app
from engine.speech_asr import SpeechAsrProcessor


class _RecordingAdapter:
    name = "recording"

    def __init__(self, texts: list[str] | None = None) -> None:
        self.texts = texts or ["hello", "hello world"]
        self.calls: list[dict] = []

    def transcribe(
        self,
        session_id: str,
        audio_path: str | None,
        language_hints: list[str],
    ) -> list[dict]:
        assert audio_path is not None
        path = Path(audio_path)
        with wave.open(str(path), "rb") as audio:
            self.calls.append(
                {
                    "session_id": session_id,
                    "language_hints": list(language_hints),
                    "channels": audio.getnchannels(),
                    "sample_width": audio.getsampwidth(),
                    "sample_rate": audio.getframerate(),
                    "frames": audio.getnframes(),
                    "path": path,
                    "mode": path.stat().st_mode & 0o777,
                }
            )
        text = self.texts[min(len(self.calls) - 1, len(self.texts) - 1)]
        return [
            {
                "speakerId": "speaker_1",
                "speakerLabel": None,
                "startMs": 0,
                "endMs": self.calls[-1]["frames"] * 1_000 // 16_000,
                "language": "en",
                "textRaw": text,
                "textNormalized": text,
                "confidence": 0.91,
                "model": "test-asr",
            }
        ]


class _FailingAdapter:
    name = "failing"

    def transcribe(
        self,
        session_id: str,
        audio_path: str | None,
        language_hints: list[str],
    ) -> list[dict]:
        raise RuntimeError("private provider diagnostic must not cross the API")


def _chunk_headers(token: str, *, index: int = 0) -> dict[str, str]:
    return {
        "Content-Type": "application/octet-stream",
        "X-VoxAgent-Stream-Token": token,
        "X-VoxAgent-Audio-Chunk-Index": str(index),
        "X-VoxAgent-Audio-Sample-Rate": "16000",
        "X-VoxAgent-Audio-Channels": "1",
        "X-VoxAgent-Audio-Encoding": "pcm_s16le",
    }


def _context_with_processor(tmp_path, adapter):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        bootstrap_admin_token=None,
    )
    context.speech_asr_processor = SpeechAsrProcessor(
        context.speech_stream_broker,
        adapter=adapter,
    )
    return context


def test_pcm_snapshot_publishes_stable_partial_then_final_and_erases_temp_wav(
    tmp_path,
) -> None:
    adapter = _RecordingAdapter()
    context = _context_with_processor(tmp_path, adapter)
    private_pcm = b"\x01\x00" * 16_000

    with TestClient(create_app(context)) as client:
        created = client.post(
            "/v1/speech/streams",
            json={"sessionId": "conversation-1", "languageHints": ["en"]},
        ).json()
        path = f"/v1/speech/streams/{created['streamId']}"
        headers = _chunk_headers(created["resumeToken"])

        accepted = client.post(
            f"{path}/audio/chunks",
            headers=headers,
            content=private_pcm,
        )
        assert accepted.status_code == 200

        completed = client.post(
            f"{path}/complete",
            headers={"X-VoxAgent-Stream-Token": created["resumeToken"]},
            json={"reason": "input_finished", "idempotencyKey": "complete-1"},
        )
        assert completed.status_code == 200
        assert completed.json()["type"] == "stream.completed"

        replay = client.get(
            f"{path}/events",
            headers={"X-VoxAgent-Stream-Token": created["resumeToken"]},
        ).json()

    transcripts = [
        event for event in replay["events"] if event["type"].startswith("transcript.")
    ]
    assert [(event["type"], event["payload"]["revision"]) for event in transcripts] == [
        ("transcript.partial", 1),
        ("transcript.final", 2),
    ]
    assert [event["payload"]["text"] for event in transcripts] == [
        "hello",
        "hello world",
    ]
    assert len({event["payload"]["segmentId"] for event in transcripts}) == 1
    assert all(call["session_id"] == "conversation-1" for call in adapter.calls)
    assert all(call["language_hints"] == ["en"] for call in adapter.calls)
    assert all(call["channels"] == 1 for call in adapter.calls)
    assert all(call["sample_width"] == 2 for call in adapter.calls)
    assert all(call["sample_rate"] == 16_000 for call in adapter.calls)
    assert all(call["mode"] == 0o600 for call in adapter.calls)
    assert all(not call["path"].exists() for call in adapter.calls)
    assert private_pcm.hex() not in json.dumps(replay)


def test_unchanged_partial_is_not_republished_and_final_reuses_revision(tmp_path) -> None:
    adapter = _RecordingAdapter(texts=["same words"])
    context = _context_with_processor(tmp_path, adapter)
    half_second = b"\x02\x00" * 8_000

    with TestClient(create_app(context)) as client:
        created = client.post(
            "/v1/speech/streams",
            json={"languageHints": ["en"]},
        ).json()
        path = f"/v1/speech/streams/{created['streamId']}"
        for index in range(4):
            response = client.post(
                f"{path}/audio/chunks",
                headers=_chunk_headers(created["resumeToken"], index=index),
                content=half_second,
            )
            assert response.status_code == 200
        completed = client.post(
            f"{path}/complete",
            headers={"X-VoxAgent-Stream-Token": created["resumeToken"]},
            json={"reason": "input_finished", "idempotencyKey": "complete-same"},
        )
        assert completed.status_code == 200
        replay = client.get(
            f"{path}/events",
            headers={"X-VoxAgent-Stream-Token": created["resumeToken"]},
        ).json()

    transcripts = [
        event for event in replay["events"] if event["type"].startswith("transcript.")
    ]
    assert [(event["type"], event["payload"]["revision"]) for event in transcripts] == [
        ("transcript.partial", 1),
        ("transcript.final", 1),
    ]
    assert len(adapter.calls) == 3


def test_provider_failure_terminates_stream_without_leaking_diagnostics(tmp_path) -> None:
    context = _context_with_processor(tmp_path, _FailingAdapter())
    private_pcm = b"\x03\x00" * 16_000

    with TestClient(create_app(context)) as client:
        created = client.post("/v1/speech/streams", json={}).json()
        path = f"/v1/speech/streams/{created['streamId']}"
        headers = _chunk_headers(created["resumeToken"])

        failed = client.post(
            f"{path}/audio/chunks",
            headers=headers,
            content=private_pcm,
        )
        assert failed.status_code == 502
        assert failed.json()["detail"] == {
            "code": "speech_asr_provider_error",
            "message": "speech recognition provider failed",
        }
        assert "private provider diagnostic" not in failed.text

        replay = client.get(
            f"{path}/events",
            headers={"X-VoxAgent-Stream-Token": created["resumeToken"]},
        ).json()
        assert replay["terminal"] is True
        assert replay["events"][-1]["type"] == "stream.error"
        assert replay["events"][-1]["payload"] == {
            "code": "speech_asr_provider_error",
            "message": "speech recognition provider failed",
            "retriable": False,
        }
        assert client.post(
            f"{path}/audio/chunks",
            headers={**headers, "X-VoxAgent-Audio-Chunk-Index": "1"},
            content=b"\x00\x00",
        ).status_code == 409

        audit = json.dumps(context.voice_audit_log.list_events())
        assert "private provider diagnostic" not in audit
        assert private_pcm.hex() not in audit
        assert "speech.stream.error" in audit


def test_final_provider_failure_returns_typed_error_and_disposes_audio(tmp_path) -> None:
    context = _context_with_processor(tmp_path, _FailingAdapter())
    half_second = b"\x04\x00" * 8_000

    with TestClient(create_app(context)) as client:
        created = client.post("/v1/speech/streams", json={}).json()
        path = f"/v1/speech/streams/{created['streamId']}"
        accepted = client.post(
            f"{path}/audio/chunks",
            headers=_chunk_headers(created["resumeToken"]),
            content=half_second,
        )
        assert accepted.status_code == 200

        failed = client.post(
            f"{path}/complete",
            headers={"X-VoxAgent-Stream-Token": created["resumeToken"]},
            json={"reason": "input_finished", "idempotencyKey": "complete-failure"},
        )
        assert failed.status_code == 502
        replay = client.get(
            f"{path}/events",
            headers={"X-VoxAgent-Stream-Token": created["resumeToken"]},
        ).json()
        assert [event["type"] for event in replay["events"]] == [
            "stream.started",
            "stream.error",
        ]


def test_default_streaming_processor_is_real_and_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("VOXAGENT_STREAMING_ASR_ADAPTER", raising=False)
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        bootstrap_admin_token=None,
    )
    assert context.speech_asr_processor.adapter_name == "sherpa"
