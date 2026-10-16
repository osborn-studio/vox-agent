import {
  captureSessionListSchema,
  captureSessionSchema,
  transcriptSegmentListSchema,
  memorySearchResponseSchema,
  memoryNodeListSchema,
  captureSummarySchema,
  tokenBalanceSchema,
  tokenLedgerEntryListSchema,
  agentAnswerSchema,
  agentRecentContextSchema,
  deviceListSchema,
  deviceSchema,
  pairingCodeSchema,
  auditEventListSchema,
  adminUserListSchema,
  adminUserSchema,
  createAdminUserSchema,
  issuedCredentialSchema,
  modelPolicySchema,
  modelPolicyPatchSchema,
  latestRetentionSweepSchema,
  retentionPolicySchema,
  retentionPolicyPatchSchema,
  retentionSweepRequestSchema,
  retentionSweepSchema,
  updateAdminUserSchema,
  type CaptureSession,
  type MemoryNode,
  type Device,
  type CreateAdminUser,
  type ModelPolicyPatch,
  type RetentionPolicyPatch,
  type UpdateAdminUser
} from "@voxlog/contracts";
import { z } from "zod";

const DEFAULT_TIMEOUT_MS = 4000;
const DEFAULT_BASE_URL = "http://127.0.0.1:18900";

/**
 * Result of a write (POST) call. Unlike the read helpers — which silently fall
 * back to typed mock data so read-only views render offline — write callers
 * must be able to tell a real success apart from an offline/error fallback, so
 * a genuine server-side failure (e.g. HTTP 404 for a bad/expired pairing code)
 * is surfaced instead of masquerading as a fake success.
 *
 * - `ok: true`  → the engine returned 2xx and the body parsed; `data` is live.
 * - `ok: false` → `offline: true` means the engine was unreachable (network
 *   error / timeout / unparseable body, no `status`); `offline: false` means
 *   the engine responded with a non-2xx `status`. `data` carries the typed
 *   fallback so callers that want to (e.g. askAgent) can still render it.
 */
