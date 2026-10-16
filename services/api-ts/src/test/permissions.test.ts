import assert from "node:assert/strict";
import test from "node:test";

import { createApp } from "../app.js";
import type { BackendClient } from "../lib/backend-client.js";

function permissionEngineMock(
  calls: Array<{
    method: "GET" | "POST";
    path: string;
    authorization: string | null;
    body?: unknown;
  }>
): BackendClient {
  return {
    async fetchFromPython(): Promise<Response> {
      throw new Error("raw fetch not used");
    },
    async parsePythonJson<T = unknown>(): Promise<T> {
      throw new Error("raw parse not used");
    },
    async getJsonFromPython<T = unknown>(
      path: string,
      init?: RequestInit
    ): Promise<T> {
      calls.push({
        method: "GET",
        path,
        authorization: new Headers(init?.headers).get("Authorization")
      });
      return [{
        approval_id: "approval-123",
        task_id: "task-456",
        agent_id: "codex",
        tool: "write",
        scope: { file_path: "/tmp/report.md" },
        requested_at: "2026-07-26T12:00:00+00:00",
        expires_at: "2026-07-26T12:02:00+00:00"
      }] as T;
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
      calls.push({
        method: "POST",
        path,
        authorization: new Headers(init?.headers).get("Authorization"),
        body
      });
      return { ok: true, decision: "approved" } as T;
    }
  };
}

test("permission gateway preserves principal auth and validates both wires", async () => {
  const calls: Array<{
    method: "GET" | "POST";
    path: string;
    authorization: string | null;
    body?: unknown;
  }> = [];
  const app = await createApp(undefined, permissionEngineMock(calls));

  const pending = await app.inject({
    method: "GET",
    url: "/v1/permissions/pending",
    headers: { authorization: "Bearer mobile-principal-token" }
  });
  assert.equal(pending.statusCode, 200);
  assert.deepEqual(
    pending.json().map((request: { approval_id: string }) => request.approval_id),
    ["approval-123"]
  );

  const decision = await app.inject({
    method: "POST",
    url: "/v1/permissions/approve",
    headers: { authorization: "Bearer mobile-principal-token" },
    payload: {
      approval_id: "approval-123",
      approved: true
    }
  });
  assert.equal(decision.statusCode, 200);
  assert.deepEqual(decision.json(), { ok: true, decision: "approved" });
  assert.deepEqual(calls, [
    {
      method: "GET",
      path: "/v1/permissions/pending",
      authorization: "Bearer mobile-principal-token"
    },
    {
      method: "POST",
      path: "/v1/permissions/approve",
      authorization: "Bearer mobile-principal-token",
      body: {
        approval_id: "approval-123",
        approved: true,
        remember_scope: false
      }
    }
  ]);
  await app.close();
});

test("permission gateway rejects malformed or augmented decisions before forwarding", async () => {
  const calls: Array<{
    method: "GET" | "POST";
    path: string;
    authorization: string | null;
    body?: unknown;
  }> = [];
  const app = await createApp(undefined, permissionEngineMock(calls));

  for (const payload of [
    { approval_id: "", approved: true },
    { approval_id: "approval-123", approved: "true" },
    { approval_id: "approval-123", approved: true, unexpected: "must-not-forward" }
  ]) {
    const response = await app.inject({
      method: "POST",
      url: "/v1/permissions/approve",
      payload
    });
    assert.equal(response.statusCode, 400);
    assert.equal(response.json().error, "invalid_permission_decision");
  }

  assert.equal(calls.length, 0);
  await app.close();
});

test("permission gateway preserves upstream auth and ownership rejections", async () => {
  const rejected = new Error("approval request not found or expired") as Error & {
    statusCode: number;
  };
  rejected.statusCode = 404;
  const engine = permissionEngineMock([]);
  engine.postJsonToPython = async () => {
    throw rejected;
  };
  const app = await createApp(undefined, engine);

  const response = await app.inject({
    method: "POST",
    url: "/v1/permissions/approve",
    headers: { authorization: "Bearer wrong-principal-token" },
    payload: {
      approval_id: "approval-owned-by-someone-else",
      approved: true,
      remember_scope: false
    }
  });

  assert.equal(response.statusCode, 404);
  assert.equal(response.json().message, "approval request not found or expired");
  await app.close();
});
