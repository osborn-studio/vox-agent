import { z } from "zod";

export const permissionRequestSchema = z.object({
  approval_id: z.string().min(1).max(160),
  task_id: z.string().max(160),
  agent_id: z.string().max(160),
  tool: z.string().min(1).max(128),
  scope: z.record(z.string(), z.string()),
  requested_at: z.string().datetime({ offset: true }),
  expires_at: z.string().datetime({ offset: true })
});

export const permissionRequestListSchema = z.array(permissionRequestSchema);

export const permissionDecisionRequestSchema = z.object({
  approval_id: z.string().min(1).max(160),
  approved: z.boolean(),
  remember_scope: z.boolean().default(false)
}).strict();

export const permissionDecisionSchema = z.object({
  ok: z.literal(true),
  decision: z.enum(["approved", "denied"])
});

export type PermissionRequest = z.infer<typeof permissionRequestSchema>;
export type PermissionDecisionRequest = z.infer<typeof permissionDecisionRequestSchema>;
export type PermissionDecision = z.infer<typeof permissionDecisionSchema>;
