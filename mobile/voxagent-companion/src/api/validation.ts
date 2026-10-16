import type {
  AgentAnswer,
  AgentCitation,
  AudioUploadResult,
  CaptureSession,
  CaptureSummary,
  ConversationInvite,
  ConversationInviteClaimed,
  ConversationLiveDraft,
  ConversationLiveSnapshot,
  ConversationReplay,
  ConversationSession,
  ConversationSessionCreated,
  ConversationTurn,
  MemoryNode,
  MemorySearchResponse,
  PairingCode,
  PermissionDecision,
  PermissionRequest,
  SpeechFinalEvent,
  SpeechErrorEvent,
  SpeechAudioChunkAck,
  SpeechEvent,
  SpeechPartialEvent,
  SpeechReplay,
  SpeechStartedEvent,
  SpeechStreamCreated,
  SpeechTerminalEvent,
  TokenBalance,
  TokenLedgerEntry,
  TranslationEvent,
  TranslationFailedEvent,
  TranslationFinalEvent,
  TranslationGlossaryEntry,
  TranslationSourceCitation,
} from "./client";

export type Validator<T> = (value: unknown) => value is T;

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: JsonRecord, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return (
    actual.length === keys.length &&
    actual.every((key) => keys.includes(key))
  );
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isSizedString(
  value: unknown,
  minimum: number,
  maximum = Number.MAX_SAFE_INTEGER
): value is string {
  return isString(value) && value.length >= minimum && value.length <= maximum;
}

function isNonEmptyString(value: unknown): value is string {
  return isSizedString(value, 1);
}

