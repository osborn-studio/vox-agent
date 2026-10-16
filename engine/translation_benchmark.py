"""Deterministic quality and latency reporting for translation adapters."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .translation import TranslationPipeline, TranslatorAdapter

_CORPUS_SCHEMA = "voxagent.translation.benchmark-corpus"
_CONTENT_POLICY = "synthetic-non-user-content"


def main(
    argv: list[str] | None = None,
    *,
    adapter: TranslatorAdapter | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark realtime translation with synthetic, non-user content."
    )
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--engine-url",
        help="Exercise a running Vox Agent engine instead of calling the provider directly.",
    )
    args = parser.parse_args(argv)

    corpus = load_translation_benchmark_corpus(args.corpus)
    if args.engine_url:
        token = os.environ.get("VOXAGENT_AUTH_TOKEN")
        authorization = f"Bearer {token}" if token else None
        report = run_translation_http_benchmark(
            args.engine_url,
            corpus,
            authorization=authorization,
        )
    else:
        if adapter is None:
            from .translation_provider import get_translator

            adapter = get_translator()
        report = run_translation_benchmark(adapter, corpus)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        sys.stdout.write(serialized)
    return 0 if report["summary"]["failed"] == 0 else 2


def load_translation_benchmark_corpus(path: str | Path) -> list[dict]:
    """Load only the versioned, synthetic corpus format used for release evidence."""

    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("translation benchmark corpus is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("translation benchmark corpus must be an object")
    if document.get("schema") != _CORPUS_SCHEMA or document.get("schemaVersion") != 1:
        raise ValueError("translation benchmark corpus schema is unsupported")
    if document.get("contentPolicy") != _CONTENT_POLICY:
        raise ValueError(
            "translation benchmark corpus must use synthetic-non-user-content"
        )
    items = document.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("translation benchmark corpus items must not be empty")
    return items


def run_translation_benchmark(
    adapter: TranslatorAdapter,
    corpus: list[dict],
    *,
    clock_seconds: Callable[[], float] | None = None,
) -> dict:
    if not corpus:
        raise ValueError("translation benchmark corpus must not be empty")
    clock = clock_seconds or time.perf_counter
    pipeline = TranslationPipeline(adapter, max_cached_requests=max(1, len(corpus)))
    items = []

    for index, sample in enumerate(corpus):
        validated = _validated_sample(sample)
        segment_id = f"benchmark-{validated['id']}"
        started = clock()
        event = pipeline.translate(
            source_language=validated["sourceLanguage"],
            target_language=validated["targetLanguage"],
            segments=[
                {
                    "streamId": "benchmark-stream",
                    "segmentId": segment_id,
                    "revision": 1,
                    "text": validated["sourceText"],
                    "languageConfidence": 1.0,
                    "audioStartMs": index * 1_000,
                    "audioEndMs": index * 1_000 + 900,
                }
            ],
            glossary=validated["glossary"],
            allow_remote=True,
            idempotency_key=f"benchmark-{validated['id']}",
        )
        latency_ms = max(0, round((clock() - started) * 1_000))
        items.append(_report_item(validated, segment_id, event, latency_ms))

    return _benchmark_report(items, provider=adapter.name, model=adapter.model)


def run_translation_http_benchmark(
    engine_url: str,
    corpus: list[dict],
    *,
    client=None,
    authorization: str | None = None,
    clock_seconds: Callable[[], float] | None = None,
    run_id: str | None = None,
) -> dict:
    """Exercise the authenticated speech-to-translation path of a running engine."""

    if not corpus:
        raise ValueError("translation benchmark corpus must not be empty")
    import httpx

    base_url = _text(engine_url, "engine_url").rstrip("/")
    resolved_run_id = _text(run_id or uuid4().hex, "run_id")
    if len(resolved_run_id) > 48:
        raise ValueError("run_id exceeds 48 characters")
    clock = clock_seconds or time.perf_counter
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=40.0)
    principal_headers = {"Authorization": authorization} if authorization else {}
    items = []
    providers: set[tuple[str, str]] = set()

    try:
        for index, sample in enumerate(corpus):
            validated = _validated_sample(sample)
            segment_id = f"benchmark-{validated['id']}"
            key_prefix = f"benchmark-{resolved_run_id}-{validated['id']}"
            stream_id = None
            stream_headers = None
            started = clock()
            try:
                created = client.post(
                    f"{base_url}/v1/speech/streams",
                    headers=principal_headers,
                    json={
                        "sessionId": f"translation-benchmark-{validated['id']}",
                        "languageHints": [validated["sourceLanguage"]],
                    },
                )
                created.raise_for_status()
                created_payload = created.json()
                stream_id = _text(created_payload.get("streamId"), "streamId")
                stream_headers = {
                    **principal_headers,
                    "X-VoxAgent-Stream-Token": _text(
                        created_payload.get("resumeToken"), "resumeToken"
                    ),
                }
                final = client.post(
                    f"{base_url}/v1/speech/streams/{stream_id}/final",
                    headers=stream_headers,
                    json={
                        "segmentId": segment_id,
                        "revision": 1,
                        "text": validated["sourceText"],
                        "language": {
                            "code": validated["sourceLanguage"],
                            "confidence": 1.0,
                        },
                        "speaker": {"id": "benchmark"},
                        "audioStartMs": index * 1_000,
                        "audioEndMs": index * 1_000 + 900,
                        "confidence": 1.0,
                        "monotonicMs": index * 1_000 + 900,
                        "idempotencyKey": f"{key_prefix}-final",
                    },
                )
                final.raise_for_status()
                translated = client.post(
                    f"{base_url}/v1/translations",
                    headers=stream_headers,
                    json={
                        "sourceLanguage": validated["sourceLanguage"],
                        "targetLanguage": validated["targetLanguage"],
                        "segments": [
                            {
                                "streamId": stream_id,
                                "segmentId": segment_id,
                                "revision": 1,
                            }
                        ],
                        "glossary": validated["glossary"],
                        "allowRemote": True,
                        "idempotencyKey": f"{key_prefix}-translation",
                    },
                )
                translated.raise_for_status()
                event = translated.json()
                if event.get("type") == "translation.final":
                    payload = event.get("payload", {})
                    providers.add(
                        (
                            _text(payload.get("provider"), "provider"),
                            _text(payload.get("model"), "model"),
                        )
                    )
            except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError):
                event = {
                    "type": "translation.failed",
                    "payload": {
                        "code": "BENCHMARK_TRANSPORT_ERROR",
                        "retriable": True,
                    },
                }
            finally:
                latency_ms = max(0, round((clock() - started) * 1_000))
                if stream_id and stream_headers:
                    try:
                        completed = client.post(
                            f"{base_url}/v1/speech/streams/{stream_id}/complete",
                            headers=stream_headers,
                            json={
                                "reason": "translation_benchmark_complete",
                                "idempotencyKey": f"{key_prefix}-complete",
                            },
                        )
                        completed.raise_for_status()
                    except httpx.HTTPError:
                        pass
            items.append(_report_item(validated, segment_id, event, latency_ms))
    finally:
        if owns_client:
            client.close()

    provider, model = next(iter(providers)) if len(providers) == 1 else ("mixed", "mixed")
    if not providers:
        provider, model = "engine-http", "unavailable"
    return _benchmark_report(items, provider=provider, model=model)


def _benchmark_report(items: list[dict], *, provider: str, model: str) -> dict:
    succeeded = [item for item in items if item["status"] == "succeeded"]
    latencies = [item["latencyMs"] for item in items]
    return {
        "schema": "voxagent.translation.benchmark",
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "corpus": _CONTENT_POLICY,
        "summary": {
            "total": len(items),
            "succeeded": len(succeeded),
            "failed": len(items) - len(succeeded),
            "meanRequiredTermRecall": _mean(
                [item["requiredTermRecall"] for item in succeeded]
            ),
            "glossaryPassRate": _mean(
                [1.0 if item["glossaryPassed"] else 0.0 for item in succeeded]
            ),
            "p50LatencyMs": _percentile(latencies, 0.50),
            "p95LatencyMs": _percentile(latencies, 0.95),
        },
        "languagePairs": _language_pair_summaries(items),
        "items": items,
    }


def _report_item(sample: dict, segment_id: str, event: dict, latency_ms: int) -> dict:
    item = {
        "id": sample["id"],
        "sourceLanguage": sample["sourceLanguage"],
        "targetLanguage": sample["targetLanguage"],
        "sourceSegmentId": segment_id,
        "latencyMs": latency_ms,
    }
    if event.get("type") == "translation.final":
        payload = event["payload"]
        output = _text(payload.get("text"), "translation text")
        expected_hits = {
            (entry["sourceText"], entry["targetText"])
            for entry in sample["glossary"]
            if entry["sourceText"] in sample["sourceText"]
        }
        actual_hits = {
            (entry["sourceText"], entry["targetText"])
            for entry in payload.get("glossaryHits", [])
            if isinstance(entry, dict)
            and isinstance(entry.get("sourceText"), str)
            and isinstance(entry.get("targetText"), str)
        }
        glossary_passed = expected_hits == actual_hits and all(
            target in output for _, target in expected_hits
        )
        item.update(
            {
                "status": "succeeded",
                "output": output,
                "confidence": payload.get("confidence"),
                "requiredTermRecall": _required_term_recall(
                    output, sample["requiredTerms"]
                ),
                "glossaryPassed": glossary_passed,
            }
        )
    else:
        payload = event.get("payload", {})
        item.update(
            {
                "status": "failed",
                "failureCode": payload.get("code", "INVALID_RESPONSE"),
                "retriable": bool(payload.get("retriable", True)),
                "requiredTermRecall": 0.0,
                "glossaryPassed": False,
            }
        )
    return item


def _validated_sample(sample: object) -> dict:
    if not isinstance(sample, dict):
        raise ValueError("translation benchmark item must be an object")
    sample_id = _text(sample.get("id"), "id")
    if len(sample_id) > 48:
        raise ValueError("id exceeds 48 characters")
    return {
        "id": sample_id,
        "sourceLanguage": _text(sample.get("sourceLanguage"), "sourceLanguage"),
        "targetLanguage": _text(sample.get("targetLanguage"), "targetLanguage"),
        "sourceText": _text(sample.get("sourceText"), "sourceText"),
        "requiredTerms": _text_list(sample.get("requiredTerms", []), "requiredTerms"),
        "glossary": _glossary(sample.get("glossary", [])),
    }


def _language_pair_summaries(items: list[dict]) -> list[dict]:
    pairs: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        key = (item["sourceLanguage"], item["targetLanguage"])
        pairs.setdefault(key, []).append(item)

    summaries = []
    for (source_language, target_language), pair_items in sorted(pairs.items()):
        succeeded = [item for item in pair_items if item["status"] == "succeeded"]
        summaries.append(
            {
                "sourceLanguage": source_language,
                "targetLanguage": target_language,
                "total": len(pair_items),
                "succeeded": len(succeeded),
                "meanRequiredTermRecall": _mean(
                    [item["requiredTermRecall"] for item in succeeded]
                ),
                "glossaryPassRate": _mean(
                    [1.0 if item["glossaryPassed"] else 0.0 for item in succeeded]
                ),
            }
        )
    return summaries


def _required_term_recall(output: str, terms: list[str]) -> float:
    if not terms:
        return 1.0
    normalized = output.casefold()
    matched = sum(1 for term in terms if term.casefold() in normalized)
    return round(matched / len(terms), 4)


def _percentile(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _text_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [_text(item, field_name) for item in value]


def _glossary(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("glossary must be a list")
    if any(not isinstance(entry, dict) for entry in value):
        raise ValueError("glossary entries must be objects")
    return [
        {
            "sourceText": _text(entry.get("sourceText"), "glossary.sourceText"),
            "targetText": _text(entry.get("targetText"), "glossary.targetText"),
        }
        for entry in value
    ]


if __name__ == "__main__":
    raise SystemExit(main())
