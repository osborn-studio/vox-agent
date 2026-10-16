import type { FastifyInstance } from "fastify";
import type { MultipartFile } from "@fastify/multipart";
import { fastPathResponseSchema } from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerVoiceRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.post("/v1/voice", async (request, reply) => {
    const multipartRequest = request as typeof request & {
      file: () => Promise<MultipartFile | undefined>;
    };
    const file = await multipartRequest.file();
    if (!file) {
      return reply.code(400).send({ error: "audio file is required" });
    }

    const buffer = await file.toBuffer();
    const form = new FormData();
    const type = file.mimetype || "application/octet-stream";
    const filename = file.filename || "utterance.bin";

    form.append("audio", new Blob([new Uint8Array(buffer)], { type }), filename);

    const fields = {
      source: "desktop-tauri",
      env: "auto",
      agent: "",
      target_app: "",
      session_id: "",
      mode: "normal"
    };

    for (const [key, value] of Object.entries(file.fields as Record<string, unknown>)) {
      const field = Array.isArray(value) ? value[0] : value;
      const fieldValue =
        field && typeof field === "object" && "value" in field ? (field as { value?: unknown }).value : undefined;
      if (typeof fieldValue === "string" && key in fields) {
        fields[key as keyof typeof fields] = fieldValue;
      }
    }

    for (const [key, value] of Object.entries(fields)) {
      form.append(key, value);
    }

    const response = await client.fetchFromPython("/v1/voice", {
      method: "POST",
      body: form
    });
    const json = await client.parsePythonJson(response);
    return fastPathResponseSchema.parse(json);
  });

  app.post("/v1/voice/text", async (request) => {
    const body = request.body as Record<string, unknown>;
    const json = await client.postJsonToPython("/v1/voice/text", body);
    return fastPathResponseSchema.parse(json);
  });
}
