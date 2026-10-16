import { z } from "zod";

export const TRANSLATION_EVENT_SCHEMA = "voxagent.translation.event" as const;
export const TRANSLATION_EVENT_SCHEMA_VERSION = 1 as const;

const languageCodeSchema = z
  .string()
  .min(2)
  .max(35)
  .regex(/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/, "language must be a BCP 47 tag");

export const translationGlossaryEntrySchema = z
  .object({
    sourceText: z.string().trim().min(1).max(256),
    targetText: z.string().trim().min(1).max(256)
  })
  .strict();

export const translationSourceReferenceSchema = z
  .object({
    streamId: z.string().min(1).max(128),
    segmentId: z.string().min(1).max(128),
    revision: z.number().int().positive()
  })
  .strict();

export const translationSourceCitationSchema = translationSourceReferenceSchema
  .extend({
    audioStartMs: z.number().int().nonnegative(),
    audioEndMs: z.number().int().nonnegative()
  })
  .refine((segment) => segment.audioEndMs >= segment.audioStartMs, {
    message: "audioEndMs must be greater than or equal to audioStartMs",
    path: ["audioEndMs"]
  });

export const translationRequestSchema = z
  .object({
    sourceLanguage: languageCodeSchema,
    targetLanguage: languageCodeSchema,
    segments: z.array(translationSourceReferenceSchema).min(1).max(8),
    glossary: z.array(translationGlossaryEntrySchema).max(128).default([]),
    allowRemote: z.boolean().default(false),
    idempotencyKey: z.string().min(1).max(160)
  })
  .strict();

const translationEventBaseSchema = z
  .object({
    schema: z.literal(TRANSLATION_EVENT_SCHEMA),
    schemaVersion: z.literal(TRANSLATION_EVENT_SCHEMA_VERSION),
    eventId: z.string().min(1),
    translationId: z.string().min(1),
    emittedAt: z.string().datetime({ offset: true }),
    latencyMs: z.number().int().nonnegative()
  })
  .strict();

const translationContextSchema = {
  sourceLanguage: languageCodeSchema,
  targetLanguage: languageCodeSchema,
  sourceSegments: z.array(translationSourceCitationSchema).min(1).max(8)
};

export const translationFinalEventSchema = translationEventBaseSchema.extend({
  type: z.literal("translation.final"),
  payload: z
    .object({
      ...translationContextSchema,
      text: z.string().min(1).max(32_000),
      confidence: z.number().min(0).max(1),
      provider: z.string().min(1).max(128),
      model: z.string().min(1).max(256),
      glossaryHits: z.array(translationGlossaryEntrySchema).max(128)
    })
    .strict()
});

export const translationFailureCodeSchema = z.enum([
  "REMOTE_DISABLED",
  "PROVIDER_UNAVAILABLE",
  "PROVIDER_TIMEOUT",
  "INVALID_RESPONSE"
]);

const translationFailedEventSchema = translationEventBaseSchema.extend({
  type: z.literal("translation.failed"),
  payload: z
    .object({
      ...translationContextSchema,
      code: translationFailureCodeSchema,
      message: z.string().min(1).max(512),
      retriable: z.boolean()
    })
    .strict()
});

export const translationEventSchema = z.discriminatedUnion("type", [
  translationFinalEventSchema,
  translationFailedEventSchema
]);

export type TranslationRequest = z.infer<typeof translationRequestSchema>;
export type TranslationEvent = z.infer<typeof translationEventSchema>;
