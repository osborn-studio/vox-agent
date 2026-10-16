import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import {
  translationEventSchema,
  translationRequestSchema
} from "@voxlog/contracts";
import type { z } from "zod";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

function authInit(request: FastifyRequest): RequestInit {
  // An empty header prevents the engine client from replacing a missing caller
  // credential with its service credential and misattributing the audit actor.
  const streamToken = request.headers["x-voxagent-stream-token"];
  return {
    headers: {
      Authorization: request.headers.authorization || "",
      "X-VoxAgent-Stream-Token": Array.isArray(streamToken)
        ? streamToken[0] || ""
        : streamToken || ""
    }
  };
}

function parseBody<Schema extends z.ZodTypeAny>(
  schema: Schema,
  body: unknown,
  reply: FastifyReply
): z.output<Schema> | undefined {
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    reply.code(400).send({
      error: "invalid_translation_request",
      details: parsed.error.flatten()
    });
    return undefined;
  }
  return parsed.data as z.output<Schema>;
}

export async function registerTranslationRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.post("/v1/translations", async (request, reply) => {
    const body = parseBody(translationRequestSchema, request.body, reply);
    if (!body) return reply;
    const json = await client.postJsonToPython(
      "/v1/translations",
      body,
      authInit(request)
    );
    return translationEventSchema.parse(json);
  });
}
