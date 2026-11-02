import assert from "node:assert/strict";
import test from "node:test";

import { fastPathResponseSchema, polishTextResponseSchema } from "@voxlog/contracts";

import type { BackendClient } from "../lib/backend-client.js";
import { createApp } from "../app.js";

const base = {
  id: "evt-voice",
  status: "ok",
  raw_text: "voice raw",
  display_text: "voice display",
  polished_text: "voice display",
  stt_provider: "whispercpp-local",
  stt_model: "base.en",
  target_app: "Cursor",
  target_risk_level: "low",
  should_autopaste: true,
  needs_review: false,
  confidence: 0.93,
  dictionary_applied: [],
  latency_ms: 450,
  session_id: "sess-voice",
  utterance_id: "utt-voice",
  output_id: "out-voice",
  output_mode: "paste",
  archive_status: "queued",
  created_at: "2026-04-03T00:00:00Z"
};

test("fast-path schema accepts responses without polish fields", () => {
  assert.equal(fastPathResponseSchema.safeParse(base).success, true);
});

test("fast-path schema accepts polish_status and polish_error, rejects unknown status", () => {
  const ok = fastPathResponseSchema.safeParse({
    ...base,
    polish_status: "failed",
    polish_error: "Qwen Bailian: HTTP 429"
  });
  assert.equal(ok.success, true);
  assert.equal(fastPathResponseSchema.safeParse({ ...base, polish_status: "maybe" }).success, false);
  assert.equal(fastPathResponseSchema.safeParse({ ...base, polish_error: "x".repeat(201) }).success, false);
});

test("polish text schema covers the three states and bounds the error", () => {
  assert.equal(polishTextResponseSchema.safeParse({ polish_status: "ok", polished_text: "t" }).success, true);
  assert.equal(
    polishTextResponseSchema.safeParse({
      polish_status: "failed",
      polished_text: "t",
      polish_error: "Qwen Bailian: timed out"
    }).success,
    true
  );
  assert.equal(
    polishTextResponseSchema.safeParse({
      polish_status: "skipped",
      polished_text: "t",
      polish_error: "qwen-turbo: not configured"
    }).success,
    true
  );
  assert.equal(polishTextResponseSchema.safeParse({ polish_status: "maybe", polished_text: "t" }).success, false);
});

interface Recorded {
  voiceForms: FormData[];
  jsonPosts: Array<{ path: string; body: unknown }>;
}

function createRecordingClient(polishReply: unknown): { client: BackendClient; recorded: Recorded } {
  const recorded: Recorded = { voiceForms: [], jsonPosts: [] };
  const client: BackendClient = {
    async fetchFromPython(path, init) {
      if (path === "/v1/voice") {
        recorded.voiceForms.push(init?.body as FormData);
        return new Response(
          JSON.stringify({ ...base, polished_text: "polished voice", polish_status: "ok" })
        );
      }
      return new Response(JSON.stringify({}));
    },
    async parsePythonJson<T = unknown>(response: Response): Promise<T> {
      return (await response.json()) as T;
    },
    async getJsonFromPython<T = unknown>(): Promise<T> {
      return {} as T;
    },
    async patchJsonToPython<T = unknown>(): Promise<T> {
      return {} as T;
    },
    async postFormToPython<T = unknown>(): Promise<T> {
      return {} as T;
    },
    async postJsonToPython<T = unknown>(path: string, body: unknown): Promise<T> {
      recorded.jsonPosts.push({ path, body });
      if (path === "/v1/text/polish") {
        return polishReply as T;
      }
      return {} as T;
    }
  };
  return { client, recorded };
}

function multipartVoice(fields: Record<string, string>): { payload: Buffer; contentType: string } {
  const boundary = "----voxlog-polish-boundary";
  let tail = "";
  for (const [name, value] of Object.entries(fields)) {
    tail += `\r\n--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}`;
  }
  const payload = Buffer.concat([
    Buffer.from(
      `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="audio"; filename="utterance.wav"\r\n` +
        `Content-Type: audio/wav\r\n\r\n`
    ),
    Buffer.from("fake-audio"),
    Buffer.from(`${tail}\r\n--${boundary}--\r\n`)
  ]);
  return { payload, contentType: `multipart/form-data; boundary=${boundary}` };
}

test("gateway forwards the multipart polish field and passes polish_status through", async () => {
  const { client, recorded } = createRecordingClient({});
  const app = await createApp(client);
  const { payload, contentType } = multipartVoice({ source: "desktop-tauri", mode: "normal", polish: "1" });
  const response = await app.inject({
    method: "POST",
    url: "/v1/voice",
    headers: { "content-type": contentType },
    payload
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().polish_status, "ok");
  assert.equal(response.json().polished_text, "polished voice");
  assert.equal(recorded.voiceForms.length, 1);
  assert.equal(recorded.voiceForms[0].get("polish"), "1");

  await app.close();
});

test("gateway sends polish=0 upstream when the client omits the field", async () => {
  const { client, recorded } = createRecordingClient({});
  const app = await createApp(client);
  const { payload, contentType } = multipartVoice({ source: "desktop-tauri", mode: "normal" });
  const response = await app.inject({
    method: "POST",
    url: "/v1/voice",
    headers: { "content-type": contentType },
    payload
  });

  assert.equal(response.statusCode, 200);
  assert.equal(recorded.voiceForms[0].get("polish"), "0");

  await app.close();
});

test("gateway proxies POST /v1/text/polish and returns the upstream body unchanged", async () => {
  const upstream = {
    polish_status: "failed",
    polished_text: "嗯 测试",
    polish_error: "Qwen Bailian: HTTP 429"
  };
  const { client, recorded } = createRecordingClient(upstream);
  const app = await createApp(client);
  const response = await app.inject({
    method: "POST",
    url: "/v1/text/polish",
    headers: { "content-type": "application/json" },
    payload: JSON.stringify({ text: "嗯 测试" })
  });

  assert.equal(response.statusCode, 200);
  assert.deepEqual(response.json(), upstream);
  assert.deepEqual(recorded.jsonPosts, [{ path: "/v1/text/polish", body: { text: "嗯 测试" } }]);

  await app.close();
});

test("gateway rejects empty, oversized and unknown-field polish requests before upstream", async () => {
  const { client, recorded } = createRecordingClient({ polish_status: "ok", polished_text: "x" });
  const app = await createApp(client);
  for (const body of [{ text: "" }, { text: "a".repeat(8193) }, { text: "x", polish: true }, { nope: 1 }]) {
    const response = await app.inject({
      method: "POST",
      url: "/v1/text/polish",
      headers: { "content-type": "application/json" },
      payload: JSON.stringify(body)
    });
    assert.equal(response.statusCode, 400, JSON.stringify(body));
  }
  assert.equal(recorded.jsonPosts.length, 0);

  await app.close();
});
