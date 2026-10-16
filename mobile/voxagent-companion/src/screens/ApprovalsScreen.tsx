import React, { useEffect, useState } from "react";
import {
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import type { ApprovalItem, ApprovalState } from "../approvals/state";
import {
  approvalActions,
  approvalPhaseLabel,
  presentApproval,
} from "../approvals/presentation";
import { useApprovalReconciliation } from "../approvals/useApprovalReconciliation";
import {
  ConnectionBanner,
  type ConnectionStatus,
} from "../components/ConnectionBanner";
import { colors, radius, spacing, typography } from "../theme";

function syncPresentation(state: ApprovalState): {
  status: ConnectionStatus;
  detail: string;
} {
  switch (state.sync) {
    case "idle":
      return {
        status: "unchecked",
        detail: "Open this screen to check principal-scoped requests.",
      };
    case "loading":
      return {
        status: "loading",
        detail: "Reconciling requests with your connected workspace.",
      };
    case "live":
      return {
        status: "live",
        detail: "Only requests assigned to this workspace identity are shown.",
      };
    case "offline":
      return {
        status: "offline",
        detail: "Decisions are disabled until the workspace reconnects.",
      };
    case "unavailable":
      return {
        status: "unavailable",
        detail: "Requests could not be verified. Refresh before acting.",
      };
  }
}

function exactDate(value: string): string {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : value;
}

function remainingLabel(expiresAt: string, now: string): string {
  const remaining = Date.parse(expiresAt) - Date.parse(now);
  if (!Number.isFinite(remaining) || remaining <= 0) return "Expired";
  const seconds = Math.ceil(remaining / 1000);
  return `${seconds}s remaining`;
}

export function ApprovalCard({
  item,
  sync,
  now,
  onDecide,
}: {
  item: ApprovalItem;
  sync: ApprovalState["sync"];
  now: string;
  onDecide: (approvalId: string, approved: boolean) => void;
}) {
  const request = item.request;
  const detail = presentApproval(request);
  const actions = approvalActions(item, sync, now);

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.phase} accessibilityLiveRegion="polite">
          {approvalPhaseLabel(item)}
        </Text>
        <Text style={styles.remaining}>
          {remainingLabel(request.expires_at, now)}
        </Text>
      </View>

      <Text style={styles.fieldLabel}>TOOL</Text>
      <Text style={styles.primaryValue}>{detail.tool}</Text>

      <Text style={styles.fieldLabel}>ACTION</Text>
      <Text style={styles.primaryValue}>{detail.action}</Text>

      <Text style={styles.fieldLabel}>RESOURCE</Text>
      <Text style={styles.primaryValue}>{detail.resource}</Text>

      <Text style={styles.fieldLabel}>REQUEST DURATION</Text>
      <Text style={styles.secondaryValue}>{detail.duration}</Text>

      <View style={styles.identityBlock}>
        <Text style={styles.identityText}>
          Request ID: {detail.requestId}
        </Text>
        <Text style={styles.identityText}>Task ID: {detail.taskId}</Text>
        <Text style={styles.identityText}>Agent ID: {detail.agentId}</Text>
        <Text style={styles.identityText}>
          Requested: {exactDate(request.requested_at)}
        </Text>
        <Text style={styles.identityText}>
          Expires: {exactDate(request.expires_at)}
        </Text>
      </View>

      <Text style={styles.fieldLabel}>VERIFIED SCOPE</Text>
      {detail.scopeEntries.map(([key, value]) => (
        <View key={key} style={styles.scopeRow}>
          <Text style={styles.scopeKey}>{key}</Text>
          <Text style={styles.scopeValue}>{value}</Text>
        </View>
      ))}
      {detail.scopeEntries.length === 0 ? (
        <Text style={styles.scopeWarning}>No verified scope was provided.</Text>
      ) : null}

      {detail.scopeNotice ? (
        <Text style={styles.scopeWarning}>{detail.scopeNotice}</Text>
      ) : null}
      {item.resolutionError ? (
        <Text style={styles.errorText} accessibilityLiveRegion="assertive">
          {item.resolutionError}
        </Text>
      ) : null}

      <View style={styles.decisionRow}>
        <TouchableOpacity
          style={[
            styles.decisionButton,
            styles.denyButton,
            !actions.canDeny && styles.disabledButton,
          ]}
          disabled={!actions.canDeny}
          onPress={() => onDecide(request.approval_id, false)}
          accessibilityRole="button"
          accessibilityLabel={`Deny permission request ${detail.requestId}`}
          accessibilityState={{ disabled: !actions.canDeny }}
        >
          <Text
            style={[
              styles.denyText,
              !actions.canDeny && styles.disabledText,
            ]}
          >
            Deny
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[
            styles.decisionButton,
            styles.approveButton,
            !actions.canApprove && styles.disabledButton,
          ]}
          disabled={!actions.canApprove}
          onPress={() => onDecide(request.approval_id, true)}
          accessibilityRole="button"
          accessibilityLabel={`Approve permission request ${detail.requestId}`}
          accessibilityState={{ disabled: !actions.canApprove }}
        >
          <Text
            style={[
              styles.approveText,
              !actions.canApprove && styles.disabledText,
            ]}
          >
            Approve
          </Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

