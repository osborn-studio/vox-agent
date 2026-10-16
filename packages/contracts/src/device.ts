import { z } from "zod";

export const deviceKindSchema = z.enum([
  "SIMULATOR",
  "SIMULATED_RECORDER",
  "VOXPIN",
  "VOXNOTE",
  "RING",
  "WATCH",
  "PHONE_WIDGET",
  "MOBILE",
  "DESKTOP",
  "ROOM_RECORDER",
  "CUSTOM_HARDWARE"
]);

export const deviceStatusSchema = z.enum([
  "PAIRING",
  "PAIRED",
  "UNPAIRED",
  "CONNECTED",
  "DISCONNECTED",
  "ONLINE",
  "OFFLINE",
  "LOW_BATTERY",
  "LOCKED",
  "WIPED",
  "REVOKED"
]);

export const deviceSchema = z.object({
  id: z.string().min(1),
  workspaceId: z.string().min(1),
  ownerUserId: z.string().min(1),
  kind: deviceKindSchema,
  displayName: z.string().min(1),
  status: deviceStatusSchema,
  batteryPercent: z.number().int().min(0).max(100).nullable(),
  firmwareVersion: z.string().nullable(),
  pairedAt: z.string().nullable(),
  lastSeenAt: z.string().nullable()
});

export const pairingCodeSchema = z.object({
  code: z.string().min(6),
  qrPayload: z.string().min(1),
  expiresAt: z.string().min(1)
});

export const devicePairingStatusSchema = z.enum(["PENDING", "CLAIMED", "EXPIRED", "CANCELLED"]);

export const devicePairingSchema = z.object({
  id: z.string().min(1),
  code: z.string().min(6),
  qrPayload: z.string().min(1),
  workspaceId: z.string().nullable(),
  ownerUserId: z.string().min(1),
  expiresAt: z.string().min(1),
  claimedAt: z.string().nullable(),
  status: devicePairingStatusSchema
});

export const deviceEventTypeSchema = z.enum([
  "RECORD_BUTTON_PRESSED",
  "RECORD_BUTTON_RELEASED",
  "MARKER_BUTTON_PRESSED",
  "BATTERY_REPORTED",
  "AUDIO_UPLOAD_READY",
  "AUDIO_CHUNK_READY",
  "BATTERY_LOW",
  "DEVICE_CONNECTED",
  "DEVICE_DISCONNECTED",
  "DEVICE_ONLINE",
  "DEVICE_OFFLINE"
]);

export const deviceEventSchema = z.object({
  id: z.string().min(1),
  deviceId: z.string().min(1),
  type: deviceEventTypeSchema,
  occurredAt: z.string().min(1),
  payload: z.record(z.string(), z.unknown()),
  signature: z.string().nullable(),
  sessionId: z.string().nullable().default(null)
});

export const deviceToolSchema = z.enum([
  "DEVICE_VIBRATE",
  "DEVICE_STATUS",
  "DEVICE_LOCK",
  "DEVICE_WIPE",
  "DEVICE_NOTIFY"
]);

export const deviceToolStatusSchema = z.enum(["REQUESTED", "SENT", "ACKNOWLEDGED", "FAILED"]);

export const deviceToolInvocationSchema = z.object({
  id: z.string().min(1),
  deviceId: z.string().min(1),
  tool: deviceToolSchema,
  args: z.record(z.string(), z.unknown()),
  requestedAt: z.string().min(1),
  status: deviceToolStatusSchema
});

export const deviceListSchema = z.array(deviceSchema);

export type Device = z.infer<typeof deviceSchema>;
export type DeviceEvent = z.infer<typeof deviceEventSchema>;
export type DeviceToolInvocation = z.infer<typeof deviceToolInvocationSchema>;
