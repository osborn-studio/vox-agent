import { z } from "zod";

export const tokenScopeTypeSchema = z.enum(["USER", "WORKSPACE"]);

export const tokenSpendTypeSchema = z.enum([
  "ASR",
  "TRANSLATION",
  "SUMMARY",
  "AGENT_REASONING",
  "AGENT_QUERY",
  "EXPORT",
  "PURCHASE",
  "REFUND",
  "MANUAL_ADJUSTMENT",
  "ADMIN_ADJUSTMENT"
]);

export const tokenBalanceSchema = z.object({
  scopeId: z.string().min(1),
  scopeType: tokenScopeTypeSchema,
  balance: z.number().int(),
  currency: z.literal("TOKENS"),
  updatedAt: z.string().min(1)
});

export const tokenLedgerEntrySchema = z.object({
  id: z.string().min(1),
  scopeId: z.string().min(1),
  scopeType: tokenScopeTypeSchema,
  sessionId: z.string().nullable(),
  spendType: tokenSpendTypeSchema,
  delta: z.number().int(),
  balanceAfter: z.number().int(),
  description: z.string(),
  createdAt: z.string().min(1)
});

export const tokenLedgerEntryListSchema = z.array(tokenLedgerEntrySchema);

export type TokenBalance = z.infer<typeof tokenBalanceSchema>;
export type TokenLedgerEntry = z.infer<typeof tokenLedgerEntrySchema>;
