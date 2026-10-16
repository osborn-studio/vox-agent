import { z } from "zod";

import {
  archiveStatusSchema,
  fastPathStatusSchema,
  outputModeSchema,
  recordModeSchema,
  targetRiskLevelSchema,
} from "./enums.js";
import { dictionaryReplacementSchema } from "./fastpath-result.js";

export const recentUtteranceSchema = z.object({
  id: z.string(),
  utterance_id: z.string(),
  session_id: z.string(),
  output_id: z.string(),
  status: fastPathStatusSchema,
  raw_text: z.string(),
  display_text: z.string(),
  target_app: z.string(),
  target_risk_level: targetRiskLevelSchema,
  recording_mode: recordModeSchema,
  output_mode: outputModeSchema,
  archive_status: archiveStatusSchema,
  confidence: z.number().min(0).max(1),
  stt_provider: z.string(),
  stt_model: z.string(),
  dictionary_applied: z.array(dictionaryReplacementSchema),
  expires_at: z.number().int()
});

export const recentEnvelopeSchema = z.object({
  recent: recentUtteranceSchema.nullable()
});

export type RecentUtterance = z.infer<typeof recentUtteranceSchema>;
export type RecentEnvelope = z.infer<typeof recentEnvelopeSchema>;
