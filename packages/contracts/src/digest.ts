import { z } from "zod";

const digestBaseSchema = z.object({
  id: z.string(),
  digest_type: z.enum(["session_digest", "daily_digest", "project_digest"]),
  session_id: z.string().default(""),
  digest_date: z.string().default(""),
  project_key: z.string().default(""),
  source_event_id: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  summary: z.string(),
  intent: z.string(),
  suggested_tags: z.array(z.string()),
  mentioned_entities: z.array(z.string()),
  enhanced: z.boolean().default(false),
  enhancer_provider: z.string().default("heuristic")
});

export const sessionDigestSchema = digestBaseSchema.extend({
  digest_type: z.literal("session_digest"),
  session_id: z.string(),
  digest_date: z.string().default("")
});

export const dailyDigestSchema = digestBaseSchema.extend({
  digest_type: z.literal("daily_digest"),
  digest_date: z.string(),
  session_id: z.string().default("")
});

export const projectDigestSchema = digestBaseSchema.extend({
  digest_type: z.literal("project_digest"),
  project_key: z.string(),
  session_id: z.string().default(""),
  digest_date: z.string().default("")
});

export type SessionDigest = z.infer<typeof sessionDigestSchema>;
export type DailyDigest = z.infer<typeof dailyDigestSchema>;
export type ProjectDigest = z.infer<typeof projectDigestSchema>;