function isSizedTrimmedString(
  value: unknown,
  minimum: number,
  maximum: number
): value is string {
  return isString(value) && isSizedString(value.trim(), minimum, maximum);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isNullableSizedString(
  value: unknown,
  minimum: number,
  maximum = Number.MAX_SAFE_INTEGER
): value is string | null {
  return value === null || isSizedString(value, minimum, maximum);
}

function isOptionalNonEmptyString(value: unknown): value is string | undefined {
  return value === undefined || isNonEmptyString(value);
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isInteger(value: unknown): value is number {
  return Number.isSafeInteger(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return isInteger(value) && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return isInteger(value) && value > 0;
}

function isConfidence(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0 && value <= 1;
}

function isDateTimeWithOffset(value: unknown): value is string {
  return (
    isNonEmptyString(value) &&
    /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}

function isLanguageCode(value: unknown): value is string {
  return (
    isSizedString(value, 2, 35) &&
    /^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(value)
  );
}

function isOneOf<const T extends readonly string[]>(
  value: unknown,
  allowed: T
): value is T[number] {
  return isString(value) && allowed.includes(value);
}

function isArrayOf<T>(
  value: unknown,
  validator: Validator<T>
): value is T[] {
  return Array.isArray(value) && value.every(validator);
}

function isBoundedArrayOf<T>(
  value: unknown,
  validator: Validator<T>,
  minimum: number,
  maximum: number
): value is T[] {
  return (
    Array.isArray(value) &&
    value.length >= minimum &&
    value.length <= maximum &&
    value.every(validator)
  );
}

const isStringArray: Validator<string[]> = (value): value is string[] =>
  isArrayOf(value, isString);

const isNonEmptyStringArray: Validator<string[]> = (
  value
): value is string[] => isArrayOf(value, isNonEmptyString);

export const isCaptureSession: Validator<CaptureSession> = (
  value
): value is CaptureSession =>
  isRecord(value) &&
  isNonEmptyString(value.id) &&
  isNonEmptyString(value.workspaceId) &&
  isNonEmptyString(value.ownerUserId) &&
  isOneOf(value.edition, ["PERSONAL", "ENTERPRISE"] as const) &&
  isOneOf(
    value.source,
    [
      "MOBILE",
      "DESKTOP",
      "WEB",
      "HARDWARE",
      "UPLOAD",
      "PHONE",
      "MEETING",
      "CLI",
    ] as const
  ) &&
  isOneOf(
    value.mode,
    [
      "MEETING",
      "INTERVIEW",
      "TRANSLATION",
      "FACE_TO_FACE",
      "REMOTE_QR",
      "LECTURE",
      "IDEA",
      "FIELD_NOTE",
      "AGENT_COMMAND",
      "LEARNING",
      "LIFE_LOG",
    ] as const
  ) &&
  isNullableString(value.title) &&
  isNonEmptyString(value.startedAt) &&
  isNullableString(value.endedAt) &&
  isNonEmptyStringArray(value.languageHints) &&
  isNonEmptyStringArray(value.detectedLanguages) &&
  isOneOf(
    value.privacyLevel,
    ["PERSONAL", "TEAM", "RESTRICTED", "REGULATED"] as const
  ) &&
  isOneOf(
    value.consentState,
    ["UNKNOWN", "REQUESTED", "GRANTED", "DENIED"] as const
  ) &&
  isNonEmptyStringArray(value.deviceIds) &&
  isBoolean(value.localOnlyUntilSync) &&
  isOneOf(
    value.status,
    ["RECORDING", "PROCESSING", "READY", "FAILED", "ARCHIVED"] as const
  ) &&
  isOptionalNonEmptyString(value.createdAt) &&
  isOptionalNonEmptyString(value.updatedAt);

export const isCaptureSessionArray: Validator<CaptureSession[]> = (
  value
): value is CaptureSession[] => isArrayOf(value, isCaptureSession);

export const isMemoryNode: Validator<MemoryNode> = (
  value
): value is MemoryNode =>
  isRecord(value) &&
  isNonEmptyString(value.id) &&
  isNonEmptyString(value.workspaceId) &&
  isNullableString(value.ownerUserId) &&
  isNullableString(value.sourceSessionId) &&
  isOneOf(
    value.type,
    [
      "SESSION",
      "SUMMARY",
      "DECISION",
      "ACTION_ITEM",
      "PROMISE",
      "PERSON",
      "ORGANIZATION",
      "PROJECT",
      "CUSTOMER",
      "TOPIC",
      "TERM",
      "TRANSLATION_NOTE",
      "TASK",
      "FACT",
      "PREFERENCE",
      "VOCABULARY",
      "QUOTE",
      "RISK",
      "DOCUMENT",
      "DEVICE_EVENT",
    ] as const
  ) &&
  isNonEmptyString(value.title) &&
  isString(value.body) &&
  isNullableString(value.language) &&
  isOneOf(
    value.importance,
    ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const
  ) &&
  isOneOf(
    value.visibility,
    ["PRIVATE", "WORKSPACE", "RESTRICTED"] as const
  ) &&
  isNonEmptyStringArray(value.tags) &&
  isRecord(value.metadata) &&
  isConfidence(value.confidence) &&
  isNonEmptyString(value.createdAt) &&
  isNonEmptyString(value.updatedAt);

export const isMemoryNodeArray: Validator<MemoryNode[]> = (
  value
): value is MemoryNode[] => isArrayOf(value, isMemoryNode);

export const isCaptureSummary: Validator<CaptureSummary> = (
  value
): value is CaptureSummary =>
  isRecord(value) &&
  isNonEmptyString(value.sessionId) &&
  isOneOf(
    value.status,
    ["RECORDING", "PROCESSING", "READY", "FAILED", "ARCHIVED"] as const
  ) &&
  isMemoryNodeArray(value.summaryNodes) &&
  isMemoryNodeArray(value.actionItems) &&
  isMemoryNodeArray(value.decisions);

export const isMemorySearchResponse: Validator<MemorySearchResponse> = (
  value
): value is MemorySearchResponse =>
  isRecord(value) &&
  isString(value.query) &&
  isArrayOf(
    value.results,
    (result): result is MemorySearchResponse["results"][number] =>
      isRecord(result) &&
      isMemoryNode(result.node) &&
      isFiniteNumber(result.score) &&
      result.score >= 0 &&
      isStringArray(result.highlights)
  );

export const isPairingCode: Validator<PairingCode> = (
  value
): value is PairingCode =>
  isRecord(value) &&
  isSizedString(value.code, 6) &&
  isNonEmptyString(value.qrPayload) &&
  isNonEmptyString(value.expiresAt);

const isAgentCitation: Validator<AgentCitation> = (
  value
): value is AgentCitation =>
  isRecord(value) &&
  isNonEmptyString(value.id) &&
  isNullableString(value.title) &&
  isNullableString(value.type);

export const isAgentAnswer: Validator<AgentAnswer> = (
  value
): value is AgentAnswer =>
  isRecord(value) &&
  isString(value.query) &&
  isString(value.answer) &&
  isNonEmptyString(value.answerer) &&
  isArrayOf(value.citations, isAgentCitation) &&
  isNonEmptyStringArray(value.contextNodeIds);

export const isPermissionRequest: Validator<PermissionRequest> = (
  value
): value is PermissionRequest =>
  isRecord(value) &&
  isSizedString(value.approval_id, 1, 160) &&
  isSizedString(value.task_id, 0, 160) &&
  isSizedString(value.agent_id, 0, 160) &&
  isSizedString(value.tool, 1, 128) &&
  isRecord(value.scope) &&
  Object.values(value.scope).every(isString) &&
  isDateTimeWithOffset(value.requested_at) &&
  isDateTimeWithOffset(value.expires_at);

export const isPermissionRequestArray: Validator<PermissionRequest[]> = (
  value
): value is PermissionRequest[] => isArrayOf(value, isPermissionRequest);

export const isPermissionDecision: Validator<PermissionDecision> = (
  value
): value is PermissionDecision =>
  isRecord(value) &&
  value.ok === true &&
  isOneOf(value.decision, ["approved", "denied"] as const);

const isTranslationGlossaryEntry: Validator<TranslationGlossaryEntry> = (
  value
): value is TranslationGlossaryEntry =>
  isRecord(value) &&
  isSizedTrimmedString(value.sourceText, 1, 256) &&
  isSizedTrimmedString(value.targetText, 1, 256);

const isTranslationSourceCitation: Validator<TranslationSourceCitation> = (
  value
): value is TranslationSourceCitation =>
  isRecord(value) &&
  isSizedString(value.streamId, 1, 128) &&
  isSizedString(value.segmentId, 1, 128) &&
  isPositiveInteger(value.revision) &&
  isNonNegativeInteger(value.audioStartMs) &&
  isNonNegativeInteger(value.audioEndMs) &&
  value.audioEndMs >= value.audioStartMs;

function hasTranslationBase(value: JsonRecord): boolean {
  return (
    value.schema === "voxagent.translation.event" &&
    value.schemaVersion === 1 &&
    isNonEmptyString(value.eventId) &&
    isNonEmptyString(value.translationId) &&
    isDateTimeWithOffset(value.emittedAt) &&
    isNonNegativeInteger(value.latencyMs)
  );
}

const isTranslationFinalEvent: Validator<TranslationFinalEvent> = (
  value
): value is TranslationFinalEvent => {
  if (
    !isRecord(value) ||
    !hasTranslationBase(value) ||
    value.type !== "translation.final"
  ) {
    return false;
  }
  const payload = value.payload;
  return (
    isRecord(payload) &&
    isLanguageCode(payload.sourceLanguage) &&
    isLanguageCode(payload.targetLanguage) &&
    isSizedString(payload.text, 1, 32_000) &&
    isConfidence(payload.confidence) &&
    isSizedString(payload.provider, 1, 128) &&
    isSizedString(payload.model, 1, 256) &&
    isBoundedArrayOf(
      payload.sourceSegments,
      isTranslationSourceCitation,
      1,
      8
    ) &&
    isBoundedArrayOf(
      payload.glossaryHits,
      isTranslationGlossaryEntry,
      0,
      128
    )
  );
};

const isTranslationFailedEvent: Validator<TranslationFailedEvent> = (
  value
): value is TranslationFailedEvent => {
  if (
    !isRecord(value) ||
    !hasTranslationBase(value) ||
    value.type !== "translation.failed"
  ) {
    return false;
  }
  const payload = value.payload;
  return (
    isRecord(payload) &&
    isLanguageCode(payload.sourceLanguage) &&
    isLanguageCode(payload.targetLanguage) &&
    isOneOf(
      payload.code,
      [
        "REMOTE_DISABLED",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_TIMEOUT",
        "INVALID_RESPONSE",
      ] as const
    ) &&
    isSizedString(payload.message, 1, 512) &&
    isBoolean(payload.retriable) &&
    isBoundedArrayOf(
      payload.sourceSegments,
      isTranslationSourceCitation,
      1,
      8
    )
  );
};

export const isTranslationEvent: Validator<TranslationEvent> = (
  value
): value is TranslationEvent =>
  isTranslationFinalEvent(value) || isTranslationFailedEvent(value);

export const isAudioUploadResult: Validator<AudioUploadResult> = (
  value
): value is AudioUploadResult =>
  isRecord(value) &&
  isNonEmptyString(value.sessionId) &&
  isString(value.audioPath) &&
  isNonNegativeInteger(value.bytes);

export const isTokenBalance: Validator<TokenBalance> = (
  value
): value is TokenBalance =>
  isRecord(value) &&
  isNonEmptyString(value.scopeId) &&
  isOneOf(value.scopeType, ["USER", "WORKSPACE"] as const) &&
  isInteger(value.balance) &&
  value.currency === "TOKENS" &&
  isNonEmptyString(value.updatedAt);

const isTokenLedgerEntry: Validator<TokenLedgerEntry> = (
  value
): value is TokenLedgerEntry =>
  isRecord(value) &&
  isNonEmptyString(value.id) &&
  isNonEmptyString(value.scopeId) &&
  isOneOf(value.scopeType, ["USER", "WORKSPACE"] as const) &&
  isNullableString(value.sessionId) &&
  isOneOf(
    value.spendType,
    [
      "ASR",
      "TRANSLATION",
      "SUMMARY",
      "AGENT_REASONING",
      "AGENT_QUERY",
      "EXPORT",
      "PURCHASE",
      "REFUND",
      "MANUAL_ADJUSTMENT",
      "ADMIN_ADJUSTMENT",
    ] as const
  ) &&
  isInteger(value.delta) &&
  isInteger(value.balanceAfter) &&
  isString(value.description) &&
  isNonEmptyString(value.createdAt);

export const isTokenLedger: Validator<TokenLedgerEntry[]> = (
  value
): value is TokenLedgerEntry[] => isArrayOf(value, isTokenLedgerEntry);

export const isConversationSession: Validator<ConversationSession> = (
  value
): value is ConversationSession =>
  isRecord(value) &&
  value.schema === "voxagent.conversation.session" &&
  value.schemaVersion === 1 &&
  isSizedString(value.id, 1, 160) &&
  isOneOf(value.mode, ["FACE_TO_FACE", "REMOTE_QR"] as const) &&
  isOneOf(value.status, ["WAITING", "ACTIVE", "ENDED"] as const) &&
  isLanguageCode(value.hostLanguage) &&
  isLanguageCode(value.guestLanguage) &&
  value.hostLanguage !== value.guestLanguage &&
  isInteger(value.participantCount) &&
  value.participantCount >= 1 &&
  value.participantCount <= 2 &&
  isDateTimeWithOffset(value.createdAt) &&
  isDateTimeWithOffset(value.expiresAt) &&
  isPositiveInteger(value.nextSequence);

const isConversationInvite: Validator<ConversationInvite> = (
  value
): value is ConversationInvite =>
  isRecord(value) &&
  isSizedString(value.inviteToken, 20, 512) &&
  isSizedString(value.qrPayload, 1, 1_024) &&
  value.qrPayload ===
    `voxagent://conversation/join#v=1&token=${encodeURIComponent(
      value.inviteToken
    )}` &&
  isDateTimeWithOffset(value.expiresAt);

export const isConversationSessionCreated: Validator<ConversationSessionCreated> = (
  value
): value is ConversationSessionCreated => {
  if (
    !isRecord(value) ||
    !isConversationSession(value.session) ||
    !isSizedString(value.hostAccessToken, 20, 512) ||
    !isNullableSizedString(value.localGuestAccessToken, 20, 512) ||
    !(value.invite === null || isConversationInvite(value.invite))
  ) {
    return false;
  }
  if (value.session.mode === "REMOTE_QR") {
    return (
      value.session.status === "WAITING" &&
      value.session.participantCount === 1 &&
      value.localGuestAccessToken === null &&
      value.invite !== null
    );
  }
  return (
    value.session.status === "ACTIVE" &&
    value.session.participantCount === 2 &&
    value.localGuestAccessToken !== null &&
    value.invite === null
  );
};

export const isConversationInviteClaimed: Validator<ConversationInviteClaimed> = (
  value
): value is ConversationInviteClaimed =>
  isRecord(value) &&
  isConversationSession(value.session) &&
  value.session.status === "ACTIVE" &&
  value.session.participantCount === 2 &&
  value.participantRole === "GUEST" &&
  isSizedString(value.accessToken, 20, 512);

export const isSpeechFinalEvent: Validator<SpeechFinalEvent> = (
  value
): value is SpeechFinalEvent => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, LIVE_SPEECH_EVENT_KEYS) ||
    value.schema !== "voxagent.speech.event" ||
    value.schemaVersion !== 1 ||
    value.type !== "transcript.final"
  ) {
    return false;
  }
  const payload = value.payload;
  return (
    isSizedString(value.eventId, 1, 128) &&
    isSizedString(value.streamId, 1, 128) &&
    isSizedString(value.sessionId, 1, 160) &&
    isPositiveInteger(value.sequence) &&
    isNonNegativeInteger(value.monotonicMs) &&
    isDateTimeWithOffset(value.emittedAt) &&
    isRecord(payload) &&
    isSizedString(payload.segmentId, 1, 128) &&
    isPositiveInteger(payload.revision) &&
    isSizedString(payload.text, 1, 32_000) &&
    isRecord(payload.language) &&
    isLanguageCode(payload.language.code) &&
    isConfidence(payload.language.confidence) &&
    isRecord(payload.speaker) &&
    isSizedString(payload.speaker.id, 1, 128) &&
    isNullableSizedString(payload.speaker.label, 1, 128) &&
    isNonNegativeInteger(payload.audioStartMs) &&
    isNonNegativeInteger(payload.audioEndMs) &&
    payload.audioEndMs >= payload.audioStartMs &&
    isConfidence(payload.confidence)
  );
};

const LIVE_SPEECH_EVENT_KEYS = [
  "schema",
  "schemaVersion",
  "eventId",
  "type",
  "streamId",
  "sessionId",
  "sequence",
  "monotonicMs",
  "emittedAt",
  "payload",
] as const;

const LIVE_SPEECH_PAYLOAD_KEYS = [
  "segmentId",
  "revision",
  "text",
  "language",
  "speaker",
  "audioStartMs",
  "audioEndMs",
  "confidence",
] as const;

export const isSpeechPartialEvent: Validator<SpeechPartialEvent> = (
  value
): value is SpeechPartialEvent => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, LIVE_SPEECH_EVENT_KEYS) ||
    value.schema !== "voxagent.speech.event" ||
    value.schemaVersion !== 1 ||
    value.type !== "transcript.partial" ||
    !isSizedString(value.eventId, 1, 128) ||
    !isSizedString(value.streamId, 1, 128) ||
    !isSizedString(value.sessionId, 1, 160) ||
    !isPositiveInteger(value.sequence) ||
    !isNonNegativeInteger(value.monotonicMs) ||
    !isDateTimeWithOffset(value.emittedAt) ||
    !isRecord(value.payload) ||
    !hasExactKeys(value.payload, LIVE_SPEECH_PAYLOAD_KEYS)
  ) {
    return false;
  }
  const payload = value.payload;
  return (
    isSizedString(payload.segmentId, 1, 128) &&
    isPositiveInteger(payload.revision) &&
    isSizedString(payload.text, 1, 32_000) &&
    isRecord(payload.language) &&
    hasExactKeys(payload.language, ["code", "confidence"]) &&
    isLanguageCode(payload.language.code) &&
    isConfidence(payload.language.confidence) &&
    isRecord(payload.speaker) &&
    hasExactKeys(payload.speaker, ["id", "label"]) &&
    isSizedString(payload.speaker.id, 1, 128) &&
    isNullableSizedString(payload.speaker.label, 1, 128) &&
    isNonNegativeInteger(payload.audioStartMs) &&
    isNonNegativeInteger(payload.audioEndMs) &&
    payload.audioEndMs >= payload.audioStartMs &&
    isConfidence(payload.confidence)
  );
};

