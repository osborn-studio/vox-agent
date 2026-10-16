import { describe, expect, it, vi } from "vitest";

import type {
  ConversationLiveDraft,
  ConversationResult,
  ConversationTurn,
  SpeechFinalEvent,
  SpeechPartialEvent,
  SpeechReplay,
  TranslationEvent,
  TranslationFinalEvent,
  TranslationRequest,
  WriteResult,
} from "../api/client";
import {
  createConversationSpeechEventAdapter,
  type ConversationSpeechEventApi,
} from "./speechEvents";

const SESSION_ID = "conv_123";
const STREAM_ID = "speech_123";
const SPEECH_TOKEN = "speech-token-with-enough-entropy-123";
const PARTICIPANT_TOKEN = "participant-token-with-enough-entropy-123";

function writeSuccess<T>(data: T): WriteResult<T> {
  return { ok: true, offline: false, source: "live", data };
}

function conversationSuccess<T>(data: T): ConversationResult<T> {
  return { ok: true, offline: false, data };
}

function transcript(
  type: "transcript.partial" | "transcript.final",
  sequence: number,
  revision: number,
  text: string
): SpeechPartialEvent | SpeechFinalEvent {
  return {
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: `speech-event-${sequence}`,
    streamId: STREAM_ID,
    sessionId: SESSION_ID,
    sequence,
    type,
    monotonicMs: sequence * 1_000,
    emittedAt: `2026-07-26T12:00:0${Math.min(sequence, 9)}Z`,
    payload: {
      segmentId: "asr-segment-1",
      revision,
      text,
      language: { code: "en", confidence: 0.96 },
      speaker: { id: "speaker_1", label: null },
      audioStartMs: 0,
      audioEndMs: 1_000,
      confidence: 0.94,
    },
  };
}

function replay(
  events: SpeechReplay["events"],
  nextSequence: number,
  terminal = false
): SpeechReplay {
  return {
    streamId: STREAM_ID,
    events,
    nextSequence,
    terminal,
  };
}

function finalTranslation(speech: SpeechFinalEvent): TranslationFinalEvent {
  return {
    schema: "voxagent.translation.event",
    schemaVersion: 1,
    eventId: `translation-${speech.eventId}`,
    translationId: `translation-id-${speech.sequence}`,
    type: "translation.final",
    emittedAt: "2026-07-26T12:00:09Z",
    latencyMs: 80,
    payload: {
      sourceLanguage: "en",
      targetLanguage: "zh-CN",
      text: "你好",
      confidence: 0.92,
      provider: "local",
      model: "test-translation",
      sourceSegments: [
        {
          streamId: speech.streamId,
          segmentId: speech.payload.segmentId,
          revision: speech.payload.revision,
          audioStartMs: speech.payload.audioStartMs,
          audioEndMs: speech.payload.audioEndMs,
        },
      ],
      glossaryHits: [],
    },
  };
}

function liveDraft(speech: SpeechPartialEvent): ConversationLiveDraft {
  return {
    schema: "voxagent.conversation.live",
    schemaVersion: 1,
    type: "conversation.live",
    sessionId: SESSION_ID,
    participantRole: "HOST",
    streamId: STREAM_ID,
    segmentId: speech.payload.segmentId,
    revision: speech.payload.revision,
    sourceLanguage: "en",
    targetLanguage: "zh-CN",
    speechEvent: speech,
    updatedAt: "2026-07-26T12:00:09Z",
  };
}

function turn(
  speech: SpeechFinalEvent,
  translation: TranslationFinalEvent,
  clientEventId: string
): ConversationTurn {
  return {
    schema: "voxagent.conversation.turn",
    schemaVersion: 1,
    type: "conversation.turn",
    eventId: "conversation-turn-1",
    sessionId: SESSION_ID,
    sequence: 1,
    participantRole: "HOST",
    clientEventId,
    speechEvent: speech,
    translationEvent: translation,
    createdAt: "2026-07-26T12:00:10Z",
  };
}

function apiMock(replays: SpeechReplay[]): ConversationSpeechEventApi {
  return {
    replaySpeechEvents: vi.fn(async () =>
      writeSuccess(replays.shift() ?? replay([], 1))
    ),
    publishConversationLive: vi.fn(async (_sessionId, _token, speech) =>
      conversationSuccess(liveDraft(speech))
    ),
    translateSegments: vi.fn(
      async (request: TranslationRequest): Promise<WriteResult<TranslationEvent>> => {
        const speech = transcript(
          "transcript.final",
          4,
          request.segments[0].revision,
          "Hello"
        ) as SpeechFinalEvent;
        return writeSuccess(finalTranslation(speech));
      }
    ),
    publishConversationTurn: vi.fn(
      async (_sessionId, _token, input) =>
        conversationSuccess(
          turn(input.speechEvent, input.translationEvent, input.clientEventId)
        )
    ),
  };
}

