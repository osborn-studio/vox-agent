import type { FastifyInstance, FastifyRequest } from "fastify";
import {
  agentAnswerSchema,
  agentQueryRequestSchema,
  agentRecentContextSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

function principalInit(request: FastifyRequest): RequestInit {
  return {
    headers: {
      Authorization: request.headers.authorization || ""
    }
  };
}

export async function registerAgentQueryRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.post("/v1/agents/query", async (request, reply) => {
    const parsed = agentQueryRequestSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({
        error: "invalid_agent_query",
        details: parsed.error.flatten()
      });
    }
    const json = await client.postJsonToPython(
      "/v1/agents/query",
      parsed.data,
      principalInit(request)
    );
    return agentAnswerSchema.parse(json);
  });

  app.get("/v1/agents/context/recent", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const days = query.days || "7";
    const limit = query.limit || "20";
    const json = await client.getJsonFromPython(
      `/v1/agents/context/recent?days=${encodeURIComponent(days)}&limit=${encodeURIComponent(limit)}`,
      principalInit(request)
    );
    return agentRecentContextSchema.parse(json);
  });
}
