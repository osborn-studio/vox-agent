import { z } from "zod";

export const agentQueryRequestSchema = z.object({
  query: z.string().min(1).max(4000),
  nodeIds: z.array(z.string().min(1).max(200)).max(50).optional(),
  limit: z.number().int().positive().max(20).optional(),
  answerer: z.string().min(1).max(80).optional()
});

export const agentCitationSchema = z.object({
  id: z.string().min(1),
  title: z.string().nullable(),
  type: z.string().nullable()
});

export const agentAnswerSchema = z.object({
  query: z.string(),
  answer: z.string(),
  answerer: z.string().min(1),
  citations: z.array(agentCitationSchema),
  contextNodeIds: z.array(z.string().min(1))
});

export const agentRecentContextNodeSchema = z.object({
  id: z.string().min(1),
  title: z.string().nullable(),
  type: z.string().nullable(),
  createdAt: z.string()
});

export const agentRecentContextSchema = z.object({
  days: z.number().int().nonnegative(),
  count: z.number().int().nonnegative(),
  nodes: z.array(agentRecentContextNodeSchema)
});

export type AgentQueryRequest = z.infer<typeof agentQueryRequestSchema>;
export type AgentAnswer = z.infer<typeof agentAnswerSchema>;
export type AgentRecentContext = z.infer<typeof agentRecentContextSchema>;
