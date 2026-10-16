import React, { useState } from "react";
import {
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { AgentAnswer, MemoryNode, askAgent, getRecent } from "../api/client";
import {
  ConnectionBanner,
  type ConnectionStatus,
} from "../components/ConnectionBanner";
import { colors, radius, spacing, typography } from "../theme";

const SUGGESTED_PROMPTS = [
  "What did I promise this week?",
  "Summarize the Japan launch meeting",
  "Draft a follow-up email",
];

interface AgentDescriptor {
  id: string;
  name: string;
  description: string;
}

const AGENTS: AgentDescriptor[] = [
  {
    id: "personal",
    name: "Personal Agent",
    description: "General-purpose assistant with access to your memory graph.",
  },
  {
    id: "meeting-analyst",
    name: "Meeting Analyst",
    description: "Summarizes meetings, extracts decisions and action items.",
  },
];

export default function AgentsScreen() {
  const [query, setQuery] = useState("");
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AgentAnswer | null>(null);
  const [answerNotice, setAnswerNotice] = useState<string | null>(null);
  const [isAsking, setIsAsking] = useState(false);
  const [contextPreview, setContextPreview] = useState<MemoryNode[] | null>(
    null
  );
  const [isLoadingContext, setIsLoadingContext] = useState(false);
  const [connectionState, setConnectionState] =
    useState<ConnectionStatus>("unchecked");

  const submitQuery = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setQuery(text);
    setPendingQuery(trimmed);
    setAnswer(null);
    setAnswerNotice(null);
    setIsAsking(true);
    try {
      const result = await askAgent(trimmed);
      if (result.ok) {
        setAnswer(result.data);
        setAnswerNotice(null);
        setConnectionState(result.source);
      } else {
        setAnswer(null);
        setConnectionState(result.offline ? "offline" : "unavailable");
        setAnswerNotice(
          result.offline
            ? "Offline — no answer was generated or fabricated."
            : `Unavailable — the engine rejected the request${result.status ? ` (${result.status})` : ""}.`
        );
      }
    } finally {
      setIsAsking(false);
    }
  };

  const useRecentMemory = async () => {
    setIsLoadingContext(true);
    try {
      const recent = await getRecent();
      if (recent.ok) {
        setContextPreview(recent.data.nodes.slice(0, 3));
        setConnectionState(recent.source === "demo" ? "demo" : "live");
      } else {
        setContextPreview([]);
        setConnectionState(recent.offline ? "offline" : "unavailable");
      }
    } finally {
      setIsLoadingContext(false);
    }
  };

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
    >
      <Text style={styles.title}>Agents</Text>
      <ConnectionBanner
        status={connectionState}
        detail={
          connectionState === "live"
            ? "Connected to the Vox Agent engine."
            : connectionState === "demo"
              ? "Connected to the configured demo engine, not production memory."
              : connectionState === "offline"
                ? "Answers and live context are unavailable."
                : connectionState === "unavailable"
                  ? "The engine rejected the last request."
                  : "Ask or load context to verify."
        }
      />

      <TextInput
        style={styles.queryInput}
        placeholder="Ask your agent anything..."
        placeholderTextColor={colors.textMuted}
        value={query}
        onChangeText={setQuery}
        onSubmitEditing={() => submitQuery(query)}
        multiline
      />

      <TouchableOpacity
        style={styles.primaryButton}
        onPress={() => submitQuery(query)}
        disabled={isAsking}
      >
        <Text style={styles.primaryButtonText}>
          {isAsking ? "Asking..." : "Ask"}
        </Text>
      </TouchableOpacity>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Suggested prompts</Text>
        <View style={styles.chipColumn}>
          {SUGGESTED_PROMPTS.map((prompt) => (
            <TouchableOpacity
              key={prompt}
              style={styles.promptChip}
              onPress={() => submitQuery(prompt)}
            >
              <Text style={styles.promptChipText}>{prompt}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {pendingQuery && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Answer</Text>
          <View style={styles.pendingCard}>
            <Text style={styles.pendingQuery}>{pendingQuery}</Text>
            {isAsking ? (
              <Text style={styles.pendingStatus}>Asking the agent...</Text>
            ) : answer ? (
              <>
                <Text style={styles.answerText}>{answer.answer}</Text>
                <Text style={styles.pendingStatus}>{answer.answerer}</Text>
              </>
            ) : answerNotice ? (
              <Text style={styles.offlineNotice}>{answerNotice}</Text>
            ) : null}
          </View>

          {!isAsking && answer && (
            <View style={{ marginTop: spacing.md }}>
              <Text style={styles.sectionSubtitle}>Citations</Text>
              {answer.citations.length === 0 ? (
                <Text style={styles.emptyText}>No matching memory</Text>
              ) : (
                answer.citations.map((citation) => (
                  <View key={citation.id} style={styles.contextRow}>
                    <Text style={styles.contextTitle} numberOfLines={1}>
                      {citation.title ?? "(untitled)"}
                    </Text>
                    <Text style={styles.contextBody} numberOfLines={1}>
                      {citation.type ?? "UNKNOWN"} · {citation.id}
                    </Text>
                  </View>
                ))
              )}
            </View>
          )}
        </View>
      )}

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Agents</Text>
        {AGENTS.map((agent) => (
          <View key={agent.id} style={styles.agentCard}>
            <Text style={styles.agentName}>{agent.name}</Text>
            <Text style={styles.agentDescription}>{agent.description}</Text>
          </View>
        ))}
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Recent context</Text>
        <TouchableOpacity
          style={styles.secondaryButton}
          onPress={useRecentMemory}
          disabled={isLoadingContext}
        >
          <Text style={styles.secondaryButtonText}>
            {isLoadingContext ? "Loading..." : "Use recent memory"}
          </Text>
        </TouchableOpacity>

        {contextPreview && (
          <View style={{ marginTop: spacing.md }}>
            {contextPreview.length === 0 && (
              <Text style={styles.emptyText}>
                {connectionState === "offline" ||
                connectionState === "unavailable"
                  ? "Recent context is unavailable; no demo memory is shown."
                  : "No recent context."}
              </Text>
            )}
            {contextPreview.map((node) => (
              <View key={node.id} style={styles.contextRow}>
                <Text style={styles.contextTitle} numberOfLines={1}>
                  {node.title}
                </Text>
                <Text style={styles.contextBody} numberOfLines={2}>
                  {node.body}
                </Text>
              </View>
            ))}
          </View>
        )}
      </View>

      <View style={styles.mcpRow}>
        <Text style={styles.mcpText}>
          MCP: vox.search_memory +7 tools — serve with `vox-engine serve-mcp`
        </Text>
      </View>
    </ScrollView>
  );
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
  queryInput: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    minHeight: 56,
    ...typography.body,
    color: colors.textPrimary,
    textAlignVertical: "top",
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    marginTop: spacing.sm,
    marginBottom: spacing.lg,
  },
  primaryButtonText: {
    color: "#FFFFFF",
    ...typography.heading,
  },
  section: {
    marginBottom: spacing.lg,
  },
  sectionTitle: {
    ...typography.heading,
    color: colors.textPrimary,
    marginBottom: spacing.sm,
  },
  chipColumn: {
    gap: spacing.sm,
  },
  promptChip: {
    backgroundColor: colors.chipBackground,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  promptChipText: {
    ...typography.body,
    color: colors.textPrimary,
  },
  pendingCard: {
    backgroundColor: colors.accentSoft,
    borderRadius: radius.md,
    padding: spacing.md,
  },
  pendingQuery: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "600",
    marginBottom: spacing.xs,
  },
  pendingStatus: {
    ...typography.caption,
    color: colors.accent,
  },
  offlineNotice: {
    ...typography.caption,
    color: colors.danger,
    marginBottom: spacing.xs,
    fontWeight: "600",
  },
  answerText: {
    ...typography.body,
    color: colors.textPrimary,
    marginBottom: spacing.xs,
  },
  sectionSubtitle: {
    ...typography.caption,
    color: colors.textSecondary,
    marginBottom: spacing.xs,
  },
  emptyText: {
    ...typography.caption,
    color: colors.textMuted,
  },
  agentCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  agentName: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "600",
    marginBottom: spacing.xs,
  },
  agentDescription: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  secondaryButton: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    backgroundColor: colors.surface,
  },
  secondaryButtonText: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "600",
  },
  contextRow: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  contextTitle: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "600",
    marginBottom: 2,
  },
  contextBody: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  mcpRow: {
    backgroundColor: colors.chipBackground,
    borderRadius: radius.md,
    padding: spacing.md,
  },
  mcpText: {
    ...typography.caption,
    color: colors.textSecondary,
  },
});
