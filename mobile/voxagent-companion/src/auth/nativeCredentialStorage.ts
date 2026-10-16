import * as SecureStore from "expo-secure-store";

import {
  restoreCredential,
  signInWithCredential,
  signOutCredential,
  type CredentialStorage,
} from "./secureCredential";

const nativeCredentialStorage: CredentialStorage = {
  getItemAsync(key) {
    return SecureStore.getItemAsync(key);
  },
  setItemAsync(key, value) {
    return SecureStore.setItemAsync(key, value, {
      keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
    });
  },
  deleteItemAsync(key) {
    return SecureStore.deleteItemAsync(key);
  },
};

export function restoreNativeCredential(): Promise<boolean> {
  return restoreCredential(nativeCredentialStorage);
}

export function signInWithNativeCredential(value: string): Promise<boolean> {
  return signInWithCredential(value, nativeCredentialStorage);
}

export function signOutNativeCredential(): Promise<void> {
  return signOutCredential(nativeCredentialStorage);
}
