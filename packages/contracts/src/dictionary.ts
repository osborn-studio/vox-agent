import { z } from "zod";

export const dictionarySchema = z.object({
  version: z.number().int().optional(),
  corrections: z.record(z.string()),
  preserve: z.array(z.string()),
  format_rules: z
    .object({
      cn_en_space: z.boolean().optional()
    })
    .optional()
});

export const dictionaryMutationSchema = z.object({
  action: z.enum(["add", "delete"]),
  wrong: z.string(),
  right: z.string().optional()
});

export const recentDictionaryResponseSchema = z.object({
  utterance_id: z.string(),
  wrong: z.string(),
  right: z.string(),
  dictionary: dictionarySchema
});

export type DictionaryPayload = z.infer<typeof dictionarySchema>;
export type DictionaryMutation = z.infer<typeof dictionaryMutationSchema>;
export type RecentDictionaryResponse = z.infer<typeof recentDictionaryResponseSchema>;
