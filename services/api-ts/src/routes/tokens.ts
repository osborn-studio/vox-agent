import type { FastifyInstance } from "fastify";
import { tokenBalanceSchema, tokenLedgerEntryListSchema } from "@voxlog/contracts";

import { pythonBackendClient, type BackendClient } from "../lib/backend-client.js";

export async function registerTokenRoutes(
  app: FastifyInstance,
  client: BackendClient = pythonBackendClient
) {
  app.get("/v1/tokens/balance", async () => {
    const json = await client.getJsonFromPython("/v1/tokens/balance");
    return tokenBalanceSchema.parse(json);
  });

  app.get("/v1/tokens/ledger", async () => {
    const json = await client.getJsonFromPython("/v1/tokens/ledger");
    return tokenLedgerEntryListSchema.parse(json);
  });
}
