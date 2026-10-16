"""ASR adapter abstraction (master brief §13).

Business logic never binds to a concrete ASR engine. Adapters implement the
``AsrAdapter`` protocol; ``get_asr_adapter`` resolves one by name (or the
``VOXAGENT_ASR_ADAPTER`` env var, default ``mock``). Phase 2 adds a
sherpa-onnx adapter behind this same seam; the deterministic mock unblocks
UI/API/workflow tests without model packaging.

Adapters return transcript-segment PAYLOADS (camelCase, matching
``transcriptSegmentSchema`` minus ``id``/``sessionId``, which the repository
and endpoint assign).
"""

from __future__ import annotations

import os
from typing import Protocol

from .mock_asr import MOCK_TRANSCRIPT_SEGMENTS


class AsrAdapter(Protocol):
    name: str

    def transcribe(
        self,
        session_id: str,
        audio_path: str | None,
        language_hints: list[str],
    ) -> list[dict]:
        """Return transcript-segment payload dicts for the given audio."""
        ...


class MockAsrAdapter:
    """Deterministic mock: always yields the sample multilingual meeting."""

    name = "mock"

    def transcribe(
        self,
        session_id: str,
        audio_path: str | None,
        language_hints: list[str],
    ) -> list[dict]:
        return [dict(segment) for segment in MOCK_TRANSCRIPT_SEGMENTS]


def _make_mock() -> "AsrAdapter":
    return MockAsrAdapter()


def _make_sherpa() -> "AsrAdapter":
    from .asr_sherpa import SherpaOnnxAsrAdapter

    return SherpaOnnxAsrAdapter()


_ADAPTER_FACTORIES = {
    "mock": _make_mock,
    "sherpa": _make_sherpa,
}


def get_asr_adapter(name: str | None = None) -> AsrAdapter:
    resolved = name or os.environ.get("VOXAGENT_ASR_ADAPTER", "mock")
    try:
        return _ADAPTER_FACTORIES[resolved]()
    except KeyError:
        raise ValueError(
            f"unknown ASR adapter: {resolved!r} (available: {sorted(_ADAPTER_FACTORIES)})"
        ) from None
