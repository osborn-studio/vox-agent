import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import {
  permissionDecisionRequestSchema,
  permissionDecisionSchema,
  permissionRequestListSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

function principalAuth(request: FastifyRequest): RequestInit {
  return {
    headers: {
      // Preserve an explicit empty value so the engine client cannot replace a
      // missing caller credential with its own service credential.
      Authorization: request.headers.authorization || ""
    }
  };
}

export async function registerPermissionRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/permissions/pending", async (request) => {
    const json = await client.getJsonFromPython(
      "/v1/permissions/pending",
      principalAuth(request)
    );
    return permissionRequestListSchema.parse(json);
  });

  app.post("/v1/permissions/approve", async (request, reply: FastifyReply) => {
    const parsed = permissionDecisionRequestSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({
        error: "invalid_permission_decision",
        details: parsed.error.flatten()
      });
    }
    const json = await client.postJsonToPython(
      "/v1/permissions/approve",
      parsed.data,
      principalAuth(request)
    );
    return permissionDecisionSchema.parse(json);
  });
}
