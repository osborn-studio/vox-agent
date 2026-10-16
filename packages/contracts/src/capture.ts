import { z } from "zod";

import { memoryNodeListSchema } from "./memory-node.js";

export const captureSourceSchema = z.enum([
  "MOBILE",
  "DESKTOP",
  "WEB",
  "HARDWARE",
  "UPLOAD",
  "PHONE",
  "MEETING",
  "CLI"
]);

export const captureModeSchema = z.enum([
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
  "LIFE_LOG"
]);

export const privacyLevelSchema = z.enum(["PERSONAL", "TEAM", "RESTRICTED", "REGULATED"]);

export const consentStateSchema = z.enum(["UNKNOWN", "REQUESTED", "GRANTED", "DENIED"]);

export const captureStatusSchema = z.enum(["RECORDING", "PROCESSING", "READY", "FAILED", "ARCHIVED"]);

export const captureEditionSchema = z.enum(["PERSONAL", "ENTERPRISE"]);

export const captureSessionSchema = z.object({
  id: z.string().min(1),
  workspaceId: z.string().min(1),
  ownerUserId: z.string().min(1),
  edition: captureEditionSchema.default("PERSONAL"),
  source: captureSourceSchema,
  mode: captureModeSchema,
  title: z.string().nullable().default(null),
  startedAt: z.string().min(1),
  endedAt: z.string().nullable(),
  languageHints: z.array(z.string().min(1)),
  detectedLanguages: z.array(z.string().min(1)).default([]),
  privacyLevel: privacyLevelSchema,
  consentState: consentStateSchema,
  deviceIds: z.array(z.string().min(1)),
  localOnlyUntilSync: z.boolean(),
  status: captureStatusSchema,
  createdAt: z.string().min(1).optional(),
  updatedAt: z.string().min(1).optional()
});

export const transcriptSegmentSchema = z
  .object({
    id: z.string().min(1),
    sessionId: z.string().min(1),
    speakerId: z.string().min(1),
    speakerLabel: z.string().nullable().default(null),
    startMs: z.number().int().nonnegative(),
    endMs: z.number().int().nonnegative(),
    language: z.string().nullable().default(null),
    textRaw: z.string(),
    textNormalized: z.string(),
    textTranslated: z.string().nullable(),
    translationTargetLanguage: z.string().nullable().default(null),
    confidence: z.number().min(0).max(1),
    model: z.string().min(1),
    isUserMarked: z.boolean(),
    embeddingId: z.string().nullable()
  })
  .refine((segment) => segment.endMs >= segment.startMs, {
    message: "endMs must be greater than or equal to startMs",
    path: ["endMs"]
  });

export const captureSessionListSchema = z.array(captureSessionSchema);
export const transcriptSegmentListSchema = z.array(transcriptSegmentSchema);

export const captureSummarySchema = z.object({
  sessionId: z.string().min(1),
  status: captureStatusSchema,
  summaryNodes: memoryNodeListSchema,
  actionItems: memoryNodeListSchema,
  decisions: memoryNodeListSchema
});

export const audioUploadResultSchema = z.object({
  sessionId: z.string().min(1),
  audioPath: z.string(),
  bytes: z.number().int().nonnegative()
});

export type CaptureSession = z.infer<typeof captureSessionSchema>;
export type TranscriptSegment = z.infer<typeof transcriptSegmentSchema>;
export type CaptureSummary = z.infer<typeof captureSummarySchema>;
export type AudioUploadResult = z.infer<typeof audioUploadResultSchema>;
