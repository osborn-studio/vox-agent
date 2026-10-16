import { z } from "zod";

import { recordModeSchema } from "./enums.js";

export const undoOutputResponseSchema = z.object({
  output_id: z.string(),
  utterance_id: z.string(),
  accepted: z.boolean(),
  archive_recalled: z.boolean(),
  ui_undo_required: z.boolean(),
  mode: recordModeSchema
});

export type UndoOutputResponse = z.infer<typeof undoOutputResponseSchema>;
