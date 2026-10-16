import type { FastifyInstance } from "fastify";
import {
  legacyMigrationResponseSchema,
  migrationDictionaryImportResponseSchema,
  migrationDictionarySchema,
  migrationHistoryImportResponseSchema,
  migrationHistoryImportSchema,
  migrationHistoryListSchema,
  migrationHistoryQuerySchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerMigrationRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/migration/history", async (request, reply) => {
    const parsed = migrationHistoryQuerySchema.safeParse(request.query);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid migration history page" });
    }
    const { offset, limit } = parsed.data;
    const json = await client.getJsonFromPython(
      `/v1/migration/history?offset=${offset}&limit=${limit}`
    );
    return migrationHistoryListSchema.parse(json);
  });

  app.post("/v1/migration/history/import", async (request, reply) => {
    const parsed = migrationHistoryImportSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid migration history import" });
    }
    const json = await client.postJsonToPython(
      "/v1/migration/history/import",
      parsed.data
    );
    return migrationHistoryImportResponseSchema.parse(json);
  });

  app.post("/v1/migration/dictionary/import", async (request, reply) => {
    const parsed = migrationDictionarySchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid migration dictionary import" });
    }
    const json = await client.postJsonToPython(
      "/v1/migration/dictionary/import",
      parsed.data
    );
    return migrationDictionaryImportResponseSchema.parse(json);
  });

  app.get("/v1/migration/legacy", async () => {
    const json = await client.getJsonFromPython("/v1/migration/legacy");
    return legacyMigrationResponseSchema.parse(json);
  });

  app.post("/v1/migration/legacy/import", async (request, reply) => {
    if (
      request.body !== undefined &&
      (typeof request.body !== "object" || request.body === null || Object.keys(request.body).length)
    ) {
      return reply.code(400).send({ error: "legacy migration import takes no fields" });
    }
    const json = await client.postJsonToPython("/v1/migration/legacy/import", {});
    return legacyMigrationResponseSchema.parse(json);
  });
}
