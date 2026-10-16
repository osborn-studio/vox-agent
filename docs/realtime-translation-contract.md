# Vox Agent cited realtime translation contract

Status: version 1, implemented and benchmarked 2026-07-18

Realtime translation is an additive Vox Agent capability built on finalized
`speech.stream.v1` segments. A caller cannot submit transcript text to the
translation endpoint or invent a citation: the engine authenticates the stream,
resolves the exact retained segment revision, verifies its source language, and
only then gives the internally held text to the selected translator.

## Protocol identity

- Event schema: `voxagent.translation.event`
- Schema version: `1`
- Desktop capability: `translation.realtime.v1`
- Canonical TypeScript schema: `packages/contracts/src/translation.ts`
- Python pipeline: `engine/translation.py`
- Provider adapter: `engine/translation_provider.py`
- HTTP transport: `POST /v1/translations`

Requests declare explicit BCP 47 `sourceLanguage` and `targetLanguage`, one to
eight `{streamId, segmentId, revision}` references, an optional bounded glossary,
an explicit `allowRemote` consent bit, and an idempotency key. The transport
requires both the product principal and `X-VoxAgent-Stream-Token`. Client
idempotency keys are hashed into an organization/user scope before entering the
shared pipeline, so one authenticated principal cannot collide with another.
Final and non-retriable results are replayed from the bounded cache; retriable
provider failures are deliberately not cached, so the same key can make a real
retry without risking duplicate translation output.

A successful `translation.final` event contains translated text, confidence,
provider/model identity, matched glossary entries, latency, and immutable source
citations with stream/segment/revision and audio start/end milliseconds. Failure
events contain typed failure metadata and citations, but never source transcript
text.

## Mobile conversation convergence

The Mobile conversation adapter consumes authenticated speech replay without
copying raw audio into the conversation protocol. Accepted partials are sent to
the best-effort live channel; a final event is translated by immutable source
reference and then paired with the exact cited `translation.final` in one
idempotent conversation turn. Stable keys are derived from the bounded speech
event ID. A citation, stream, session, role, or language mismatch fails closed
before a turn is accepted, and a retriable translation failure does not advance
the final cursor.

The speech resume token and conversation participant token remain separate
closure-held capabilities: the former is used only for speech replay and
translation, and the latter only for live/final conversation publication.
Neither token enters event bodies, URLs, persistence, diagnostics, or
serialization.

## Consent, privacy, and audit review

- Cross-language remote translation does not run unless `allowRemote` is the
  JSON boolean `true`; omitted, string, numeric, and false values fail closed.
- The public translation request carries references, not source text. Only the
  authenticated engine resolves transcript text, immediately before provider
  invocation.
- The pipeline has no transcript store and retains only the bounded idempotent
  response cache. Provider credentials are loaded by the macOS app from
  Keychain and injected into its child engine without command-line arguments or
  log output.
- Translation audit records derive actor/workspace identity from the verified
  principal. They record languages, source IDs, provider/model or failure code,
  never transcript, glossary contents, translated text, token, or credential.
- The gateway validates the strict shared schema at runtime and forwards both
  caller authorization and the stream-scoped token; it does not substitute a
  body-supplied identity. Mobile mirrors the same field shapes at compile time,
  while the engine remains the runtime validation authority.
- Release benchmarking uses the versioned `synthetic-non-user-content` corpus.
  Production transcripts are forbidden in that corpus and are omitted from its
  generated report.

## Offline and failure behavior

| Condition | Result | Retry guidance |
| --- | --- | --- |
| Source and target languages match | Local identity transform; matching glossary replacements are applied | No remote provider or consent required |
| Remote adapter selected and `allowRemote` is false | `REMOTE_DISABLED` | Ask for explicit consent; do not retry silently |
| Provider is not configured | `PROVIDER_UNAVAILABLE` | Non-retriable until settings change |
| Provider times out | `PROVIDER_TIMEOUT` | Retriable with the same idempotency key |
| Network/HTTP provider failure | `PROVIDER_UNAVAILABLE` | Retriable only when event metadata says so |
| Provider violates JSON or glossary contract | `INVALID_RESPONSE` | Retriable; never display unvalidated provider text |
| Segment is forged, partial, evicted, or wrong revision/language | HTTP 409 before provider invocation | Recover/replay the authoritative final segment |

Version 1 does not claim an offline cross-language model. With no configured
remote provider, cross-language requests expose a typed failure rather than a
fabricated translation. A future local adapter can implement the same interface
without changing event bodies.

## Quality and latency evidence

The release corpus has 12 synthetic prompts and two examples for each direction:
Simplified Chinese ↔ English, Japanese ↔ English, and Simplified Chinese ↔
Japanese. The benchmark exercises the complete local HTTP path: create stream,
publish final segment, authenticate the source reference, translate, then
complete the stream.

```bash
.venv/bin/python -m engine.translation_benchmark \
  --corpus docs/benchmarks/realtime-translation-corpus-v1.json \
  --engine-url http://127.0.0.1:18900 \
  --output docs/benchmarks/realtime-translation-2026-07-18.json
```

For Enterprise auth, set `VOXAGENT_AUTH_TOKEN`; the token is read from the
environment and is never written to the report. Direct adapter benchmarking is
also available by omitting `--engine-url` and setting the documented
`VOXAGENT_TRANSLATION_*` environment variables.

The 2026-07-18 run used the macOS Keychain-selected OpenAI-compatible
`qwen-plus` configuration and passed 12/12 cases. Mean required-term recall and
glossary pass rate were both `1.0`; end-to-end p50 was `2056 ms` and p95 was
`2241 ms`. The full report is
`docs/benchmarks/realtime-translation-2026-07-18.json`.

This is a deterministic terminology and integration release gate, not a human
fluency study. Broader semantic scoring, noisy real-audio ASR quality, and
physical-device UI latency belong to later product/hardware acceptance evidence.

## Compatibility policy

Consumers reject unknown schema names, major versions, event types, failure
codes, and extra request fields. Additive optional fields are compatible. A
required-field, citation, consent, identity, or failure-semantic change requires
a new schema version and coordinated Python, shared-contract, gateway, desktop,
and mobile updates.
