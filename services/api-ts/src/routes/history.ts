import type { FastifyInstance } from "fastify";
import { agentSummaryListSchema, historyListSchema, statsResponseSchema } from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerHistoryRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/agents", async () => {
    const json = await client.getJsonFromPython("/v1/agents");
    return agentSummaryListSchema.parse(json);
  });

  app.get("/v1/history", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const q = query.q || "";
    const limit = query.limit || "50";
    const json = await client.getJsonFromPython(
      `/v1/history?q=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}`
    );
    return historyListSchema.parse(json);
  });

  app.get("/v1/history/agent", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const agent = query.agent || "";
    const limit = query.limit || "200";
    const json = await client.getJsonFromPython(
      `/v1/history/agent?agent=${encodeURIComponent(agent)}&limit=${encodeURIComponent(limit)}`
    );
    return historyListSchema.parse(json);
  });

  app.get("/v1/stats", async () => {
    const json = await client.getJsonFromPython("/v1/stats");
    return statsResponseSchema.parse(json);
  });
}
