# Vox Agent streaming speech event contract

Status: version 1, implemented 2026-07-18

This contract is the boundary between speech recognition adapters and Vox Agent
consumers. Unlike the legacy VoxLog request/response transcription endpoint, a
stream is an ordered sequence of partial revisions, finalized segments, and an
explicit terminal outcome. Consumers can reconnect without guessing which text
was already handled.

## Protocol identity

- Schema: `voxagent.speech.event`
- Version: `1`
- Desktop capability: `speech.stream.v1`
- Canonical TypeScript schema: `packages/contracts/src/streaming-speech.ts`
- Python authority: `engine/streaming_speech.py`
- HTTP transport: `/v1/speech/streams`

Every event has a unique event ID, stream ID, optional capture session ID,
strictly increasing sequence, non-decreasing stream-relative monotonic time,
UTC emission time, event type, and typed payload. Transcript events additionally
require a BCP 47 language tag, language confidence, stable speaker identity,
audio range, revision, and transcript confidence.

## Lifecycle and reconnect

1. Create a stream with optional `sessionId` and up to eight `languageHints`.
2. Store the returned high-entropy `resumeToken`; the engine only retains its
   SHA-256 digest.
3. Publish `transcript.partial` revisions with monotonically increasing revision
   numbers. Publish exactly one `transcript.final` for each stable segment ID.
4. Track the last acknowledged `sequence`. After disconnect, request events with
   `afterSequence` and the stream token. Responses contain every retained event
   after that cursor and the next expected sequence.
5. End with exactly one of `stream.cancelled`, `stream.completed`, or
   `stream.error`. Later writes fail with a conflict.

Transcript and terminal writes carry an `idempotencyKey`. Retrying the same
retained key returns the original event rather than creating a duplicate. A
cursor older than the bounded replay window fails with `409 speech_replay_gap`;
clients must not silently skip to the newest event. A cursor ahead of the stream
also fails closed.

Replay responses are strict and bounded to 10,000 events. Every retained event
must match the envelope stream ID, sequences must increase and remain below
`nextSequence`, and the terminal flag must agree with the final event. Mobile
and the TypeScript gateway validate these same invariants before exposing
events to application code.

Version 1 reconnects within the engine process and retained replay window. The
broker is intentionally process-local; durable cross-restart streams require a
future storage-backed contract version or an additive persistence capability.

## Authenticated PCM ingress

The additive `voxagent.speech.audio-chunk` version 1 transport accepts ordered
mono 16-bit little-endian PCM for an active speech stream:

```text
POST /v1/speech/streams/{streamId}/audio/chunks
Content-Type: application/octet-stream
Authorization: Bearer <product-principal>
X-VoxAgent-Stream-Token: <stream-capability>
X-VoxAgent-Audio-Chunk-Index: <zero-based-index>
X-VoxAgent-Audio-Sample-Rate: <8000-48000>
X-VoxAgent-Audio-Channels: 1
X-VoxAgent-Audio-Encoding: pcm_s16le
```

The engine and TypeScript gateway enforce a 256 KiB chunk limit, 6 MiB stream
limit, 60 second duration limit, complete PCM frames, one fixed format per
stream, and strictly ordered indices. Retrying an accepted index with identical
bytes returns the original acknowledgement; reusing it with different bytes or
skipping an index fails with a conflict. The acknowledgement contains counts
and duration only, never audio contents.

Bytes are held only in a bounded process-local private buffer. They are erased
when the stream completes, is cancelled, or the provider fails and are not
copied into speech events, conversation state, audit, analytics, or application
logs. Terminal operations are serialized with appends so audio cannot arrive
after disposal.

The engine runs the existing file-based ASR adapter behind a bounded bridge.
Every one second, at most the latest eight seconds of PCM is copied into a
mode-0600 temporary WAV for a replaceable `transcript.partial`; completion uses
one final full-stream snapshot and publishes `transcript.final` before
`stream.completed`. Snapshot memory is zeroed and the temporary file is deleted
after every adapter call. The stable segment ID and monotonic revision let
clients replace drafts and converge on the final result.

`VOXAGENT_STREAMING_ASR_ADAPTER` selects this bridge's adapter and defaults to
`sherpa`, independently from the legacy capture-processing adapter default. A
missing runtime/model or invalid provider output fails closed: the engine emits
a sanitized, non-retriable `speech_asr_provider_error`, disposes PCM, records
metadata-only audit evidence, and returns `502` to the failed write. Automated
tests inject explicit fake adapters; the production path never silently falls
back to the deterministic mock.

## Event types

| Type | Meaning | Terminal |
| --- | --- | --- |
| `stream.started` | Stream identity and declared language hints | No |
| `transcript.partial` | Replaceable revision for a segment | No |
| `transcript.final` | Immutable finalized transcript segment | No |
| `stream.cancelled` | User or system deliberately stopped work | Yes |
| `stream.completed` | Input ended normally | Yes |
| `stream.error` | Processing ended with a typed error | Yes |

State transitions and validation are authoritative in the Python broker. The
TypeScript gateway validates request bodies before forwarding and validates all
engine responses before exposing them to desktop or mobile clients.

## Security and privacy

- Replay and mutation require the stream-scoped token in
  `X-VoxAgent-Stream-Token`; tokens from another stream are rejected.
- Every HTTP operation also requires a verified product principal in Enterprise
  edition. Personal edition resolves the local administrator without a bearer
  header.
- Raw audio is never placed in an event or audit record.
- Audit records cover stream creation and terminal actions with the verified
  principal as actor, never the caller-controlled `cancelledBy` value, and do
  not contain transcript contents.
- Resume tokens must be treated as secrets and never logged or placed in URLs.
- Replay is bounded to 10,000 events per stream by default. The broker admits at
  most 32 concurrent retained streams, expires terminal streams after one hour
  and inactive non-terminal streams after eight hours, and removes evicted
  idempotency and segment-revision state with its events.

## Compatibility policy

Consumers must reject an unknown schema name or major `schemaVersion`. New
optional event fields may be added compatibly. A required-field change, event
semantic change, or sequence/reconnect change requires a new major version and
an explicit desktop capability.

Version 1 exposes an HTTP create/publish/replay transport. Push delivery such as
SSE or WebSocket may be added later without changing event bodies; the replay
cursor remains the source of truth after any transport interruption.

## Verification and benchmark

`tests/test_streaming_speech.py` covers version identity, multilingual payloads,
revision ordering, idempotency, token isolation, cancellation, replay gaps,
principal auth, bounded stream/segment state, and a 3,600-segment/7,201-event
loss test.

`tests/test_speech_audio.py`, `tests/test_speech_asr.py`, and
`services/api-ts/src/test/speech.test.ts` cover PCM ordering, idempotency,
bounds, rolling snapshots, partial/final convergence, provider failure,
principal plus capability authentication, terminal disposal, gateway metadata
filtering, and the absence of per-chunk audit data.

Run the declared transport benchmark with:

```bash
.venv/bin/python scripts/benchmark_streaming_speech.py \
  --output docs/benchmarks/streaming-speech-contract-2026-07-18.json
```

The corpus is `tests/fixtures/streaming_speech_corpus.json` and declares
Simplified Chinese, Japanese, and English text and speaker identities. The
generated report records its SHA-256 digest, per-language finalized counts,
publish/replay latency, missing sequences, duplicates, and segment loss.

This benchmark measures event-contract transport only. It makes no claim about
voice activity detection, recognition quality, diarization quality, or
audio-to-text latency; those require the declared real-audio corpus in P3.2.
