import type { FastifyInstance, FastifyRequest } from "fastify";
import {
  adminUserListSchema,
  adminUserSchema,
  auditEventListSchema,
  createAdminUserSchema,
  issuedCredentialSchema,
  modelPolicyPatchSchema,
  modelPolicySchema,
  latestRetentionSweepSchema,
  retentionPolicyPatchSchema,
  retentionPolicySchema,
  retentionSweepRequestSchema,
  retentionSweepSchema,
  updateAdminUserSchema
} from "@voxlog/contracts";
import { z } from "zod";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

const credentialRequestSchema = z.object({ name: z.string().min(1).max(80) }).strict();

function authInit(request: FastifyRequest): RequestInit {
  // Always set the header, even when empty, so fetchFromPython cannot replace
  // a missing end-user credential with the gateway's service credential.
  return { headers: { Authorization: request.headers.authorization || "" } };
}

export async function registerAdminRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/admin/audit-events", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const limit = query.limit || "50";
    const json = await client.getJsonFromPython(
      `/v1/admin/audit-events?limit=${encodeURIComponent(limit)}`,
      authInit(request)
    );
    return auditEventListSchema.parse(json);
  });

  app.get("/v1/admin/model-policy", async (request) => {
    const json = await client.getJsonFromPython("/v1/admin/model-policy", authInit(request));
    return modelPolicySchema.parse(json);
  });

  app.patch("/v1/admin/model-policy", async (request) => {
    const body = modelPolicyPatchSchema.parse(request.body);
    const json = await client.patchJsonToPython("/v1/admin/model-policy", body, authInit(request));
    return modelPolicySchema.parse(json);
  });

  app.get("/v1/admin/retention-policy", async (request) => {
    const json = await client.getJsonFromPython("/v1/admin/retention-policy", authInit(request));
    return retentionPolicySchema.parse(json);
  });

  app.patch("/v1/admin/retention-policy", async (request) => {
    const body = retentionPolicyPatchSchema.parse(request.body);
    const json = await client.patchJsonToPython("/v1/admin/retention-policy", body, authInit(request));
    return retentionPolicySchema.parse(json);
  });

  app.get("/v1/admin/retention/sweeps/latest", async (request) => {
    const json = await client.getJsonFromPython(
      "/v1/admin/retention/sweeps/latest",
      authInit(request)
    );
    return latestRetentionSweepSchema.parse(json);
  });

  app.post("/v1/admin/retention/sweeps", async (request) => {
    const body = retentionSweepRequestSchema.parse(request.body);
    const json = await client.postJsonToPython(
      "/v1/admin/retention/sweeps",
      body,
      authInit(request)
    );
    return retentionSweepSchema.parse(json);
  });

  app.get("/v1/admin/users", async (request) => {
    const json = await client.getJsonFromPython("/v1/admin/users", authInit(request));
    return adminUserListSchema.parse(json);
  });

  app.post("/v1/admin/users", async (request, reply) => {
    const body = createAdminUserSchema.parse(request.body);
    const json = await client.postJsonToPython("/v1/admin/users", body, authInit(request));
    return reply.code(201).send(adminUserSchema.parse(json));
  });

  app.patch("/v1/admin/users/:userId", async (request) => {
    const { userId } = request.params as { userId: string };
    const body = updateAdminUserSchema.parse(request.body);
    const json = await client.patchJsonToPython(
      `/v1/admin/users/${encodeURIComponent(userId)}`,
      body,
      authInit(request)
    );
    return adminUserSchema.parse(json);
  });

  app.post("/v1/admin/users/:userId/credentials", async (request, reply) => {
    const { userId } = request.params as { userId: string };
    const body = credentialRequestSchema.parse(request.body);
    const json = await client.postJsonToPython(
      `/v1/admin/users/${encodeURIComponent(userId)}/credentials`,
      body,
      authInit(request)
    );
    return reply.code(201).send(issuedCredentialSchema.parse(json));
  });

  app.delete("/v1/admin/credentials/:credentialId", async (request, reply) => {
    const { credentialId } = request.params as { credentialId: string };
    const response = await client.fetchFromPython(
      `/v1/admin/credentials/${encodeURIComponent(credentialId)}`,
      { ...authInit(request), method: "DELETE" }
    );
    if (!response.ok) await client.parsePythonJson(response);
    return reply.code(204).send();
  });
}
