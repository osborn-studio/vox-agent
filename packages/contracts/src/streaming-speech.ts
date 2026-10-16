import { z } from "zod";

export const SPEECH_EVENT_SCHEMA = "voxagent.speech.event" as const;
export const SPEECH_EVENT_SCHEMA_VERSION = 1 as const;
export const SPEECH_AUDIO_CHUNK_SCHEMA = "voxagent.speech.audio-chunk" as const;
export const SPEECH_AUDIO_CHUNK_SCHEMA_VERSION = 1 as const;

export const speechLanguageCodeSchema = z
  .string()
  .min(2)
  .max(35)
  .regex(/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/, "language must be a BCP 47 tag");

export const speechLanguageSchema = z.object({
  code: speechLanguageCodeSchema,
  confidence: z.number().min(0).max(1)
}).strict();

export const speechSpeakerSchema = z.object({
  id: z.string().min(1).max(128),
  label: z.string().min(1).max(128).nullable()
}).strict();

const speechTranscriptPayloadObjectSchema = z.object({
  segmentId: z.string().min(1).max(128),
  revision: z.number().int().positive(),
  text: z.string().min(1).max(32_000),
  language: speechLanguageSchema,
  speaker: speechSpeakerSchema,
  audioStartMs: z.number().int().nonnegative(),
  audioEndMs: z.number().int().nonnegative(),
  confidence: z.number().min(0).max(1)
}).strict();

export const speechTranscriptPayloadSchema = speechTranscriptPayloadObjectSchema.refine(
  (payload) => payload.audioEndMs >= payload.audioStartMs,
  {
    message: "audioEndMs must be greater than or equal to audioStartMs",
    path: ["audioEndMs"]
  }
);

const speechEventBaseSchema = z.object({
  schema: z.literal(SPEECH_EVENT_SCHEMA),
  schemaVersion: z.literal(SPEECH_EVENT_SCHEMA_VERSION),
  eventId: z.string().min(1).max(128),
  streamId: z.string().min(1).max(128),
  sessionId: z.string().min(1).max(160).nullable(),
  sequence: z.number().int().positive(),
  monotonicMs: z.number().int().nonnegative(),
  emittedAt: z.string().datetime({ offset: true })
}).strict();

const speechStreamStartedEventSchema = speechEventBaseSchema.extend({
  type: z.literal("stream.started"),
  payload: z.object({
    languageHints: z.array(speechLanguageCodeSchema).max(8)
  })
});

export const speechPartialEventSchema = speechEventBaseSchema.extend({
  type: z.literal("transcript.partial"),
  payload: speechTranscriptPayloadSchema
});

export const speechFinalEventSchema = speechEventBaseSchema.extend({
  type: z.literal("transcript.final"),
  payload: speechTranscriptPayloadSchema
});

export const speechCancelledEventSchema = speechEventBaseSchema.extend({
  type: z.literal("stream.cancelled"),
  payload: z.object({
    reason: z.string().min(1).max(256),
    cancelledBy: z.string().min(1).max(128)
  })
});

export const speechCompletedEventSchema = speechEventBaseSchema.extend({
  type: z.literal("stream.completed"),
  payload: z.object({
    reason: z.string().min(1).max(256)
  })
});

const speechErrorEventSchema = speechEventBaseSchema.extend({
  type: z.literal("stream.error"),
  payload: z.object({
    code: z.string().min(1).max(80),
    message: z.string().min(1).max(512),
    retriable: z.boolean()
  })
});

export const speechTerminalEventSchema = z.discriminatedUnion("type", [
  speechCancelledEventSchema,
  speechCompletedEventSchema
]);

export const speechEventSchema = z.discriminatedUnion("type", [
  speechStreamStartedEventSchema,
  speechPartialEventSchema,
  speechFinalEventSchema,
  speechCancelledEventSchema,
  speechCompletedEventSchema,
  speechErrorEventSchema
]);

