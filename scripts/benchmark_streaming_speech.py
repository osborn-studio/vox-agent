#!/usr/bin/env python3
"""Benchmark speech event v1 ordering, replay, and broker latency.

This deliberately bypasses ASR. It proves that a declared multilingual event
corpus crosses the protocol authority without sequence or finalized-segment
loss; model quality and audio latency belong to the P3.2 ASR benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.streaming_speech import SpeechStreamBroker  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def run_benchmark(corpus_path: Path, segment_count: int) -> dict:
    raw_corpus = corpus_path.read_bytes()
    corpus = json.loads(raw_corpus)
    samples = corpus.get("samples", [])
    if not samples:
        raise ValueError("corpus must declare at least one sample")
    if segment_count < len(samples):
        raise ValueError(f"segments must be at least the corpus size ({len(samples)})")

    broker = SpeechStreamBroker(max_events_per_stream=(segment_count * 2) + 2)
    languages = sorted({sample["language"] for sample in samples})
    created = broker.create_stream(
        session_id="benchmark-streaming-speech-v1",
        language_hints=languages,
    )
    latencies_ms: list[float] = []
    expected_languages: Counter[str] = Counter()

    for index in range(segment_count):
        sample = samples[index % len(samples)]
        expected_languages[sample["language"]] += 1
        payload = {
            "segment_id": f"segment-{index:06d}",
            "revision": 1,
            "text": sample["text"],
            "language": {"code": sample["language"], "confidence": 0.99},
            "speaker": sample["speaker"],
            "audio_start_ms": index * 1_000,
            "audio_end_ms": (index * 1_000) + 900,
            "confidence": 0.97,
            "monotonic_ms": index * 1_000,
        }
        for event_type, prefix in (
            ("transcript.partial", "partial"),
            ("transcript.final", "final"),
        ):
            started_ns = time.perf_counter_ns()
            broker.publish_transcript(
                created["streamId"],
                created["resumeToken"],
                event_type=event_type,
                idempotency_key=f"{prefix}-{index}",
                **payload,
            )
            latencies_ms.append((time.perf_counter_ns() - started_ns) / 1_000_000)

    replay_started_ns = time.perf_counter_ns()
    replay = broker.replay(
        created["streamId"], created["resumeToken"], after_sequence=0
    )
    replay_latency_ms = (time.perf_counter_ns() - replay_started_ns) / 1_000_000
    events = replay["events"]
    sequences = [event["sequence"] for event in events]
    expected_sequences = list(range(1, (segment_count * 2) + 2))
    finals = [event for event in events if event["type"] == "transcript.final"]
    observed_languages = Counter(
        event["payload"]["language"]["code"] for event in finals
    )
    final_segment_ids = [event["payload"]["segmentId"] for event in finals]
    missing_sequences = sorted(set(expected_sequences) - set(sequences))
    duplicate_sequences = len(sequences) - len(set(sequences))
    duplicate_final_segments = len(final_segment_ids) - len(set(final_segment_ids))
    final_segment_loss = segment_count - len(set(final_segment_ids))
    passed = (
        sequences == expected_sequences
        and observed_languages == expected_languages
        and duplicate_sequences == 0
        and duplicate_final_segments == 0
        and final_segment_loss == 0
    )

    return {
        "schema": "voxagent.streaming-speech.benchmark",
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "event-contract-transport-only",
        "corpus": {
            "path": str(corpus_path.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(raw_corpus).hexdigest(),
            "declaredSamples": len(samples),
            "languages": languages,
        },
        "workload": {
            "segments": segment_count,
            "events": len(events),
            "expectedEvents": (segment_count * 2) + 1,
            "finalSegmentsByLanguage": dict(sorted(observed_languages.items())),
        },
        "latencyMs": {
            "eventPublishP50": round(statistics.median(latencies_ms), 6),
            "eventPublishP95": round(_percentile(latencies_ms, 0.95), 6),
            "eventPublishMax": round(max(latencies_ms), 6),
            "fullReplay": round(replay_latency_ms, 6),
        },
        "integrity": {
            "missingSequences": missing_sequences,
            "duplicateSequences": duplicate_sequences,
            "finalSegmentLoss": final_segment_loss,
            "duplicateFinalSegments": duplicate_final_segments,
        },
        "result": "PASS" if passed else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "tests/fixtures/streaming_speech_corpus.json",
    )
    parser.add_argument("--segments", type=int, default=3_600)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(args.corpus.resolve(), args.segments)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
