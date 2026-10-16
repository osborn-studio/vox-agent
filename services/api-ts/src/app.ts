import formbody from "@fastify/formbody";
import multipart from "@fastify/multipart";
import Fastify, { LogController, type FastifyInstance } from "fastify";
import {
  DESKTOP_CONTRACT_VERSION,
  DESKTOP_PRODUCT_NAME,
  desktopContractSchema,
  healthResponseSchema
} from "@voxlog/contracts";

import { config } from "./config.js";
import {
  pythonBackendClient,
  voxAgentEngineClient,
  type BackendClient
} from "./lib/backend-client.js";
import { registerAdminRoutes } from "./routes/admin.js";
import { registerAgentQueryRoutes } from "./routes/agents-query.js";
import { registerCaptureRoutes } from "./routes/capture.js";
import { registerConversationRoutes } from "./routes/conversation.js";
import { registerDeviceRoutes } from "./routes/devices.js";
import { registerDictionaryRoutes } from "./routes/dictionary.js";
import { registerDigestRoutes } from "./routes/digest.js";
import { registerHistoryRoutes } from "./routes/history.js";
import { registerMemoryRoutes } from "./routes/memory.js";
import { registerMigrationRoutes } from "./routes/migration.js";
import { registerPermissionRoutes } from "./routes/permissions.js";
import { registerRecentRoutes } from "./routes/recent.js";
import { registerSettingsRoutes } from "./routes/settings.js";
import { registerSpeechRoutes } from "./routes/speech.js";
import { registerTokenRoutes } from "./routes/tokens.js";
import { registerTranslationRoutes } from "./routes/translation.js";
import { registerVoiceRoutes } from "./routes/voice.js";
import {
  resolveRequestId,
  runWithRequestId,
  safeErrorCode,
  safeRequestSerializer
} from "./lib/request-correlation.js";

export async function createApp(
  client?: BackendClient,
  engineClient?: BackendClient
): Promise<FastifyInstance> {
  // A single injected client keeps unit tests concise. Production intentionally
  // uses separate upstreams so Agent routes cannot fall back to the legacy
  // recorder backend.
  const legacy = client || pythonBackendClient;
  const engine = engineClient || (client ? client : voxAgentEngineClient);
  const app = Fastify({
    genReqId: (request) =>
      resolveRequestId(request.headers["x-request-id"]),
    logController: new LogController({ disableRequestLogging: true }),
    logger: {
      base: null,
      serializers: {
        req: safeRequestSerializer
      }
    }
  });

  await app.register(multipart);
  await app.register(formbody);
  app.addHook("onRequest", (request, _reply, done) => {
    runWithRequestId(request.id, done);
  });
  app.addHook("onSend", (request, reply, payload, done) => {
    reply.header("x-request-id", request.id);
    done(null, payload);
  });
  app.addHook("onError", (request, reply, error, done) => {
    request.log.warn(
      {
        method: safeRequestSerializer(request).method,
        route: request.routeOptions.url,
        errorCode: safeErrorCode(error)
      },
      "request failed"
    );
    done();
  });
  app.addHook("onResponse", (request, reply, done) => {
    request.log.info(
      {
        method: safeRequestSerializer(request).method,
        route: request.routeOptions.url,
        statusCode: reply.statusCode,
        responseTimeMs: Math.round(reply.elapsedTime)
      },
      "request completed"
    );
    done();
  });

  app.get("/health", async () => {
    return healthResponseSchema.parse({
      status: "ok",
      service: "api-ts",
      python_backend_base_url: config.pythonBackendBaseUrl,
      vox_agent_engine_base_url: config.voxAgentEngineBaseUrl
    });
  });

  app.get("/v1/desktop/contract", async () => {
    return desktopContractSchema.parse({
      api_version: DESKTOP_CONTRACT_VERSION,
      product: DESKTOP_PRODUCT_NAME,
      capabilities: [
        "agent.query",
        "speech.stream.v1",
        "translation.realtime.v1",
        "conversation.session.v1",
        "voice.capture",
        "history.search",
        "data.migration.v1",
        "settings.providers",
        "digest.export"
      ]
    });
  });

  await registerRecentRoutes(app, legacy);
  await registerSettingsRoutes(app, legacy);
  await registerDictionaryRoutes(app, legacy);
  await registerDigestRoutes(app, legacy);
  await registerHistoryRoutes(app, legacy);
  await registerMigrationRoutes(app, legacy);
  await registerVoiceRoutes(app, legacy);
  await registerCaptureRoutes(app, engine);
  await registerMemoryRoutes(app, engine);
  await registerPermissionRoutes(app, engine);
  await registerDeviceRoutes(app, engine);
  await registerTokenRoutes(app, engine);
  await registerSpeechRoutes(app, engine);
  await registerTranslationRoutes(app, engine);
  await registerConversationRoutes(app, engine);
  await registerAgentQueryRoutes(app, engine);
  await registerAdminRoutes(app, engine);

  return app;
}
