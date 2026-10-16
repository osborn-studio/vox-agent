import type { FastifyInstance } from "fastify";
import {
  recentDictionaryResponseSchema,
  recentDismissResponseSchema,
  recentEnvelopeSchema,
  recentModeResponseSchema,
  recentRetryResponseSchema,
  undoOutputResponseSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerRecentRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/recent", async () => {
    const json = await client.getJsonFromPython("/v1/recent");
    return recentEnvelopeSchema.parse(json);
  });

  app.post("/v1/recent/retry", async (request) => {
    const body = request.body as Record<string, string>;
    const json = await client.postFormToPython("/v1/recent/retry", {
      utterance_id: body.utterance_id || "",
      provider: body.provider || ""
    });
    return recentRetryResponseSchema.parse(json);
  });

  app.post("/v1/recent/mode", async (request) => {
    const body = request.body as Record<string, string>;
    const json = await client.postFormToPython("/v1/recent/mode", {
      utterance_id: body.utterance_id || "",
      mode: body.mode || ""
    });
    return recentModeResponseSchema.parse(json);
  });

  app.post("/v1/recent/dismiss", async (request) => {
    const body = request.body as Record<string, string>;
    const json = await client.postFormToPython("/v1/recent/dismiss", { utterance_id: body.utterance_id || "" });
    return recentDismissResponseSchema.parse(json);
  });

  app.post("/v1/recent/dictionary", async (request) => {
    const body = request.body as Record<string, string>;
    const json = await client.postFormToPython("/v1/recent/dictionary", {
      utterance_id: body.utterance_id || "",
      wrong: body.wrong || "",
      right: body.right || ""
    });
    return recentDictionaryResponseSchema.parse(json);
  });

  app.post("/v1/output/undo", async (request) => {
    const body = request.body as Record<string, string>;
    const json = await client.postFormToPython("/v1/output/undo", { output_id: body.output_id || "" });
    return undoOutputResponseSchema.parse(json);
  });
}
