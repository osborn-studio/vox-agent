import { z } from "zod";

export const recordModeSchema = z.enum(["normal", "private", "ephemeral"]);
export type RecordMode = z.infer<typeof recordModeSchema>;

export const targetRiskLevelSchema = z.enum(["low", "medium", "high"]);
export type TargetRiskLevel = z.infer<typeof targetRiskLevelSchema>;

export const outputModeSchema = z.enum(["paste", "direct_typing", "preview_only", "none"]);
export type OutputMode = z.infer<typeof outputModeSchema>;

export const fastPathStatusSchema = z.enum(["ok", "needs_review", "failed"]);
export type FastPathStatus = z.infer<typeof fastPathStatusSchema>;

export const archiveStatusSchema = z.enum(["skipped", "queued", "raw_only", "polished", "failed"]);
export type ArchiveStatus = z.infer<typeof archiveStatusSchema>;
