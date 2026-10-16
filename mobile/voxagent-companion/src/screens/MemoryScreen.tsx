import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import {
  CaptureSummary,
  CaptureSession,
  MemoryNode,
  MemoryNodeType,
  MemorySearchResult,
  getActionItems,
  getCaptureSummary,
  getRecent,
  searchMemory,
} from "../api/client";
import {
  ConnectionBanner,
  type ConnectionStatus,
} from "../components/ConnectionBanner";
import { colors, radius, spacing, typography } from "../theme";

type Segment = "Today" | "Inbox";
export type CaptureSummaryPresentationState =
  | { phase: "idle" }
  | { phase: "loading"; sessionId: string }
  | { phase: "ready"; summary: CaptureSummary }
  | {
      phase: "failed";
      sessionId: string;
      kind: "offline" | "unavailable";
    };

const NODE_TYPE_ORDER: MemoryNodeType[] = [
  "SESSION",
  "SUMMARY",
  "DECISION",
  "ACTION_ITEM",
  "PROMISE",
  "TASK",
  "PROJECT",
  "CUSTOMER",
  "PERSON",
  "ORGANIZATION",
  "TOPIC",
  "TERM",
  "TRANSLATION_NOTE",
  "FACT",
  "PREFERENCE",
  "VOCABULARY",
  "QUOTE",
  "RISK",
  "DOCUMENT",
  "DEVICE_EVENT",
];

