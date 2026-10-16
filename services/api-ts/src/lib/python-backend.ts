import { currentRequestId } from "./request-correlation.js";

export function createPythonBackendClient(baseUrl: string, token: string) {
  async function fetchFromPython(path: string, init?: RequestInit): Promise<Response> {
    const headers = new Headers(init?.headers || {});
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    const requestId = currentRequestId();
    if (requestId && !headers.has("X-Request-ID")) {
      headers.set("X-Request-ID", requestId);
    }
    return fetch(`${baseUrl}${path}`, {
      ...init,
      headers
    });
  }

  async function parsePythonJson<T = unknown>(response: Response): Promise<T> {
    if (!response.ok) {
      const text = await response.text();
      const error = new Error(
        `python backend ${response.status}: ${text || response.statusText}`
      ) as Error & {
        statusCode: number;
      };
      error.statusCode = response.status;
      throw error;
    }
    return (await response.json()) as T;
  }

  async function getJsonFromPython<T = unknown>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetchFromPython(path, init);
    return parsePythonJson<T>(response);
  }

  async function postFormToPython<T = unknown>(
    path: string,
    form: Record<string, string>
  ): Promise<T> {
    const response = await fetchFromPython(path, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(form).toString()
    });
    return parsePythonJson<T>(response);
  }

  async function postJsonToPython<T = unknown>(
    path: string,
    body: unknown,
    init?: RequestInit
  ): Promise<T> {
    const headers = new Headers(init?.headers);
    headers.set("content-type", "application/json");
    const response = await fetchFromPython(path, {
      ...init,
      method: "POST",
      headers,
      body: JSON.stringify(body)
    });
    return parsePythonJson<T>(response);
  }

  async function patchJsonToPython<T = unknown>(
    path: string,
    body: unknown,
    init?: RequestInit
  ): Promise<T> {
    const headers = new Headers(init?.headers);
    headers.set("content-type", "application/json");
    const response = await fetchFromPython(path, {
      ...init,
      method: "PATCH",
      headers,
      body: JSON.stringify(body)
    });
    return parsePythonJson<T>(response);
  }

  return {
    fetchFromPython,
    getJsonFromPython,
    parsePythonJson,
    patchJsonToPython,
    postFormToPython,
    postJsonToPython
  };
}

const defaultClient = createPythonBackendClient(
  process.env.VOXLOG_PY_BACKEND_URL || "http://127.0.0.1:7902",
  process.env.VOXLOG_PY_BACKEND_API_TOKEN || ""
);

export const fetchFromPython = defaultClient.fetchFromPython;
export const getJsonFromPython = defaultClient.getJsonFromPython;
export const parsePythonJson = defaultClient.parsePythonJson;
export const patchJsonToPython = defaultClient.patchJsonToPython;
export const postFormToPython = defaultClient.postFormToPython;
export const postJsonToPython = defaultClient.postJsonToPython;
