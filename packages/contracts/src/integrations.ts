import { z } from "zod";

export const obsidianExportResponseSchema = z.object({
  ok: z.boolean(),
  vault_path: z.string(),
  note_path: z.string(),
  bytes_written: z.number().int().nonnegative()
});

export const aiMateMemoryExportResponseSchema = z.object({
  ok: z.boolean(),
  base_path: z.string(),
  record_path: z.string(),
  bytes_written: z.number().int().nonnegative()
});

export type ObsidianExportResponse = z.infer<typeof obsidianExportResponseSchema>;
export type AIMateMemoryExportResponse = z.infer<typeof aiMateMemoryExportResponseSchema>;