export type WriteResult<T> =
  | { ok: true; offline: false; data: T }
  | { ok: false; offline: boolean; status?: number; data: T };

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly code?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function safeGet<S extends z.ZodTypeAny>(
  base: string,
  path: string,
  schema: S,
  fallback: () => z.output<S>,
  timeoutMs: number
): Promise<z.output<S>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}${path}`, { signal: controller.signal });
    if (!res.ok) return fallback();
    return schema.parse(await res.json());
  } catch {
    return fallback();
  } finally {
    clearTimeout(timer);
  }
}

async function safePost<S extends z.ZodTypeAny>(
  base: string,
  path: string,
  body: unknown,
  schema: S,
  fallback: () => z.output<S>,
  timeoutMs: number
): Promise<WriteResult<z.output<S>>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal
    });
    if (!res.ok) return { ok: false, offline: false, status: res.status, data: fallback() };
    return { ok: true, offline: false, data: schema.parse(await res.json()) };
  } catch {
    return { ok: false, offline: true, data: fallback() };
  } finally {
    clearTimeout(timer);
  }
}

async function strictJson<S extends z.ZodTypeAny>(
  base: string,
  path: string,
  schema: S,
  timeoutMs: number,
  token: string,
  init: RequestInit = {}
): Promise<z.output<S>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("content-type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  try {
    const response = await fetch(`${base}${path}`, {
      ...init,
      headers,
      signal: controller.signal
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as
        | { detail?: { code?: string; message?: string } | string }
        | null;
      const detail = body?.detail;
      const message = typeof detail === "string"
        ? detail
        : detail?.message || `Admin request failed (${response.status})`;
      throw new ApiError(message, response.status, typeof detail === "object" ? detail?.code : undefined);
    }
    return schema.parse(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      error instanceof DOMException && error.name === "AbortError"
        ? "Admin request timed out"
        : "Admin service is unreachable"
    );
  } finally {
    clearTimeout(timer);
  }
}

async function strictNoContent(
  base: string,
  path: string,
  timeoutMs: number,
  token: string,
  init: RequestInit
): Promise<void> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  try {
    const response = await fetch(`${base}${path}`, { ...init, headers, signal: controller.signal });
    if (!response.ok) throw new ApiError(`Admin request failed (${response.status})`, response.status);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Admin service is unreachable");
  } finally {
    clearTimeout(timer);
  }
}

export const MOCK_SESSIONS: CaptureSession[] = [
  {
    id: "cap_mock_japan_launch",
    workspaceId: "ws_mock",
    ownerUserId: "user_mock",
    edition: "PERSONAL",
    source: "MOBILE",
    mode: "MEETING",
    title: "Japan launch planning",
    startedAt: "2026-07-01T09:00:00Z",
    endedAt: "2026-07-01T09:42:00Z",
    languageHints: ["en"],
    detectedLanguages: ["en", "ja"],
    privacyLevel: "PERSONAL",
    consentState: "GRANTED",
    deviceIds: ["device_mock"],
    localOnlyUntilSync: false,
    status: "READY",
    createdAt: "2026-07-01T09:00:00Z",
    updatedAt: "2026-07-01T09:42:00Z"
  }
];

export const MOCK_NODES: MemoryNode[] = [
  {
    id: "node_mock_summary",
    workspaceId: "ws_mock",
    ownerUserId: "user_mock",
    sourceSessionId: "cap_mock_japan_launch",
    type: "SUMMARY",
    title: "Japan launch planning summary",
    body: "Team reviewed timeline for the Japan launch and confirmed the localization vendor.",
    language: "en",
    importance: "MEDIUM",
    visibility: "PRIVATE",
    tags: ["japan-launch"],
    metadata: {},
    confidence: 0.9,
    createdAt: "2026-07-01T09:42:00Z",
    updatedAt: "2026-07-01T09:42:00Z"
  },
  {
    id: "node_mock_decision",
    workspaceId: "ws_mock",
    ownerUserId: "user_mock",
    sourceSessionId: "cap_mock_japan_launch",
    type: "DECISION",
    title: "Use local vendor for JP localization",
    body: "Decided to engage the Tokyo-based localization vendor instead of building in-house.",
    language: "en",
    importance: "HIGH",
    visibility: "PRIVATE",
    tags: ["japan-launch", "vendor"],
    metadata: {},
    confidence: 0.85,
    createdAt: "2026-07-01T09:42:00Z",
    updatedAt: "2026-07-01T09:42:00Z"
  },
  {
    id: "node_mock_action_item",
    workspaceId: "ws_mock",
    ownerUserId: "user_mock",
    sourceSessionId: "cap_mock_japan_launch",
    type: "ACTION_ITEM",
    title: "Send vendor contract for review",
    body: "Gil to send the localization vendor contract to legal for review by Friday.",
    language: "en",
    importance: "MEDIUM",
    visibility: "PRIVATE",
    tags: ["japan-launch", "follow-up"],
    metadata: {},
    confidence: 0.8,
    createdAt: "2026-07-01T09:42:00Z",
    updatedAt: "2026-07-01T09:42:00Z"
  }
];

export const MOCK_DEVICES: Device[] = [
  {
    id: "device_mock_ring",
    workspaceId: "ws_mock",
    ownerUserId: "user_mock",
    kind: "RING",
    displayName: "VoxAgent Ring (mock)",
    status: "PAIRED",
    batteryPercent: 72,
    firmwareVersion: "1.2.0",
    pairedAt: "2026-07-01T09:00:00Z",
    lastSeenAt: "2026-07-05T08:00:00Z"
  }
];

export function createClient(baseUrl: string = DEFAULT_BASE_URL, timeoutMs: number = DEFAULT_TIMEOUT_MS) {
  const base = baseUrl.replace(/\/$/, "");
  let adminToken = "";
  return {
    setAdminToken: (token: string) => {
      adminToken = token.trim();
    },
    listSessions: () =>
      safeGet(base, "/v1/capture/sessions", captureSessionListSchema, () => MOCK_SESSIONS, timeoutMs),
    getSession: (id: string) =>
      safeGet(
        base,
        `/v1/capture/sessions/${encodeURIComponent(id)}`,
        captureSessionSchema,
        () => MOCK_SESSIONS[0],
        timeoutMs
      ),
    getSegments: (id: string) =>
      safeGet(
        base,
        `/v1/capture/sessions/${encodeURIComponent(id)}/transcript-segments`,
        transcriptSegmentListSchema,
        () => [],
        timeoutMs
      ),
    getSummary: (id: string) =>
      safeGet(
        base,
        `/v1/capture/sessions/${encodeURIComponent(id)}/summary`,
        captureSummarySchema,
        () => ({ sessionId: id, status: "READY" as const, summaryNodes: [], actionItems: [], decisions: [] }),
        timeoutMs
      ),
    searchMemory: (q: string, limit = 20) =>
      safeGet(
        base,
        `/v1/memory/search?q=${encodeURIComponent(q)}&limit=${limit}`,
        memorySearchResponseSchema,
        () => ({ query: q, results: [] }),
        timeoutMs
      ),
    getRecent: (days = 7, limit = 20) =>
      safeGet(base, `/v1/memory/recent?days=${days}&limit=${limit}`, memoryNodeListSchema, () => MOCK_NODES, timeoutMs),
    getActionItems: (status?: string) =>
      safeGet(
        base,
        `/v1/memory/action-items${status ? `?status=${encodeURIComponent(status)}` : ""}`,
        memoryNodeListSchema,
        () => [],
        timeoutMs
      ),
    getTokenBalance: () =>
      safeGet(
        base,
        "/v1/tokens/balance",
        tokenBalanceSchema,
        () => ({ scopeId: "user_local", scopeType: "USER" as const, balance: 0, currency: "TOKENS" as const, updatedAt: "" }),
        timeoutMs
      ),
    getTokenLedger: () => safeGet(base, "/v1/tokens/ledger", tokenLedgerEntryListSchema, () => [], timeoutMs),
    getAuditEvents: (limit = 50) =>
      strictJson(base, `/v1/admin/audit-events?limit=${limit}`, auditEventListSchema, timeoutMs, adminToken),
    getModelPolicy: () =>
      strictJson(base, "/v1/admin/model-policy", modelPolicySchema, timeoutMs, adminToken),
    patchModelPolicy: (payload: ModelPolicyPatch) =>
      strictJson(base, "/v1/admin/model-policy", modelPolicySchema, timeoutMs, adminToken, {
        method: "PATCH",
        body: JSON.stringify(modelPolicyPatchSchema.parse(payload))
      }),
    getRetentionPolicy: () =>
      strictJson(base, "/v1/admin/retention-policy", retentionPolicySchema, timeoutMs, adminToken),
    patchRetentionPolicy: (payload: RetentionPolicyPatch) =>
      strictJson(base, "/v1/admin/retention-policy", retentionPolicySchema, timeoutMs, adminToken, {
        method: "PATCH",
        body: JSON.stringify(retentionPolicyPatchSchema.parse(payload))
      }),
    getLatestRetentionSweep: () =>
      strictJson(base, "/v1/admin/retention/sweeps/latest", latestRetentionSweepSchema, timeoutMs, adminToken),
    runRetentionSweep: (dryRun: boolean) =>
      strictJson(base, "/v1/admin/retention/sweeps", retentionSweepSchema, timeoutMs, adminToken, {
        method: "POST",
        body: JSON.stringify(retentionSweepRequestSchema.parse({ dryRun }))
      }),
    listAdminUsers: () =>
      strictJson(base, "/v1/admin/users", adminUserListSchema, timeoutMs, adminToken),
    createAdminUser: (payload: CreateAdminUser) =>
      strictJson(base, "/v1/admin/users", adminUserSchema, timeoutMs, adminToken, {
        method: "POST",
        body: JSON.stringify(createAdminUserSchema.parse(payload))
      }),
    updateAdminUser: (id: string, payload: UpdateAdminUser) =>
      strictJson(
        base,
        `/v1/admin/users/${encodeURIComponent(id)}`,
        adminUserSchema,
        timeoutMs,
        adminToken,
        { method: "PATCH", body: JSON.stringify(updateAdminUserSchema.parse(payload)) }
      ),
    issueAdminCredential: (userId: string, name: string) =>
      strictJson(
        base,
        `/v1/admin/users/${encodeURIComponent(userId)}/credentials`,
        issuedCredentialSchema,
        timeoutMs,
        adminToken,
        { method: "POST", body: JSON.stringify({ name }) }
      ),
    revokeAdminCredential: (credentialId: string) =>
      strictNoContent(
        base,
        `/v1/admin/credentials/${encodeURIComponent(credentialId)}`,
        timeoutMs,
        adminToken,
        { method: "DELETE" }
      ),
    listDevices: () => safeGet(base, "/v1/devices", deviceListSchema, () => MOCK_DEVICES, timeoutMs),
    createPairingCode: () =>
      safePost(
        base,
        "/v1/devices/pairing-codes",
        {},
        pairingCodeSchema,
        () => ({
          code: "MOCK01",
          qrPayload: "voxagent://pair/MOCK01",
          expiresAt: new Date(Date.now() + 10 * 60 * 1000).toISOString()
        }),
        timeoutMs
      ),
    pairDevice: (code: string, ownerUserId: string, workspaceId: string) =>
      safePost(
        base,
        "/v1/devices/pair",
        { code, ownerUserId, workspaceId },
        deviceSchema,
        () => MOCK_DEVICES[0],
        timeoutMs
      ),
    askAgent: (query: string, opts?: { nodeIds?: string[]; limit?: number }) =>
      safePost(
        base,
        "/v1/agents/query",
        { query, ...opts },
        agentAnswerSchema,
        () => ({
          query,
          answer: "(offline) engine unreachable — no answer.",
          answerer: "mock",
          citations: [],
          contextNodeIds: []
        }),
        timeoutMs
      ),
    getAgentContext: (days = 7, limit = 20) =>
      safeGet(
        base,
        `/v1/agents/context/recent?days=${days}&limit=${limit}`,
        agentRecentContextSchema,
        () => ({ days, count: 0, nodes: [] }),
        timeoutMs
      )
  };
}

export type VoxClient = ReturnType<typeof createClient>;
