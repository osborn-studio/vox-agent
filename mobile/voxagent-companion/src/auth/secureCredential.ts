import {
  clearCredential,
  installCredential,
  normalizeCredential,
} from "./runtime";

export const CREDENTIAL_STORAGE_KEY = "voxagent.apiCredential.v1";

export interface CredentialStorage {
  getItemAsync(key: string): Promise<string | null>;
  setItemAsync(key: string, value: string): Promise<void>;
  deleteItemAsync(key: string): Promise<void>;
}

export async function restoreCredential(
  storage: CredentialStorage
): Promise<boolean> {
  const stored = await storage.getItemAsync(CREDENTIAL_STORAGE_KEY);
  if (!stored) {
    clearCredential();
    return false;
  }
  if (!installCredential(stored)) {
    await storage.deleteItemAsync(CREDENTIAL_STORAGE_KEY);
    return false;
  }
  return true;
}

export async function signInWithCredential(
  value: string,
  storage: CredentialStorage
): Promise<boolean> {
  const normalized = normalizeCredential(value);
  if (!normalized) return false;
  await storage.setItemAsync(CREDENTIAL_STORAGE_KEY, normalized);
  return installCredential(normalized);
}

export async function signOutCredential(
  storage: CredentialStorage
): Promise<void> {
  clearCredential();
  await storage.deleteItemAsync(CREDENTIAL_STORAGE_KEY);
}
