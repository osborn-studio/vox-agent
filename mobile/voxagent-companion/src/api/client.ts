/**
 * Typed fetch client for the VOXAGENT Python engine (engine/server.py).
 *
 * BASE_URL points at the local engine (`vox-engine start`, default port
 * 18900) — the same HTTP surface exercised by the CLI and the MCP tool
 * server (see voxagent_mcp/voxagent_server.py) and asserted against in
 * tests/test_voice_memory_contract_flow.py.
 *
 * NOTE ON DUPLICATION: the interfaces below intentionally mirror the
 * camelCase field names defined in packages/contracts/src (CaptureSession,
 * MemoryNode, etc.) but are re-declared locally rather than imported. The
 * Expo app is kept dependency-self-contained (no workspace package import)
 * so the mobile shell can be installed/typechecked in isolation.
 *
 * Release behavior is fail-closed: network and server failures are returned
 * explicitly and mutations never fabricate successful data. A build with
 * EXPO_PUBLIC_VOX_DEMO_MODE exactly "true" labels successful reads from its
 * configured demo engine as source: "demo"; no seed entities are bundled.
 */

import {
  type Validator,
  isAgentAnswer,
  isAudioUploadResult,
  isCaptureSession,
  isCaptureSessionArray,
  isCaptureSummary,
  isConversationInviteClaimed,
  isConversationLiveDraft,
  isConversationLiveSnapshot,
  isConversationReplay,
  isConversationSession,
  isConversationSessionCreated,
  isConversationTurn,
  isMemoryNodeArray,
  isMemorySearchResponse,
  isPairingCode,
  isPermissionDecision,
  isPermissionRequestArray,
  isSpeechAudioChunkAck,
  isSpeechReplay,
  isSpeechStreamCreated,
  isSpeechTerminalEvent,
  isTokenBalance,
  isTokenLedger,
  isTranslationEvent,
} from "./validation";
import {
  applyPrincipalAuthorization,
  reportCredentialRejected,
} from "../auth/runtime";
import {
  installRequestCorrelation,
  recordRequestDiagnostic,
  responseCorrelationId,
} from "../diagnostics/runtime";

const DEFAULT_BASE_URL = "http://127.0.0.1:18900";

/**
 * Resolve the engine endpoint at bundle time. Expo exposes variables prefixed
 * with EXPO_PUBLIC_ to application code, which lets a physical phone use the
 * development machine's LAN address without editing tracked source.
 *
 * Credentials, paths, query parameters, and non-HTTP schemes are rejected so
 * a copied URL cannot accidentally bundle a token into the application.
 */
