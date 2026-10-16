import type { FastifyInstance } from "fastify";
import {
  providerConnectivitySchema,
  providerQualificationSchema,
  providerSettingsSchema,
  providerSettingsUpdateSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerSettingsRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/settings/providers", async () => {
    const json = await client.getJsonFromPython("/v1/settings/providers");
    return providerSettingsSchema.parse(json);
  });

  app.get("/v1/settings/providers/test", async () => {
    const json = await client.getJsonFromPython("/v1/settings/providers/test");
    return providerConnectivitySchema.parse(json);
  });

  app.get("/v1/settings/provider-qualifications", async () => {
    const json = await client.getJsonFromPython("/v1/settings/provider-qualifications");
    return providerQualificationSchema.parse(json);
  });

  app.post("/v1/settings/providers", async (request, reply) => {
    const parsed = providerSettingsUpdateSchema.safeParse(request.body);
    if (!parsed.success) {
      reply.code(400);
      return {
        error: "INVALID_PROVIDER_SETTINGS",
        message: "Provider settings did not match the bounded contract."
      };
    }
    const body = parsed.data;
    const json = await client.postJsonToPython("/v1/settings/providers", body);
    return providerSettingsSchema.parse(json);
  });
}
