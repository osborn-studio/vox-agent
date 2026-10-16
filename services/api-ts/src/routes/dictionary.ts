import type { FastifyInstance } from "fastify";
import { dictionaryMutationSchema, dictionarySchema } from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerDictionaryRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/dictionary", async () => {
    const json = await client.getJsonFromPython("/v1/dictionary");
    return dictionarySchema.parse(json);
  });

  app.post("/v1/dictionary", async (request) => {
    const body = dictionaryMutationSchema.parse(request.body);
    const json = await client.postJsonToPython("/v1/dictionary", body);
    return dictionarySchema.parse(json);
  });
}
