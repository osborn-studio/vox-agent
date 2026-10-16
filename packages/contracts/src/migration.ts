import { z } from "zod";

import { dictionarySchema } from "./dictionary.js";
import { historyItemSchema } from "./history.js";

export const migrationHistoryItemSchema = historyItemSchema.refine(
  (item) => item.recording_mode !== "ephemeral",
  "ephemeral history cannot enter a migration bundle"
);

export const migrationHistoryListSchema = z.array(migrationHistoryItemSchema).max(500);

export const migrationHistoryQuerySchema = z
  .object({
    offset: z.coerce.number().int().min(0).max(50_000).default(0),
    limit: z.coerce.number().int().min(1).max(500).default(500)
  })
  .strict();

export const migrationHistoryImportSchema = z
  .object({
    items: z.array(migrationHistoryItemSchema).max(200)
  })
  .strict();

export const migrationHistoryImportResponseSchema = z
  .object({
    imported: z.number().int().nonnegative(),
    skipped: z.number().int().nonnegative()
  })
  .strict();

export const migrationDictionarySchema = dictionarySchema
  .extend({
    corrections: z
      .record(z.string().min(1).max(200), z.string().min(1).max(500))
      .refine((value) => Object.keys(value).length <= 10_000),
    preserve: z.array(z.string().min(1).max(500)).max(10_000)
  })
  .strict();

export const migrationDictionaryImportResponseSchema = z
  .object({
    imported_corrections: z.number().int().nonnegative(),
    skipped_corrections: z.number().int().nonnegative(),
    imported_preserved_terms: z.number().int().nonnegative(),
    dictionary: dictionarySchema
  })
  .strict();

export const legacyMigrationSourceSchema = z
  .object({
    path: z.string().min(1).max(4_000),
    kind: z.string().min(1).max(100),
    records_found: z.number().int().nonnegative().max(50_000),
    imported: z.number().int().nonnegative().max(50_000),
    skipped: z.number().int().nonnegative().max(50_000),
    invalid: z.number().int().nonnegative().max(50_000),
    error: z.string().max(1_000).nullable()
  })
  .strict();

export const legacyMigrationResponseSchema = z
  .object({
    sources: z.array(legacyMigrationSourceSchema).max(8),
    records_found: z.number().int().nonnegative().max(200_000),
    imported: z.number().int().nonnegative().max(200_000),
    skipped: z.number().int().nonnegative().max(200_000),
    invalid: z.number().int().nonnegative().max(200_000)
  })
  .strict();

export type MigrationHistoryImport = z.infer<typeof migrationHistoryImportSchema>;
export type LegacyMigrationResponse = z.infer<typeof legacyMigrationResponseSchema>;
