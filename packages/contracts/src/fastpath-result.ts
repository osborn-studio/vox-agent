import { z } from "zod";

import {
  archiveStatusSchema,
  fastPathStatusSchema,
  outputModeSchema,
  targetRiskLevelSchema,
} from "./enums.js";

export const dictionaryReplacementSchema = z.object({
  from: z.string(),
  to: z.string()
});

export const polishStatusSchema = z.enum(["ok", "skipped", "failed"]);

export const fastPathResponseSchema = z.object({
  id: z.string(),
  status: fastPathStatusSchema,
  raw_text: z.string(),
  display_text: z.string(),
  polished_text: z.string(),
  // Additive, optional (PR-D): set when the caller asked for inline polish.
  // Clients key on polish_status, never on polished_text === display_text.
  polish_status: polishStatusSchema.optional(),
  polish_error: z.string().max(200).optional(),
  stt_provider: z.string(),
  stt_model: z.string(),
  target_app: z.string(),
  target_risk_level: targetRiskLevelSchema,
  should_autopaste: z.boolean(),
  needs_review: z.boolean(),
  confidence: z.number().min(0).max(1),
  dictionary_applied: z.array(dictionaryReplacementSchema),
  latency_ms: z.number().int().nonnegative(),
  session_id: z.string(),
  utterance_id: z.string(),
  output_id: z.string(),
  output_mode: outputModeSchema,
  archive_status: archiveStatusSchema,
  created_at: z.string()
});

export type FastPathResponse = z.infer<typeof fastPathResponseSchema>;
export type DictionaryReplacement = z.infer<typeof dictionaryReplacementSchema>;

export const polishTextRequestSchema = z
  .object({
    text: z.string().min(1).max(8192)
  })
  .strict();

export const polishTextResponseSchema = z.object({
  polish_status: polishStatusSchema,
  polished_text: z.string(),
  polish_error: z.string().max(200).optional()
});

export type PolishStatus = z.infer<typeof polishStatusSchema>;
export type PolishTextRequest = z.infer<typeof polishTextRequestSchema>;
export type PolishTextResponse = z.infer<typeof polishTextResponseSchema>;
