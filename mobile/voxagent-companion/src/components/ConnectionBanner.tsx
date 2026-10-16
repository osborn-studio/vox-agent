import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { colors, radius, spacing, typography } from "../theme";

export type ConnectionStatus =
  | "unchecked"
  | "loading"
  | "live"
  | "demo"
  | "offline"
  | "unavailable";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  unchecked: "○ Connection not checked",
  loading: "… Connecting",
  live: "✓ Live",
  demo: "◇ Demo data",
  offline: "! Offline",
  unavailable: "! Unavailable",
};

export function ConnectionBanner({
  status,
  detail,
}: {
  status: ConnectionStatus;
  detail: string;
}) {
  const isFailure = status === "offline" || status === "unavailable";
  return (
    <View
      style={[
        styles.banner,
        status === "live" && styles.live,
        status === "demo" && styles.demo,
        isFailure && styles.failure,
      ]}
      accessibilityRole="text"
      accessibilityLabel={`${STATUS_LABEL[status]}. ${detail}`}
    >
      <Text
        style={[
          styles.text,
          status === "live" && styles.liveText,
          status === "demo" && styles.demoText,
          isFailure && styles.failureText,
        ]}
      >
        {STATUS_LABEL[status]} — {detail}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    backgroundColor: colors.chipBackground,
    borderColor: colors.border,
    borderRadius: radius.md,
    borderWidth: 1,
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  live: {
    backgroundColor: colors.successSoft,
    borderColor: colors.success,
  },
  demo: {
    backgroundColor: colors.warningSoft,
    borderColor: colors.warning,
  },
  failure: {
    backgroundColor: colors.dangerSoft,
    borderColor: colors.danger,
  },
  text: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  liveText: {
    color: colors.success,
  },
  demoText: {
    color: colors.warning,
  },
  failureText: {
    color: colors.danger,
  },
});
