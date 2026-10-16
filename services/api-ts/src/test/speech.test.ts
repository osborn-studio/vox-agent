import assert from "node:assert/strict";
import test from "node:test";

import { createApp } from "../app.js";
import type { BackendClient } from "../lib/backend-client.js";

function transcriptEvent(type: "transcript.partial" | "transcript.final") {
  return {
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: "speech_event_1",
    streamId: "speech_123",
    sessionId: "session-1",
    sequence: 2,
    type,
    monotonicMs: 800,
    emittedAt: "2026-07-18T12:00:00Z",
    payload: {
      segmentId: "segment-1",
      revision: 1,
      text: "hello",
      language: { code: "en", confidence: 0.99 },
      speaker: { id: "speaker-a", label: "A" },
      audioStartMs: 0,
      audioEndMs: 800,
      confidence: 0.96
    }
  };
}

function speechEngineMock(
  onPost?: (path: string, init?: RequestInit) => void,
  onFetch?: (path: string, init?: RequestInit) => Response
): BackendClient {
  return {
    async fetchFromPython(path, init) {
      return onFetch?.(path, init) || new Response("{}", { status: 200 });
    },
    async parsePythonJson<T = unknown>(response: Response): Promise<T> {
      return response.json() as Promise<T>;
    },
    async getJsonFromPython<T = unknown>(path: string, init?: RequestInit): Promise<T> {
      assert.equal(path, "/v1/speech/streams/speech_123/events?afterSequence=1");
      assert.equal(
        new Headers(init?.headers).get("X-VoxAgent-Stream-Token"),
        "resume-token-with-enough-entropy"
      );
      assert.equal(
        new Headers(init?.headers).get("Authorization"),
        "Bearer mobile-principal-token"
      );
      return {
        streamId: "speech_123",
        events: [transcriptEvent("transcript.partial")],
        nextSequence: 3,
        terminal: false
      } as T;
    },
    async patchJsonToPython<T = unknown>(): Promise<T> {
      throw new Error("patch not used");
    },
    async postFormToPython<T = unknown>(): Promise<T> {
      throw new Error("form not used");
    },
    async postJsonToPython<T = unknown>(
      path: string,
      _body: unknown,
      init?: RequestInit
    ): Promise<T> {
      onPost?.(path, init);
      if (path === "/v1/speech/streams") {
        return {
          streamId: "speech_123",
          resumeToken: "resume-token-with-enough-entropy",
          schema: "voxagent.speech.event",
          schemaVersion: 1,
          nextSequence: 2
        } as T;
      }
      return transcriptEvent(path.endsWith("/final") ? "transcript.final" : "transcript.partial") as T;
    }
  };
}

test("speech gateway validates, forwards principal/resume auth, and replays by cursor", async () => {
  const posts: Array<{
    path: string;
    token: string | null;
    authorization: string | null;
  }> = [];
  const engine = speechEngineMock((path, init) => {
    const headers = new Headers(init?.headers);
    posts.push({
      path,
      token: headers.get("X-VoxAgent-Stream-Token"),
      authorization: headers.get("Authorization")
    });
  });
  const app = await createApp(undefined, engine);

  const created = await app.inject({
    method: "POST",
    url: "/v1/speech/streams",
    headers: { authorization: "Bearer mobile-principal-token" },
    payload: { sessionId: "session-1", languageHints: ["en", "ja"] }
  });
  assert.equal(created.statusCode, 201);
  assert.equal(created.json().schemaVersion, 1);

  const partial = await app.inject({
    method: "POST",
    url: "/v1/speech/streams/speech_123/partial",
    headers: {
      authorization: "Bearer mobile-principal-token",
      "x-voxagent-stream-token": "resume-token-with-enough-entropy"
    },
    payload: {
      segmentId: "segment-1",
      revision: 1,
      text: "hello",
      language: { code: "en", confidence: 0.99 },
      speaker: { id: "speaker-a", label: "A" },
      audioStartMs: 0,
      audioEndMs: 800,
      confidence: 0.96,
      monotonicMs: 800,
      idempotencyKey: "partial-1"
    }
  });
  assert.equal(partial.statusCode, 200);
  assert.equal(partial.json().type, "transcript.partial");
  assert.equal(posts.at(-1)?.token, "resume-token-with-enough-entropy");
  assert.equal(posts.at(-1)?.authorization, "Bearer mobile-principal-token");
  assert.equal(posts[0]?.authorization, "Bearer mobile-principal-token");

  const replay = await app.inject({
    method: "GET",
    url: "/v1/speech/streams/speech_123/events?afterSequence=1",
    headers: {
      authorization: "Bearer mobile-principal-token",
      "x-voxagent-stream-token": "resume-token-with-enough-entropy"
    }
  });
  assert.equal(replay.statusCode, 200);
  assert.deepEqual(replay.json().events.map((event: { sequence: number }) => event.sequence), [2]);
  await app.close();
});