export const isSpeechStreamCreated: Validator<SpeechStreamCreated> = (
  value
): value is SpeechStreamCreated =>
  isRecord(value) &&
  hasExactKeys(value, [
    "streamId",
    "resumeToken",
    "schema",
    "schemaVersion",
    "nextSequence",
  ]) &&
  isSizedString(value.streamId, 1, 128) &&
  isSizedString(value.resumeToken, 20, 512) &&
  value.schema === "voxagent.speech.event" &&
  value.schemaVersion === 1 &&
  isPositiveInteger(value.nextSequence);

export const isSpeechAudioChunkAck: Validator<SpeechAudioChunkAck> = (
  value
): value is SpeechAudioChunkAck =>
  isRecord(value) &&
  hasExactKeys(value, [
    "schema",
    "schemaVersion",
    "streamId",
    "chunkIndex",
    "acceptedBytes",
    "totalBytes",
    "durationMs",
    "nextChunkIndex",
  ]) &&
  value.schema === "voxagent.speech.audio-chunk" &&
  value.schemaVersion === 1 &&
  isSizedString(value.streamId, 1, 128) &&
  isNonNegativeInteger(value.chunkIndex) &&
  isPositiveInteger(value.acceptedBytes) &&
  value.acceptedBytes <= 256 * 1024 &&
  isPositiveInteger(value.totalBytes) &&
  value.totalBytes <= 6 * 1024 * 1024 &&
  value.totalBytes >= value.acceptedBytes &&
  isNonNegativeInteger(value.durationMs) &&
  value.durationMs <= 60_000 &&
  isPositiveInteger(value.nextChunkIndex) &&
  value.nextChunkIndex === value.chunkIndex + 1;

