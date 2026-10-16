import { z } from "zod";

export const DESKTOP_CONTRACT_VERSION = "2026-07-19.1" as const;
export const DESKTOP_PRODUCT_NAME = "Vox Agent" as const;

export const desktopCapabilitySchema = z.enum([
  "agent.query",
  "speech.stream.v1",
  "translation.realtime.v1",
  "conversation.session.v1",
  "voice.capture",
  "history.search",
  "data.migration.v1",
  "settings.providers",
  "digest.export"
]);

export const desktopContractSchema = z.object({
  api_version: z.literal(DESKTOP_CONTRACT_VERSION),
  product: z.literal(DESKTOP_PRODUCT_NAME),
  capabilities: z.array(desktopCapabilitySchema)
});

export type DesktopCapability = z.infer<typeof desktopCapabilitySchema>;
export type DesktopContract = z.infer<typeof desktopContractSchema>;
