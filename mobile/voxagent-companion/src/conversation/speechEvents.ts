import {
  publishConversationLive,
  publishConversationTurn,
  replaySpeechEvents,
  translateSegments,
  type ApiFailure,
  type ApiFailureCode,
  type ConversationLiveDraft,
  type ConversationParticipantRole,
  type ConversationResult,
  type ConversationTurn,
  type SpeechEvent,
  type SpeechFinalEvent,
  type SpeechPartialEvent,
  type SpeechReplay,
  type TranslationEvent,
  type TranslationFinalEvent,
  type TranslationRequest,
  type WriteResult,
} from "../api/client";

export interface ConversationSpeechEventApi {
  replaySpeechEvents(
    streamId: string,
    resumeToken: string,
    afterSequence: number
  ): Promise<WriteResult<SpeechReplay>>;
  publishConversationLive(
    sessionId: string,
    accessToken: string,
    speechEvent: SpeechPartialEvent
  ): Promise<ConversationResult<ConversationLiveDraft>>;
  translateSegments(
    request: TranslationRequest,
    streamToken: string
  ): Promise<WriteResult<TranslationEvent>>;
  publishConversationTurn(
    sessionId: string,
    accessToken: string,
    input: {
      clientEventId: string;
      speechEvent: SpeechFinalEvent;
      translationEvent: TranslationFinalEvent;
    }
  ): Promise<ConversationResult<ConversationTurn>>;
}

export interface ConversationSpeechEventAdapterConfig {
  sessionId: string;
  participantRole: ConversationParticipantRole;
  conversationAccessToken: string;
  streamId: string;
  streamResumeToken: string;
  sourceLanguage: string;
  targetLanguage: string;
  allowRemoteTranslation: boolean;
  glossary?: TranslationRequest["glossary"];
  initialAfterSequence?: number;
}

export type ConversationSpeechSyncResult =
  | {
      ok: true;
      cursor: number;
      publishedDrafts: number;
      publishedTurns: number;
      terminal: "stream.completed" | "stream.cancelled" | null;
    }
  | {
      ok: false;
      cursor: number;
      stage: "speech" | "live" | "translation" | "turn";
      code: string;
      retriable: boolean;
      offline: boolean;
    };

export interface ConversationSpeechEventAdapter {
  poll(): Promise<ConversationSpeechSyncResult>;
  getCursor(): number;
}

const defaultApi: ConversationSpeechEventApi = {
  replaySpeechEvents,
  publishConversationLive,
  translateSegments,
  publishConversationTurn,
};

