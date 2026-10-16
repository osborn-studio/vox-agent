export type DiagnosticEndpointClass =
  | "loopback"
  | "private-lan"
  | "remote-https"
  | "remote-http"
  | "invalid";

export type DiagnosticNetworkState = "reachable" | "offline";

export interface RequestDiagnostic {
  occurredAt: string;
  correlationId: string;
  endpointClass: DiagnosticEndpointClass;
  networkState: DiagnosticNetworkState;
  errorCode: string;
}

const MAX_REQUEST_DIAGNOSTICS = 20;
const SAFE_REQUEST_ID = /^[A-Za-z0-9_-]{8,64}$/;
const SAFE_ERROR_CODE = /^[A-Z][A-Z0-9_]{0,63}$/;
let requestCounter = 0;
let requestDiagnostics: RequestDiagnostic[] = [];

function isPrivateIpv4(hostname: string): boolean {
  const parts = hostname.split(".").map(Number);
  if (
    parts.length !== 4 ||
    parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)
  ) {
    return false;
  }
  return (
    parts[0] === 10 ||
    parts[0] === 127 ||
    (parts[0] === 192 && parts[1] === 168) ||
    (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
  );
}

export function classifyDiagnosticEndpoint(
  origin: string
): DiagnosticEndpointClass {
  try {
    const url = new URL(origin);
    const hostname = url.hostname.toLocaleLowerCase();
    if (
      hostname === "localhost" ||
      hostname === "::1" ||
      hostname === "[::1]" ||
      hostname.startsWith("127.")
    ) {
      return "loopback";
    }
    if (
      isPrivateIpv4(hostname) ||
      hostname.endsWith(".local") ||
      hostname.endsWith(".lan")
    ) {
      return "private-lan";
    }
    if (url.protocol === "https:") return "remote-https";
    if (url.protocol === "http:") return "remote-http";
    return "invalid";
  } catch {
    return "invalid";
  }
}

export function createRequestCorrelationId(): string {
  requestCounter = (requestCounter + 1) % 1_000_000;
  return `mobile-${Date.now().toString(36)}-${requestCounter.toString(36)}`;
}

export function installRequestCorrelation(headers: Headers): string {
  const existing = headers.get("X-Request-ID") || "";
  const correlationId = SAFE_REQUEST_ID.test(existing)
    ? existing
    : createRequestCorrelationId();
  headers.set("X-Request-ID", correlationId);
  return correlationId;
}

export function responseCorrelationId(
  response: Response,
  fallback: string
): string {
  const candidate = response.headers.get("X-Request-ID") || "";
  return SAFE_REQUEST_ID.test(candidate) ? candidate : fallback;
}

export function redactDiagnosticErrorCode(error: unknown): string {
  if (typeof error !== "object" || error === null) {
    return "UNCLASSIFIED_ERROR";
  }
  const candidate = Reflect.get(error, "code");
  return typeof candidate === "string" && SAFE_ERROR_CODE.test(candidate)
    ? candidate
    : "UNCLASSIFIED_ERROR";
}

export function recordRequestDiagnostic(input: {
  correlationId: string;
  endpointOrigin: string;
  networkState: DiagnosticNetworkState;
  error: unknown;
  now?: Date;
}): void {
  const correlationId = SAFE_REQUEST_ID.test(input.correlationId)
    ? input.correlationId
    : createRequestCorrelationId();
  const event: RequestDiagnostic = {
    occurredAt: (input.now ?? new Date()).toISOString(),
    correlationId,
    endpointClass: classifyDiagnosticEndpoint(input.endpointOrigin),
    networkState: input.networkState,
    errorCode: redactDiagnosticErrorCode(input.error),
  };
  requestDiagnostics = [event, ...requestDiagnostics].slice(
    0,
    MAX_REQUEST_DIAGNOSTICS
  );
}

export function getRequestDiagnostics(): RequestDiagnostic[] {
  return requestDiagnostics.map((event) => ({ ...event }));
}

export function clearRequestDiagnosticsForTests(): void {
  requestDiagnostics = [];
  requestCounter = 0;
}
