import assert from "node:assert/strict";
import test from "node:test";

import { createApp } from "../app.js";
import { createPythonBackendClient } from "../lib/python-backend.js";
import {
  runWithRequestId,
  safeErrorCode,
  safeRequestSerializer
} from "../lib/request-correlation.js";

test("gateway echoes a bounded caller correlation ID", async () => {
  const app = await createApp();
  const response = await app.inject({
    method: "GET",
    url: "/health?prompt=private-transcript",
    headers: { "x-request-id": "mobile-request_123" }
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.headers["x-request-id"], "mobile-request_123");
  await app.close();
});

test("gateway replaces unsafe correlation input and serializer omits private request data", async () => {
  const app = await createApp();
  const response = await app.inject({
    method: "GET",
    url: "/health?prompt=private-transcript",
    headers: { "x-request-id": "unsafe private content" }
  });

  assert.match(
    String(response.headers["x-request-id"]),
    /^[A-Za-z0-9_-]{8,64}$/
  );
  assert.notEqual(
    response.headers["x-request-id"],
    "unsafe private content"
  );
  assert.deepEqual(
    safeRequestSerializer({
      method: "GET",
      url: "/health?prompt=private-transcript",
      headers: { authorization: "private-credential" }
    } as { method: string }),
    { method: "GET" }
  );
  assert.equal(
    safeErrorCode({
      code: "UPSTREAM_REJECTED",
      message: "private transcript",
      stack: "private credential"
    }),
    "UPSTREAM_REJECTED"
  );
  assert.equal(
    safeErrorCode({
      code: "unsafe code: private transcript",
      message: "private transcript"
    }),
    "UNCLASSIFIED_ERROR"
  );
  await app.close();
});

test("backend client forwards only the current request correlation metadata", async () => {
  const originalFetch = globalThis.fetch;
  let receivedHeaders = new Headers();
  globalThis.fetch = async (_input, init) => {
    receivedHeaders = new Headers(init?.headers);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" }
    });
  };

  try {
    const client = createPythonBackendClient(
      "http://127.0.0.1:18900",
      ""
    );
    await runWithRequestId("mobile-request_123", () =>
      client.getJsonFromPython("/health")
    );

    assert.equal(
      receivedHeaders.get("x-request-id"),
      "mobile-request_123"
    );
    assert.equal(receivedHeaders.get("authorization"), null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("gateway request context propagates correlation to the engine client", async () => {
  const originalFetch = globalThis.fetch;
  let receivedHeaders = new Headers();
  globalThis.fetch = async (_input, init) => {
    receivedHeaders = new Headers(init?.headers);
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "content-type": "application/json" }
    });
  };

  const client = createPythonBackendClient(
    "http://127.0.0.1:18900",
    ""
  );
  const app = await createApp(client);
  try {
    const response = await app.inject({
      method: "GET",
      url: "/v1/permissions/pending",
      headers: {
        authorization: "Bearer principal-credential",
        "x-request-id": "mobile-request_789"
      }
    });

    assert.equal(response.statusCode, 200);
    assert.equal(
      response.headers["x-request-id"],
      "mobile-request_789"
    );
    assert.equal(
      receivedHeaders.get("x-request-id"),
      "mobile-request_789"
    );
    assert.equal(
      receivedHeaders.get("authorization"),
      "Bearer principal-credential"
    );
  } finally {
    await app.close();
    globalThis.fetch = originalFetch;
  }
});
