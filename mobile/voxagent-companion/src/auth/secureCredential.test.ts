import { afterEach, describe, expect, it } from "vitest";

import {
  clearCredential,
  hasCredential,
} from "./runtime";
import {
  CREDENTIAL_STORAGE_KEY,
  restoreCredential,
  signInWithCredential,
  signOutCredential,
  type CredentialStorage,
} from "./secureCredential";

class FakeStorage implements CredentialStorage {
  value: string | null = null;
  deleted = false;
  failDelete = false;

  async getItemAsync() {
    return this.value;
  }

  async setItemAsync(_key: string, value: string) {
    this.value = value;
  }

  async deleteItemAsync() {
    this.deleted = true;
    this.value = null;
    if (this.failDelete) throw new Error("keychain unavailable");
  }
}

afterEach(() => {
  clearCredential();
});

describe("secure credential persistence", () => {
  it("persists a normalized credential and installs it only after storage succeeds", async () => {
    const storage = new FakeStorage();

    const signedIn = await signInWithCredential(
      `  ${"a".repeat(32)}  `,
      storage
    );

    expect(signedIn).toBe(true);
    expect(storage.value).toBe("a".repeat(32));
    expect(hasCredential()).toBe(true);
    expect(CREDENTIAL_STORAGE_KEY).toBe("voxagent.apiCredential.v1");
  });

  it("restores a valid credential and deletes an invalid stored value", async () => {
    const validStorage = new FakeStorage();
    validStorage.value = "restored-token-with-enough-entropy";
    expect(await restoreCredential(validStorage)).toBe(true);
    expect(hasCredential()).toBe(true);

    clearCredential();
    const invalidStorage = new FakeStorage();
    invalidStorage.value = "short";
    expect(await restoreCredential(invalidStorage)).toBe(false);
    expect(invalidStorage.deleted).toBe(true);
    expect(hasCredential()).toBe(false);
  });

  it("clears the in-memory credential even if Keychain deletion fails", async () => {
    const storage = new FakeStorage();
    await signInWithCredential("member-token-with-enough-entropy", storage);
    storage.failDelete = true;

    await expect(signOutCredential(storage)).rejects.toThrow(
      "keychain unavailable"
    );
    expect(hasCredential()).toBe(false);
  });
});
