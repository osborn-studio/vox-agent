from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from engine.translation import TranslationResult
from engine.translation_benchmark import (
    load_translation_benchmark_corpus,
    main,
    run_translation_benchmark,
    run_translation_http_benchmark,
)


class ReferenceTranslator:
    name = "reference"
    model = "reference-v1"
    is_remote = False

    def translate(self, *, source_text, source_language, target_language, glossary):
        del source_language, target_language, glossary
        outputs = {
            "明天开会": "Meet tomorrow at Vox Agent HQ.",
            "明日の会議": "Tomorrow's Vox Agent meeting.",
        }
        return TranslationResult(text=outputs[source_text], confidence=0.95)


def test_translation_benchmark_reports_quality_latency_and_glossary_evidence():
    ticks = iter([0.0, 0.1, 1.0, 1.3])
    corpus = [
        {
            "id": "zh-en-1",
            "sourceLanguage": "zh-Hans",
            "targetLanguage": "en",
            "sourceText": "明天开会",
            "requiredTerms": ["tomorrow", "Vox Agent"],
            "glossary": [{"sourceText": "开会", "targetText": "Vox Agent"}],
        },
        {
            "id": "ja-en-1",
            "sourceLanguage": "ja",
            "targetLanguage": "en",
            "sourceText": "明日の会議",
            "requiredTerms": ["tomorrow", "Vox Agent"],
            "glossary": [{"sourceText": "会議", "targetText": "Vox Agent"}],
        },
    ]

    report = run_translation_benchmark(
        ReferenceTranslator(),
        corpus,
        clock_seconds=lambda: next(ticks),
    )

    assert report["schema"] == "voxagent.translation.benchmark"
    assert report["provider"] == "reference"
    assert report["summary"] == {
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "meanRequiredTermRecall": 1.0,
        "glossaryPassRate": 1.0,
        "p50LatencyMs": 200,
        "p95LatencyMs": 290,
    }
    assert report["languagePairs"] == [
        {
            "sourceLanguage": "ja",
            "targetLanguage": "en",
            "total": 1,
            "succeeded": 1,
            "meanRequiredTermRecall": 1.0,
            "glossaryPassRate": 1.0,
        },
        {
            "sourceLanguage": "zh-Hans",
            "targetLanguage": "en",
            "total": 1,
            "succeeded": 1,
            "meanRequiredTermRecall": 1.0,
            "glossaryPassRate": 1.0,
        },
    ]
    assert all(item["sourceSegmentId"] == f"benchmark-{item['id']}" for item in report["items"])