export const isSpeechTerminalEvent: Validator<SpeechTerminalEvent> = (
  value
): value is SpeechTerminalEvent => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "schema",
      "schemaVersion",
      "eventId",
      "streamId",
      "sessionId",
      "sequence",
      "type",
      "monotonicMs",
      "emittedAt",
      "payload",
    ]) ||
    value.schema !== "voxagent.speech.event" ||
    value.schemaVersion !== 1 ||
    !isSizedString(value.eventId, 1, 128) ||
    !isSizedString(value.streamId, 1, 128) ||
    !isNullableSizedString(value.sessionId, 1, 160) ||
    !isPositiveInteger(value.sequence) ||
    !isNonNegativeInteger(value.monotonicMs) ||
    !isDateTimeWithOffset(value.emittedAt) ||
    !isRecord(value.payload)
  ) {
    return false;
  }
  if (value.type === "stream.completed") {
    return (
      hasExactKeys(value.payload, ["reason"]) &&
      isSizedString(value.payload.reason, 1, 256)
    );
  }
  return (
    value.type === "stream.cancelled" &&
    hasExactKeys(value.payload, ["reason", "cancelledBy"]) &&
    isSizedString(value.payload.reason, 1, 256) &&
    isSizedString(value.payload.cancelledBy, 1, 128)
  );
};