export function createConversationSpeechEventAdapter(
  config: ConversationSpeechEventAdapterConfig,
  api: ConversationSpeechEventApi = defaultApi
): ConversationSpeechEventAdapter {
  validateConfig(config);
  let cursor = config.initialAfterSequence ?? 0;
  let inFlight: Promise<ConversationSpeechSyncResult> | null = null;
  const finalizedSegments = new Set<string>();
  const glossary = (config.glossary ?? []).map((entry) => ({ ...entry }));

  const run = async (): Promise<ConversationSpeechSyncResult> => {
    const replayResult = await api.replaySpeechEvents(
      config.streamId,
      config.streamResumeToken,
      cursor
    );
    if (!replayResult.ok) return apiFailure("speech", cursor, replayResult);
    const replay = replayResult.data;
    if (replay.streamId !== config.streamId) {
      return invalidResponse("speech", cursor);
    }

    const finalKeys = new Set(
      replay.events
        .filter(
          (event): event is SpeechFinalEvent =>
            event.type === "transcript.final"
        )
        .map(segmentKey)
    );
    let publishedDrafts = 0;
    let publishedTurns = 0;
    let terminal: "stream.completed" | "stream.cancelled" | null = null;

    for (const event of replay.events) {
      if (!eventMatchesStream(config, event)) {
        return invalidResponse("speech", cursor);
      }
      if (event.type === "stream.started") {
        cursor = event.sequence;
        continue;
      }
      if (event.type === "transcript.partial") {
        const key = segmentKey(event);
        if (finalKeys.has(key) || finalizedSegments.has(key)) {
          cursor = event.sequence;
          continue;
        }
        const result = await api.publishConversationLive(
          config.sessionId,
          config.conversationAccessToken,
          event
        );
        if (!result.ok) return apiFailure("live", cursor, result);
        if (!draftMatches(config, event, result.data)) {
          return invalidResponse("live", cursor);
        }
        publishedDrafts += 1;
        cursor = event.sequence;
        continue;
      }
      if (event.type === "transcript.final") {
        const translation = await api.translateSegments(
          {
            sourceLanguage: config.sourceLanguage,
            targetLanguage: config.targetLanguage,
            segments: [
              {
                streamId: event.streamId,
                segmentId: event.payload.segmentId,
                revision: event.payload.revision,
              },
            ],
            glossary,
            allowRemote: config.allowRemoteTranslation,
            idempotencyKey: `mobile-translation:${event.eventId}`,
          },
          config.streamResumeToken
        );
        if (!translation.ok) {
          return apiFailure("translation", cursor, translation);
        }
        if (translation.data.type === "translation.failed") {
          return {
            ok: false,
            cursor,
            stage: "translation",
            code: translation.data.payload.code,
            retriable: translation.data.payload.retriable,
            offline: false,
          };
        }
        if (!translationMatches(config, event, translation.data)) {
          return invalidResponse("translation", cursor);
        }
        const clientEventId = `mobile-final:${event.eventId}`;
        const published = await api.publishConversationTurn(
          config.sessionId,
          config.conversationAccessToken,
          {
            clientEventId,
            speechEvent: event,
            translationEvent: translation.data,
          }
        );
        if (!published.ok) return apiFailure("turn", cursor, published);
        if (
          !turnMatches(
            config,
            event,
            translation.data,
            clientEventId,
            published.data
          )
        ) {
          return invalidResponse("turn", cursor);
        }
        finalizedSegments.add(segmentKey(event));
        publishedTurns += 1;
        cursor = event.sequence;
        continue;
      }
      cursor = event.sequence;
      if (event.type === "stream.error") {
        return {
          ok: false,
          cursor,
          stage: "speech",
          code: event.payload.code,
          retriable: event.payload.retriable,
          offline: false,
        };
      }
      terminal = event.type;
    }
    return {
      ok: true,
      cursor,
      publishedDrafts,
      publishedTurns,
      terminal,
    };
  };

  return {
    poll() {
      if (inFlight !== null) return inFlight;
      inFlight = run().finally(() => {
        inFlight = null;
      });
      return inFlight;
    },
    getCursor() {
      return cursor;
    },
  };
}

function validateConfig(config: ConversationSpeechEventAdapterConfig): void {
  const glossary = config.glossary ?? [];
  if (
    !isOpaque(config.sessionId, 1, 160) ||
    !isOpaque(config.conversationAccessToken, 20, 512) ||
    !isOpaque(config.streamId, 1, 128) ||
    !isOpaque(config.streamResumeToken, 20, 512) ||
    !["HOST", "GUEST"].includes(config.participantRole) ||
    !isLanguage(config.sourceLanguage) ||
    !isLanguage(config.targetLanguage) ||
    config.sourceLanguage === config.targetLanguage ||
    typeof config.allowRemoteTranslation !== "boolean" ||
    glossary.length > 128 ||
    glossary.some(
      (entry) =>
        !isTrimmedText(entry.sourceText, 1, 256) ||
        !isTrimmedText(entry.targetText, 1, 256)
    ) ||
    (config.initialAfterSequence !== undefined &&
      (!Number.isSafeInteger(config.initialAfterSequence) ||
        config.initialAfterSequence < 0))
  ) {
    throw new Error("invalid conversation speech adapter configuration");
  }
}

