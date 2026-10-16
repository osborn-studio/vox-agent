#!/usr/bin/env python3
"""Inspect Windows local ASR readiness and optionally run a real audio smoke.

The default inspection is side-effect free. ``--audio`` invokes exactly one
selected local adapter and records only transcript size and SHA-256, never the
transcript or audio content. Use the resulting JSON as operator evidence; a
non-Windows run is useful for development but does not satisfy the Windows gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.fastpath.stt import _get_provider  # noqa: E402
from runtime.models.config import VoxLogConfig  # noqa: E402
from runtime.provider_qualification import build_provider_qualification  # noqa: E402

EVIDENCE_CONTRACT_VERSION = "2026-07-19.windows-asr.1"
LOCAL_ASR_PROVIDERS = (
    "sherpa-sensevoice",
    "whispercpp-local",
    "qwen-local",
)


def build_evidence(
    qualification: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Reduce the shared matrix to a secret-free Windows ASR evidence record."""

    local_asr = [
        provider
        for provider in qualification["providers"]
        if provider["kind"] == "stt" and provider["transport"] == "local"
    ]
    return {
        "contract_version": EVIDENCE_CONTRACT_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "host_platform": qualification["platform"],
        "windows_host": qualification["platform"] == "windows",
        "qualification_contract_version": qualification["contract_version"],
        "network_environment": qualification["network_environment"],
        "local_asr_providers": local_asr,
        "audio_smoke": None,
    }


async def _transcribe(provider_id: str, audio: bytes) -> str:
    provider = _get_provider(provider_id, VoxLogConfig())
    return await provider.transcribe(audio)


async def run_audio_smoke(
    provider_id: str,
    audio_path: Path,
    *,
    transcriber: Callable[[str, bytes], Awaitable[str]] = _transcribe,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run one local adapter without retaining audio or transcript content."""

    started = clock()
    try:
        audio_size = audio_path.stat().st_size
        if audio_path.suffix.lower() != ".wav" or not 0 < audio_size <= 64 * 1024 * 1024:
            raise ValueError("audio must be between 1 byte and 64 MiB")
        audio = audio_path.read_bytes()
        transcript = (await transcriber(provider_id, audio)).strip()
        if not transcript or len(transcript) > 1_000_000:
            raise ValueError("provider returned no speech")
    except Exception as exc:  # Evidence is intentionally bounded at the adapter boundary.
        return {
            "provider_id": provider_id,
            "status": "failed",
            "latency_ms": max(0, round((clock() - started) * 1000)),
            "error_type": type(exc).__name__,
        }
    return {
        "provider_id": provider_id,
        "status": "passed",
        "latency_ms": max(0, round((clock() - started) * 1000)),
        "audio_bytes": len(audio),
        "transcript_char_count": len(transcript),
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=LOCAL_ASR_PROVIDERS, default="sherpa-sensevoice")
    parser.add_argument("--audio", type=Path, help="16-bit PCM WAV for one explicit local smoke")
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--require-windows", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    qualification = build_provider_qualification(VoxLogConfig())
    evidence = build_evidence(qualification)
    selected = next(
        item for item in evidence["local_asr_providers"] if item["id"] == args.provider
    )
    if args.audio:
        evidence["audio_smoke"] = asyncio.run(run_audio_smoke(args.provider, args.audio))

    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    if args.require_windows and not evidence["windows_host"]:
        return 3
    if args.require_ready and not selected["runtime_available"]:
        return 4
    if evidence["audio_smoke"] and evidence["audio_smoke"]["status"] != "passed":
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
