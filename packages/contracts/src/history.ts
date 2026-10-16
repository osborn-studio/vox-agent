import { z } from "zod";

import {
  archiveStatusSchema,
  outputModeSchema,
  recordModeSchema,
  targetRiskLevelSchema,
} from "./enums.js";

export const historyItemSchema = z.object({
  id: z.string(),
  created_at: z.string(),
  utterance_id: z.string(),
  output_id: z.string(),
  session_id: z.string(),
  raw_text: z.string(),
  display_text: z.string(),
  polished_text: z.string(),
  stt_provider: z.string(),
  stt_model: z.string(),
  llm_provider: z.string(),
  latency_ms: z.number().int().nonnegative(),
  target_app: z.string(),
  target_risk_level: targetRiskLevelSchema,
  role: z.string(),
  recording_mode: recordModeSchema,
  output_mode: outputModeSchema,
  confidence: z.number().min(0).max(1),
  archive_status: archiveStatusSchema,
  agent: z.string()
});

export const historyListSchema = z.array(historyItemSchema);

export const agentSummarySchema = z.object({
  agent: z.string(),
  count: z.number().int().nonnegative(),
  last_active: z.string()
});

export const agentSummaryListSchema = z.array(agentSummarySchema);

export type HistoryItem = z.infer<typeof historyItemSchema>;
export type AgentSummary = z.infer<typeof agentSummarySchema>;
