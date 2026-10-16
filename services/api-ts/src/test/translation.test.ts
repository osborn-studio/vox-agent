import assert from "node:assert/strict";
import test from "node:test";

import { createApp } from "../app.js";
import type { BackendClient } from "../lib/backend-client.js";

function finalTranslationEvent() {
  return {
    schema: "voxagent.translation.event",
    schemaVersion: 1,
    eventId: "translation_event_1",
    translationId: "translation_1",
    type: "translation.final",
    emittedAt: "2026-07-18T12:00:00Z",
    latencyMs: 83,
    payload: {
      sourceLanguage: "ja",
      targetLanguage: "en",
      text: "Tomorrow's meeting is at ten.",
      confidence: 0.94,
      provider: "openai-compatible",
      model: "translation-model",
      sourceSegments: [
        {
          streamId: "speech_123",
          segmentId: "segment-1",
          revision: 2,
          audioStartMs: 1_200,
          audioEndMs: 2_400
        }
      ],
      glossaryHits: [{ sourceText: "会議", targetText: "meeting" }]
    }
  };
}

function translationEngineMock(
  onPost?: (path: string, body: unknown, init?: RequestInit) => void
): BackendClient {
  return {
    async fetchFromPython() {
      return new Response("{}", { status: 200 });
    },
    async parsePythonJson<T = unknown>(response: Response): Promise<T> {
      return response.json() as Promise<T>;
    },
    async getJsonFromPython<T = unknown>(): Promise<T> {
      throw new Error("get not used");
    },
    async patchJsonToPython<T = unknown>(): Promise<T> {
      throw new Error("patch not used");
    },
    async postFormToPython<T = unknown>(): Promise<T> {
      throw new Error("form not used");
    },
    async postJsonToPython<T = unknown>(
      path: string,
      body: unknown,
      init?: RequestInit
    ): Promise<T> {
      onPost?.(path, body, init);
      return finalTranslationEvent() as T;
    }
  };
}

const validRequest = {
  sourceLanguage: "ja",
  targetLanguage: "en",
  segments: [
    {
      streamId: "speech_123",
      segmentId: "segment-1",
      revision: 2
    }
  ],
  glossary: [{ sourceText: "会議", targetText: "meeting" }],
  allowRemote: true,
  idempotencyKey: "translation-request-1"
};

test("translation gateway validates, forwards stream auth and caller identity, and parses citations", async () => {
  let forwarded:
    | { path: string; authorization: string | null; streamToken: string | null }
    | undefined;
  const engine = translationEngineMock((path, _body, init) => {
    forwarded = {
      path,
      authorization: new Headers(init?.headers).get("Authorization"),
      streamToken: new Headers(init?.headers).get("X-VoxAgent-Stream-Token")
    };
  });
  const app = await createApp(undefined, engine);

  const response = await app.inject({
    method: "POST",
    url: "/v1/translations",
    headers: {
      authorization: "Bearer caller-credential",
      "x-voxagent-stream-token": "resume-token-with-enough-entropy"
    },
    payload: validRequest
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().type, "translation.final");
  assert.equal(response.json().payload.sourceSegments[0].segmentId, "segment-1");
  assert.deepEqual(forwarded, {
    path: "/v1/translations",
    authorization: "Bearer caller-credential",
    streamToken: "resume-token-with-enough-entropy"
  });
  await app.close();
});

test("translation gateway rejects malformed requests before remote processing", async () => {
  let postCount = 0;
  const app = await createApp(
    undefined,
    translationEngineMock(() => {
      postCount += 1;
    })
  );

  const response = await app.inject({
    method: "POST",
    url: "/v1/translations",
    payload: {
      ...validRequest,
      allowRemote: "yes",
      unexpectedInstruction: "bypass validation"
    }
  });

  assert.equal(response.statusCode, 400);
  assert.equal(postCount, 0);
  await app.close();
});