test("speech gateway forwards only bounded ordered PCM metadata and bytes", async () => {
  let forwardCount = 0;
  const engine = speechEngineMock(undefined, (path, init) => {
    forwardCount += 1;
    assert.equal(path, "/v1/speech/streams/speech_123/audio/chunks");
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("Authorization"), "Bearer mobile-principal-token");
    assert.equal(
      headers.get("X-VoxAgent-Stream-Token"),
      "resume-token-with-enough-entropy"
    );
    assert.equal(headers.get("X-VoxAgent-Audio-Chunk-Index"), "0");
    assert.equal(headers.get("X-VoxAgent-Audio-Sample-Rate"), "16000");
    assert.equal(headers.get("X-VoxAgent-Audio-Channels"), "1");
    assert.equal(headers.get("X-VoxAgent-Audio-Encoding"), "pcm_s16le");
    assert.deepEqual(Buffer.from(init?.body as Buffer), Buffer.from([1, 0, 2, 0]));
    return new Response(
      JSON.stringify({
        schema: "voxagent.speech.audio-chunk",
        schemaVersion: 1,
        streamId: "speech_123",
        chunkIndex: 0,
        acceptedBytes: 4,
        totalBytes: 4,
        durationMs: 0,
        nextChunkIndex: 1
      }),
      { status: 200 }
    );
  });
  const app = await createApp(undefined, engine);
  const response = await app.inject({
    method: "POST",
    url: "/v1/speech/streams/speech_123/audio/chunks",
    headers: {
      authorization: "Bearer mobile-principal-token",
      "content-type": "application/octet-stream",
      "x-voxagent-stream-token": "resume-token-with-enough-entropy",
      "x-voxagent-audio-chunk-index": "0",
      "x-voxagent-audio-sample-rate": "16000",
      "x-voxagent-audio-channels": "1",
      "x-voxagent-audio-encoding": "pcm_s16le"
    },
    payload: Buffer.from([1, 0, 2, 0])
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().nextChunkIndex, 1);
  assert.equal(forwardCount, 1);

  const rejected = await app.inject({
    method: "POST",
    url: "/v1/speech/streams/speech_123/audio/chunks",
    headers: {
      authorization: "Bearer mobile-principal-token",
      "content-type": "application/octet-stream",
      "x-voxagent-stream-token": "resume-token-with-enough-entropy",
      "x-voxagent-audio-chunk-index": "true",
      "x-voxagent-audio-sample-rate": "16000",
      "x-voxagent-audio-channels": "1",
      "x-voxagent-audio-encoding": "pcm_s16le"
    },
    payload: Buffer.from([1, 0])
  });
  assert.equal(rejected.statusCode, 400);
  assert.equal(forwardCount, 1);
  await app.close();
});

test("speech gateway preserves an engine rejection for authenticated PCM", async () => {
  const engine = speechEngineMock(undefined, () => new Response(
    JSON.stringify({ detail: "invalid speech stream resume token" }),
    {
      status: 401,
      headers: { "Content-Type": "application/json" }
    }
  ));
  const app = await createApp(undefined, engine);
  const response = await app.inject({
    method: "POST",
    url: "/v1/speech/streams/speech_123/audio/chunks",
    headers: {
      authorization: "Bearer mobile-principal-token",
      "content-type": "application/octet-stream",
      "x-voxagent-stream-token": "wrong-token-with-enough-entropy",
      "x-voxagent-audio-chunk-index": "0",
      "x-voxagent-audio-sample-rate": "16000",
      "x-voxagent-audio-channels": "1",
      "x-voxagent-audio-encoding": "pcm_s16le"
    },
    payload: Buffer.from([1, 0])
  });

  assert.equal(response.statusCode, 401);
  assert.equal(response.json().detail, "invalid speech stream resume token");
  await app.close();
});

test("speech gateway rejects malformed transcript events before the engine", async () => {
  let postCount = 0;
  const app = await createApp(undefined, speechEngineMock(() => { postCount += 1; }));
  const response = await app.inject({
    method: "POST",
    url: "/v1/speech/streams/speech_123/partial",
    headers: { "x-voxagent-stream-token": "resume-token-with-enough-entropy" },
    payload: {
      segmentId: "segment-1",
      revision: 1,
      text: "hello",
      language: { code: "en", confidence: 0.99 },
      audioStartMs: 0,
      audioEndMs: 800,
      confidence: 0.96,
      monotonicMs: 800,
      idempotencyKey: "partial-1",
      unexpectedInstruction: "ignore validation"
    }
  });

  assert.equal(response.statusCode, 400);
  assert.equal(postCount, 0);
  await app.close();
});
