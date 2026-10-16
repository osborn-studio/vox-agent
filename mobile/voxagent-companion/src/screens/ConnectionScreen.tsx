import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { BASE_URL } from "../api/client";
import {
  probeEngineConnection,
  type ConnectionFailureCode,
} from "../auth/connection";
import { signInWithNativeCredential } from "../auth/nativeCredentialStorage";
import { colors, radius, spacing, typography } from "../theme";

type ConnectionError = ConnectionFailureCode | "SECURE_STORAGE_UNAVAILABLE";

export function connectionFailureMessage(error: ConnectionError): string {
  switch (error) {
    case "AUTH_REQUIRED":
      return "That credential is invalid, expired, or revoked.";
    case "AUTHORIZATION_DENIED":
      return "This account is not allowed to use Mobile capture.";
    case "DNS_ERROR":
      return "The engine hostname could not be resolved.";
    case "TLS_ERROR":
      return "The secure connection certificate could not be verified.";
    case "NETWORK_UNREACHABLE":
      return "The engine could not be reached from this device.";
    case "HEALTH_REJECTED":
      return "The endpoint responded, but it is not a healthy Vox Agent engine.";
    case "SECURE_STORAGE_UNAVAILABLE":
      return "The credential could not be saved securely on this device.";
  }
}

export default function ConnectionScreen({
  onConnected,
  notice,
}: {
  onConnected: () => void;
  notice?: string | null;
}) {
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ConnectionError | null>(null);

  const connect = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    const probe = await probeEngineConnection(credential, BASE_URL);
    if (!probe.ok) {
      setError(probe.code);
      setBusy(false);
      return;
    }
    try {
      const stored = await signInWithNativeCredential(credential);
      if (!stored) {
        setError("AUTH_REQUIRED");
        setBusy(false);
        return;
      }
    } catch {
      setError("SECURE_STORAGE_UNAVAILABLE");
      setBusy(false);
      return;
    }
    setCredential("");
    setBusy(false);
    onConnected();
  };

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.card}>
        <Text style={styles.eyebrow}>VOX AGENT · PRIVATE CONNECTION</Text>
        <Text style={styles.title}>Connect your workspace</Text>
        <Text style={styles.body}>
          Enter a revocable Mobile credential issued by your Vox Agent
          administrator. It is stored in this device&apos;s secure Keychain and
          never added to a URL or app log.
        </Text>

        <View style={styles.endpointCard}>
          <Text style={styles.endpointLabel}>ENGINE</Text>
          <Text style={styles.endpointValue}>{BASE_URL}</Text>
        </View>

        <Text style={styles.label}>Mobile credential</Text>
        <TextInput
          style={styles.input}
          value={credential}
          onChangeText={setCredential}
          placeholder="Paste credential"
          placeholderTextColor={colors.textMuted}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          editable={!busy}
          accessibilityLabel="Mobile credential"
        />

        {(notice || error) && (
          <View style={styles.errorCard}>
            <Text style={styles.errorText}>
              {error ? connectionFailureMessage(error) : notice}
            </Text>
          </View>
        )}

        <TouchableOpacity
          style={[styles.button, busy && styles.buttonDisabled]}
          onPress={() => void connect()}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel="Verify and connect"
        >
          <Text style={styles.buttonText}>
            {busy ? "Checking…" : "Verify and connect"}
          </Text>
        </TouchableOpacity>
        <Text style={styles.caption}>
          Verification checks engine health, TLS/network reachability, and
          account authorization before anything is saved.
        </Text>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    justifyContent: "center",
    backgroundColor: colors.background,
    padding: spacing.xl,
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.lg,
    padding: spacing.xl,
    gap: spacing.md,
  },
  eyebrow: {
    ...typography.caption,
    color: colors.accent,
    letterSpacing: 1,
  },
  title: {
    ...typography.title,
    color: colors.textPrimary,
  },
  body: {
    ...typography.body,
    color: colors.textSecondary,
    lineHeight: 22,
  },
  endpointCard: {
    backgroundColor: colors.chipBackground,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.xs,
  },
  endpointLabel: {
    ...typography.caption,
    color: colors.textMuted,
  },
  endpointValue: {
    ...typography.body,
    color: colors.textPrimary,
  },
  label: {
    ...typography.caption,
    color: colors.textSecondary,
    marginTop: spacing.sm,
  },
  input: {
    ...typography.body,
    color: colors.textPrimary,
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  errorCard: {
    backgroundColor: colors.dangerSoft,
    borderColor: colors.danger,
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.md,
  },
  errorText: {
    ...typography.caption,
    color: colors.danger,
  },
  button: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    alignItems: "center",
    paddingVertical: spacing.md,
    marginTop: spacing.sm,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  buttonText: {
    ...typography.body,
    color: colors.chipTextActive,
    fontWeight: "700",
  },
  caption: {
    ...typography.caption,
    color: colors.textMuted,
    lineHeight: 17,
  },
});
