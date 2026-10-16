import type { FastifyInstance, FastifyRequest } from "fastify";
import {
  memorySearchResponseSchema,
  memoryNodeSchema,
  memoryNodeListSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

function principalInit(request: FastifyRequest): RequestInit {
  return {
    headers: {
      Authorization: request.headers.authorization || ""
    }
  };
}

export async function registerMemoryRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/memory/search", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const q = query.q || "";
    const limit = query.limit || "20";
    const json = await client.getJsonFromPython(
      `/v1/memory/search?q=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}`,
      principalInit(request)
    );
    return memorySearchResponseSchema.parse(json);
  });

  app.get("/v1/memory/recent", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const days = query.days || "7";
    const limit = query.limit || "20";
    const json = await client.getJsonFromPython(
      `/v1/memory/recent?days=${encodeURIComponent(days)}&limit=${encodeURIComponent(limit)}`,
      principalInit(request)
    );
    return memoryNodeListSchema.parse(json);
  });

  app.get("/v1/memory/action-items", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const status = query.status
      ? `?status=${encodeURIComponent(query.status)}`
      : "";
    const json = await client.getJsonFromPython(
      `/v1/memory/action-items${status}`,
      principalInit(request)
    );
    return memoryNodeListSchema.parse(json);
  });

  app.get("/v1/memory/nodes/:id", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.getJsonFromPython(
      `/v1/memory/nodes/${encodeURIComponent(id)}`,
      principalInit(request)
    );
    return memoryNodeSchema.parse(json);
  });

  app.post("/v1/memory/nodes", async (request) => {
    const json = await client.postJsonToPython(
      "/v1/memory/nodes",
      request.body,
      principalInit(request)
    );
    return memoryNodeSchema.parse(json);
  });
}
