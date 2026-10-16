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

export const fastPathResponseSchema = z.object({
  id: z.string(),
  status: fastPathStatusSchema,
  raw_text: z.string(),
  display_text: z.string(),
  polished_text: z.string(),
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