export default function MemoryScreen({
  focusSessionId = null,
}: {
  focusSessionId?: string | null;
}) {
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemorySearchResult[] | null>(
    null
  );
  const [isSearching, setIsSearching] = useState(false);
  const [segment, setSegment] = useState<Segment>("Today");
  const [sessions, setSessions] = useState<CaptureSession[]>([]);
  const [nodes, setNodes] = useState<MemoryNode[]>([]);
  const [actionItems, setActionItems] = useState<MemoryNode[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [dataState, setDataState] = useState<ConnectionStatus>("loading");
  const [summaryState, setSummaryState] =
    useState<CaptureSummaryPresentationState>({ phase: "idle" });
  const summaryRequest = useRef(0);

  const loadCaptureSummary = useCallback(async (sessionId: string) => {
    const requestId = ++summaryRequest.current;
    setSummaryState({ phase: "loading", sessionId });
    const result = await getCaptureSummary(sessionId);
    if (requestId !== summaryRequest.current) return;
    if (result.ok) {
      setSummaryState({ phase: "ready", summary: result.data });
      setDataState(result.source === "demo" ? "demo" : "live");
      return;
    }
    const kind = result.offline ? "offline" : "unavailable";
    setSummaryState({ phase: "failed", sessionId, kind });
    setDataState(kind);
  }, []);

  useEffect(() => {
    if (focusSessionId) void loadCaptureSummary(focusSessionId);
  }, [focusSessionId, loadCaptureSummary]);

  useEffect(
    () => () => {
      summaryRequest.current += 1;
    },
    []
  );

  useEffect(() => {
    let mounted = true;
    (async () => {
      const [recent, actions] = await Promise.all([
        getRecent(),
        getActionItems(),
      ]);
      if (!mounted) return;
      if (!recent.ok || !actions.ok) {
        const offline =
          (!recent.ok && recent.offline) || (!actions.ok && actions.offline);
        setSessions([]);
        setNodes([]);
        setActionItems([]);
        setDataState(offline ? "offline" : "unavailable");
      } else {
        setSessions(recent.data.sessions);
        setNodes(recent.data.nodes);
        setActionItems(actions.data);
        setDataState(
          recent.source === "demo" || actions.source === "demo"
            ? "demo"
            : "live"
        );
      }
      setIsLoading(false);
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const runSearch = async (q: string) => {
    setQuery(q);
    if (!q.trim()) {
      setSearchResults(null);
      return;
    }
    setIsSearching(true);
    try {
      const result = await searchMemory(q);
      if (result.ok) {
        setSearchResults(result.data.results);
        setDataState(result.source === "demo" ? "demo" : "live");
      } else {
        setSearchResults([]);
        setSessions([]);
        setNodes([]);
        setActionItems([]);
        setDataState(result.offline ? "offline" : "unavailable");
      }
    } finally {
      setIsSearching(false);
    }
  };

  const visibleSessions =
    segment === "Today" ? sessions.slice(0, 1) : sessions;

  const groupedNodes = NODE_TYPE_ORDER.map((type) => ({
    type,
    items: nodes.filter((n) => n.type === type),
  })).filter((group) => group.items.length > 0);

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
    >
      <Text style={styles.title}>Memory</Text>
      <ConnectionBanner
        status={dataState}
        detail={
          dataState === "live"
            ? "Memory is loaded from the Vox Agent engine."
            : dataState === "demo"
              ? "Loaded from the configured demo engine, not production memory."
              : dataState === "offline"
                ? "Memory is unavailable; no demo data is shown."
                : dataState === "unavailable"
                  ? "The engine rejected the memory request."
                  : "Connecting to the Vox Agent engine."
        }
      />

      <CaptureSummaryPanel
        state={summaryState}
        onRetry={(sessionId) => void loadCaptureSummary(sessionId)}
      />

      <TextInput
        style={styles.searchInput}
        placeholder="Search memory (e.g. Japan launch)"
        placeholderTextColor={colors.textMuted}
        value={query}
        onChangeText={runSearch}
        autoCapitalize="none"
        autoCorrect={false}
      />

      {isSearching && <ActivityIndicator style={{ marginTop: spacing.md }} />}

      {searchResults !== null ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Results ({searchResults.length})
          </Text>
          {searchResults.length === 0 && (
            <Text style={styles.emptyText}>
              {dataState === "offline" || dataState === "unavailable"
                ? "Memory search is unavailable; no demo results are shown."
                : "No matches yet."}
            </Text>
          )}
          {searchResults.map((result) => (
            <MemoryCard key={result.node.id} node={result.node} />
          ))}
        </View>
      ) : (
        <>
          <View style={styles.segmentedControl}>
            {(["Today", "Inbox"] as Segment[]).map((s) => (
              <TouchableOpacity
                key={s}
                style={[
                  styles.segmentButton,
                  segment === s && styles.segmentButtonActive,
                ]}
                onPress={() => setSegment(s)}
              >
                <Text
                  style={[
                    styles.segmentButtonText,
                    segment === s && styles.segmentButtonTextActive,
                  ]}
                >
                  {s}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {isLoading ? (
            <ActivityIndicator style={{ marginTop: spacing.xl }} />
          ) : (
            <>
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Recent sessions</Text>
                {visibleSessions.length === 0 && (
                  <Text style={styles.emptyText}>
                    {dataState === "offline" || dataState === "unavailable"
                      ? "Recent sessions are unavailable."
                      : "No sessions yet."}
                  </Text>
                )}
                {visibleSessions.map((session) => (
                  <TouchableOpacity
                    key={session.id}
                    style={styles.sessionRow}
                    onPress={() => void loadCaptureSummary(session.id)}
                    accessibilityRole="button"
                    accessibilityLabel={`Open memory for session ${session.id}`}
                  >
                    <View style={styles.sessionDot} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.sessionTitle}>
                        {formatMode(session.mode)} · {session.status}
                      </Text>
                      <Text style={styles.sessionMeta}>
                        {session.id} · {session.source}
                      </Text>
                    </View>
                    <Text style={styles.sessionOpenLabel}>Open</Text>
                  </TouchableOpacity>
                ))}
              </View>

              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Action items</Text>
                {actionItems.length === 0 && (
                  <Text style={styles.emptyText}>
                    {dataState === "offline" || dataState === "unavailable"
                      ? "Action items are unavailable."
                      : "Nothing pending."}
                  </Text>
                )}
                {actionItems.map((item) => (
                  <MemoryCard key={item.id} node={item} />
                ))}
              </View>

              {groupedNodes.map((group) => (
                <View key={group.type} style={styles.section}>
                  <Text style={styles.sectionTitle}>
                    {formatType(group.type)}
                  </Text>
                  {group.items.map((node) => (
                    <MemoryCard key={node.id} node={node} />
                  ))}
                </View>
              ))}
            </>
          )}
        </>
      )}
    </ScrollView>
  );
}

export function CaptureSummaryPanel({
  state,
  onRetry,
}: {
  state: CaptureSummaryPresentationState;
  onRetry: (sessionId: string) => void;
}) {
  if (state.phase === "idle") return null;

  if (state.phase === "loading") {
    return (
      <View style={styles.summaryPanel} accessibilityRole="summary">
        <ActivityIndicator />
        <Text style={styles.summaryStatus}>
          Loading memory for session {state.sessionId}…
        </Text>
      </View>
    );
  }

  if (state.phase === "failed") {
    return (
      <View style={styles.summaryPanel} accessibilityRole="summary">
        <Text style={styles.summaryErrorTitle}>! Memory unavailable</Text>
        <Text style={styles.summaryStatus}>
          {state.kind === "offline"
            ? "The engine is offline. No replacement data is shown."
            : "The engine rejected or could not validate this session summary."}
        </Text>
        <TouchableOpacity
          style={styles.summaryRetry}
          onPress={() => onRetry(state.sessionId)}
          accessibilityRole="button"
          accessibilityLabel="Retry loading session memory"
        >
          <Text style={styles.summaryRetryText}>Try again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const groups = [
    { label: "Summary", nodes: state.summary.summaryNodes },
    { label: "Decisions", nodes: state.summary.decisions },
    { label: "Action items", nodes: state.summary.actionItems },
  ];
  const nodeCount = groups.reduce((total, group) => total + group.nodes.length, 0);

  return (
    <View style={styles.summaryPanel} accessibilityRole="summary">
      <Text style={styles.summaryReadyTitle}>✓ Processed memory</Text>
      <Text style={styles.summaryStatus}>
        Session {state.summary.sessionId} · {state.summary.status}
      </Text>
      {nodeCount === 0 ? (
        <Text style={styles.emptyText}>
          Processing completed without producing memory nodes.
        </Text>
      ) : (
        groups.map(
          (group) =>
            group.nodes.length > 0 && (
              <View key={group.label} style={styles.summaryGroup}>
                <Text style={styles.summaryGroupTitle}>{group.label}</Text>
                {group.nodes.map((node) => (
                  <MemoryCard key={node.id} node={node} />
                ))}
              </View>
            )
        )
      )}
    </View>
  );
}

function MemoryCard({ node }: { node: MemoryNode }) {
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle} numberOfLines={1}>
          {node.title}
        </Text>
        <View style={styles.typeBadge}>
          <Text style={styles.typeBadgeText}>{formatType(node.type)}</Text>
        </View>
      </View>
      <Text style={styles.cardBody} numberOfLines={2}>
        {node.body}
      </Text>
    </View>
  );
}

function formatMode(mode: string): string {
  return mode.charAt(0) + mode.slice(1).toLowerCase();
}

function formatType(type: MemoryNodeType): string {
  return type
    .split("_")
    .map((part) => part.charAt(0) + part.slice(1).toLowerCase())
    .join(" ");
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  title: {
    ...typography.title,
    color: colors.textPrimary,
    marginBottom: spacing.lg,
  },
  searchInput: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    ...typography.body,
    color: colors.textPrimary,
  },
  segmentedControl: {
    flexDirection: "row",
    backgroundColor: colors.chipBackground,
    borderRadius: radius.md,
    padding: spacing.xs,
    marginTop: spacing.lg,
    marginBottom: spacing.lg,
  },
  segmentButton: {
    flex: 1,
    paddingVertical: spacing.sm,
    borderRadius: radius.sm,
    alignItems: "center",
  },
  segmentButtonActive: {
    backgroundColor: colors.surface,
  },
  segmentButtonText: {
    ...typography.body,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  segmentButtonTextActive: {
    color: colors.textPrimary,
  },
  section: {
    marginBottom: spacing.lg,
  },
  sectionTitle: {
    ...typography.heading,
    color: colors.textPrimary,
    marginBottom: spacing.sm,
  },
  emptyText: {
    ...typography.body,
    color: colors.textMuted,
  },
  sessionRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  sessionDot: {
    width: 8,
    height: 8,
    borderRadius: radius.circle,
    backgroundColor: colors.success,
    marginRight: spacing.md,
  },
  sessionTitle: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "600",
  },
  sessionMeta: {
    ...typography.caption,
    color: colors.textMuted,
    marginTop: 2,
  },
  sessionOpenLabel: {
    ...typography.caption,
    color: colors.accent,
    fontWeight: "700",
    marginLeft: spacing.sm,
  },
  summaryPanel: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  summaryStatus: {
    ...typography.body,
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
  summaryReadyTitle: {
    ...typography.heading,
    color: colors.success,
  },
  summaryErrorTitle: {
    ...typography.heading,
    color: colors.danger,
  },
  summaryRetry: {
    marginTop: spacing.md,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.accent,
    alignItems: "center",
  },
  summaryRetryText: {
    ...typography.body,
    color: colors.chipTextActive,
    fontWeight: "600",
  },
  summaryGroup: {
    marginTop: spacing.md,
  },
  summaryGroupTitle: {
    ...typography.caption,
    color: colors.textSecondary,
    fontWeight: "700",
    marginBottom: spacing.sm,
    textTransform: "uppercase",
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.xs,
  },
  cardTitle: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "600",
    flex: 1,
    marginRight: spacing.sm,
  },
  typeBadge: {
    backgroundColor: colors.accentSoft,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.pill,
  },
  typeBadgeText: {
    ...typography.caption,
    color: colors.accent,
  },
  cardBody: {
    ...typography.body,
    color: colors.textSecondary,
  },
});