const isSpeechStartedEvent: Validator<SpeechStartedEvent> = (
  value
): value is SpeechStartedEvent =>
  isRecord(value) &&
  hasExactKeys(value, [
    "schema",
    "schemaVersion",
    "eventId",
    "streamId",
    "sessionId",
    "sequence",
    "type",
    "monotonicMs",
    "emittedAt",
    "payload",
  ]) &&
  value.schema === "voxagent.speech.event" &&
  value.schemaVersion === 1 &&
  value.type === "stream.started" &&
  isSizedString(value.eventId, 1, 128) &&
  isSizedString(value.streamId, 1, 128) &&
  isNullableSizedString(value.sessionId, 1, 160) &&
  isPositiveInteger(value.sequence) &&
  isNonNegativeInteger(value.monotonicMs) &&
  isDateTimeWithOffset(value.emittedAt) &&
  isRecord(value.payload) &&
  hasExactKeys(value.payload, ["languageHints"]) &&
  Array.isArray(value.payload.languageHints) &&
  value.payload.languageHints.length <= 8 &&
  value.payload.languageHints.every(isLanguageCode);

const isSpeechErrorEvent: Validator<SpeechErrorEvent> = (
  value
): value is SpeechErrorEvent =>
  isRecord(value) &&
  hasExactKeys(value, [
    "schema",
    "schemaVersion",
    "eventId",
    "streamId",
    "sessionId",
    "sequence",
    "type",
    "monotonicMs",
    "emittedAt",
    "payload",
  ]) &&
  value.schema === "voxagent.speech.event" &&
  value.schemaVersion === 1 &&
  value.type === "stream.error" &&
  isSizedString(value.eventId, 1, 128) &&
  isSizedString(value.streamId, 1, 128) &&
  isNullableSizedString(value.sessionId, 1, 160) &&
  isPositiveInteger(value.sequence) &&
  isNonNegativeInteger(value.monotonicMs) &&
  isDateTimeWithOffset(value.emittedAt) &&
  isRecord(value.payload) &&
  hasExactKeys(value.payload, ["code", "message", "retriable"]) &&
  isSizedString(value.payload.code, 1, 80) &&
  isSizedString(value.payload.message, 1, 512) &&
  isBoolean(value.payload.retriable);