export const speechStreamCreatedSchema = z.object({
  streamId: z.string().min(1),
  resumeToken: z.string().min(20),
  schema: z.literal(SPEECH_EVENT_SCHEMA),
  schemaVersion: z.literal(SPEECH_EVENT_SCHEMA_VERSION),
  nextSequence: z.number().int().positive()
}).strict();

export const speechAudioChunkAckSchema = z
  .object({
    schema: z.literal(SPEECH_AUDIO_CHUNK_SCHEMA),
    schemaVersion: z.literal(SPEECH_AUDIO_CHUNK_SCHEMA_VERSION),
    streamId: z.string().min(1).max(128),
    chunkIndex: z.number().int().nonnegative(),
    acceptedBytes: z.number().int().positive().max(256 * 1024),
    totalBytes: z.number().int().positive().max(6 * 1024 * 1024),
    durationMs: z.number().int().nonnegative().max(60_000),
    nextChunkIndex: z.number().int().positive()
  })
  .strict()
  .superRefine((ack, context) => {
    if (ack.nextChunkIndex !== ack.chunkIndex + 1) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "nextChunkIndex must acknowledge exactly one ordered chunk",
        path: ["nextChunkIndex"]
      });
    }
    if (ack.totalBytes < ack.acceptedBytes) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "totalBytes must include acceptedBytes",
        path: ["totalBytes"]
      });
    }
  });

export const speechReplaySchema = z
  .object({
    streamId: z.string().min(1).max(128),
    events: z.array(speechEventSchema).max(10_000),
    nextSequence: z.number().int().positive(),
    terminal: z.boolean()
  })
  .strict()
  .superRefine((replay, context) => {
    let previousSequence = 0;
    for (const [index, event] of replay.events.entries()) {
      if (event.streamId !== replay.streamId) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "event streamId must match replay streamId",
          path: ["events", index, "streamId"]
        });
      }
      if (event.sequence <= previousSequence || event.sequence >= replay.nextSequence) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "event sequences must increase and precede nextSequence",
          path: ["events", index, "sequence"]
        });
      }
      previousSequence = event.sequence;
    }
    const last = replay.events.at(-1);
    const lastIsTerminal =
      last !== undefined &&
      ["stream.completed", "stream.cancelled", "stream.error"].includes(last.type);
    if (replay.terminal ? last !== undefined && !lastIsTerminal : lastIsTerminal) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "terminal must match the last replay event",
        path: ["terminal"]
      });
    }
  });

export const createSpeechStreamRequestSchema = z
  .object({
    sessionId: z.string().min(1).max(128).nullable().optional(),
    languageHints: z.array(speechLanguageCodeSchema).max(8).default([])
  })
  .strict();

export const speechTranscriptRequestSchema = speechTranscriptPayloadObjectSchema
  .extend({
    language: speechLanguageSchema.strict(),
    speaker: speechSpeakerSchema.strict(),
    monotonicMs: z.number().int().nonnegative(),
    idempotencyKey: z.string().min(1).max(160)
  })
  .strict()
  .refine((payload) => payload.audioEndMs >= payload.audioStartMs, {
    message: "audioEndMs must be greater than or equal to audioStartMs",
    path: ["audioEndMs"]
  });

export const cancelSpeechStreamRequestSchema = z
  .object({
    reason: z.string().min(1).max(256),
    cancelledBy: z.string().min(1).max(128),
    idempotencyKey: z.string().min(1).max(160)
  })
  .strict();

export const completeSpeechStreamRequestSchema = z
  .object({
    reason: z.string().min(1).max(256),
    idempotencyKey: z.string().min(1).max(160)
  })
  .strict();

export type SpeechEvent = z.infer<typeof speechEventSchema>;
export type SpeechStreamCreated = z.infer<typeof speechStreamCreatedSchema>;
export type SpeechAudioChunkAck = z.infer<typeof speechAudioChunkAckSchema>;
export type SpeechTerminalEvent = z.infer<typeof speechTerminalEventSchema>;
export type SpeechReplay = z.infer<typeof speechReplaySchema>;
