import { z } from "zod";
import { contextInputAttributionSchema, contextInputQuerySchema } from "./context-inputs.js";

const id = (maximum = 128) => z.string().min(1).max(maximum)
  .regex(/^[A-Za-z0-9_-]+$/).refine((value) => !/\s/.test(value));
const tags = z.array(z.enum(["decision", "progress", "blocker", "question", "verification"]))
  .max(5).refine((values) => new Set(values).size === values.length);
const text = z.string().min(1).max(16384).refine((value) => {
  const scalars = Array.from(value);
  return !value.includes("\0") && scalars.length <= 8192 && scalars.every((scalar) => scalar.length === 2
    || scalar.charCodeAt(0) < 0xd800 || scalar.charCodeAt(0) > 0xdfff);
});
const revision = z.number().int().min(1).max(1_000_000);
export const contextPrivateInputSchema = z.object({
  eventId: id(), text, tags: tags.default([]),
  projectId: id(80).nullable().optional(),
  expectedRepositoryId: contextInputAttributionSchema.shape.repositoryId.nullable().optional(), visibility: z.literal("private").default("private")
}).strict().refine((value) => value.expectedRepositoryId == null || value.projectId != null);
export const contextPrivateEditSchema = z.object({
  expectedRevision: revision, text, tags: tags.default([])
}).strict();
export const contextPrivateWithdrawalSchema = z.object({ expectedRevision: revision }).strict();
export const contextPrivateReferenceCheckSchema = z.object({
  sources: z.array(z.object({ id: id(), revision }).strict()).min(1).max(100)
}).strict().refine((value) => new Set(value.sources.map((source) => source.id)).size === value.sources.length);
export const contextPrivateReferenceResultSchema = z.object({
  version: z.literal(1), valid: z.boolean(), requiresUserReview: z.literal(true),
  meaning: z.literal("current-authorized-private-revisions-only"), evidenceKind: z.literal("user-statement")
}).strict();
const base = {
  version: z.literal(1), id: id(), eventId: id(), revision,
  visibility: z.literal("private"), evidenceKind: z.literal("user-statement"),
  createdAt: z.string().datetime({ offset: true }), revisionChangedAt: z.string().datetime({ offset: true })
};
export const contextPrivateSourceSchema = z.discriminatedUnion("withdrawn", [
  z.object({ ...base, withdrawn: z.literal(false), text, tags,
    projectId: id(80).nullable(), repositoryId: contextInputAttributionSchema.shape.repositoryId.nullable()
  }).strict(),
  z.object({ ...base, withdrawn: z.literal(true) }).strict()
]).refine((source) => source.withdrawn || (source.projectId === null) === (source.repositoryId === null));
export const contextPrivatePageSchema = z.object({
  version: z.literal(1), scope: z.literal("own-private-context"),
  items: z.array(contextPrivateSourceSchema).max(50), hasMore: z.boolean(),
  nextCursor: id(256).nullable(), omittedRecords: z.boolean(), evidenceKind: z.literal("user-statement")
}).strict().refine((page) => page.hasMore === (page.nextCursor !== null)
  && new Set(page.items.map((item) => item.id)).size === page.items.length
  && page.items.every((item) => !item.withdrawn));

export const contextPrivateSearchSchema = z.object({
  start: z.string().max(80), end: z.string().max(80), limit: z.number().int().min(1).max(50).default(20),
  cursor: id(256).nullable().optional(), q: z.string().min(1).max(256).refine((value) => {
    const scalars = Array.from(value);
    return !value.includes("\0") && scalars.length <= 128 && scalars.every((scalar) => scalar.length === 2
      || scalar.charCodeAt(0) < 0xd800 || scalar.charCodeAt(0) > 0xdfff);
  }).nullable().optional(),
  tag: z.enum(["decision", "progress", "blocker", "question", "verification"]).nullable().optional(),
  projectId: id(80).nullable().optional()
}).strict().refine((query) => contextInputQuerySchema.safeParse({ start: query.start, end: query.end, limit: query.limit,
  ...(query.cursor ? { cursor: query.cursor } : {}) }).success);
