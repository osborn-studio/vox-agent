import { config } from "./config.js";
import { createApp } from "./app.js";

const app = await createApp();
await app.listen({
  host: "127.0.0.1",
  port: config.port
});
