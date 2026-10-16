import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import {
  claimConversationInviteRequestSchema,
  conversationInviteClaimedSchema,
  conversationLiveDraftSchema,
  conversationLiveSnapshotSchema,
  conversationReplaySchema,
  conversationSessionCreatedSchema,
  conversationSessionSchema,
  conversationTurnSchema,
  createConversationSessionRequestSchema,
  publishConversationLiveRequestSchema,
  publishConversationTurnRequestSchema
} from "@voxlog/contracts";
import type { z } from "zod";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

function conversationInit(
  request: FastifyRequest,
  options: { includePrincipal: boolean; includeConversationToken: boolean }
): RequestInit {
  const tokenHeader = request.headers["x-voxagent-conversation-token"];
  const conversationToken = Array.isArray(tokenHeader) ? tokenHeader[0] : tokenHeader;
  return {
    headers: {
      // Explicit empty values stop the engine client from adding its service
      // credential to a public guest request or misattributing a host action.
      Authorization: options.includePrincipal ? request.headers.authorization || "" : "",
      "X-VoxAgent-Conversation-Token": options.includeConversationToken
        ? conversationToken || ""
        : ""
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
      error: "invalid_conversation_request",
      details: parsed.error.flatten()
    });
    return undefined;
  }
  return parsed.data as z.output<Schema>;
}

function replayCursor(request: FastifyRequest, reply: FastifyReply): string | undefined {
  const query = request.query as Record<string, unknown>;
  const value = query.afterSequence === undefined ? "0" : query.afterSequence;
  if (typeof value !== "string" || !/^\d+$/.test(value)) {
    reply.code(400).send({
      error: "afterSequence must be a non-negative integer"
    });
    return undefined;
  }
  return value;
}

export async function registerConversationRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.post("/v1/conversation-sessions", async (request, reply) => {
    const body = parseBody(createConversationSessionRequestSchema, request.body, reply);
    if (!body) return reply;
    const json = await client.postJsonToPython(
      "/v1/conversation-sessions",
      body,
      conversationInit(request, {
        includePrincipal: true,
        includeConversationToken: false
      })
    );
    return reply.code(201).send(conversationSessionCreatedSchema.parse(json));
  });

  app.post("/v1/conversation-sessions/:id/end", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.postJsonToPython(
      `/v1/conversation-sessions/${encodeURIComponent(id)}/end`,
      {},
      conversationInit(request, {
        includePrincipal: true,
        includeConversationToken: true
      })
    );
    return conversationSessionSchema.parse(json);
  });

  app.post("/public/v1/conversation-sessions/claim", async (request, reply) => {
    const body = parseBody(
      claimConversationInviteRequestSchema,
      request.body,
      reply
    );
    if (!body) return reply;
    const json = await client.postJsonToPython(
      "/public/v1/conversation-sessions/claim",
      body,
      conversationInit(request, {
        includePrincipal: false,
        includeConversationToken: false
      })
    );
    return conversationInviteClaimedSchema.parse(json);
  });

  app.get("/public/v1/conversation-sessions/:id/live", async (request) => {
    const { id } = request.params as { id: string };
    const json = await client.getJsonFromPython(
      `/public/v1/conversation-sessions/${encodeURIComponent(id)}/live`,
      conversationInit(request, {
        includePrincipal: false,
        includeConversationToken: true
      })
    );
    return conversationLiveSnapshotSchema.parse(json);
  });

  app.post("/public/v1/conversation-sessions/:id/live", async (request, reply) => {
    const { id } = request.params as { id: string };
    const body = parseBody(
      publishConversationLiveRequestSchema,
      request.body,
      reply
    );
    if (!body) return reply;
    const json = await client.postJsonToPython(
      `/public/v1/conversation-sessions/${encodeURIComponent(id)}/live`,
      body,
      conversationInit(request, {
        includePrincipal: false,
        includeConversationToken: true
      })
    );
    return conversationLiveDraftSchema.parse(json);
  });

  app.get("/public/v1/conversation-sessions/:id/turns", async (request, reply) => {
    const { id } = request.params as { id: string };
    const afterSequence = replayCursor(request, reply);
    if (afterSequence === undefined) return reply;
    const json = await client.getJsonFromPython(
      `/public/v1/conversation-sessions/${encodeURIComponent(id)}/turns?afterSequence=${encodeURIComponent(afterSequence)}`,
      conversationInit(request, {
        includePrincipal: false,
        includeConversationToken: true
      })
    );
    return conversationReplaySchema.parse(json);
  });

  app.post("/public/v1/conversation-sessions/:id/turns", async (request, reply) => {
    const { id } = request.params as { id: string };
    const body = parseBody(
      publishConversationTurnRequestSchema,
      request.body,
      reply
    );
    if (!body) return reply;
    const json = await client.postJsonToPython(
      `/public/v1/conversation-sessions/${encodeURIComponent(id)}/turns`,
      body,
      conversationInit(request, {
        includePrincipal: false,
        includeConversationToken: true
      })
    );
    return conversationTurnSchema.parse(json);
  });
}
