import { z } from "zod";

// Defense in depth: require canonical whitespace-free identifiers/timestamps
// without trimming or otherwise changing the caller's supplied value.
const identifier = (maximum: number) => z.string().min(1).max(maximum)
  .regex(/^[A-Za-z0-9_-]+$/).refine((value) => !/\s/.test(value));
const timestamp = z.string().datetime({ offset: true })
  .refine((value) => value === value.trim());

// Own accepted-backup source protocol. Transport state is never code evidence.
export const contextInputAttributionSchema = z.object({
  projectId: identifier(80),
  repositoryId: z.string().max(200).regex(/^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/)
    .refine((value) => !/\s/.test(value) && value.split("/").every((part) => part !== "." && part !== "..")),
  source: z.literal("manual")
}).strict();

export const contextInputSourceSchema = z.object({
  eventId: identifier(128),
  text: z.string().min(1).max(8192),
  submittedAt: timestamp,
  targetId: z.string().min(1).max(80),
  shareIntent: z.literal("backup-destination"),
  deliveryStatus: z.enum(["pending", "sending", "unknown", "delivered", "blocked", "rejected"]),
  attribution: contextInputAttributionSchema.nullable(),
  evidenceKind: z.literal("user-statement")
}).strict();

const cursorSchema = identifier(256).nullable();
const pageShape = {
  version: z.literal(1),
  nextCursor: cursorSchema,
  hasMore: z.boolean(),
  omittedRecords: z.boolean(),
  coverage: z.literal("currently-authorized-accepted-backups"),
  notices: z.array(z.string().min(1).max(1024)).min(1).max(10)
};

function pageConsistency(value: { hasMore: boolean; nextCursor: string | null }, context: z.RefinementCtx) {
  if (value.hasMore !== (value.nextCursor !== null)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "cursor must match hasMore", path: ["nextCursor"] });
  }
}

export const contextInputPageSchema = z.object({
  ...pageShape,
  scope: z.literal("own-submitted-backup-inputs"),
  items: z.array(contextInputSourceSchema).max(50)
}).strict().superRefine((page, context) => {
  pageConsistency(page, context);
  if (new Set(page.items.map((item) => item.eventId)).size !== page.items.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "duplicate source event", path: ["items"] });
  }
});

const weekSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine((value) => {
  const day = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(day.getTime()) && day.getUTCFullYear() >= 1 && day.toISOString().slice(0, 10) === value && day.getUTCDay() === 1
    && Number.isFinite(new Date(day.getTime() + 7 * 86400000).getTime())
    && new Date(day.getTime() + 7 * 86400000).getUTCFullYear() <= 9999;
}, "week must be a valid Monday with a representable end date");

export const contextWeeklyDraftSchema = z.object({
  ...pageShape,
  week: weekSchema,
  timezone: z.literal("Asia/Shanghai"),
  kind: z.literal("source-review-draft"),
  requiresUserReview: z.literal(true),
  sources: z.array(contextInputSourceSchema).max(50)
}).strict().superRefine((draft, context) => {
  pageConsistency(draft, context);
  if (new Set(draft.sources.map((item) => item.eventId)).size !== draft.sources.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "duplicate source event", path: ["sources"] });
  }
});

function isoMicroseconds(value: string): bigint | null {
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) return null;
  const year = new Date(milliseconds).getUTCFullYear();
  if (year < 1 || year > 9999 || Number(value.slice(0, 4)) < 1) return null;
  // Python datetime truncates beyond six fractional digits. Date.parse keeps
  // only three; retain the remaining microseconds without floating rounding.
  const fraction = value.match(/\.(\d+)(?:Z|[+-]\d{2}:?\d{2})$/)?.[1] ?? "";
  return BigInt(milliseconds) * 1000n + BigInt(fraction.padEnd(6, "0").slice(3, 6));
}

export const contextInputQuerySchema = z.object({
  start: timestamp,
  end: timestamp,
  limit: z.number().int().min(1).max(50).optional(),
  cursor: cursorSchema.unwrap().optional()
}).strict().refine((query) => {
  const start = isoMicroseconds(query.start);
  const end = isoMicroseconds(query.end);
  if (start === null || end === null) return false;
  const duration = end - start;
  return duration > 0n && duration <= 31n * 86400000000n;
}, "window must be positive and at most 31 days");

export const contextWeeklyDraftQuerySchema = z.object({
  week: weekSchema,
  cursor: cursorSchema.unwrap().optional()
}).strict();

export type ContextInputSource = z.infer<typeof contextInputSourceSchema>;
export type ContextInputPage = z.infer<typeof contextInputPageSchema>;
export type ContextWeeklyDraft = z.infer<typeof contextWeeklyDraftSchema>;
