import type { FastifyInstance } from "fastify";
import {
  aiMateMemoryExportResponseSchema,
  dailyDigestSchema,
  obsidianExportResponseSchema,
  projectDigestSchema,
  sessionDigestSchema
} from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerDigestRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/digests/session", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const sessionId = query.session_id || "";
    const json = await client.getJsonFromPython(
      `/v1/digests/session?session_id=${encodeURIComponent(sessionId)}`
    );
    return sessionDigestSchema.parse(json);
  });

  app.get("/v1/digests/daily", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const date = query.date || "";
    const json = await client.getJsonFromPython(
      `/v1/digests/daily?date=${encodeURIComponent(date)}`
    );
    return dailyDigestSchema.parse(json);
  });

  app.get("/v1/digests/project", async (request) => {
    const query = request.query as Record<string, string | undefined>;
    const projectKey = query.project_key || "";
    const json = await client.getJsonFromPython(
      `/v1/digests/project?project_key=${encodeURIComponent(projectKey)}`
    );
    return projectDigestSchema.parse(json);
  });

  app.post("/v1/digests/rebuild", async (request) => {
    const body = request.body as Record<string, unknown>;
    const json = await client.postJsonToPython("/v1/digests/rebuild", body);
    const parsed = json as Record<string, unknown>;
    if (parsed.digest_type === "daily_digest") {
      return dailyDigestSchema.parse(parsed);
    }
    if (parsed.digest_type === "project_digest") {
      return projectDigestSchema.parse(parsed);
    }
    return sessionDigestSchema.parse(parsed);
  });

  app.post("/v1/integrations/obsidian/export", async (request) => {
    const body = request.body as Record<string, unknown>;
    const json = await client.postJsonToPython("/v1/integrations/obsidian/export", body);
    return obsidianExportResponseSchema.parse(json);
  });

  app.post("/v1/integrations/ai-mate-memory/export", async (request) => {
    const body = request.body as Record<string, unknown>;
    const json = await client.postJsonToPython("/v1/integrations/ai-mate-memory/export", body);
    return aiMateMemoryExportResponseSchema.parse(json);
  });
}
