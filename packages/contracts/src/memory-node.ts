import { z } from "zod";

export const memoryNodeTypeSchema = z.enum([
  "SESSION",
  "SUMMARY",
  "DECISION",
  "ACTION_ITEM",
  "PROMISE",
  "PERSON",
  "ORGANIZATION",
  "PROJECT",
  "CUSTOMER",
  "TOPIC",
  "TERM",
  "TRANSLATION_NOTE",
  "TASK",
  "FACT",
  "PREFERENCE",
  "VOCABULARY",
  "QUOTE",
  "RISK",
  "DOCUMENT",
  "DEVICE_EVENT"
]);

export const memoryNodeImportanceSchema = z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]);

export const memoryNodeVisibilitySchema = z.enum(["PRIVATE", "WORKSPACE", "RESTRICTED"]);

export const memoryNodeSchema = z.object({
  id: z.string().min(1),
  workspaceId: z.string().min(1),
  ownerUserId: z.string().nullable().default(null),
  sourceSessionId: z.string().nullable(),
  type: memoryNodeTypeSchema,
  title: z.string().min(1),
  body: z.string(),
  language: z.string().nullable().default(null),
  importance: memoryNodeImportanceSchema.default("MEDIUM"),
  visibility: memoryNodeVisibilitySchema.default("PRIVATE"),
  tags: z.array(z.string().min(1)),
  metadata: z.record(z.string(), z.unknown()).default({}),
  confidence: z.number().min(0).max(1),
  createdAt: z.string().min(1),
  updatedAt: z.string().min(1)
});

export const memorySearchResultSchema = z.object({
  node: memoryNodeSchema,
  score: z.number().min(0),
  highlights: z.array(z.string())
});

export const memorySearchResponseSchema = z.object({
  query: z.string(),
  results: z.array(memorySearchResultSchema)
});

export const memoryNodeListSchema = z.array(memoryNodeSchema);

export const memoryEdgeRelationSchema = z.enum([
  "MENTIONS",
  "DECIDES",
  "ASSIGNS",
  "RELATES_TO",
  "FOLLOWS_UP",
  "TRANSLATES_TO",
  "BELONGS_TO_PROJECT",
  "BELONGS_TO_CUSTOMER"
]);

export const memoryEdgeSchema = z.object({
  id: z.string().min(1),
  fromNodeId: z.string().min(1),
  toNodeId: z.string().min(1),
  relation: memoryEdgeRelationSchema,
  confidence: z.number().min(0).max(1).nullable(),
  createdAt: z.string().min(1)
});

export type MemoryNode = z.infer<typeof memoryNodeSchema>;
export type MemoryEdge = z.infer<typeof memoryEdgeSchema>;
export type MemorySearchResponse = z.infer<typeof memorySearchResponseSchema>;
