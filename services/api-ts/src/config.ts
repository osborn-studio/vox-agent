export const config = {
  port: Number(process.env.VOXLOG_TS_PORT || "7901"),
  pythonBackendBaseUrl: process.env.VOXLOG_PY_BACKEND_URL || "http://127.0.0.1:7902",
  pythonBackendApiToken: process.env.VOXLOG_PY_BACKEND_API_TOKEN || "",
  voxAgentEngineBaseUrl: process.env.VOXAGENT_ENGINE_BASE_URL || "http://127.0.0.1:7878",
  voxAgentEngineApiToken: process.env.VOXAGENT_ENGINE_API_TOKEN || ""
};
