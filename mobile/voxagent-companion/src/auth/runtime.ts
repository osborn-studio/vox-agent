const MIN_CREDENTIAL_LENGTH = 24;
const MAX_CREDENTIAL_LENGTH = 512;

let activeCredential: string | null = null;
const rejectionSubscribers = new Set<() => void>();

export function normalizeCredential(value: string): string | null {
  const normalized = value.trim();
  if (
    normalized.length < MIN_CREDENTIAL_LENGTH ||
    normalized.length > MAX_CREDENTIAL_LENGTH ||
    /\s/.test(normalized)
  ) {
    return null;
  }
  return normalized;
}

export function installCredential(value: string): boolean {
  const normalized = normalizeCredential(value);
  if (!normalized) {
    activeCredential = null;
    return false;
  }
  activeCredential = normalized;
  return true;
}

export function clearCredential(): void {
  activeCredential = null;
}

export function hasCredential(): boolean {
  return activeCredential !== null;
}

export function applyPrincipalAuthorization(headers: Headers): void {
  if (activeCredential && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${activeCredential}`);
  }
}

export function subscribeCredentialRejected(
  subscriber: () => void
): () => void {
  rejectionSubscribers.add(subscriber);
  return () => rejectionSubscribers.delete(subscriber);
}

export function reportCredentialRejected(): void {
  for (const subscriber of rejectionSubscribers) subscriber();
}
