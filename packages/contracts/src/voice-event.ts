import { z } from "zod";

import {
  archiveStatusSchema,
  outputModeSchema,
  recordModeSchema,
  targetRiskLevelSchema,
} from "./enums.js";

export const voiceEventSchema = z.object({
  id: z.string(),
  created_at: z.string(),
  utterance_id: z.string(),
  output_id: z.string(),
  session_id: z.string(),
  session_type: z.string().default("general"),
  source: z.string(),
  target_app: z.string(),
  target_risk_level: targetRiskLevelSchema,
  raw_text: z.string(),
  display_text: z.string(),
  stt_provider: z.string(),
  stt_model: z.string(),
  latency_stt_ms: z.number().int().nonnegative().optional(),
  latency_fast_total_ms: z.number().int().nonnegative().optional(),
  recording_mode: recordModeSchema,
  output_mode: outputModeSchema,
  confidence: z.number().min(0).max(1),
  archive_status: archiveStatusSchema
});

export type VoiceEvent = z.infer<typeof voiceEventSchema>;
