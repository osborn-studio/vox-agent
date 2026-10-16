import { z } from "zod";

export const auditEventSchema = z.object({
  id: z.string().min(1),
  action: z.string().min(1),
  actorId: z.string().nullable(),
  workspaceId: z.string().nullable(),
  targetId: z.string().nullable(),
  metadata: z.record(z.string(), z.unknown()),
  createdAt: z.string().min(1)
});

export const auditEventListSchema = z.array(auditEventSchema);

export type AuditEvent = z.infer<typeof auditEventSchema>;

export const modelPolicySchema = z.object({
  answerer: z.string().min(1),
  asrAdapter: z.string().min(1),
  answererOverride: z.string().nullable(),
  asrAdapterOverride: z.string().nullable(),
  availableAnswerers: z.array(z.string()),
  availableAsrAdapters: z.array(z.string()),
  llmConfigured: z.boolean(),
  llmModel: z.string().nullable()
});

export type ModelPolicy = z.infer<typeof modelPolicySchema>;

export const modelPolicyPatchSchema = z
  .object({
    answerer: z.string().min(1).nullable().optional(),
    asrAdapter: z.string().min(1).nullable().optional()
  })
  .strict();

export type ModelPolicyPatch = z.infer<typeof modelPolicyPatchSchema>;

export const retentionPolicySchema = z.object({
  audioRetentionDays: z.number().int().positive(),
  transcriptRetentionDays: z.number().int().positive(),
  memoryRetentionDays: z.number().int().positive(),
  autoDeleteEnabled: z.boolean(),
  audioRetentionDaysOverride: z.number().int().positive().nullable(),
  transcriptRetentionDaysOverride: z.number().int().positive().nullable(),
  memoryRetentionDaysOverride: z.number().int().positive().nullable(),
  autoDeleteOverride: z.boolean().nullable()
});

export type RetentionPolicy = z.infer<typeof retentionPolicySchema>;

export const retentionPolicyPatchSchema = z
  .object({
    audioRetentionDays: z.number().int().positive().nullable().optional(),
    transcriptRetentionDays: z.number().int().positive().nullable().optional(),
    memoryRetentionDays: z.number().int().positive().nullable().optional(),
    autoDeleteEnabled: z.boolean().nullable().optional()
  })
  .strict();

export type RetentionPolicyPatch = z.infer<typeof retentionPolicyPatchSchema>;

const retentionCountsSchema = z.object({
  audio: z.number().int().nonnegative(),
  transcripts: z.number().int().nonnegative(),
  memory: z.number().int().nonnegative()
});

export const retentionSweepSchema = z.object({
  id: z.string().min(1),
  dryRun: z.boolean(),
  startedAt: z.string().min(1),
  completedAt: z.string().min(1),
  cutoffs: z.object({
    audio: z.string().min(1),
    transcripts: z.string().min(1),
    memory: z.string().min(1)
  }),
  candidates: retentionCountsSchema,
  deleted: retentionCountsSchema,
  failures: z.array(z.object({
    code: z.string().min(1),
    sessionId: z.string().min(1),
    message: z.string().optional()
  }))
});

export const retentionSweepRequestSchema = z.object({ dryRun: z.boolean() }).strict();
export const latestRetentionSweepSchema = retentionSweepSchema.nullable();

export type RetentionSweep = z.infer<typeof retentionSweepSchema>;

export const adminRoleSchema = z.enum([
  "member",
  "product_manager",
  "tech_lead",
  "engineering_manager",
  "admin"
]);

export const adminUserSchema = z.object({
  id: z.string().min(1),
  orgId: z.string().min(1),
  email: z.string().min(3),
  displayName: z.string().min(1),
  role: adminRoleSchema,
  status: z.enum(["active", "suspended"]),
  createdAt: z.string().min(1)
});

export const adminUserListSchema = z.array(adminUserSchema);

export const createAdminUserSchema = z
  .object({
    email: z.string().min(3),
    displayName: z.string().min(1),
    role: adminRoleSchema.default("member")
  })
  .strict();

export const updateAdminUserSchema = z
  .object({
    displayName: z.string().min(1).optional(),
    role: adminRoleSchema.optional(),
    status: z.enum(["active", "suspended"]).optional()
  })
  .strict()
  .refine((value) => Object.keys(value).length > 0, "at least one user field is required");

export const issuedCredentialSchema = z.object({
  id: z.string().min(1),
  userId: z.string().min(1),
  name: z.string().min(1),
  createdAt: z.string().min(1),
  token: z.string().min(24)
});

export type AdminRole = z.infer<typeof adminRoleSchema>;
export type AdminUser = z.infer<typeof adminUserSchema>;
export type CreateAdminUser = z.input<typeof createAdminUserSchema>;
export type UpdateAdminUser = z.infer<typeof updateAdminUserSchema>;
export type IssuedCredential = z.infer<typeof issuedCredentialSchema>;