function adapter(api: ConversationSpeechEventApi) {
  return createConversationSpeechEventAdapter(
    {
      sessionId: SESSION_ID,
      participantRole: "HOST",
      conversationAccessToken: PARTICIPANT_TOKEN,
      streamId: STREAM_ID,
      streamResumeToken: SPEECH_TOKEN,
      sourceLanguage: "en",
      targetLanguage: "zh-CN",
      allowRemoteTranslation: false,
    },
    api
  );
}

describe("conversation speech-event adapter", () => {
  it("publishes monotonic live drafts then one cited idempotent final turn", async () => {
    const partial1 = transcript(
      "transcript.partial",
      2,
      1,
      "Hel"
    ) as SpeechPartialEvent;
    const partial2 = transcript(
      "transcript.partial",
      3,
      2,
      "Hello"
    ) as SpeechPartialEvent;
    const final = transcript(
      "transcript.final",
      4,
      2,
      "Hello"
    ) as SpeechFinalEvent;
    const api = apiMock([
      replay([partial1], 3),
      replay([partial2], 4),
      replay(
        [
          final,
          {
            schema: "voxagent.speech.event",
            schemaVersion: 1,
            eventId: "completed-5",
            streamId: STREAM_ID,
            sessionId: SESSION_ID,
            sequence: 5,
            type: "stream.completed",
            monotonicMs: 5_000,
            emittedAt: "2026-07-26T12:00:05Z",
            payload: { reason: "input_finished" },
          },
        ],
        6,
        true
      ),
    ]);
    const subject = adapter(api);

    expect(await subject.poll()).toMatchObject({
      ok: true,
      cursor: 2,
      publishedDrafts: 1,
      publishedTurns: 0,
    });
    expect(await subject.poll()).toMatchObject({
      ok: true,
      cursor: 3,
      publishedDrafts: 1,
      publishedTurns: 0,
    });
    expect(await subject.poll()).toEqual({
      ok: true,
      cursor: 5,
      publishedDrafts: 0,
      publishedTurns: 1,
      terminal: "stream.completed",
    });

    expect(api.publishConversationLive).toHaveBeenNthCalledWith(
      1,
      SESSION_ID,
      PARTICIPANT_TOKEN,
      partial1
    );
    expect(api.publishConversationLive).toHaveBeenNthCalledWith(
      2,
      SESSION_ID,
      PARTICIPANT_TOKEN,
      partial2
    );
    expect(api.translateSegments).toHaveBeenCalledWith(
      {
        sourceLanguage: "en",
        targetLanguage: "zh-CN",
        segments: [
          {
            streamId: STREAM_ID,
            segmentId: final.payload.segmentId,
            revision: final.payload.revision,
          },
        ],
        glossary: [],
        allowRemote: false,
        idempotencyKey: `mobile-translation:${final.eventId}`,
      },
      SPEECH_TOKEN
    );
    expect(api.publishConversationTurn).toHaveBeenCalledWith(
      SESSION_ID,
      PARTICIPANT_TOKEN,
      expect.objectContaining({
        clientEventId: `mobile-final:${final.eventId}`,
        speechEvent: final,
      })
    );
  });

  it("cold replay skips obsolete partials and uses stable final idempotency keys", async () => {
    const partial = transcript(
      "transcript.partial",
      2,
      1,
      "Old draft"
    ) as SpeechPartialEvent;
    const final = transcript(
      "transcript.final",
      3,
      2,
      "Final words"
    ) as SpeechFinalEvent;
    const firstApi = apiMock([replay([partial, final], 4)]);
    const secondApi = apiMock([replay([partial, final], 4)]);

    await adapter(firstApi).poll();
    await adapter(secondApi).poll();

    expect(firstApi.publishConversationLive).not.toHaveBeenCalled();
    expect(secondApi.publishConversationLive).not.toHaveBeenCalled();
    const firstTranslationRequest = vi.mocked(firstApi.translateSegments).mock
      .calls[0][0];
    const secondTranslationRequest = vi.mocked(secondApi.translateSegments).mock
      .calls[0][0];
    expect(firstTranslationRequest.idempotencyKey).toBe(
      secondTranslationRequest.idempotencyKey
    );
    expect(
      vi.mocked(firstApi.publishConversationTurn).mock.calls[0][2].clientEventId
    ).toBe(
      vi.mocked(secondApi.publishConversationTurn).mock.calls[0][2].clientEventId
    );
  });

  it("keeps the final cursor retryable when translation fails", async () => {
    const final = transcript(
      "transcript.final",
      2,
      1,
      "Hello"
    ) as SpeechFinalEvent;
    const api = apiMock([replay([final], 3), replay([final], 3)]);
    vi.mocked(api.translateSegments).mockResolvedValue(
      writeSuccess({
        schema: "voxagent.translation.event",
        schemaVersion: 1,
        eventId: "translation-failed-1",
        translationId: "translation-id-failed-1",
        type: "translation.failed",
        emittedAt: "2026-07-26T12:00:03Z",
        latencyMs: 50,
        payload: {
          sourceLanguage: "en",
          targetLanguage: "zh-CN",
          code: "PROVIDER_UNAVAILABLE",
          message: "Provider unavailable.",
          retriable: true,
          sourceSegments: [
            {
              streamId: STREAM_ID,
              segmentId: final.payload.segmentId,
              revision: final.payload.revision,
              audioStartMs: final.payload.audioStartMs,
              audioEndMs: final.payload.audioEndMs,
            },
          ],
        },
      })
    );
    const subject = adapter(api);

    expect(await subject.poll()).toEqual({
      ok: false,
      cursor: 0,
      stage: "translation",
      code: "PROVIDER_UNAVAILABLE",
      retriable: true,
      offline: false,
    });
    expect(await subject.poll()).toMatchObject({
      ok: false,
      cursor: 0,
      stage: "translation",
    });
    expect(api.translateSegments).toHaveBeenCalledTimes(2);
    expect(api.publishConversationTurn).not.toHaveBeenCalled();
  });

  it("rejects cross-session speech before publishing or translating", async () => {
    const foreign = {
      ...(transcript("transcript.partial", 2, 1, "Foreign") as SpeechPartialEvent),
      sessionId: "conv_foreign",
    };
    const api = apiMock([replay([foreign], 3)]);
    const subject = adapter(api);

    expect(await subject.poll()).toEqual({
      ok: false,
      cursor: 0,
      stage: "speech",
      code: "INVALID_RESPONSE",
      retriable: false,
      offline: false,
    });
    expect(api.publishConversationLive).not.toHaveBeenCalled();
    expect(api.translateSegments).not.toHaveBeenCalled();
  });

  it("keeps both capabilities in closure memory and out of serialization", () => {
    const subject = adapter(apiMock([]));
    const serialized = JSON.stringify(subject);

    expect(serialized).not.toContain(SPEECH_TOKEN);
    expect(serialized).not.toContain(PARTICIPANT_TOKEN);
    expect(Object.keys(subject)).toEqual(["poll", "getCursor"]);
  });

  it("rejects an unbound start event and an oversized glossary before use", async () => {
    const unboundStart: SpeechReplay["events"][number] = {
      schema: "voxagent.speech.event",
      schemaVersion: 1,
      eventId: "speech-start-1",
      streamId: STREAM_ID,
      sessionId: null,
      sequence: 1,
      type: "stream.started",
      monotonicMs: 0,
      emittedAt: "2026-07-26T12:00:00Z",
      payload: { languageHints: ["en"] },
    };
    const api = apiMock([replay([unboundStart], 2)]);

    expect(await adapter(api).poll()).toEqual({
      ok: false,
      cursor: 0,
      stage: "speech",
      code: "INVALID_RESPONSE",
      retriable: false,
      offline: false,
    });
    expect(() =>
      createConversationSpeechEventAdapter(
        {
          sessionId: SESSION_ID,
          participantRole: "HOST",
          conversationAccessToken: PARTICIPANT_TOKEN,
          streamId: STREAM_ID,
          streamResumeToken: SPEECH_TOKEN,
          sourceLanguage: "en",
          targetLanguage: "zh-CN",
          allowRemoteTranslation: false,
          glossary: Array.from({ length: 129 }, () => ({
            sourceText: "source",
            targetText: "target",
          })),
        },
        api
      )
    ).toThrow("invalid conversation speech adapter configuration");
  });
});
