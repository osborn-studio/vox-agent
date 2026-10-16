import {
  fetchFromPython,
  getJsonFromPython,
  parsePythonJson,
  patchJsonToPython,
  postFormToPython,
  postJsonToPython
} from "./python-backend.js";
import { createPythonBackendClient } from "./python-backend.js";
import { config } from "../config.js";

export interface BackendClient {
  fetchFromPython: typeof fetchFromPython;
  getJsonFromPython: typeof getJsonFromPython;
  parsePythonJson: typeof parsePythonJson;
  patchJsonToPython: typeof patchJsonToPython;
  postFormToPython: typeof postFormToPython;
  postJsonToPython: typeof postJsonToPython;
}

export const pythonBackendClient: BackendClient = {
  fetchFromPython,
  getJsonFromPython,
  parsePythonJson,
  patchJsonToPython,
  postFormToPython,
  postJsonToPython
};

export const voxAgentEngineClient: BackendClient = createPythonBackendClient(
  config.voxAgentEngineBaseUrl,
  config.voxAgentEngineApiToken
);
