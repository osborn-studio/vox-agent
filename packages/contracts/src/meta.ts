import { z } from "zod";

import { recordModeSchema } from "./enums.js";
import { recentUtteranceSchema } from "./recent-utterance.js";

export const recentModeResponseSchema = z.object({
  utterance_id: z.string(),
  mode: recordModeSchema
});

export const recentDismissResponseSchema = z.object({
  utterance_id: z.string(),
  dismissed: z.boolean()
});

export const recentRetryResponseSchema = recentUtteranceSchema.extend({
  retried_from: z.string()
});

export const statsResponseSchema = z.object({
  count: z.number().int().nonnegative(),
  profile: z.string()
});

export const healthResponseSchema = z.object({
  status: z.string(),
  service: z.string().optional(),
  version: z.string().optional(),
  profile: z.string().optional(),
  python_backend_base_url: z.string().optional(),
  vox_agent_engine_base_url: z.string().optional()
});

export type RecentModeResponse = z.infer<typeof recentModeResponseSchema>;
export type RecentDismissResponse = z.infer<typeof recentDismissResponseSchema>;
export type RecentRetryResponse = z.infer<typeof recentRetryResponseSchema>;
export type StatsResponse = z.infer<typeof statsResponseSchema>;
export type HealthResponse = z.infer<typeof healthResponseSchema>;