export default function ApprovalsScreen({ active }: { active: boolean }) {
  const { state, refresh, decide } = useApprovalReconciliation(active);
  const [now, setNow] = useState(() => new Date().toISOString());
  const connection = syncPresentation(state);

  useEffect(() => {
    if (!active) return;
    setNow(new Date().toISOString());
    const timer = setInterval(
      () => setNow(new Date().toISOString()),
      1_000
    );
    return () => clearInterval(timer);
  }, [active]);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
    >
      <View style={styles.titleRow}>
        <View style={styles.titleCopy}>
          <Text style={styles.title}>Approvals</Text>
          <Text style={styles.subtitle}>
            Review exact Agent scope before deciding.
          </Text>
        </View>
        <TouchableOpacity
          style={styles.refreshButton}
          onPress={() => void refresh()}
          accessibilityRole="button"
          accessibilityLabel="Refresh permission requests"
        >
          <Text style={styles.refreshText}>Refresh</Text>
        </TouchableOpacity>
      </View>

      <ConnectionBanner status={connection.status} detail={connection.detail} />

      {state.items.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No requests to review</Text>
          <Text style={styles.emptyBody}>
            Pending requests from your connected workspace will appear here.
            Approvals are never available directly from the tab bar.
          </Text>
        </View>
      ) : (
        state.items.map((item) => (
          <ApprovalCard
            key={item.request.approval_id}
            item={item}
            sync={state.sync}
            now={now}
            onDecide={(approvalId, approved) =>
              void decide(approvalId, approved)
            }
          />
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: spacing.md,
    marginBottom: spacing.lg,
  },
  titleCopy: {
    flex: 1,
  },
  title: {
    ...typography.title,
    color: colors.textPrimary,
  },
  subtitle: {
    ...typography.body,
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
  refreshButton: {
    borderWidth: 1,
    borderColor: colors.accent,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  refreshText: {
    ...typography.caption,
    color: colors.accent,
  },
  empty: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.xl,
  },
  emptyTitle: {
    ...typography.heading,
    color: colors.textPrimary,
  },
  emptyBody: {
    ...typography.body,
    color: colors.textSecondary,
    marginTop: spacing.sm,
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.lg,
    marginBottom: spacing.lg,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: spacing.sm,
    marginBottom: spacing.lg,
  },
  phase: {
    ...typography.caption,
    color: colors.textPrimary,
  },
  remaining: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  fieldLabel: {
    ...typography.caption,
    color: colors.textMuted,
    marginTop: spacing.md,
  },
  primaryValue: {
    ...typography.body,
    color: colors.textPrimary,
    marginTop: spacing.xs,
  },
  secondaryValue: {
    ...typography.body,
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
  identityBlock: {
    backgroundColor: colors.chipBackground,
    borderRadius: radius.sm,
    padding: spacing.md,
    marginTop: spacing.lg,
  },
  identityText: {
    ...typography.caption,
    color: colors.textSecondary,
    marginBottom: spacing.xs,
  },
  scopeRow: {
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingVertical: spacing.sm,
  },
  scopeKey: {
    ...typography.caption,
    color: colors.textMuted,
  },
  scopeValue: {
    ...typography.body,
    color: colors.textPrimary,
    marginTop: spacing.xs,
  },
  scopeWarning: {
    ...typography.caption,
    color: colors.warning,
    backgroundColor: colors.warningSoft,
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginTop: spacing.md,
  },
  errorText: {
    ...typography.caption,
    color: colors.danger,
    marginTop: spacing.md,
  },
  decisionRow: {
    flexDirection: "row",
    gap: spacing.md,
    marginTop: spacing.lg,
  },
  decisionButton: {
    flex: 1,
    alignItems: "center",
    borderRadius: radius.md,
    borderWidth: 1,
    paddingVertical: spacing.md,
  },
  denyButton: {
    borderColor: colors.danger,
    backgroundColor: colors.dangerSoft,
  },
  approveButton: {
    borderColor: colors.accent,
    backgroundColor: colors.accent,
  },
  denyText: {
    ...typography.body,
    color: colors.danger,
  },
  approveText: {
    ...typography.body,
    color: colors.surface,
  },
  disabledButton: {
    borderColor: colors.border,
    backgroundColor: colors.chipBackground,
  },
  disabledText: {
    color: colors.textMuted,
  },
});
