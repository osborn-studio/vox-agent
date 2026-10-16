import type { FastifyInstance, FastifyRequest } from "fastify";
import {
  captureSessionListSchema,
  captureSessionSchema,
  captureSummarySchema,
  transcriptSegmentSchema,
  transcriptSegmentListSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

function captureInit(request: FastifyRequest): RequestInit {
  return {
    headers: {
      // An explicit empty value prevents the engine client from attributing a
      // caller's capture data to its own service credential.
      Authorization: request.headers.authorization || ""
    }
  };
}

export async function registerCaptureRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/capture/sessions", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const limit = query.limit || "50";
    const json = await client.getJsonFromPython(
      `/v1/capture/sessions?limit=${encodeURIComponent(limit)}`,
      captureInit(request)
    );
    return captureSessionListSchema.parse(json);
  });

  app.post("/v1/capture/sessions", async (request) => {
    const json = await client.postJsonToPython(
      "/v1/capture/sessions",
      request.body,
      captureInit(request)
    );
    return captureSessionSchema.parse(json);
  });

  app.get("/v1/capture/sessions/:id", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.getJsonFromPython(
      `/v1/capture/sessions/${encodeURIComponent(id)}`,
      captureInit(request)
    );
    return captureSessionSchema.parse(json);
  });

  app.patch("/v1/capture/sessions/:id", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.patchJsonToPython(
      `/v1/capture/sessions/${encodeURIComponent(id)}`,
      request.body,
      captureInit(request)
    );
    return captureSessionSchema.parse(json);
  });

  app.post("/v1/capture/sessions/:id/transcript-segments", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.postJsonToPython(
      `/v1/capture/sessions/${encodeURIComponent(id)}/transcript-segments`,
      request.body,
      captureInit(request)
    );
    return transcriptSegmentSchema.parse(json);
  });

  app.get("/v1/capture/sessions/:id/transcript-segments", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.getJsonFromPython(
      `/v1/capture/sessions/${encodeURIComponent(id)}/transcript-segments`,
      captureInit(request)
    );
    return transcriptSegmentListSchema.parse(json);
  });

  app.post("/v1/capture/sessions/:id/process", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.postJsonToPython(
      `/v1/capture/sessions/${encodeURIComponent(id)}/process`,
      request.body ?? {},
      captureInit(request)
    );
    return captureSessionSchema.parse(json);
  });

  app.get("/v1/capture/sessions/:id/summary", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.getJsonFromPython(
      `/v1/capture/sessions/${encodeURIComponent(id)}/summary`,
      captureInit(request)
    );
    return captureSummarySchema.parse(json);
  });
}