export const isSpeechEvent: Validator<SpeechEvent> = (
  value
): value is SpeechEvent =>
  isSpeechStartedEvent(value) ||
  isSpeechPartialEvent(value) ||
  isSpeechFinalEvent(value) ||
  isSpeechTerminalEvent(value) ||
  isSpeechErrorEvent(value);

export const isSpeechReplay: Validator<SpeechReplay> = (
  value
): value is SpeechReplay => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["streamId", "events", "nextSequence", "terminal"]) ||
    !isSizedString(value.streamId, 1, 128) ||
    !Array.isArray(value.events) ||
    value.events.length > 10_000 ||
    !value.events.every(isSpeechEvent) ||
    !isPositiveInteger(value.nextSequence) ||
    !isBoolean(value.terminal)
  ) {
    return false;
  }
  let previousSequence = 0;
  for (const event of value.events) {
    if (
      event.streamId !== value.streamId ||
      event.sequence <= previousSequence ||
      event.sequence >= value.nextSequence
    ) {
      return false;
    }
    previousSequence = event.sequence;
  }
  const last = value.events.at(-1);
  const lastIsTerminal =
    last !== undefined &&
    ["stream.completed", "stream.cancelled", "stream.error"].includes(last.type);
  return value.terminal ? last === undefined || lastIsTerminal : !lastIsTerminal;
};

