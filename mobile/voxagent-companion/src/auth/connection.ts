import {
  applyPrincipalAuthorization,
  normalizeCredential,
} from "./runtime";

const CONNECTION_TIMEOUT_MS = 6000;

export type ConnectionFailureCode =
  | "AUTH_REQUIRED"
  | "AUTHORIZATION_DENIED"
  | "DNS_ERROR"
  | "TLS_ERROR"
  | "NETWORK_UNREACHABLE"
  | "HEALTH_REJECTED";

export type ConnectionProbeResult =
  | { ok: true }
  | { ok: false; code: ConnectionFailureCode };

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export async function probeEngineConnection(
  credential: string,
  origin: string,
  fetchImpl: FetchLike = fetch
): Promise<ConnectionProbeResult> {
  const normalized = normalizeCredential(credential);
  if (!normalized) return { ok: false, code: "AUTH_REQUIRED" };
  return probeWithAuthorization(`Bearer ${normalized}`, origin, fetchImpl);
}

export function probeInstalledEngineConnection(
  origin: string,
  fetchImpl: FetchLike = fetch
): Promise<ConnectionProbeResult> {
  const headers = new Headers();
  applyPrincipalAuthorization(headers);
  const authorization = headers.get("Authorization");
  if (!authorization) {
    return Promise.resolve({ ok: false, code: "AUTH_REQUIRED" });
  }
  return probeWithAuthorization(authorization, origin, fetchImpl);
}

async function probeWithAuthorization(
  authorization: string,
  origin: string,
  fetchImpl: FetchLike
): Promise<ConnectionProbeResult> {
  try {
    const health = await timedFetch(fetchImpl, `${origin}/health`);
    if (!health.ok) return { ok: false, code: "HEALTH_REJECTED" };
    const healthBody: unknown = await health.json();
    if (
      typeof healthBody !== "object" ||
      healthBody === null ||
      (healthBody as Record<string, unknown>).status !== "ok"
    ) {
      return { ok: false, code: "HEALTH_REJECTED" };
    }

    const principal = await timedFetch(
      fetchImpl,
      `${origin}/v1/capture/sessions?limit=1`,
      { headers: { Authorization: authorization } }
    );
    if (principal.status === 401) {
      return { ok: false, code: "AUTH_REQUIRED" };
    }
    if (principal.status === 403) {
      return { ok: false, code: "AUTHORIZATION_DENIED" };
    }
    if (!principal.ok) return { ok: false, code: "HEALTH_REJECTED" };
    const principalBody: unknown = await principal.json();
    return Array.isArray(principalBody)
      ? { ok: true }
      : { ok: false, code: "HEALTH_REJECTED" };
  } catch (error) {
    return { ok: false, code: classifyTransportError(error) };
  }
}

async function timedFetch(
  fetchImpl: FetchLike,
  input: string,
  init?: RequestInit
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONNECTION_TIMEOUT_MS);
  try {
    return await fetchImpl(input, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function classifyTransportError(error: unknown): ConnectionFailureCode {
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  if (
    message.includes("certificate") ||
    message.includes("ssl") ||
    message.includes("tls")
  ) {
    return "TLS_ERROR";
  }
  if (
    message.includes("dns") ||
    message.includes("enotfound") ||
    message.includes("getaddrinfo") ||
    message.includes("resolve host")
  ) {
    return "DNS_ERROR";
  }
  return "NETWORK_UNREACHABLE";
}
