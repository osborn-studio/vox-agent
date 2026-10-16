import type { FastifyInstance } from "fastify";
import {
  pairingCodeSchema,
  deviceSchema,
  deviceListSchema,
  deviceEventSchema,
  deviceToolInvocationSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerDeviceRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.post("/v1/devices/pairing-codes", async (request) => {
    const json = await client.postJsonToPython("/v1/devices/pairing-codes", request.body ?? {});
    return pairingCodeSchema.parse(json);
  });

  app.post("/v1/devices/pair", async (request) => {
    const json = await client.postJsonToPython("/v1/devices/pair", request.body);
    return deviceSchema.parse(json);
  });

  app.get("/v1/devices", async () => {
    const json = await client.getJsonFromPython("/v1/devices");
    return deviceListSchema.parse(json);
  });

  app.post("/v1/devices/:id/events", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.postJsonToPython(
      `/v1/devices/${encodeURIComponent(id)}/events`,
      request.body
    );
    return deviceEventSchema.parse(json);
  });

  app.post("/v1/devices/:id/tools", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.postJsonToPython(
      `/v1/devices/${encodeURIComponent(id)}/tools`,
      request.body
    );
    return deviceToolInvocationSchema.parse(json);
  });
}