function translationMatchesSpeech(
  speech: SpeechFinalEvent,
  translation: TranslationFinalEvent
): boolean {
  if (translation.payload.sourceSegments.length !== 1) return false;
  const citation = translation.payload.sourceSegments[0];
  return (
    translation.payload.sourceLanguage === speech.payload.language.code &&
    citation.streamId === speech.streamId &&
    citation.segmentId === speech.payload.segmentId &&
    citation.revision === speech.payload.revision &&
    citation.audioStartMs === speech.payload.audioStartMs &&
    citation.audioEndMs === speech.payload.audioEndMs
  );
}

export const isConversationTurn: Validator<ConversationTurn> = (
  value
): value is ConversationTurn => {
  if (
    !isRecord(value) ||
    value.schema !== "voxagent.conversation.turn" ||
    value.schemaVersion !== 1 ||
    value.type !== "conversation.turn" ||
    !isSizedString(value.eventId, 1, 128) ||
    !isSizedString(value.sessionId, 1, 160) ||
    !isPositiveInteger(value.sequence) ||
    !isOneOf(value.participantRole, ["HOST", "GUEST"] as const) ||
    !isSizedString(value.clientEventId, 1, 160) ||
    !isSpeechFinalEvent(value.speechEvent) ||
    !isTranslationFinalEvent(value.translationEvent) ||
    !isDateTimeWithOffset(value.createdAt)
  ) {
    return false;
  }
  return (
    value.speechEvent.sessionId === value.sessionId &&
    translationMatchesSpeech(value.speechEvent, value.translationEvent)
  );
};