def test_load_translation_benchmark_corpus_requires_synthetic_versioned_content(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(
        json.dumps(
            {
                "schema": "voxagent.translation.benchmark-corpus",
                "schemaVersion": 1,
                "contentPolicy": "synthetic-non-user-content",
                "items": [
                    {
                        "id": "en-ja-1",
                        "sourceLanguage": "en",
                        "targetLanguage": "ja",
                        "sourceText": "Hello",
                        "requiredTerms": ["こんにちは"],
                        "glossary": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_translation_benchmark_corpus(corpus_path)[0]["id"] == "en-ja-1"

    document = json.loads(corpus_path.read_text(encoding="utf-8"))
    document["contentPolicy"] = "production-transcripts"
    corpus_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic-non-user-content"):
        load_translation_benchmark_corpus(corpus_path)


def test_translation_benchmark_rejects_malformed_glossary_entries():
    corpus = [
        {
            "id": "en-ja-1",
            "sourceLanguage": "en",
            "targetLanguage": "ja",
            "sourceText": "Hello",
            "requiredTerms": [],
            "glossary": ["not-an-object"],
        }
    ]

    with pytest.raises(ValueError, match="glossary entries must be objects"):
        run_translation_benchmark(ReferenceTranslator(), corpus)


def test_translation_benchmark_cli_writes_machine_readable_report(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    output_path = tmp_path / "report.json"
    corpus_path.write_text(
        json.dumps(
            {
                "schema": "voxagent.translation.benchmark-corpus",
                "schemaVersion": 1,
                "contentPolicy": "synthetic-non-user-content",
                "items": [
                    {
                        "id": "zh-en-1",
                        "sourceLanguage": "zh-Hans",
                        "targetLanguage": "en",
                        "sourceText": "明天开会",
                        "requiredTerms": ["tomorrow", "Vox Agent"],
                        "glossary": [
                            {"sourceText": "开会", "targetText": "Vox Agent"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        ["--corpus", str(corpus_path), "--output", str(output_path)],
        adapter=ReferenceTranslator(),
    )

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["summary"]["failed"] == 0


def test_release_corpus_covers_each_chinese_japanese_english_direction():
    path = (
        Path(__file__).parents[1]
        / "docs"
        / "benchmarks"
        / "realtime-translation-corpus-v1.json"
    )

    corpus = load_translation_benchmark_corpus(path)
    pairs = {(item["sourceLanguage"], item["targetLanguage"]) for item in corpus}

    assert len(corpus) == 12
    assert pairs == {
        ("en", "ja"),
        ("en", "zh-Hans"),
        ("ja", "en"),
        ("ja", "zh-Hans"),
        ("zh-Hans", "en"),
        ("zh-Hans", "ja"),
    }


def test_http_benchmark_exercises_cited_engine_route_without_source_in_report():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/speech/streams":
            return httpx.Response(
                201,
                json={"streamId": "stream-1", "resumeToken": "stream-token"},
            )
        if request.url.path.endswith("/final"):
            return httpx.Response(200, json={"type": "transcript.final"})
        if request.url.path == "/v1/translations":
            return httpx.Response(
                200,
                json={
                    "type": "translation.final",
                    "payload": {
                        "text": "Meet tomorrow at Vox Agent HQ.",
                        "confidence": 0.96,
                        "provider": "openai-compatible",
                        "model": "quality-model",
                        "glossaryHits": [
                            {"sourceText": "开会", "targetText": "Vox Agent"}
                        ],
                    },
                },
            )
        if request.url.path.endswith("/complete"):
            return httpx.Response(200, json={"type": "stream.completed"})
        return httpx.Response(404)

    corpus = [
        {
            "id": "zh-en-http-1",
            "sourceLanguage": "zh-Hans",
            "targetLanguage": "en",
            "sourceText": "明天开会",
            "requiredTerms": ["tomorrow", "Vox Agent"],
            "glossary": [{"sourceText": "开会", "targetText": "Vox Agent"}],
        }
    ]
    ticks = iter([0.0, 0.25])
    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:18900",
    ) as client:
        report = run_translation_http_benchmark(
            "http://127.0.0.1:18900",
            corpus,
            client=client,
            clock_seconds=lambda: next(ticks),
            run_id="review-run",
        )

    assert report["provider"] == "openai-compatible"
    assert report["model"] == "quality-model"
    assert report["summary"]["p50LatencyMs"] == 250
    assert report["summary"]["meanRequiredTermRecall"] == 1.0
    assert "明天开会" not in repr(report)
    assert [request.url.path for request in requests] == [
        "/v1/speech/streams",
        "/v1/speech/streams/stream-1/final",
        "/v1/translations",
        "/v1/speech/streams/stream-1/complete",
    ]
    request_bodies = [
        json.loads(request.content)
        for request in requests
        if request.method == "POST" and request.content
    ]
    assert all(
        "review-run" in body["idempotencyKey"]
        for body in request_bodies
        if "idempotencyKey" in body
    )


def test_http_benchmark_records_malformed_engine_json_as_transport_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/speech/streams":
            return httpx.Response(
                201,
                json={"streamId": "stream-1", "resumeToken": "stream-token"},
            )
        if request.url.path.endswith("/final"):
            return httpx.Response(200, json={"type": "transcript.final"})
        if request.url.path == "/v1/translations":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/complete"):
            return httpx.Response(200, json={"type": "stream.completed"})
        return httpx.Response(404)

    corpus = [
        {
            "id": "en-ja-malformed",
            "sourceLanguage": "en",
            "targetLanguage": "ja",
            "sourceText": "Hello",
            "requiredTerms": [],
            "glossary": [],
        }
    ]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_translation_http_benchmark(
            "http://127.0.0.1:18900",
            corpus,
            client=client,
            run_id="malformed-run",
        )

    assert report["summary"]["failed"] == 1
    assert report["items"][0]["failureCode"] == "BENCHMARK_TRANSPORT_ERROR"