function parseHttpOrigin(configured: string | undefined): string | null {
  if (!configured?.trim()) return null;
  try {
    const url = new URL(configured.trim());
    const hasUnsafeParts =
      (url.protocol !== "http:" && url.protocol !== "https:") ||
      Boolean(url.username || url.password || url.search || url.hash) ||
      (url.pathname !== "" && url.pathname !== "/");
    if (hasUnsafeParts) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function resolveBaseUrl(
  configured: string | undefined = process.env.EXPO_PUBLIC_VOX_ENGINE_URL
): string {
  return parseHttpOrigin(configured) ?? DEFAULT_BASE_URL;
}

export const BASE_URL = resolveBaseUrl();

/**
 * Public guest operations may use a separately allowlisted ingress. The QR
 * never carries this origin: it is fixed by the signed app/build. Missing or
 * invalid configuration safely falls back to the private engine origin for a
 * trusted-LAN development build.
 */
export function resolveConversationBaseUrl(
  configured: string | undefined = process.env.EXPO_PUBLIC_VOX_CONVERSATION_URL,
  engineBaseUrl: string = BASE_URL
): string {
  return parseHttpOrigin(configured) ?? engineBaseUrl;
}

export const CONVERSATION_BASE_URL = resolveConversationBaseUrl();

export function resolveDemoMode(
  configured: string | undefined = process.env.EXPO_PUBLIC_VOX_DEMO_MODE
): boolean {
  return configured === "true";
}

export const DEMO_MODE = resolveDemoMode();

// ---------------------------------------------------------------------------
// Types (mirrors @voxlog/contracts field names, camelCase)
// ---------------------------------------------------------------------------

export type CaptureMode =
  | "MEETING"
  | "INTERVIEW"
  | "TRANSLATION"
  | "FACE_TO_FACE"
  | "REMOTE_QR"
  | "LECTURE"
  | "IDEA"
  | "FIELD_NOTE"
  | "AGENT_COMMAND"
  | "LEARNING"
  | "LIFE_LOG";
export type CaptureSource =
  | "MOBILE"
  | "DESKTOP"
  | "WEB"
  | "HARDWARE"
  | "UPLOAD"
  | "PHONE"
  | "MEETING"
  | "CLI";
export type SessionStatus = "RECORDING" | "PROCESSING" | "READY" | "FAILED" | "ARCHIVED";
export type PrivacyLevel = "PERSONAL" | "TEAM" | "RESTRICTED" | "REGULATED";
export type ConsentState = "UNKNOWN" | "REQUESTED" | "GRANTED" | "DENIED";

export interface CaptureSession {
  id: string;
  workspaceId: string;
  ownerUserId: string;
  edition: "PERSONAL" | "ENTERPRISE";
  source: CaptureSource;
  mode: CaptureMode;
  title: string | null;
  startedAt: string;
  endedAt: string | null;
  languageHints: string[];
  detectedLanguages: string[];
  privacyLevel: PrivacyLevel;
  consentState: ConsentState;
  deviceIds: string[];
  localOnlyUntilSync: boolean;
  status: SessionStatus;
  createdAt?: string;
  updatedAt?: string;
}

export type MemoryNodeType =
  | "SESSION"
  | "SUMMARY"
  | "DECISION"
  | "ACTION_ITEM"
  | "PROMISE"
  | "PERSON"
  | "ORGANIZATION"
  | "PROJECT"
  | "CUSTOMER"
  | "TOPIC"
  | "TERM"
  | "TRANSLATION_NOTE"
  | "TASK"
  | "FACT"
  | "PREFERENCE"
  | "VOCABULARY"
  | "QUOTE"
  | "RISK"
  | "DOCUMENT"
  | "DEVICE_EVENT";

export interface MemoryNode {
  id: string;
  workspaceId: string;
  ownerUserId: string | null;
  sourceSessionId: string | null;
  type: MemoryNodeType;
  title: string;
  body: string;
  language: string | null;
  importance: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  visibility: "PRIVATE" | "WORKSPACE" | "RESTRICTED";
  tags: string[];
  metadata: Record<string, unknown>;
  confidence: number;
  createdAt: string;
  updatedAt: string;
}

export interface MemorySearchResult {
  node: MemoryNode;
  score: number;
  highlights: string[];
}

export interface MemorySearchResponse {
  query: string;
  results: MemorySearchResult[];
}

export type DeviceKind =
  | "SIMULATOR"
  | "SIMULATED_RECORDER"
  | "VOXPIN"
  | "VOXNOTE"
  | "RING"
  | "WATCH"
  | "PHONE_WIDGET"
  | "MOBILE"
  | "DESKTOP"
  | "ROOM_RECORDER"
  | "CUSTOM_HARDWARE";
export type DeviceStatus =
  | "PAIRING"
  | "PAIRED"
  | "UNPAIRED"
  | "CONNECTED"
  | "DISCONNECTED"
  | "ONLINE"
  | "OFFLINE"
  | "LOW_BATTERY"
  | "LOCKED"
  | "WIPED"
  | "REVOKED";

export interface Device {
  id: string;
  workspaceId: string;
  ownerUserId: string;
  kind: DeviceKind;
  displayName: string;
  status: DeviceStatus;
  batteryPercent: number | null;
  firmwareVersion: string | null;
  pairedAt: string | null;
  lastSeenAt: string | null;
}

export interface PairingCode {
  code: string;
  qrPayload: string;
  expiresAt: string;
}

export interface TokenBalance {
  scopeId: string;
  scopeType: "USER" | "WORKSPACE";
  balance: number;
  currency: "TOKENS";
  updatedAt: string;
}

export interface AgentCitation {
  id: string;
  title: string | null;
  type: string | null;
}

export interface AgentAnswer {
  query: string;
  answer: string;
  answerer: string;
  citations: AgentCitation[];
  contextNodeIds: string[];
}

/** Principal-scoped permission wire shape shared by engine and gateway. */
export interface PermissionRequest {
  approval_id: string;
  task_id: string;
  agent_id: string;
  tool: string;
  scope: Record<string, string>;
  requested_at: string;
  expires_at: string;
}

export interface PermissionDecision {
  ok: true;
  decision: "approved" | "denied";
}

export interface TokenLedgerEntry {
  id: string;
  scopeId: string;
  scopeType: "USER" | "WORKSPACE";
  sessionId: string | null;
  spendType:
    | "ASR"
    | "TRANSLATION"
    | "SUMMARY"
    | "AGENT_REASONING"
    | "AGENT_QUERY"
    | "EXPORT"
    | "PURCHASE"
    | "REFUND"
    | "MANUAL_ADJUSTMENT"
    | "ADMIN_ADJUSTMENT";
  delta: number;
  balanceAfter: number;
  description: string;
  createdAt: string;
}

export interface TranslationGlossaryEntry {
  sourceText: string;
  targetText: string;
}

export interface TranslationSourceReference {
  streamId: string;
  segmentId: string;
  revision: number;
}

export interface TranslationSourceCitation {
  streamId: string;
  segmentId: string;
  revision: number;
  audioStartMs: number;
  audioEndMs: number;
}

export interface TranslationRequest {
  sourceLanguage: string;
  targetLanguage: string;
  segments: TranslationSourceReference[];
  glossary: TranslationGlossaryEntry[];
  allowRemote: boolean;
  idempotencyKey: string;
}

interface TranslationEventBase {
  schema: "voxagent.translation.event";
  schemaVersion: 1;
  eventId: string;
  translationId: string;
  emittedAt: string;
  latencyMs: number;
}

export interface TranslationFinalEvent extends TranslationEventBase {
  type: "translation.final";
  payload: {
    sourceLanguage: string;
    targetLanguage: string;
    text: string;
    confidence: number;
    provider: string;
    model: string;
    sourceSegments: TranslationSourceCitation[];
    glossaryHits: TranslationGlossaryEntry[];
  };
}

export interface TranslationFailedEvent extends TranslationEventBase {
  type: "translation.failed";
  payload: {
    sourceLanguage: string;
    targetLanguage: string;
    code:
      | "REMOTE_DISABLED"
      | "PROVIDER_UNAVAILABLE"
      | "PROVIDER_TIMEOUT"
      | "INVALID_RESPONSE";
    message: string;
    retriable: boolean;
    sourceSegments: TranslationSourceCitation[];
  };
}

export type TranslationEvent = TranslationFinalEvent | TranslationFailedEvent;

export type ConversationMode = "FACE_TO_FACE" | "REMOTE_QR";
export type ConversationStatus = "WAITING" | "ACTIVE" | "ENDED";
export type ConversationParticipantRole = "HOST" | "GUEST";

export interface ConversationSession {
  schema: "voxagent.conversation.session";
  schemaVersion: 1;
  id: string;
  mode: ConversationMode;
  status: ConversationStatus;
  hostLanguage: string;
  guestLanguage: string;
  participantCount: number;
  createdAt: string;
  expiresAt: string;
  nextSequence: number;
}

export interface ConversationInvite {
  inviteToken: string;
  qrPayload: string;
  expiresAt: string;
}

export interface ConversationSessionCreated {
  session: ConversationSession;
  hostAccessToken: string;
  localGuestAccessToken: string | null;
  invite: ConversationInvite | null;
}

export interface ConversationInviteClaimed {
  session: ConversationSession;
  participantRole: "GUEST";
  accessToken: string;
}

export interface SpeechFinalEvent {
  schema: "voxagent.speech.event";
  schemaVersion: 1;
  eventId: string;
  type: "transcript.final";
  streamId: string;
  sessionId: string;
  sequence: number;
  monotonicMs: number;
  emittedAt: string;
  payload: {
    segmentId: string;
    revision: number;
    text: string;
    language: { code: string; confidence: number };
    speaker: { id: string; label: string | null };
    audioStartMs: number;
    audioEndMs: number;
    confidence: number;
  };
}

export interface SpeechPartialEvent
  extends Omit<SpeechFinalEvent, "type"> {
  type: "transcript.partial";
}

export interface SpeechStartedEvent {
  schema: "voxagent.speech.event";
  schemaVersion: 1;
  eventId: string;
  streamId: string;
  sessionId: string | null;
  sequence: number;
  type: "stream.started";
  monotonicMs: number;
  emittedAt: string;
  payload: { languageHints: string[] };
}

export interface SpeechStreamCreated {
  streamId: string;
  resumeToken: string;
  schema: "voxagent.speech.event";
  schemaVersion: 1;
  nextSequence: number;
}

export interface SpeechAudioChunkAck {
  schema: "voxagent.speech.audio-chunk";
  schemaVersion: 1;
  streamId: string;
  chunkIndex: number;
  acceptedBytes: number;
  totalBytes: number;
  durationMs: number;
  nextChunkIndex: number;
}

interface SpeechTerminalEventBase {
  schema: "voxagent.speech.event";
  schemaVersion: 1;
  eventId: string;
  streamId: string;
  sessionId: string | null;
  sequence: number;
  monotonicMs: number;
  emittedAt: string;
}

export type SpeechTerminalEvent =
  | (SpeechTerminalEventBase & {
      type: "stream.completed";
      payload: { reason: string };
    })
  | (SpeechTerminalEventBase & {
      type: "stream.cancelled";
      payload: { reason: string; cancelledBy: string };
    });

export interface SpeechErrorEvent extends SpeechTerminalEventBase {
  type: "stream.error";
  payload: {
    code: string;
    message: string;
    retriable: boolean;
  };
}

export type SpeechEvent =
  | SpeechStartedEvent
  | SpeechPartialEvent
  | SpeechFinalEvent
  | SpeechTerminalEvent
  | SpeechErrorEvent;

export interface SpeechReplay {
  streamId: string;
  events: SpeechEvent[];
  nextSequence: number;
  terminal: boolean;
}

export interface ConversationTurn {
  schema: "voxagent.conversation.turn";
  schemaVersion: 1;
  type: "conversation.turn";
  eventId: string;
  sessionId: string;
  sequence: number;
  participantRole: ConversationParticipantRole;
  clientEventId: string;
  speechEvent: SpeechFinalEvent;
  translationEvent: TranslationFinalEvent;
  createdAt: string;
}

export interface ConversationReplay {
  sessionId: string;
  turns: ConversationTurn[];
  nextSequence: number;
  status: "WAITING" | "ACTIVE";
}

export interface ConversationLiveDraft {
  schema: "voxagent.conversation.live";
  schemaVersion: 1;
  type: "conversation.live";
  sessionId: string;
  participantRole: ConversationParticipantRole;
  streamId: string;
  segmentId: string;
  revision: number;
  sourceLanguage: string;
  targetLanguage: string;
  speechEvent: SpeechPartialEvent;
  updatedAt: string;
}

export interface ConversationLiveSnapshot {
  sessionId: string;
  host: ConversationLiveDraft | null;
  guest: ConversationLiveDraft | null;
  status: "ACTIVE";
}

export type ApiFailureCode =
  | "ENGINE_OFFLINE"
  | "AUTH_REQUIRED"
  | "AUTHORIZATION_DENIED"
  | "REQUEST_REJECTED"
  | "INVALID_RESPONSE";

export interface ApiFailure {
  ok: false;
  offline: boolean;
  status?: number;
  code: ApiFailureCode;
}

export interface ApiSuccess<T> {
  ok: true;
  offline: false;
  source: "live" | "demo";
  data: T;
}

export type ReadResult<T> = ApiSuccess<T> | ApiFailure;

export type WriteResult<T> = ApiSuccess<T> | ApiFailure;

export type ConversationResult<T> =
  | { ok: true; offline: false; data: T }
  | ApiFailure;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FETCH_TIMEOUT_MS = 4000;

function recordApiDiagnostic<T>(
  result: ApiSuccess<T>,
  correlationId: string,
  endpointOrigin: string,
  response?: Response,
  offline?: boolean
): ApiSuccess<T>;
function recordApiDiagnostic(
  result: ApiFailure,
  correlationId: string,
  endpointOrigin: string,
  response?: Response,
  offline?: boolean
): ApiFailure;
function recordApiDiagnostic<T>(
  result: { ok: true; offline: false; data: T },
  correlationId: string,
  endpointOrigin: string,
  response?: Response,
  offline?: boolean
): { ok: true; offline: false; data: T };
function recordApiDiagnostic(
  result: { ok: boolean },
  correlationId: string,
  endpointOrigin: string,
  response?: Response,
  offline: boolean = false
): { ok: boolean } {
  recordRequestDiagnostic({
    correlationId: response
      ? responseCorrelationId(response, correlationId)
      : correlationId,
    endpointOrigin,
    networkState: offline ? "offline" : "reachable",
    error: result.ok ? { code: "OK" } : result,
  });
  return result;
}

function rejectedRequest(
  status: number,
  principalProtected: boolean
): ApiFailure {
  if (status === 401) {
    if (principalProtected) reportCredentialRejected();
    return {
      ok: false,
      offline: false,
      status,
      code: "AUTH_REQUIRED",
    };
  }
  if (status === 403) {
    return {
      ok: false,
      offline: false,
      status,
      code: "AUTHORIZATION_DENIED",
    };
  }
  return {
    ok: false,
    offline: false,
    status,
    code: "REQUEST_REJECTED",
  };
}

async function safeRead<T>(
  input: string,
  validator: Validator<T>,
  init?: RequestInit
): Promise<ReadResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  applyPrincipalAuthorization(headers);
  const correlationId = installRequestCorrelation(headers);
  try {
    const res = await fetch(`${BASE_URL}${input}`, {
      ...init,
      headers,
      signal: controller.signal,
    });
    if (!res.ok) {
      return recordApiDiagnostic(
        rejectedRequest(res.status, true),
        correlationId,
        BASE_URL,
        res
      );
    }
    try {
      const data: unknown = await res.json();
      if (!validator(data)) {
        return recordApiDiagnostic(
          { ok: false, offline: false, code: "INVALID_RESPONSE" },
          correlationId,
          BASE_URL,
          res
        );
      }
      return recordApiDiagnostic(
        {
          ok: true,
          offline: false,
          source: DEMO_MODE ? "demo" : "live",
          data,
        },
        correlationId,
        BASE_URL,
        res
      );
    } catch {
      return recordApiDiagnostic(
        { ok: false, offline: false, code: "INVALID_RESPONSE" },
        correlationId,
        BASE_URL,
        res
      );
    }
  } catch {
    return recordApiDiagnostic(
      { ok: false, offline: true, code: "ENGINE_OFFLINE" },
      correlationId,
      BASE_URL,
      undefined,
      true
    );
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Mutation helper. All failures are data-free so callers cannot accidentally
 * display a fabricated object or interpret an offline path as a success.
 */
async function safeWrite<T>(
  input: string,
  validator: Validator<T>,
  init?: RequestInit,
  timeoutMs: number = FETCH_TIMEOUT_MS,
  externalSignal?: AbortSignal
): Promise<WriteResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const abortFromCaller = () => controller.abort();
  if (externalSignal?.aborted) {
    controller.abort();
  } else {
    externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  applyPrincipalAuthorization(headers);
  const correlationId = installRequestCorrelation(headers);
  try {
    const res = await fetch(`${BASE_URL}${input}`, {
      ...init,
      headers,
      signal: controller.signal,
    });
    if (!res.ok) {
      return recordApiDiagnostic(
        rejectedRequest(res.status, true),
        correlationId,
        BASE_URL,
        res
      );
    }
    try {
      const data: unknown = await res.json();
      if (!validator(data)) {
        return recordApiDiagnostic(
          { ok: false, offline: false, code: "INVALID_RESPONSE" },
          correlationId,
          BASE_URL,
          res
        );
      }
      return recordApiDiagnostic(
        {
          ok: true,
          offline: false,
          source: DEMO_MODE ? "demo" : "live",
          data,
        },
        correlationId,
        BASE_URL,
        res
      );
    } catch {
      return recordApiDiagnostic(
        { ok: false, offline: false, code: "INVALID_RESPONSE" },
        correlationId,
        BASE_URL,
        res
      );
    }
  } catch {
    return recordApiDiagnostic(
      { ok: false, offline: true, code: "ENGINE_OFFLINE" },
      correlationId,
      BASE_URL,
      undefined,
      true
    );
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener("abort", abortFromCaller);
  }
}

async function conversationFetch<T>(
  input: string,
  validator: Validator<T>,
  init?: RequestInit,
  baseUrl: string = BASE_URL,
  includePrincipal: boolean = false
): Promise<ConversationResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (includePrincipal) applyPrincipalAuthorization(headers);
  const correlationId = installRequestCorrelation(headers);
  try {
    const response = await fetch(`${baseUrl}${input}`, {
      ...init,
      headers,
      signal: controller.signal,
    });
    if (!response.ok) {
      return recordApiDiagnostic(
        rejectedRequest(response.status, includePrincipal),
        correlationId,
        baseUrl,
        response
      );
    }
    try {
      const data: unknown = await response.json();
      if (!validator(data)) {
        return recordApiDiagnostic(
          { ok: false, offline: false, code: "INVALID_RESPONSE" },
          correlationId,
          baseUrl,
          response
        );
      }
      return recordApiDiagnostic(
        {
          ok: true,
          offline: false,
          data,
        },
        correlationId,
        baseUrl,
        response
      );
    } catch {
      return recordApiDiagnostic(
        { ok: false, offline: false, code: "INVALID_RESPONSE" },
        correlationId,
        baseUrl,
        response
      );
    }
  } catch {
    return recordApiDiagnostic(
      { ok: false, offline: true, code: "ENGINE_OFFLINE" },
      correlationId,
      baseUrl,
      undefined,
      true
    );
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export interface CreateCaptureSessionInput {
  mode: CaptureMode;
  languageHints?: string[];
  source?: CaptureSource;
}

export async function createCaptureSession(
  input: CreateCaptureSessionInput
): Promise<WriteResult<CaptureSession>> {
  const payload = {
    source: input.source ?? "MOBILE",
    mode: input.mode,
    languageHints: input.languageHints ?? ["auto"],
    privacyLevel: "PERSONAL",
    consentState: "UNKNOWN",
    deviceIds: [],
    localOnlyUntilSync: false,
  };
  return safeWrite<CaptureSession>(
    "/v1/capture/sessions",
    isCaptureSession,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export interface AudioUploadResult {
  sessionId: string;
  audioPath: string;
  bytes: number;
}

export interface CaptureSummary {
  sessionId: string;
  status: SessionStatus;
  summaryNodes: MemoryNode[];
  actionItems: MemoryNode[];
  decisions: MemoryNode[];
}

// A longer timeout than the JSON helpers — audio uploads are larger.
const UPLOAD_TIMEOUT_MS = 30000;

/**
 * Upload a recorded audio file to a capture session's /audio endpoint as
 * multipart form-data. Returns a {@link WriteResult} so the caller can tell a
 * live/demo success from an offline/server error without fallback data.
 * `fileUri` is the on-device URI produced by the recorder (e.g. expo-audio's
 * `recorder.uri`).
 */
export async function uploadAudio(
  sessionId: string,
  fileUri: string,
  opts?: { fileName?: string; mimeType?: string }
): Promise<WriteResult<AudioUploadResult>> {
  const form = new FormData();
  // React Native's FormData accepts a { uri, name, type } file descriptor;
  // the cast satisfies the DOM FormData typings used by tsc.
  form.append("file", {
    uri: fileUri,
    name: opts?.fileName ?? "recording.m4a",
    type: opts?.mimeType ?? "audio/m4a",
  } as unknown as Blob);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
  const headers = new Headers();
  applyPrincipalAuthorization(headers);
  const correlationId = installRequestCorrelation(headers);
  try {
    // No explicit Content-Type — fetch sets the multipart boundary from FormData.
    const res = await fetch(
      `${BASE_URL}/v1/capture/sessions/${encodeURIComponent(sessionId)}/audio`,
      { method: "POST", body: form, headers, signal: controller.signal }
    );
    if (!res.ok) {
      return recordApiDiagnostic(
        rejectedRequest(res.status, true),
        correlationId,
        BASE_URL,
        res
      );
    }
    try {
      const data: unknown = await res.json();
      if (!isAudioUploadResult(data)) {
        return recordApiDiagnostic(
          { ok: false, offline: false, code: "INVALID_RESPONSE" },
          correlationId,
          BASE_URL,
          res
        );
      }
      return recordApiDiagnostic(
        {
          ok: true,
          offline: false,
          source: DEMO_MODE ? "demo" : "live",
          data,
        },
        correlationId,
        BASE_URL,
        res
      );
    } catch {
      return recordApiDiagnostic(
        { ok: false, offline: false, code: "INVALID_RESPONSE" },
        correlationId,
        BASE_URL,
        res
      );
    }
  } catch {
    return recordApiDiagnostic(
      { ok: false, offline: true, code: "ENGINE_OFFLINE" },
      correlationId,
      BASE_URL,
      undefined,
      true
    );
  } finally {
    clearTimeout(timer);
  }
}

const activeCaptureProcesses = new Map<
  string,
  Promise<WriteResult<CaptureSession>>
>();
const PROCESS_CAPTURE_TIMEOUT_MS = 5 * 60 * 1000;

export function processCaptureSession(
  sessionId: string
): Promise<WriteResult<CaptureSession>> {
  const active = activeCaptureProcesses.get(sessionId);
  if (active) return active;

  const operation = (async () => {
    try {
      return await safeWrite<CaptureSession>(
        `/v1/capture/sessions/${encodeURIComponent(sessionId)}/process`,
        (value): value is CaptureSession =>
          isCaptureSession(value) &&
          value.id === sessionId &&
          value.status === "READY",
        { method: "POST", body: "{}" },
        PROCESS_CAPTURE_TIMEOUT_MS
      );
    } finally {
      activeCaptureProcesses.delete(sessionId);
    }
  })();
  activeCaptureProcesses.set(sessionId, operation);
  return operation;
}

export async function getCaptureSummary(
  sessionId: string
): Promise<ReadResult<CaptureSummary>> {
  return safeRead<CaptureSummary>(
    `/v1/capture/sessions/${encodeURIComponent(sessionId)}/summary`,
    (value): value is CaptureSummary =>
      isCaptureSummary(value) &&
      value.sessionId === sessionId &&
      [
        ...value.summaryNodes,
        ...value.actionItems,
        ...value.decisions,
      ].every((node) => node.sourceSessionId === sessionId)
  );
}

export async function searchMemory(
  query: string
): Promise<ReadResult<MemorySearchResponse>> {
  return safeRead<MemorySearchResponse>(
    `/v1/memory/search?q=${encodeURIComponent(query)}`,
    isMemorySearchResponse
  );
}

export interface RecentContext {
  sessions: CaptureSession[];
  nodes: MemoryNode[];
}

export async function getRecent(): Promise<ReadResult<RecentContext>> {
  const [sessionsResult, nodesResult] = await Promise.all([
    safeRead<CaptureSession[]>(
      "/v1/capture/sessions?limit=50",
      isCaptureSessionArray
    ),
    safeRead<MemoryNode[]>("/v1/memory/recent", isMemoryNodeArray),
  ]);
  if (!sessionsResult.ok) return sessionsResult;
  if (!nodesResult.ok) return nodesResult;
  const data = {
    sessions: sessionsResult.data,
    nodes: nodesResult.data,
  };
  const source =
    sessionsResult.source === "demo" || nodesResult.source === "demo"
      ? "demo"
      : "live";
  return { ok: true, offline: false, source, data };
}

export async function getActionItems(): Promise<ReadResult<MemoryNode[]>> {
  return safeRead<MemoryNode[]>("/v1/memory/action-items", isMemoryNodeArray);
}

export async function listPendingPermissions(): Promise<
  ReadResult<PermissionRequest[]>
> {
  return safeRead<PermissionRequest[]>(
    "/v1/permissions/pending",
    isPermissionRequestArray
  );
}

export async function decidePermission(
  approvalId: string,
  approved: boolean
): Promise<WriteResult<PermissionDecision>> {
  if (!isBoundedClientText(approvalId, 1, 160)) {
    return rejectedClientWrite();
  }
  return safeWrite<PermissionDecision>(
    "/v1/permissions/approve",
    isPermissionDecision,
    {
      method: "POST",
      body: JSON.stringify({
        approval_id: approvalId,
        approved,
        remember_scope: false,
      }),
    }
  );
}

export async function createPairingCode(): Promise<WriteResult<PairingCode>> {
  return safeWrite<PairingCode>(
    "/v1/devices/pairing-codes",
    isPairingCode,
    { method: "POST" }
  );
}

export async function askAgent(query: string): Promise<WriteResult<AgentAnswer>> {
  return safeWrite<AgentAnswer>(
    "/v1/agents/query",
    isAgentAnswer,
    { method: "POST", body: JSON.stringify({ query }) }
  );
}

export async function translateSegments(
  request: TranslationRequest,
  streamToken: string
): Promise<WriteResult<TranslationEvent>> {
  return safeWrite<TranslationEvent>(
    "/v1/translations",
    isTranslationEvent,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-VoxAgent-Stream-Token": streamToken,
      },
      body: JSON.stringify(request),
    }
  );
}

export async function getTokenBalance(): Promise<ReadResult<TokenBalance>> {
  return safeRead<TokenBalance>("/v1/tokens/balance", isTokenBalance);
}

export async function getTokenLedger(): Promise<ReadResult<TokenLedgerEntry[]>> {
  return safeRead<TokenLedgerEntry[]>("/v1/tokens/ledger", isTokenLedger);
}

export async function startSpeechStream(input: {
  sessionId: string;
  languageHints: string[];
}): Promise<WriteResult<SpeechStreamCreated>> {
  if (
    !isBoundedClientText(input.sessionId, 1, 128) ||
    input.languageHints.length > 8 ||
    !input.languageHints.every((language) =>
      /^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(language)
    )
  ) {
    return rejectedClientWrite();
  }
  return safeWrite<SpeechStreamCreated>(
    "/v1/speech/streams",
    isSpeechStreamCreated,
    {
      method: "POST",
      body: JSON.stringify({
        sessionId: input.sessionId,
        languageHints: input.languageHints,
      }),
    }
  );
}

export async function sendSpeechAudioChunk(input: {
  streamId: string;
  resumeToken: string;
  chunkIndex: number;
  sampleRate: number;
  channels: 1;
  audio: ArrayBuffer;
  signal: AbortSignal;
}): Promise<WriteResult<SpeechAudioChunkAck>> {
  if (
    !Number.isSafeInteger(input.chunkIndex) ||
    input.chunkIndex < 0 ||
    !Number.isSafeInteger(input.sampleRate) ||
    input.sampleRate < 8_000 ||
    input.sampleRate > 48_000 ||
    input.channels !== 1 ||
    input.audio.byteLength < 2 ||
    input.audio.byteLength > 256 * 1024 ||
    input.audio.byteLength % 2 !== 0 ||
    !isBoundedClientText(input.streamId, 1, 128) ||
    !isBoundedClientText(input.resumeToken, 20, 512)
  ) {
    return rejectedClientWrite();
  }
  return safeWrite<SpeechAudioChunkAck>(
    `/v1/speech/streams/${encodeURIComponent(input.streamId)}/audio/chunks`,
    (value): value is SpeechAudioChunkAck =>
      isSpeechAudioChunkAck(value) &&
      value.streamId === input.streamId &&
      value.chunkIndex === input.chunkIndex,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-VoxAgent-Stream-Token": input.resumeToken,
        "X-VoxAgent-Audio-Chunk-Index": String(input.chunkIndex),
        "X-VoxAgent-Audio-Sample-Rate": String(input.sampleRate),
        "X-VoxAgent-Audio-Channels": String(input.channels),
        "X-VoxAgent-Audio-Encoding": "pcm_s16le",
      },
      body: input.audio,
    },
    FETCH_TIMEOUT_MS,
    input.signal
  );
}

export async function replaySpeechEvents(
  streamId: string,
  resumeToken: string,
  afterSequence: number
): Promise<ReadResult<SpeechReplay>> {
  if (
    !isBoundedClientText(streamId, 1, 128) ||
    !isBoundedClientText(resumeToken, 20, 512) ||
    !Number.isSafeInteger(afterSequence) ||
    afterSequence < 0
  ) {
    return rejectedClientWrite();
  }
  return safeRead<SpeechReplay>(
    `/v1/speech/streams/${encodeURIComponent(streamId)}/events?afterSequence=${encodeURIComponent(String(afterSequence))}`,
    (value): value is SpeechReplay =>
      isSpeechReplay(value) && value.streamId === streamId,
    {
      headers: { "X-VoxAgent-Stream-Token": resumeToken },
    }
  );
}

export async function completeSpeechStream(
  streamId: string,
  resumeToken: string
): Promise<WriteResult<SpeechTerminalEvent>> {
  if (
    !isBoundedClientText(streamId, 1, 128) ||
    !isBoundedClientText(resumeToken, 20, 512)
  ) {
    return rejectedClientWrite();
  }
  return safeWrite<SpeechTerminalEvent>(
    `/v1/speech/streams/${encodeURIComponent(streamId)}/complete`,
    (value): value is SpeechTerminalEvent =>
      isSpeechTerminalEvent(value) &&
      value.streamId === streamId &&
      value.type === "stream.completed",
    {
      method: "POST",
      headers: { "X-VoxAgent-Stream-Token": resumeToken },
      body: JSON.stringify({
        reason: "input_finished",
        idempotencyKey: `mobile-complete-${streamId}`,
      }),
    }
  );
}

export async function cancelSpeechStream(
  streamId: string,
  resumeToken: string
): Promise<WriteResult<SpeechTerminalEvent>> {
  if (
    !isBoundedClientText(streamId, 1, 128) ||
    !isBoundedClientText(resumeToken, 20, 512)
  ) {
    return rejectedClientWrite();
  }
  return safeWrite<SpeechTerminalEvent>(
    `/v1/speech/streams/${encodeURIComponent(streamId)}/cancel`,
    (value): value is SpeechTerminalEvent =>
      isSpeechTerminalEvent(value) &&
      value.streamId === streamId &&
      value.type === "stream.cancelled",
    {
      method: "POST",
      headers: { "X-VoxAgent-Stream-Token": resumeToken },
      body: JSON.stringify({
        reason: "user_cancelled",
        cancelledBy: "mobile",
        idempotencyKey: `mobile-cancel-${streamId}`,
      }),
    }
  );
}

function isBoundedClientText(
  value: unknown,
  minimum: number,
  maximum: number
): value is string {
  return (
    typeof value === "string" &&
    value.length >= minimum &&
    value.length <= maximum &&
    value.trim() === value &&
    !/\s/.test(value)
  );
}

function rejectedClientWrite(): ApiFailure {
  return {
    ok: false,
    offline: false,
    code: "REQUEST_REJECTED",
  };
}

export interface CreateConversationSessionInput {
  mode: ConversationMode;
  hostLanguage: string;
  guestLanguage: string;
}

export async function createConversationSession(
  input: CreateConversationSessionInput
): Promise<ConversationResult<ConversationSessionCreated>> {
  return conversationFetch<ConversationSessionCreated>(
    "/v1/conversation-sessions",
    isConversationSessionCreated,
    {
      method: "POST",
      body: JSON.stringify({
        mode: input.mode,
        hostLanguage: input.hostLanguage,
        guestLanguage: input.guestLanguage,
      }),
    },
    BASE_URL,
    true
  );
}

export async function claimConversationInvite(
  inviteToken: string
): Promise<ConversationResult<ConversationInviteClaimed>> {
  return conversationFetch<ConversationInviteClaimed>(
    "/public/v1/conversation-sessions/claim",
    isConversationInviteClaimed,
    {
      method: "POST",
      body: JSON.stringify({ inviteToken }),
    },
    CONVERSATION_BASE_URL
  );
}

export async function replayConversationTurns(
  sessionId: string,
  accessToken: string,
  afterSequence: number
): Promise<ConversationResult<ConversationReplay>> {
  if (
    !Number.isSafeInteger(afterSequence) ||
    afterSequence < 0 ||
    afterSequence > Number.MAX_SAFE_INTEGER
  ) {
    return {
      ok: false,
      offline: false,
      code: "REQUEST_REJECTED",
    };
  }
  return conversationFetch<ConversationReplay>(
    `/public/v1/conversation-sessions/${encodeURIComponent(sessionId)}/turns?afterSequence=${encodeURIComponent(String(afterSequence))}`,
    isConversationReplay,
    {
      headers: {
        "X-VoxAgent-Conversation-Token": accessToken,
      },
    },
    CONVERSATION_BASE_URL
  );
}

export async function readConversationLive(
  sessionId: string,
  accessToken: string
): Promise<ConversationResult<ConversationLiveSnapshot>> {
  return conversationFetch<ConversationLiveSnapshot>(
    `/public/v1/conversation-sessions/${encodeURIComponent(sessionId)}/live`,
    isConversationLiveSnapshot,
    {
      headers: {
        "X-VoxAgent-Conversation-Token": accessToken,
      },
    },
    CONVERSATION_BASE_URL
  );
}

export async function publishConversationLive(
  sessionId: string,
  accessToken: string,
  speechEvent: SpeechPartialEvent
): Promise<ConversationResult<ConversationLiveDraft>> {
  return conversationFetch<ConversationLiveDraft>(
    `/public/v1/conversation-sessions/${encodeURIComponent(sessionId)}/live`,
    (value): value is ConversationLiveDraft =>
      isConversationLiveDraft(value) &&
      value.sessionId === sessionId &&
      value.speechEvent.eventId === speechEvent.eventId,
    {
      method: "POST",
      headers: {
        "X-VoxAgent-Conversation-Token": accessToken,
      },
      body: JSON.stringify({ speechEvent }),
    },
    CONVERSATION_BASE_URL
  );
}

export async function publishConversationTurn(
  sessionId: string,
  accessToken: string,
  input: {
    clientEventId: string;
    speechEvent: SpeechFinalEvent;
    translationEvent: TranslationFinalEvent;
  }
): Promise<ConversationResult<ConversationTurn>> {
  return conversationFetch<ConversationTurn>(
    `/public/v1/conversation-sessions/${encodeURIComponent(sessionId)}/turns`,
    isConversationTurn,
    {
      method: "POST",
      headers: {
        "X-VoxAgent-Conversation-Token": accessToken,
      },
      body: JSON.stringify(input),
    },
    CONVERSATION_BASE_URL
  );
}

export async function endConversationSession(
  sessionId: string,
  hostAccessToken: string
): Promise<ConversationResult<ConversationSession>> {
  const headers = new Headers({
    "X-VoxAgent-Conversation-Token": hostAccessToken,
  });
  return conversationFetch<ConversationSession>(
    `/v1/conversation-sessions/${encodeURIComponent(sessionId)}/end`,
    isConversationSession,
    { method: "POST", headers },
    BASE_URL,
    true
  );
}