export const isConversationReplay: Validator<ConversationReplay> = (
  value
): value is ConversationReplay =>
  isRecord(value) &&
  isSizedString(value.sessionId, 1, 160) &&
  isBoundedArrayOf(value.turns, isConversationTurn, 0, 1_000) &&
  isPositiveInteger(value.nextSequence) &&
  isOneOf(value.status, ["WAITING", "ACTIVE"] as const);

const CONVERSATION_LIVE_DRAFT_KEYS = [
  "schema",
  "schemaVersion",
  "type",
  "sessionId",
  "participantRole",
  "streamId",
  "segmentId",
  "revision",
  "sourceLanguage",
  "targetLanguage",
  "speechEvent",
  "updatedAt",
] as const;

export const isConversationLiveDraft: Validator<ConversationLiveDraft> = (
  value
): value is ConversationLiveDraft => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, CONVERSATION_LIVE_DRAFT_KEYS) ||
    value.schema !== "voxagent.conversation.live" ||
    value.schemaVersion !== 1 ||
    value.type !== "conversation.live" ||
    !isSizedString(value.sessionId, 1, 160) ||
    !isOneOf(value.participantRole, ["HOST", "GUEST"] as const) ||
    !isSizedString(value.streamId, 1, 128) ||
    !isSizedString(value.segmentId, 1, 128) ||
    !isPositiveInteger(value.revision) ||
    !isLanguageCode(value.sourceLanguage) ||
    !isLanguageCode(value.targetLanguage) ||
    value.sourceLanguage === value.targetLanguage ||
    !isSpeechPartialEvent(value.speechEvent) ||
    !isDateTimeWithOffset(value.updatedAt)
  ) {
    return false;
  }
  return (
    value.speechEvent.sessionId === value.sessionId &&
    value.speechEvent.streamId === value.streamId &&
    value.speechEvent.payload.segmentId === value.segmentId &&
    value.speechEvent.payload.revision === value.revision &&
    value.speechEvent.payload.language.code === value.sourceLanguage
  );
};

export const isConversationLiveSnapshot: Validator<ConversationLiveSnapshot> = (
  value
): value is ConversationLiveSnapshot => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["sessionId", "host", "guest", "status"]) ||
    !isSizedString(value.sessionId, 1, 160) ||
    !(value.host === null || isConversationLiveDraft(value.host)) ||
    !(value.guest === null || isConversationLiveDraft(value.guest)) ||
    value.status !== "ACTIVE"
  ) {
    return false;
  }
  return (
    (value.host === null ||
      (value.host.sessionId === value.sessionId &&
        value.host.participantRole === "HOST")) &&
    (value.guest === null ||
      (value.guest.sessionId === value.sessionId &&
        value.guest.participantRole === "GUEST"))
  );
};
