import React, { useEffect, useState } from "react";
import {
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { StatusBar } from "expo-status-bar";

import { BASE_URL } from "./src/api/client";
import { probeInstalledEngineConnection } from "./src/auth/connection";
import {
  restoreNativeCredential,
  signOutNativeCredential,
} from "./src/auth/nativeCredentialStorage";
import {
  clearCredential,
  subscribeCredentialRejected,
} from "./src/auth/runtime";
import { PersistentTabPanel } from "./src/components/PersistentTabPanel";
import CaptureScreen from "./src/screens/CaptureScreen";
import ConversationScreen from "./src/screens/ConversationScreen";
import ConnectionScreen from "./src/screens/ConnectionScreen";
import MemoryScreen from "./src/screens/MemoryScreen";
import AgentsScreen from "./src/screens/AgentsScreen";
import ApprovalsScreen from "./src/screens/ApprovalsScreen";
import { colors, spacing, typography } from "./src/theme";
import { sharePrivacySafeDiagnostics } from "./src/diagnostics/share";

type TabKey = "Converse" | "Capture" | "Memory" | "Agents" | "Approvals";
type AuthState = "restoring" | "signed-out" | "signed-in";

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: "Converse", label: "Converse", icon: "◎" },
  { key: "Capture", label: "Capture", icon: "●" },
  { key: "Memory", label: "Memory", icon: "⌘" },
  { key: "Agents", label: "Agents", icon: "✦" },
  { key: "Approvals", label: "Approvals", icon: "□" },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("Converse");
  const [authState, setAuthState] = useState<AuthState>("restoring");
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [memorySessionId, setMemorySessionId] = useState<string | null>(null);
  const [diagnosticsNotice, setDiagnosticsNotice] = useState<string | null>(
    null
  );

  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      try {
        const restored = await restoreNativeCredential();
        if (!restored) {
          if (!cancelled) setAuthState("signed-out");
          return;
        }
        const probe = await probeInstalledEngineConnection(BASE_URL);
        if (
          !probe.ok &&
          (probe.code === "AUTH_REQUIRED" ||
            probe.code === "AUTHORIZATION_DENIED")
        ) {
          await signOutNativeCredential();
          if (!cancelled) {
            setAuthNotice("Your saved credential is no longer valid.");
            setAuthState("signed-out");
          }
          return;
        }
        if (!cancelled) setAuthState("signed-in");
      } catch {
        clearCredential();
        if (!cancelled) {
          setAuthNotice("Secure credential storage is unavailable.");
          setAuthState("signed-out");
        }
      }
    };
    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () =>
      subscribeCredentialRejected(() => {
        setAuthNotice("Your credential was revoked. Connect again.");
        setAuthState("signed-out");
        void signOutNativeCredential().catch(clearCredential);
      }),
    []
  );

  const disconnect = async () => {
    setAuthState("signed-out");
    setAuthNotice(null);
    setMemorySessionId(null);
    try {
      await signOutNativeCredential();
    } catch {
      clearCredential();
    }
  };

  const shareDiagnostics = async () => {
    setDiagnosticsNotice(null);
    try {
      await sharePrivacySafeDiagnostics();
    } catch {
      setDiagnosticsNotice("Diagnostics unavailable");
    }
  };

  if (authState === "restoring") {
    return (
      <SafeAreaView style={styles.restoring}>
        <StatusBar style="dark" />
        <Text style={styles.restoringText}>Restoring secure connection…</Text>
      </SafeAreaView>
    );
  }

  if (authState === "signed-out") {
    return (
      <SafeAreaView style={styles.root}>
        <StatusBar style="dark" />
        <ConnectionScreen
          notice={authNotice}
          onConnected={() => {
            setAuthNotice(null);
            setActiveTab("Converse");
            setMemorySessionId(null);
            setAuthState("signed-in");
          }}
        />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="dark" />
      <View style={styles.sessionBar}>
        <Text
          style={
            diagnosticsNotice
              ? styles.sessionErrorLabel
              : styles.sessionLabel
          }
          accessibilityLiveRegion="polite"
        >
          {diagnosticsNotice ?? "Secure workspace session"}
        </Text>
        <View style={styles.sessionActions}>
          <TouchableOpacity
            onPress={() => void shareDiagnostics()}
            accessibilityRole="button"
            accessibilityLabel="Share privacy-safe diagnostics"
          >
            <Text style={styles.diagnosticsLabel}>Diagnostics</Text>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={() => void disconnect()}
            accessibilityRole="button"
            accessibilityLabel="Disconnect workspace"
          >
            <Text style={styles.disconnectLabel}>Disconnect</Text>
          </TouchableOpacity>
        </View>
      </View>
      <View style={styles.screenArea}>
        <PersistentTabPanel active={activeTab === "Converse"}>
          <ConversationScreen />
        </PersistentTabPanel>
        <PersistentTabPanel active={activeTab === "Capture"}>
          <CaptureScreen
            onOpenMemory={(sessionId) => {
              setMemorySessionId(sessionId);
              setActiveTab("Memory");
            }}
          />
        </PersistentTabPanel>
        <PersistentTabPanel active={activeTab === "Memory"}>
          <MemoryScreen focusSessionId={memorySessionId} />
        </PersistentTabPanel>
        <PersistentTabPanel active={activeTab === "Agents"}>
          <AgentsScreen />
        </PersistentTabPanel>
        <PersistentTabPanel active={activeTab === "Approvals"}>
          <ApprovalsScreen active={activeTab === "Approvals"} />
        </PersistentTabPanel>
      </View>

      <View style={styles.tabBar}>
        {TABS.map((tab) => {
          const active = tab.key === activeTab;
          return (
            <TouchableOpacity
              key={tab.key}
              style={styles.tabButton}
              onPress={() => setActiveTab(tab.key)}
              accessibilityRole="tab"
              accessibilityState={{ selected: active }}
            >
              <Text style={[styles.tabIcon, active && styles.tabIconActive]}>
                {tab.icon}
              </Text>
              <Text style={[styles.tabLabel, active && styles.tabLabelActive]}>
                {tab.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background,
  },
  screenArea: {
    flex: 1,
  },
  restoring: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
  },
  restoringText: {
    ...typography.body,
    color: colors.textSecondary,
  },
  sessionBar: {
    minHeight: 36,
    paddingHorizontal: spacing.lg,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surface,
  },
  sessionLabel: {
    ...typography.caption,
    color: colors.success,
  },
  sessionErrorLabel: {
    ...typography.caption,
    color: colors.danger,
  },
  sessionActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
  },
  diagnosticsLabel: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  disconnectLabel: {
    ...typography.caption,
    color: colors.accent,
  },
  tabBar: {
    flexDirection: "row",
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surface,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
  },
  tabButton: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
  },
  tabIcon: {
    fontSize: 18,
    color: colors.textMuted,
  },
  tabIconActive: {
    color: colors.accent,
  },
  tabLabel: {
    ...typography.caption,
    color: colors.textMuted,
  },
  tabLabelActive: {
    color: colors.accent,
    fontWeight: "700",
  },
});
