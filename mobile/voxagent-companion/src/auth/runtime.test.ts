import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyPrincipalAuthorization,
  clearCredential,
  hasCredential,
  installCredential,
  normalizeCredential,
  reportCredentialRejected,
  subscribeCredentialRejected,
} from "./runtime";

afterEach(() => {
  clearCredential();
});

describe("credential runtime", () => {
  it("accepts bounded bearer tokens and rejects whitespace or short values", () => {
    expect(normalizeCredential(`  ${"a".repeat(32)}  `)).toBe("a".repeat(32));
    expect(normalizeCredential("short")).toBeNull();
    expect(normalizeCredential(`${"a".repeat(24)} leaked`)).toBeNull();
    expect(normalizeCredential("a".repeat(513))).toBeNull();
  });

  it("injects the installed credential without overriding an explicit header", () => {
    expect(installCredential("member-token-with-enough-entropy")).toBe(true);
    const headers = new Headers();
    applyPrincipalAuthorization(headers);
    expect(headers.get("Authorization")).toBe(
      "Bearer member-token-with-enough-entropy"
    );

    const explicit = new Headers({ Authorization: "Bearer explicit" });
    applyPrincipalAuthorization(explicit);
    expect(explicit.get("Authorization")).toBe("Bearer explicit");
  });

  it("notifies subscribers on rejection without exposing the credential", () => {
    installCredential("member-token-with-enough-entropy");
    const listener = vi.fn();
    const unsubscribe = subscribeCredentialRejected(listener);

    reportCredentialRejected();
    unsubscribe();
    reportCredentialRejected();

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith();
    expect(hasCredential()).toBe(true);
    expect(JSON.stringify(listener.mock.calls)).not.toContain("member-token");
  });
});
