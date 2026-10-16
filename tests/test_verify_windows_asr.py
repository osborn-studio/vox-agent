from __future__ import annotations

import asyncio

from scripts.verify_windows_asr import build_evidence, run_audio_smoke


def test_evidence_contains_only_local_asr_and_secret_free_network_flags() -> None:
    qualification = {
        "contract_version": "2026-07-19.2",
        "platform": "windows",
        "network_environment": {
            "httpx_trust_env": True,
            "proxy_environment_configured": True,
            "no_proxy_configured": False,
        },
        "providers": [
            {"id": "sherpa-sensevoice", "kind": "stt", "transport": "local"},
            {"id": "openai-whisper", "kind": "stt", "transport": "cloud"},
            {"id": "ollama", "kind": "llm", "transport": "local"},
        ],
    }

    evidence = build_evidence(qualification, generated_at="2026-07-19T00:00:00+00:00")

    assert evidence["contract_version"] == "2026-07-19.windows-asr.1"
    assert [item["id"] for item in evidence["local_asr_providers"]] == [
        "sherpa-sensevoice"
    ]
    assert evidence["network_environment"]["proxy_environment_configured"] is True
    assert "proxy.example" not in str(evidence)


def test_audio_smoke_records_hash_and_size_but_never_transcript(tmp_path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"RIFF-test-audio")

    async def transcribe(_provider_id: str, audio: bytes) -> str:
        assert audio == b"RIFF-test-audio"
        return "private transcript text"

    result = asyncio.run(
        run_audio_smoke(
            "sherpa-sensevoice",
            audio_path,
            transcriber=transcribe,
            clock=lambda: 100.0,
        )
    )

    assert result["status"] == "passed"
    assert result["transcript_char_count"] == 23
    assert len(result["transcript_sha256"]) == 64
    assert "private transcript text" not in str(result)


def test_audio_smoke_returns_a_bounded_failure_without_provider_details(tmp_path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"RIFF-test-audio")

    async def transcribe(_provider_id: str, _audio: bytes) -> str:
        raise RuntimeError("credential-like provider detail should never escape")

    result = asyncio.run(
        run_audio_smoke(
            "sherpa-sensevoice",
            audio_path,
            transcriber=transcribe,
            clock=lambda: 100.0,
        )
    )

    assert result == {
        "provider_id": "sherpa-sensevoice",
        "status": "failed",
        "latency_ms": 0,
        "error_type": "RuntimeError",
    }
    assert "credential-like" not in str(result)
