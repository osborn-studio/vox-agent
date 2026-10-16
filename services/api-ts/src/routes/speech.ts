import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import {
  cancelSpeechStreamRequestSchema,
  completeSpeechStreamRequestSchema,
  createSpeechStreamRequestSchema,
  speechAudioChunkAckSchema,
  speechEventSchema,
  speechReplaySchema,
  speechStreamCreatedSchema,
  speechTranscriptRequestSchema
} from "@voxlog/contracts";
import type { z } from "zod";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

function streamAuth(request: FastifyRequest): RequestInit {
  const value = request.headers["x-voxagent-stream-token"];
  const token = Array.isArray(value) ? value[0] : value;
  return {
    headers: {
      Authorization: request.headers.authorization || "",
      "X-VoxAgent-Stream-Token": token || ""
    }
  };
}

function principalAuth(request: FastifyRequest): RequestInit {
  return {
    headers: {
      Authorization: request.headers.authorization || ""
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
      error: "invalid_speech_stream_request",
      details: parsed.error.flatten()
    });
    return undefined;
  }
  return parsed.data as z.output<Schema>;
}

export async function registerSpeechRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.addContentTypeParser(
    "application/octet-stream",
    { parseAs: "buffer", bodyLimit: 256 * 1024 },
    (_request, body, done) => done(null, body)
  );

  app.post("/v1/speech/streams", async (request, reply) => {
    const body = parseBody(createSpeechStreamRequestSchema, request.body, reply);
    if (!body) return reply;
    const json = await client.postJsonToPython(
      "/v1/speech/streams",
      body,
      principalAuth(request)
    );
    return reply.code(201).send(speechStreamCreatedSchema.parse(json));
  });

  app.get("/v1/speech/streams/:id/events", async (request, reply) => {
    const { id } = request.params as { id: string };
    const query = request.query as Record<string, string | undefined>;
    const afterSequence = query.afterSequence || "0";
    if (!/^\d+$/.test(afterSequence)) {
      return reply.code(400).send({ error: "afterSequence must be a non-negative integer" });
    }
    const json = await client.getJsonFromPython(
      `/v1/speech/streams/${encodeURIComponent(id)}/events?afterSequence=${encodeURIComponent(afterSequence)}`,
      streamAuth(request)
    );
    return speechReplaySchema.parse(json);
  });

  app.post("/v1/speech/streams/:id/audio/chunks", async (request, reply) => {
    const { id } = request.params as { id: string };
    const body = request.body;
    if (!Buffer.isBuffer(body) || body.length === 0 || body.length % 2 !== 0) {
      return reply.code(400).send({ error: "invalid_pcm_audio_chunk" });
    }
    const chunkIndex = requiredIntegerHeader(
      request,
      "x-voxagent-audio-chunk-index",
      0
    );
    const sampleRate = requiredIntegerHeader(
      request,
      "x-voxagent-audio-sample-rate",
      8_000,
      48_000
    );
    const channels = requiredIntegerHeader(
      request,
      "x-voxagent-audio-channels",
      1,
      1
    );
    const encoding = oneHeader(request, "x-voxagent-audio-encoding");
    if (
      chunkIndex === undefined ||
      sampleRate === undefined ||
      channels === undefined ||
      encoding !== "pcm_s16le"
    ) {
      return reply.code(400).send({ error: "invalid_pcm_audio_metadata" });
    }
    const headers = new Headers(streamAuth(request).headers);
    headers.set("Content-Type", "application/octet-stream");
    headers.set("X-VoxAgent-Audio-Chunk-Index", String(chunkIndex));
    headers.set("X-VoxAgent-Audio-Sample-Rate", String(sampleRate));
    headers.set("X-VoxAgent-Audio-Channels", String(channels));
    headers.set("X-VoxAgent-Audio-Encoding", encoding);
    const response = await client.fetchFromPython(
      `/v1/speech/streams/${encodeURIComponent(id)}/audio/chunks`,
      {
        method: "POST",
        headers,
        body: Uint8Array.from(body).buffer
      }
    );
    const json = await client.parsePythonJson(response);
    if (!response.ok) {
      return reply.code(response.status).send(json);
    }
    return speechAudioChunkAckSchema.parse(json);
  });

  for (const [suffix, schema] of [
    ["partial", speechTranscriptRequestSchema],
    ["final", speechTranscriptRequestSchema],
    ["cancel", cancelSpeechStreamRequestSchema],
    ["complete", completeSpeechStreamRequestSchema]
  ] as const) {
    app.post(`/v1/speech/streams/:id/${suffix}`, async (request, reply) => {
      const { id } = request.params as { id: string };
      const body = parseBody(schema, request.body, reply);
      if (!body) return reply;
      const json = await client.postJsonToPython(
        `/v1/speech/streams/${encodeURIComponent(id)}/${suffix}`,
        body,
        streamAuth(request)
      );
      return speechEventSchema.parse(json);
    });
  }
}

function oneHeader(
  request: FastifyRequest,
  name: string
): string | undefined {
  const value = request.headers[name];
  return Array.isArray(value) ? value[0] : value;
}

function requiredIntegerHeader(
  request: FastifyRequest,
  name: string,
  minimum: number,
  maximum: number = Number.MAX_SAFE_INTEGER
): number | undefined {
  const value = oneHeader(request, name);
  if (!value || !/^\d+$/.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) &&
    parsed >= minimum &&
    parsed <= maximum
    ? parsed
    : undefined;
}
