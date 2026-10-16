import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";

const SAFE_REQUEST_ID = /^[A-Za-z0-9_-]{8,64}$/;
const storage = new AsyncLocalStorage<string>();

export function resolveRequestId(value: unknown): string {
  const candidate =
    typeof value === "string"
      ? value
      : Array.isArray(value) && typeof value[0] === "string"
        ? value[0]
        : "";
  return SAFE_REQUEST_ID.test(candidate) ? candidate : randomUUID();
}

export function runWithRequestId<T>(
  requestId: string,
  operation: () => T
): T {
  return storage.run(requestId, operation);
}

export function currentRequestId(): string | undefined {
  return storage.getStore();
}

export function safeRequestSerializer(request: {
  method?: string;
}): { method: string } {
  return {
    method:
      typeof request.method === "string" && request.method.length <= 16
        ? request.method
        : "UNKNOWN",
  };
}

export function safeErrorCode(error: unknown): string {
  if (typeof error !== "object" || error === null) {
    return "UNCLASSIFIED_ERROR";
  }
  const candidate = Reflect.get(error, "code");
  return typeof candidate === "string" &&
    /^[A-Z][A-Z0-9_]{0,63}$/.test(candidate)
    ? candidate
    : "UNCLASSIFIED_ERROR";
}