function eventMatchesStream(
  config: ConversationSpeechEventAdapterConfig,
  event: SpeechEvent
): boolean {
  if (event.streamId !== config.streamId) return false;
  if (event.type === "stream.started") {
    return event.sessionId === config.sessionId;
  }
  if (event.sessionId !== config.sessionId) return false;
  if (
    event.type === "transcript.partial" ||
    event.type === "transcript.final"
  ) {
    return event.payload.language.code === config.sourceLanguage;
  }
  return true;
}

function segmentKey(event: SpeechPartialEvent | SpeechFinalEvent): string {
  return `${event.streamId}\u0000${event.payload.segmentId}`;
}

function draftMatches(
  config: ConversationSpeechEventAdapterConfig,
  speech: SpeechPartialEvent,
  draft: ConversationLiveDraft
): boolean {
  return (
    draft.sessionId === config.sessionId &&
    draft.participantRole === config.participantRole &&
    draft.streamId === speech.streamId &&
    draft.segmentId === speech.payload.segmentId &&
    draft.revision === speech.payload.revision &&
    draft.sourceLanguage === config.sourceLanguage &&
    draft.targetLanguage === config.targetLanguage &&
    draft.speechEvent.eventId === speech.eventId
  );
}

function translationMatches(
  config: ConversationSpeechEventAdapterConfig,
  speech: SpeechFinalEvent,
  translation: TranslationFinalEvent
): boolean {
  if (
    translation.payload.sourceLanguage !== config.sourceLanguage ||
    translation.payload.targetLanguage !== config.targetLanguage ||
    translation.payload.sourceSegments.length !== 1
  ) {
    return false;
  }
  const citation = translation.payload.sourceSegments[0];
  return (
    citation.streamId === speech.streamId &&
    citation.segmentId === speech.payload.segmentId &&
    citation.revision === speech.payload.revision &&
    citation.audioStartMs === speech.payload.audioStartMs &&
    citation.audioEndMs === speech.payload.audioEndMs
  );
}

function turnMatches(
  config: ConversationSpeechEventAdapterConfig,
  speech: SpeechFinalEvent,
  translation: TranslationFinalEvent,
  clientEventId: string,
  turn: ConversationTurn
): boolean {
  return (
    turn.sessionId === config.sessionId &&
    turn.participantRole === config.participantRole &&
    turn.clientEventId === clientEventId &&
    turn.speechEvent.eventId === speech.eventId &&
    turn.translationEvent.eventId === translation.eventId
  );
}

function apiFailure(
  stage: "speech" | "live" | "translation" | "turn",
  cursor: number,
  failure: ApiFailure
): ConversationSpeechSyncResult {
  return {
    ok: false,
    cursor,
    stage,
    code: failure.code,
    retriable:
      failure.offline ||
      (failure.status !== undefined && failure.status >= 500),
    offline: failure.offline,
  };
}

function invalidResponse(
  stage: "speech" | "live" | "translation" | "turn",
  cursor: number
): ConversationSpeechSyncResult {
  return {
    ok: false,
    cursor,
    stage,
    code: "INVALID_RESPONSE" satisfies ApiFailureCode,
    retriable: false,
    offline: false,
  };
}

function isOpaque(value: unknown, minimum: number, maximum: number): boolean {
  return (
    typeof value === "string" &&
    value.length >= minimum &&
    value.length <= maximum &&
    value.trim() === value &&
    !/\s/.test(value)
  );
}

function isLanguage(value: unknown): boolean {
  return (
    typeof value === "string" &&
    /^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(value)
  );
}

function isTrimmedText(
  value: unknown,
  minimum: number,
  maximum: number
): boolean {
  return (
    typeof value === "string" &&
    value.length >= minimum &&
    value.length <= maximum &&
    value.trim() === value
  );
}
