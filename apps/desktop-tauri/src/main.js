import { createCaptureController } from "./capture/controller.js";
import { initContextUi } from "./context/ui.js";
import { providerBadge } from "./settings/provider-badge.js";

const isTauri =
  typeof window !== "undefined" &&
  window.__TAURI__ &&
  typeof window.__TAURI__.core?.invoke === "function";

const invoke = async (command, args = {}) => {
  if (!isTauri) {
    throw new Error("Tauri runtime not available");
  }
  return window.__TAURI__.core.invoke(command, args);
};

const listen = async (event, handler) => {
  if (!isTauri || typeof window.__TAURI__.event?.listen !== "function") {
    return () => {};
  }
  return window.__TAURI__.event.listen(event, handler);
};

const storage = {
  get(key, fallback = "") {
    try {
      const value = window.localStorage.getItem(key);
      return value ?? fallback;
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // ignore storage failures
    }
  },
  getBool(key, fallback = false) {
    const value = storage.get(key, fallback ? "true" : "false");
    return value === "true";
  },
};

const STORAGE_KEYS = {
  backendUrl: "voxagent.backendUrl",
  hotkeyAccelerator: "voxagent.hotkeyAccelerator",
  hotkeyBehavior: "voxagent.hotkeyBehavior",
  hotkeyEnabled: "voxagent.hotkeyEnabled",
  targetApp: "voxagent.targetApp",
  targetRisk: "voxagent.targetRisk",
  sessionId: "voxagent.sessionId",
  recordMode: "voxagent.recordMode",
  retryProvider: "voxagent.retryProvider",
  historyQuery: "voxagent.historyQuery",
  historyAgent: "voxagent.historyAgent",
  historySession: "voxagent.historySession",
  historyCurrentOnly: "voxagent.historyCurrentOnly",
};

const LEGACY_STORAGE_KEYS = Object.fromEntries(
  Object.entries(STORAGE_KEYS).map(([name, key]) => [name, key.replace("voxagent.", "voxlog2.")]),
);

function migrateLegacyPreferences() {
  try {
    for (const name of Object.keys(STORAGE_KEYS)) {
      const currentKey = STORAGE_KEYS[name];
      const legacyKey = LEGACY_STORAGE_KEYS[name];
      if (window.localStorage.getItem(currentKey) === null) {
        const legacyValue = window.localStorage.getItem(legacyKey);
        if (legacyValue !== null) {
          window.localStorage.setItem(currentKey, legacyValue);
        }
      }
    }
  } catch {
    // Preferences are optional; secure credentials never use Web storage.
  }
}

const elements = {
  backendUrl: document.querySelector("#backend-url"),
  saveBackend: document.querySelector("#save-backend"),
  saveSettings: document.querySelector("#save-settings"),
  testSettings: document.querySelector("#test-settings"),
  rebuildDigest: document.querySelector("#rebuild-digest"),
  exportObsidian: document.querySelector("#export-obsidian"),
  exportAiMateMemory: document.querySelector("#export-ai-mate-memory"),
  completeOnboarding: document.querySelector("#complete-onboarding"),
  hotkeyAccelerator: document.querySelector("#hotkey-accelerator"),
  hotkeyBehavior: document.querySelector("#hotkey-behavior"),
  hotkeyEnabled: document.querySelector("#hotkey-enabled"),
  saveHotkey: document.querySelector("#save-hotkey"),
  refreshRecent: document.querySelector("#refresh-recent"),
  refreshHealth: document.querySelector("#refresh-health"),
  askAgent: document.querySelector("#ask-agent"),
  agentQuery: document.querySelector("#agent-query"),
  refreshDigest: document.querySelector("#refresh-digest"),
  refreshHistory: document.querySelector("#refresh-history"),
  exportHistoryMarkdown: document.querySelector("#export-history-markdown"),
  exportHistoryJson: document.querySelector("#export-history-json"),
  openHistoryExports: document.querySelector("#open-history-exports"),
  historyExportStatus: document.querySelector("#history-export-status"),
  dismissRecent: document.querySelector("#dismiss-recent"),
  undoOutput: document.querySelector("#undo-output"),
  retryRecent: document.querySelector("#retry-recent"),
  applyRecentMode: document.querySelector("#apply-recent-mode"),
  addDictionary: document.querySelector("#add-dictionary"),
  dictionaryForm: document.querySelector("#dictionary-form"),
  dictionaryStatus: document.querySelector("#dictionary-status"),
  dictionaryWrongInput: document.querySelector("#dictionary-wrong-input"),
  dictionaryRightInput: document.querySelector("#dictionary-right-input"),
  saveDictionaryTerm: document.querySelector("#save-dictionary-term"),
  refreshDictionary: document.querySelector("#refresh-dictionary"),
  dictionaryEmpty: document.querySelector("#dictionary-empty"),
  dictionaryCorrections: document.querySelector("#dictionary-corrections"),
  dictionaryPreserveEmpty: document.querySelector("#dictionary-preserve-empty"),
  dictionaryPreserve: document.querySelector("#dictionary-preserve"),
  dictionaryPreserveCount: document.querySelector("#dictionary-preserve-count"),
  namedModeForm: document.querySelector("#named-mode-form"),
  namedModeStatus: document.querySelector("#named-mode-status"),
  namedModeName: document.querySelector("#named-mode-name"),
  namedModeTargetApp: document.querySelector("#named-mode-target-app"),
  namedModeRisk: document.querySelector("#named-mode-risk"),
  namedModeRecording: document.querySelector("#named-mode-recording"),
  namedModeHotkey: document.querySelector("#named-mode-hotkey"),
  saveNamedMode: document.querySelector("#save-named-mode"),
  refreshNamedModes: document.querySelector("#refresh-named-modes"),
  namedModeCount: document.querySelector("#named-mode-count"),
  namedModeEmpty: document.querySelector("#named-mode-empty"),
  namedModeList: document.querySelector("#named-mode-list"),
  refreshPermissions: document.querySelector("#refresh-permissions"),
  startRecording: document.querySelector("#start-recording"),
  stopRecording: document.querySelector("#stop-recording"),
  newSession: document.querySelector("#new-session"),
  targetApp: document.querySelector("#target-app"),
  targetRisk: document.querySelector("#target-risk"),
  backendApiToken: document.querySelector("#backend-api-token"),
  engineApiToken: document.querySelector("#engine-api-token"),
  activeProfile: document.querySelector("#active-profile"),
  profileSummary: document.querySelector("#profile-summary"),
  providerRecommendation: document.querySelector("#provider-recommendation"),
  neverArchiveApps: document.querySelector("#never-archive-apps"),
  fastPathOnlyApps: document.querySelector("#fast-path-only-apps"),
  disableDirectTypingApps: document.querySelector("#disable-direct-typing-apps"),
  dashscopeKeyUs: document.querySelector("#dashscope-key-us"),
  dashscopeKeyCn: document.querySelector("#dashscope-key-cn"),
  openaiKey: document.querySelector("#openai-key"),
  siliconflowKey: document.querySelector("#siliconflow-key"),
  doubaoKey: document.querySelector("#doubao-key"),
  anthropicKey: document.querySelector("#anthropic-key"),
  customLlmKey: document.querySelector("#custom-llm-key"),
  openaiLlmModel: document.querySelector("#openai-llm-model"),
  qwenLlmModel: document.querySelector("#qwen-llm-model"),
  doubaoModel: document.querySelector("#doubao-model"),
  anthropicModel: document.querySelector("#anthropic-model"),
  customLlmBaseUrl: document.querySelector("#custom-llm-base-url"),
  customLlmModel: document.querySelector("#custom-llm-model"),
  ollamaModel: document.querySelector("#ollama-model"),
  sttMainRoute: document.querySelector("#stt-main-route"),
  sttFallbackRoute: document.querySelector("#stt-fallback-route"),
  llmMainRoute: document.querySelector("#llm-main-route"),
  llmFallbackRoute: document.querySelector("#llm-fallback-route"),
  digestEnhancementEnabled: document.querySelector("#digest-enhancement-enabled"),
  digestEnhancementProvider: document.querySelector("#digest-enhancement-provider"),
  obsidianVaultDir: document.querySelector("#obsidian-vault-dir"),
  aiMateMemoryDir: document.querySelector("#ai-mate-memory-dir"),
  crossPlatformSyncStatus: document.querySelector("#cross-platform-sync-status"),
  crossPlatformSyncSummary: document.querySelector("#cross-platform-sync-summary"),
  notionSyncEnabled: document.querySelector("#notion-sync-enabled"),
  notionToken: document.querySelector("#notion-token"),
  notionDataSourceId: document.querySelector("#notion-data-source-id"),
  notionSyncDirection: document.querySelector("#notion-sync-direction"),
  notionIncludeRawText: document.querySelector("#notion-include-raw-text"),
  gitSyncEnabled: document.querySelector("#git-sync-enabled"),
  gitSyncRepoUrl: document.querySelector("#git-sync-repo-url"),
  gitSyncLocalPath: document.querySelector("#git-sync-local-path"),
  runNotionSync: document.querySelector("#run-notion-sync"),
  runGitSync: document.querySelector("#run-git-sync"),
  migrationStatus: document.querySelector("#migration-status"),
  migrationBundlePath: document.querySelector("#migration-bundle-path"),
  migrationSummary: document.querySelector("#migration-summary"),
  importLegacyVoxlog: document.querySelector("#import-previous-data"),
  exportMigrationBundle: document.querySelector("#export-migration-bundle"),
  importMigrationBundle: document.querySelector("#import-migration-bundle"),
  openMigrationDirectory: document.querySelector("#open-migration-directory"),
  modelPolicyStatus: document.querySelector("#model-policy-status"),
  modelAnswerer: document.querySelector("#model-answerer"),
  modelAsrAdapter: document.querySelector("#model-asr-adapter"),
  modelPolicySummary: document.querySelector("#model-policy-summary"),
  applyModelPolicy: document.querySelector("#apply-model-policy"),
  refreshModelPolicy: document.querySelector("#refresh-model-policy"),
  engineDiagnosticsStatus: document.querySelector("#engine-diagnostics-status"),
  engineProcessState: document.querySelector("#engine-process-state"),
  engineServiceState: document.querySelector("#engine-service-state"),
  engineVersionState: document.querySelector("#engine-version-state"),
  backendServiceState: document.querySelector("#backend-service-state"),
  engineLogPath: document.querySelector("#engine-log-path"),
  refreshEngineDiagnostics: document.querySelector("#refresh-engine-diagnostics"),
  startSettingsEngine: document.querySelector("#start-settings-engine"),
  stopSettingsEngine: document.querySelector("#stop-settings-engine"),
  obsidianSyncDays: document.querySelector("#obsidian-sync-days"),
  obsidianSyncStatus: document.querySelector("#obsidian-sync-status"),
  obsidianSyncSummary: document.querySelector("#obsidian-sync-summary"),
  runObsidianSync: document.querySelector("#run-obsidian-sync"),
  sessionId: document.querySelector("#session-id"),
  recordMode: document.querySelector("#record-mode"),
  retryProvider: document.querySelector("#retry-provider"),
  historyQuery: document.querySelector("#history-query"),
  historyAgent: document.querySelector("#history-agent"),
  historySession: document.querySelector("#history-session"),
  useCurrentSession: document.querySelector("#use-current-session"),
  historyCurrentOnly: document.querySelector("#history-current-only"),
  recentModeSelect: document.querySelector("#recent-mode-select"),
  dictionaryWrong: document.querySelector("#dictionary-wrong"),
  dictionaryRight: document.querySelector("#dictionary-right"),
  hotkeyStatus: document.querySelector("#hotkey-status"),
  providerStatus: document.querySelector("#provider-status"),
  settingsStatus: document.querySelector("#settings-status"),
  onboardingStatus: document.querySelector("#onboarding-status"),
  bridgeStatus: document.querySelector("#bridge-status"),
  contractStatus: document.querySelector("#contract-status"),
  permissionStatus: document.querySelector("#permission-status"),
  recordingStatus: document.querySelector("#recording-status"),
  transcriptStatus: document.querySelector("#transcript-status"),
  digestStatus: document.querySelector("#digest-status"),
  digestType: document.querySelector("#digest-type"),
  digestDate: document.querySelector("#digest-date"),
  digestProjectKey: document.querySelector("#digest-project-key"),
  recentStatus: document.querySelector("#recent-status"),
  historyStatus: document.querySelector("#history-status"),
  historyResultCount: document.querySelector("#history-result-count"),
  recentEmpty: document.querySelector("#recent-empty"),
  onboardingPanel: document.querySelector("#onboarding-panel"),
  recentCard: document.querySelector("#recent-card"),
  historyEmpty: document.querySelector("#history-empty"),
  historyList: document.querySelector("#history-list"),
  recentApp: document.querySelector("#recent-app"),
  recentRisk: document.querySelector("#recent-risk"),
  recentProvider: document.querySelector("#recent-provider"),
  recentConfidence: document.querySelector("#recent-confidence"),
  recentDisplay: document.querySelector("#recent-display"),
  recentRaw: document.querySelector("#recent-raw"),
  recentIds: document.querySelector("#recent-ids"),
  transcriptProvider: document.querySelector("#transcript-provider"),
  transcriptModel: document.querySelector("#transcript-model"),
  transcriptSegments: document.querySelector("#transcript-segments"),
  partialTranscript: document.querySelector("#partial-transcript"),
  finalTranscript: document.querySelector("#final-transcript"),
  outputPolicyStrategy: document.querySelector("#output-policy-strategy"),
  outputPolicyReason: document.querySelector("#output-policy-reason"),
  digestSummary: document.querySelector("#digest-summary"),
  digestIntent: document.querySelector("#digest-intent"),
  digestTags: document.querySelector("#digest-tags"),
  digestEntities: document.querySelector("#digest-entities"),
  digestEnhancer: document.querySelector("#digest-enhancer"),
  digestRebuiltAt: document.querySelector("#digest-rebuilt-at"),
  digestExportStatus: document.querySelector("#digest-export-status"),
  aiMateExportStatus: document.querySelector("#ai-mate-export-status"),
  policyPreviewCard: document.querySelector("#policy-preview-card"),
  policyPreviewStatus: document.querySelector("#policy-preview-status"),
  policyPreviewText: document.querySelector("#policy-preview-text"),
  policyPreviewSend: document.querySelector("#policy-preview-send"),
  policyPreviewCancel: document.querySelector("#policy-preview-cancel"),
  activityLog: document.querySelector("#activity-log"),
  providerFlags: document.querySelector("#provider-flags"),
  settingsChecks: document.querySelector("#settings-checks"),
  providerQualificationStatus: document.querySelector("#provider-qualification-status"),
  providerQualificationSummary: document.querySelector("#provider-qualification-summary"),
  providerQualificationList: document.querySelector("#provider-qualification-list"),
  refreshProviderQualification: document.querySelector("#refresh-provider-qualification"),
  onboardingChecks: document.querySelector("#onboarding-checks"),
  agentAnswerCard: document.querySelector("#agent-answer-card"),
  agentAnswer: document.querySelector("#agent-answer"),
  agentAnswerer: document.querySelector("#agent-answerer"),
  agentContext: document.querySelector("#agent-context"),
  workspaceTitle: document.querySelector("#workspace-title"),
  workspaceViews: [...document.querySelectorAll("[data-view]")],
  workspaceNav: [...document.querySelectorAll("[data-view-target]")],
  agentRoster: document.querySelector("#agent-roster"),
  agentCount: document.querySelector("#agent-count"),
  runningCount: document.querySelector("#running-count"),
  queuedCount: document.querySelector("#queued-count"),
  approvalCount: document.querySelector("#approval-count"),
  failedCount: document.querySelector("#failed-count"),
  engineTaskTimeline: document.querySelector("#engine-task-timeline"),
  enginePermissionList: document.querySelector("#engine-permission-list"),
  engineTimelineEmpty: document.querySelector("#engine-timeline-empty"),
  engineStreamStatus: document.querySelector("#engine-stream-status"),
};

// Conversation targets, panes, Context composer and receipts (T11–T14).
const contextUi = initContextUi({
  document,
  invoke,
  listen,
  storage,
  onAgentSubmit: () => askAgent(),
});

let currentRecent = null;
let currentSettingsSnapshot = null;
let currentConnectivity = null;
let mediaRecorder = null;
let mediaChunks = [];
let mediaStream = null;
let isRecording = false;
let globalHotkeyUnlisten = null;
let engineEventUnlisten = null;
let engineStreamUnlisten = null;
let microphonePermissionStatus = null;
let healthPollTimer = null;
let recentPollTimer = null;
let historyPollTimer = null;
let enginePollTimer = null;
let historyExportDirectory = "";
let migrationBundleDirectory = "";
let recordingStartedAt = 0;
let captureStatusPollTimer = null;
let currentOutputPolicy = null;
let pendingUploadPayload = null;
let desktopContractReady = false;
let agentEngineReady = false;
let currentEngineOperations = { agents: [], tasks: [], pendingApprovals: [] };
let currentNamedModes = [];
let engineRefreshTimer = null;
let voiceHudLevelTimer = null;
let voiceHudHideTimer = null;
let voiceHudPollInFlight = false;
let voiceHudPollGeneration = 0;

function currentVoiceTargetAgent() {
  const agents = currentEngineOperations.agents || [];
  const tasks = currentEngineOperations.tasks || [];
  const runningTask = tasks.find((task) =>
    ["assigned", "running"].includes(task.status),
  );
  const agent = agents.find((item) => item.id === runningTask?.agentId) || agents[0];
  return agent?.name || agent?.id || "Vox Agent Coordinator";
}

async function updateVoiceHud(update, options = {}) {
  if (!isTauri) return;
  const phase = update.phase || "hidden";
  try {
    await invoke("update_voice_hud", {
      update: {
        phase,
        targetAgent: update.targetAgent || currentVoiceTargetAgent(),
        instruction: update.instruction || "Release to send · Esc to cancel",
        transcript: update.transcript || "",
        elapsedMs: Math.max(0, Number(update.elapsedMs || 0)),
        rmsLevel: Number(update.rmsLevel || 0),
        peakLevel: Number(update.peakLevel || 0),
        visible: phase !== "hidden",
      },
    });
  } catch (error) {
    if (!options.silent) {
      log("Voice HUD update failed.", { phase, error: String(error) });
    }
  }
}

function stopVoiceHudLevelPolling() {
  voiceHudPollGeneration += 1;
  if (voiceHudLevelTimer) {
    clearInterval(voiceHudLevelTimer);
    voiceHudLevelTimer = null;
  }
  voiceHudPollInFlight = false;
}

function startVoiceHudLevelPolling() {
  stopVoiceHudLevelPolling();
  const generation = voiceHudPollGeneration;
  voiceHudLevelTimer = window.setInterval(async () => {
    if (!isRecording || voiceHudPollInFlight) return;
    voiceHudPollInFlight = true;
    try {
      const status =
        capture.active()?.mode === "native"
          ? await invoke("get_native_capture_status")
          : { recording: true, elapsedMs: Date.now() - recordingStartedAt, rmsLevel: 0, peakLevel: 0 };
      if (!isRecording || generation !== voiceHudPollGeneration) return;
      const ended = capture.observe(status);
      if (ended) {
        handleCaptureEnded(ended);
        return;
      }
      await updateVoiceHud(
        {
          phase: "listening",
          elapsedMs: status.elapsedMs,
          rmsLevel: status.rmsLevel,
          peakLevel: status.peakLevel,
        },
        { silent: true },
      );
    } catch (error) {
      log("Native microphone level unavailable.", { error: String(error) });
    } finally {
      if (generation === voiceHudPollGeneration) {
        voiceHudPollInFlight = false;
      }
    }
  }, 100);
}

function clearVoiceHudHideTimer() {
  if (!voiceHudHideTimer) return;
  clearTimeout(voiceHudHideTimer);
  voiceHudHideTimer = null;
}

function scheduleVoiceHudHide(delayMs) {
  clearVoiceHudHideTimer();
  voiceHudHideTimer = window.setTimeout(() => {
    voiceHudHideTimer = null;
    void updateVoiceHud({ phase: "hidden" }, { silent: true });
  }, delayMs);
}

function showVoiceHudError(instruction) {
  stopVoiceHudLevelPolling();
  void updateVoiceHud({
    phase: "error",
    instruction,
    elapsedMs: recordingStartedAt ? Date.now() - recordingStartedAt : 0,
  });
  scheduleVoiceHudHide(3_200);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseRuleList(value) {
  return String(value || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinRuleList(items) {
  return (items || []).join("\n");
}

function log(message, data) {
  const stamp = new Date().toLocaleTimeString();
  const line = data ? `[${stamp}] ${message} ${JSON.stringify(data)}` : `[${stamp}] ${message}`;
  elements.activityLog.textContent = `${line}\n${elements.activityLog.textContent}`.trim();
}

function setBadge(node, label, kind = "neutral") {
  node.textContent = label;
  node.className = `badge ${kind}`;
}

const WORKSPACE_TITLES = {
  command: "Command",
  sessions: "Sessions & Memory",
  history: "History",
  settings: "Settings",
  vocabulary: "Vocabulary",
  modes: "Modes",
};

function showWorkspaceView(viewName, options = {}) {
  if (!WORKSPACE_TITLES[viewName]) {
    return;
  }
  for (const view of elements.workspaceViews) {
    view.hidden = view.dataset.view !== viewName;
  }
  for (const item of elements.workspaceNav) {
    const selected = item.dataset.viewTarget === viewName;
    item.classList.toggle("active", selected);
    if (selected) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  }
  elements.workspaceTitle.textContent = WORKSPACE_TITLES[viewName];
  if (options.focusHeading) {
    elements.workspaceTitle.focus?.();
  }
}

function statusKind(status) {
  if (["completed", "done", "ok", "connected"].includes(status)) return "live";
  if (["failed", "error", "blocked", "cancelled"].includes(status)) return "danger";
  if (["running", "assigned", "queued", "pending", "reconnecting"].includes(status)) return "warn";
  return "neutral";
}

function renderAgentRoster(agents, tasks = []) {
  elements.agentRoster.innerHTML = "";
  elements.agentCount.textContent = String(agents.length);
  if (!agents.length) {
    const empty = document.createElement("p");
    empty.className = "inspector-empty";
    empty.textContent = "No specialist activity yet.";
    elements.agentRoster.append(empty);
    return;
  }

  for (const item of agents) {
    const agentTasks = tasks.filter((task) => task.agentId === item.id);
    const active = agentTasks.some((task) => ["assigned", "running"].includes(task.status));
    const queued = agentTasks.filter((task) => ["queued", "pending"].includes(task.status)).length;
    const target = contextUi.currentTarget();
    const selected = target.kind === "agent" && target.id === item.id;
    const row = document.createElement("button");
    row.type = "button";
    row.className = `agent-row${selected ? " selected" : ""}`;
    row.dataset.agentId = item.id;
    row.setAttribute("aria-pressed", String(selected));
    if (typeof item.id === "string" && item.id) {
      row.addEventListener("click", () => contextUi.selectAgent(item));
    } else {
      row.disabled = true;
    }

    const status = document.createElement("span");
    status.className = `status-form ${active ? "running" : "idle"}`;
    status.setAttribute("aria-hidden", "true");

    const copy = document.createElement("span");
    copy.className = "agent-copy";
    const name = document.createElement("strong");
    name.textContent = `${item.emoji || ""} ${item.name || item.id || "Unnamed agent"}`.trim();
    const lastActive = document.createElement("small");
    lastActive.textContent = `${item.provider || "local"} · ${(item.skills || []).length} skills`;
    copy.append(name, lastActive);

    const count = document.createElement("span");
    count.className = "agent-state";
    count.textContent = active ? "running" : queued ? `${queued} queued` : "ready";
    row.append(status, copy, count);
    elements.agentRoster.append(row);
  }
}

function createTaskTurn(task) {
  const article = document.createElement("article");
  article.className = "timeline-turn task-turn";
  article.dataset.taskId = task.id;

  const identity = document.createElement("div");
  identity.className = "turn-identity";
  const avatar = document.createElement("span");
  avatar.className = "agent-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = (task.agentId || "A").slice(0, 1).toUpperCase();
  const identityCopy = document.createElement("span");
  const agent = document.createElement("strong");
  agent.textContent = task.agentId || "Agent";
  const role = document.createElement("small");
  role.textContent = `Task ${task.id}`;
  identityCopy.append(agent, role);
  identity.append(avatar, identityCopy);

  const body = document.createElement("div");
  body.className = "turn-body";
  const heading = document.createElement("div");
  heading.className = "task-turn-heading";
  const title = document.createElement("strong");
  title.textContent = task.title || "Untitled task";
  const badge = document.createElement("span");
  badge.className = `badge ${statusKind(task.status)}`;
  badge.textContent = task.status || "unknown";
  heading.append(title, badge);
  const detail = document.createElement("p");
  detail.className = "secondary-copy";
  detail.textContent = task.error
    ? `Failed: ${String(task.error).slice(0, 500)}`
    : `Priority ${task.priority ?? 1} · ${task.createdAt ? new Date(task.createdAt).toLocaleString() : "just now"}`;
  body.append(heading, detail);
  article.append(identity, body);
  return article;
}

function updateOperationCounts() {
  const tasks = currentEngineOperations.tasks || [];
  const pendingApprovals = currentEngineOperations.pendingApprovals || [];
  elements.runningCount.textContent = String(
    tasks.filter((task) => ["assigned", "running"].includes(task.status)).length,
  );
  elements.queuedCount.textContent = String(
    tasks.filter((task) => ["queued", "pending"].includes(task.status)).length,
  );
  elements.failedCount.textContent = String(tasks.filter((task) => task.status === "failed").length);
  const outputApproval = elements.policyPreviewCard.classList.contains("hidden") ? 0 : 1;
  elements.approvalCount.textContent = String(pendingApprovals.length + outputApproval);
}

function renderEngineTasks(tasks) {
  elements.engineTaskTimeline.innerHTML = "";
  const visible = [...tasks]
    .sort((left, right) => String(right.createdAt || "").localeCompare(String(left.createdAt || "")))
    .slice(0, 12);
  elements.engineTimelineEmpty.classList.toggle("hidden", visible.length > 0);
  for (const task of visible) {
    elements.engineTaskTimeline.append(createTaskTurn(task));
  }
}

function createPermissionCard(request) {
  const card = document.createElement("article");
  card.className = "inline-card permission-card engine-permission-card";
  card.dataset.approvalId = request.approvalId;

  const edge = document.createElement("span");
  edge.className = "card-edge awaiting";
  edge.setAttribute("aria-hidden", "true");
  const content = document.createElement("div");
  content.className = "card-content";
  const heading = document.createElement("div");
  heading.className = "card-heading";
  const headingCopy = document.createElement("div");
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Tool approval";
  const title = document.createElement("h2");
  title.textContent = `${request.agentId || "Agent"} requests ${request.tool || "a tool"}`;
  headingCopy.append(eyebrow, title);
  const badge = document.createElement("span");
  badge.className = "badge warn";
  badge.textContent = "Awaiting approval";
  heading.append(headingCopy, badge);

  const metadata = document.createElement("p");
  metadata.className = "secondary-copy";
  metadata.textContent = `Task ${request.taskId || "unknown"} · expires ${request.expiresAt ? new Date(request.expiresAt).toLocaleTimeString() : "soon"}`;

  const scope = document.createElement("dl");
  scope.className = "permission-scope-list";
  for (const [key, rawValue] of Object.entries(request.scope || {})) {
    const row = document.createElement("div");
    const name = document.createElement("dt");
    name.textContent = key;
    const value = document.createElement("dd");
    const normalized = String(rawValue);
    value.textContent = normalized.length > 2_000 ? `${normalized.slice(0, 2_000)}…` : normalized;
    row.append(name, value);
    scope.append(row);
  }

  const remember = document.createElement("label");
  remember.className = "check-control permission-remember";
  const rememberInput = document.createElement("input");
  rememberInput.type = "checkbox";
  rememberInput.disabled = !(request.scope?.path || request.scope?.file_path || request.scope?.filePath);
  const rememberText = document.createElement("span");
  rememberText.textContent = "Remember this exact file scope";
  remember.append(rememberInput, rememberText);

  const actions = document.createElement("div");
  actions.className = "actions";
  const approve = document.createElement("button");
  approve.className = "button success";
  approve.type = "button";
  approve.textContent = "Approve once";
  approve.addEventListener("click", () => {
    decideEnginePermission(request.approvalId, true, rememberInput.checked, card);
  });
  const deny = document.createElement("button");
  deny.className = "button quiet";
  deny.type = "button";
  deny.textContent = "Deny";
  deny.addEventListener("click", () => {
    decideEnginePermission(request.approvalId, false, false, card);
  });
  actions.append(approve, deny);
  content.append(heading, metadata, scope, remember, actions);
  card.append(edge, content);
  return card;
}

function renderEnginePermissions(requests) {
  elements.enginePermissionList.innerHTML = "";
  for (const request of requests) {
    elements.enginePermissionList.append(createPermissionCard(request));
  }
}

function renderEngineOperations(snapshot) {
  currentEngineOperations = snapshot || { agents: [], tasks: [], pendingApprovals: [] };
  renderAgentRoster(currentEngineOperations.agents || [], currentEngineOperations.tasks || []);
  renderEngineTasks(currentEngineOperations.tasks || []);
  renderEnginePermissions(currentEngineOperations.pendingApprovals || []);
  updateOperationCounts();
}

function renderRecent(recent) {
  currentRecent = recent;
  if (!recent) {
    elements.recentEmpty.classList.remove("hidden");
    elements.recentCard.classList.add("hidden");
    setBadge(elements.recentStatus, "Waiting", "neutral");
    return;
  }

  elements.recentEmpty.classList.add("hidden");
  elements.recentCard.classList.remove("hidden");
  elements.recentApp.textContent = recent.target_app || "Unknown";
  elements.recentRisk.textContent = recent.target_risk_level;
  elements.recentProvider.textContent = `${recent.stt_provider} / ${recent.stt_model}`;
  elements.recentConfidence.textContent = recent.confidence.toFixed(2);
  elements.recentDisplay.textContent = recent.display_text || "(empty)";
  elements.recentRaw.textContent = recent.raw_text || "(empty)";
  elements.recentIds.textContent = `${recent.utterance_id} / ${recent.output_id}`;
  elements.recentModeSelect.value = recent.recording_mode;
  if (!elements.dictionaryWrong.value.trim()) {
    elements.dictionaryWrong.value = recent.raw_text || "";
  }
  setBadge(
    elements.recentStatus,
    recent.status === "needs_review" ? "Needs Review" : "Ready",
    recent.status === "needs_review" ? "warn" : "live",
  );
}

function createTextElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = String(text ?? "");
  return element;
}

function createHistoryMeta(values) {
  const metadata = document.createElement("div");
  metadata.className = "history-item-meta";
  for (const value of values) {
    metadata.append(createTextElement("span", "", value));
  }
  return metadata;
}

function createHistoryAction(action, label) {
  const button = createTextElement("button", "button mini secondary", label);
  button.type = "button";
  button.dataset.action = action;
  return button;
}

async function copyTextToClipboard(text) {
  if (!text.trim()) {
    throw new Error("Nothing to copy");
  }
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Windows WebView clipboard permissions can fail; use the native bridge below.
    }
  }
  if (!isTauri) {
    throw new Error("Clipboard is unavailable");
  }
  await invoke("copy_text", { text });
}

function copyHistoryValue(item, value, label) {
  copyTextToClipboard(value).then(
    () => log(`Copied history ${label}.`, { utterance_id: item.utterance_id }),
    (error) => log(`Failed to copy history ${label}.`, { utterance_id: item.utterance_id, error: String(error) }),
  );
}

function renderHistory(items) {
  elements.historyList.replaceChildren();
  if (!items.length) {
    elements.historyEmpty.classList.remove("hidden");
    elements.historyList.classList.add("hidden");
    elements.historyResultCount.textContent = "0 items";
    setBadge(elements.historyStatus, "Empty", "neutral");
    return;
  }

  elements.historyEmpty.classList.add("hidden");
  elements.historyList.classList.remove("hidden");
  elements.historyResultCount.textContent = `${items.length} ${items.length === 1 ? "item" : "items"}`;
  setBadge(elements.historyStatus, "Ready", "neutral");

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "history-item";
    card.setAttribute("role", "listitem");
    const header = document.createElement("div");
    header.className = "history-item-header";
    header.append(
      createTextElement("strong", "", item.target_app || "Unknown"),
      createHistoryMeta([
        item.agent || "no-agent",
        item.recording_mode,
        item.stt_provider,
        item.confidence.toFixed(2),
      ]),
    );
    const text = createTextElement("div", "history-item-text", item.display_text || "(empty)");
    const identifiers = createHistoryMeta([item.created_at, item.utterance_id]);
    const actions = document.createElement("div");
    actions.className = "history-item-actions";
    const replay = createHistoryAction("replay", "Replay context");
    const useSession = createHistoryAction("session", "Use Session");
    const copyText = createHistoryAction("copy-text", "Copy text");
    const copyIds = createHistoryAction("copy-ids", "Copy IDs");
    const historyContext = item.created_at || item.utterance_id;
    replay.setAttribute("aria-label", ["Replay context from", historyContext].join(" "));
    useSession.setAttribute("aria-label", ["Use session from", historyContext].join(" "));
    copyText.setAttribute("aria-label", ["Copy transcript from", historyContext].join(" "));
    copyIds.setAttribute("aria-label", ["Copy identifiers from", historyContext].join(" "));
    actions.append(replay, useSession, copyText, copyIds);
    card.append(header, text, identifiers, actions);
    card.addEventListener("click", (event) => {
      const action = event.target?.dataset?.action;
      if (!action) {
        return;
      }
      event.stopPropagation();
      if (action === "replay") {
        applyHistoryItem(item);
        showWorkspaceView("command");
        return;
      }
      if (action === "session") {
        elements.historySession.value = item.session_id || "";
        elements.historyCurrentOnly.checked = false;
        persistPreferences();
        refreshHistory({ silent: true });
        log("Applied history session filter.", { session_id: item.session_id });
        return;
      }
      if (action === "copy-text") {
        copyHistoryValue(item, item.display_text || item.raw_text, "text");
        return;
      }
      if (action === "copy-ids") {
        copyHistoryValue(
          item,
          `${item.utterance_id} ${item.output_id} ${item.session_id}`.trim(),
          "IDs",
        );
      }
    });
    elements.historyList.append(card);
  }
}

function renderDictionary(dictionary) {
  const corrections = Object.entries(dictionary?.corrections || {}).sort(([left], [right]) =>
    left.localeCompare(right),
  );
  const preservedTerms = [...new Set(dictionary?.preserve || [])].sort((left, right) =>
    left.localeCompare(right),
  );

  elements.dictionaryCorrections.replaceChildren();
  elements.dictionaryEmpty.classList.toggle("hidden", corrections.length > 0);
  elements.dictionaryCorrections.classList.toggle("hidden", corrections.length === 0);

  for (const [wrong, right] of corrections) {
    const row = document.createElement("div");
    row.className = "vocabulary-row";
    row.setAttribute("role", "listitem");

    const mapping = document.createElement("div");
    mapping.className = "vocabulary-mapping";
    const recognized = createTextElement("span", "vocabulary-source", wrong);
    const arrow = createTextElement("span", "vocabulary-arrow", "→");
    arrow.setAttribute("aria-hidden", "true");
    const preferred = createTextElement("strong", "vocabulary-target", right);
    mapping.append(recognized, arrow, preferred);

    const remove = createTextElement("button", "button mini danger", "Delete");
    remove.type = "button";
    remove.setAttribute("aria-label", ["Delete correction from", wrong, "to", right].join(" "));
    remove.addEventListener("click", () => deleteDictionaryTerm(wrong));
    row.append(mapping, remove);
    elements.dictionaryCorrections.append(row);
  }

  elements.dictionaryPreserve.replaceChildren();
  elements.dictionaryPreserveEmpty.classList.toggle("hidden", preservedTerms.length > 0);
  elements.dictionaryPreserve.classList.toggle("hidden", preservedTerms.length === 0);
  elements.dictionaryPreserveCount.textContent = `${preservedTerms.length} ${preservedTerms.length === 1 ? "term" : "terms"}`;
  for (const term of preservedTerms) {
    const chip = createTextElement("span", "term-chip", term);
    chip.setAttribute("role", "listitem");
    elements.dictionaryPreserve.append(chip);
  }

  setBadge(
    elements.dictionaryStatus,
    corrections.length === 0 ? "Empty" : "Ready",
    "neutral",
  );
}

async function refreshDictionary(options = {}) {
  if (!isTauri) return;
  try {
    setBadge(elements.dictionaryStatus, "Loading", "neutral");
    const dictionary = await invoke("get_dictionary");
    renderDictionary(dictionary);
    if (!options.silent) {
      log("Loaded recognition vocabulary.", {
        corrections: Object.keys(dictionary.corrections || {}).length,
        preserved_terms: (dictionary.preserve || []).length,
      });
    }
  } catch (error) {
    setBadge(elements.dictionaryStatus, "Unavailable", "danger");
    if (!options.silent) {
      log("Failed to load recognition vocabulary.", { error: String(error) });
    }
  }
}

async function saveDictionaryTerm(event) {
  event?.preventDefault();
  if (!isTauri) return;

  const wrong = elements.dictionaryWrongInput.value.trim();
  const right = elements.dictionaryRightInput.value.trim();
  if (!wrong || !right) {
    setBadge(elements.dictionaryStatus, "Both Fields Required", "danger");
    return;
  }

  try {
    elements.saveDictionaryTerm.disabled = true;
    setBadge(elements.dictionaryStatus, "Saving", "neutral");
    const dictionary = await invoke("mutate_dictionary", {
      input: { action: "add", wrong, right },
    });
    renderDictionary(dictionary);
    elements.dictionaryForm.reset();
    elements.dictionaryWrongInput.focus();
    log("Added recognition correction.", { wrong, right });
  } catch (error) {
    setBadge(elements.dictionaryStatus, "Save Failed", "danger");
    log("Failed to add recognition correction.", { error: String(error) });
  } finally {
    elements.saveDictionaryTerm.disabled = false;
  }
}

async function deleteDictionaryTerm(wrong) {
  if (!isTauri) return;
  try {
    setBadge(elements.dictionaryStatus, "Deleting", "neutral");
    const dictionary = await invoke("mutate_dictionary", {
      input: { action: "delete", wrong },
    });
    renderDictionary(dictionary);
    log("Deleted recognition correction.", { wrong });
  } catch (error) {
    setBadge(elements.dictionaryStatus, "Delete Failed", "danger");
    log("Failed to delete recognition correction.", { error: String(error) });
  }
}

function renderNamedModes(modes) {
  currentNamedModes = modes;
  elements.namedModeList.replaceChildren();
  elements.namedModeEmpty.classList.toggle("hidden", modes.length > 0);
  elements.namedModeList.classList.toggle("hidden", modes.length === 0);
  elements.namedModeCount.textContent = `${modes.length} ${modes.length === 1 ? "mode" : "modes"}`;

  for (const mode of modes) {
    const card = document.createElement("article");
    card.className = "named-mode-item";
    card.setAttribute("role", "listitem");

    const copy = document.createElement("div");
    copy.className = "named-mode-copy";
    copy.append(
      createTextElement("strong", "", mode.name),
      createHistoryMeta([
        mode.target_app,
        mode.target_risk_level,
        mode.recording_mode,
        mode.hotkey_accelerator || "No hotkey",
      ]),
    );

    const actions = document.createElement("div");
    actions.className = "history-item-actions";
    const apply = createTextElement("button", "button mini secondary", "Apply");
    apply.type = "button";
    apply.setAttribute("aria-label", ["Apply named mode", mode.name].join(" "));
    apply.addEventListener("click", () => applyNamedMode(mode));
    const remove = createTextElement("button", "button mini danger", "Delete");
    remove.type = "button";
    remove.setAttribute("aria-label", ["Delete named mode", mode.name].join(" "));
    remove.addEventListener("click", () => deleteNamedMode(mode.id));
    actions.append(apply, remove);
    card.append(copy, actions);
    elements.namedModeList.append(card);
  }

  setBadge(
    elements.namedModeStatus,
    modes.length === 0 ? "Empty" : "Ready",
    "neutral",
  );
}

async function refreshNamedModes(options = {}) {
  if (!isTauri) return;
  try {
    setBadge(elements.namedModeStatus, "Loading", "neutral");
    const modes = await invoke("get_named_modes");
    renderNamedModes(modes);
    await syncNamedModeHotkeys({ silent: options.silent });
    if (!options.silent) {
      log("Loaded named capture modes.", { count: modes.length });
    }
    return modes;
  } catch (error) {
    setBadge(elements.namedModeStatus, "Unavailable", "danger");
    if (!options.silent) {
      log("Failed to load named capture modes.", { error: String(error) });
    }
    return null;
  }
}

async function syncNamedModeHotkeys(options = {}) {
  if (!isTauri) return null;
  try {
    const report = await invoke("sync_named_mode_hotkeys");
    if (report.failures.length) {
      setBadge(elements.namedModeStatus, `${report.failures.length} Hotkey Failed`, "danger");
    } else if (report.registered) {
      setBadge(elements.namedModeStatus, `${report.registered} Hotkey Ready`, "live");
    }
    if (!options.silent || report.failures.length) {
      log("Synchronized named-mode global hotkeys.", report);
    }
    return report;
  } catch (error) {
    setBadge(elements.namedModeStatus, "Hotkey Sync Failed", "danger");
    if (!options.silent) {
      log("Failed to synchronize named-mode hotkeys.", { error: String(error) });
    }
    return null;
  }
}

async function createNamedMode(event) {
  event?.preventDefault();
  if (!isTauri) return;
  const input = {
    name: elements.namedModeName.value.trim(),
    targetApp: elements.namedModeTargetApp.value.trim(),
    targetRiskLevel: elements.namedModeRisk.value,
    recordingMode: elements.namedModeRecording.value,
    hotkeyAccelerator: elements.namedModeHotkey.value.trim() || null,
  };
  if (!input.name || !input.targetApp) {
    setBadge(elements.namedModeStatus, "Required Fields Missing", "danger");
    return;
  }

  try {
    elements.saveNamedMode.disabled = true;
    setBadge(elements.namedModeStatus, "Saving", "neutral");
    const modes = await invoke("create_named_mode", { input });
    renderNamedModes(modes);
    elements.namedModeForm.reset();
    elements.namedModeName.focus();
    setBadge(elements.namedModeStatus, "Saved", "live");
    log("Created named capture mode.", { name: input.name });
  } catch (error) {
    setBadge(elements.namedModeStatus, "Save Failed", "danger");
    log("Failed to create named capture mode.", { error: String(error) });
  } finally {
    elements.saveNamedMode.disabled = false;
  }
}

function findNamedModeForHotkey(accelerator) {
  return currentNamedModes.find(
    (mode) => mode.hotkey_accelerator && mode.hotkey_accelerator === String(accelerator || ""),
  );
}

function applyNamedMode(mode) {
  elements.recordMode.value = mode.recording_mode;
  elements.targetRisk.value = mode.target_risk_level;
  elements.targetApp.value = mode.target_app;
  persistPreferences();
  showWorkspaceView("command");
  log("Applied named capture mode.", {
    name: mode.name,
    target_app: mode.target_app,
    target_risk_level: mode.target_risk_level,
    recording_mode: mode.recording_mode,
  });
}

async function deleteNamedMode(id) {
  if (!isTauri) return;
  try {
    setBadge(elements.namedModeStatus, "Deleting", "neutral");
    const modes = await invoke("delete_named_mode", { id });
    renderNamedModes(modes);
    log("Deleted named capture mode.", { id });
  } catch (error) {
    setBadge(elements.namedModeStatus, "Delete Failed", "danger");
    log("Failed to delete named capture mode.", { error: String(error) });
  }
}

function setRecordingState(label, kind = "neutral") {
  setBadge(elements.recordingStatus, label, kind);
}

function setTranscriptState(label, kind = "neutral") {
  setBadge(elements.transcriptStatus, label, kind);
}

function setDigestState(label, kind = "neutral") {
  setBadge(elements.digestStatus, label, kind);
}

function setHotkeyState(label, kind = "neutral") {
  setBadge(elements.hotkeyStatus, label, kind);
}

function setPermissionState(label, kind = "neutral") {
  setBadge(elements.permissionStatus, label, kind);
}

function setBridgeState(label, kind = "neutral") {
  setBadge(elements.bridgeStatus, label, kind);
}

function setHistoryState(label, kind = "neutral") {
  setBadge(elements.historyStatus, label, kind);
}

// True while the latest settings snapshot reports that the OS credential store
// could not be read. Provider/token presence is then unknown, so no status
// path may claim "configured" or "missing" until a healthy snapshot arrives.
function credentialStoreUnavailable() {
  return Boolean(currentSettingsSnapshot?.credential_store_error);
}

// While the store is unreadable, success/neutral states are replaced by the
// unavailable label, and operation failures ("danger") stay visible with the
// credential note appended, so neither condition hides the other.
function credentialAwareLabel(label, kind, unavailableLabel) {
  if (!credentialStoreUnavailable()) return [label, kind];
  if (kind !== "danger" || label === unavailableLabel) return [unavailableLabel, "danger"];
  return [`${label}; Credentials Unavailable`, "danger"];
}

function setProviderState(label, kind = "neutral") {
  setBadge(elements.providerStatus, ...credentialAwareLabel(label, kind, "Credentials Unavailable"));
}

function setSettingsState(label, kind = "neutral") {
  setBadge(elements.settingsStatus, ...credentialAwareLabel(label, kind, "Credential Store Unavailable"));
}

function setOnboardingState(label, kind = "neutral") {
  setBadge(elements.onboardingStatus, label, kind);
}

function renderProviderFlags(flags) {
  elements.providerFlags.replaceChildren();
  const items = [
    ["dashscope-us", flags.dashscope_us],
    ["dashscope-cn", flags.dashscope_cn],
    ["openai", flags.openai],
    ["siliconflow", flags.siliconflow],
    ["doubao", flags.doubao],
    ["anthropic", flags.anthropic],
    ["custom-llm", flags.custom_llm],
  ];
  for (const [label, configured] of items) {
    const pill = document.createElement("span");
    pill.textContent = credentialStoreUnavailable()
      ? `${label}: unknown`
      : configured ? `${label}: configured` : `${label}: missing`;
    elements.providerFlags.append(pill);
  }
}

function renderProfileOptions(snapshot) {
  const profiles = snapshot?.profiles || [];
  const currentValue = snapshot?.active_profile || elements.activeProfile.value || "home";
  if (!profiles.length) {
    const option = document.createElement("option");
    option.value = "home";
    option.textContent = "home";
    elements.activeProfile.replaceChildren(option);
    elements.activeProfile.value = currentValue;
    return;
  }

  elements.activeProfile.replaceChildren();
  for (const profile of profiles) {
    const option = document.createElement("option");
    option.value = profile.name;
    option.textContent = profile.name;
    elements.activeProfile.append(option);
  }
  if ([...elements.activeProfile.options].some((option) => option.value === currentValue)) {
    elements.activeProfile.value = currentValue;
  }
}

function renderProfileSummary(snapshot) {
  const profiles = snapshot?.profiles || [];
  const selected = profiles.find((profile) => profile.name === elements.activeProfile.value);
  elements.profileSummary.replaceChildren();
  if (!selected) {
    elements.profileSummary.textContent = "Select a profile to inspect its STT and LLM routing.";
    return;
  }

  const summary = document.createElement("div");
  summary.className = "summary-grid";
  for (const [labelText, value] of [
    ["Profile", selected.name],
    ["STT Main", selected.stt_main],
    ["STT Fallback", selected.stt_fallback],
    ["LLM Main", selected.llm_main],
    ["LLM Fallback", selected.llm_fallback],
  ]) {
    const item = document.createElement("div");
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = labelText;
    const valueLabel = document.createElement("strong");
    valueLabel.textContent = value;
    item.append(label, valueLabel);
    summary.append(item);
  }
  const routeHint = document.createElement("div");
  routeHint.className = "route-hint";
  for (const value of [`STT ${selected.stt_main}`, `Fallback ${selected.stt_fallback}`]) {
    const badge = document.createElement("span");
    badge.className = "badge neutral";
    badge.textContent = value;
    routeHint.append(badge);
  }
  elements.profileSummary.append(summary, routeHint);
  elements.llmMainRoute.value = selected.llm_main;
  elements.llmFallbackRoute.value = selected.llm_fallback;
  elements.sttMainRoute.value = selected.stt_main;
  elements.sttFallbackRoute.value = selected.stt_fallback;
}

function renderProviderRecommendation(connectivity) {
  if (!connectivity) {
    elements.providerRecommendation.textContent =
      "Readiness check will show the configured STT route for this profile.";
    return;
  }

  const kind = connectivity.ready ? "live" : "warn";
  const count = connectivity.configured_provider_count;
  const summary = document.createElement("div");
  summary.className = "summary-grid";
  for (const [labelText, value] of [
    ["Recommended STT", connectivity.recommended_stt_provider],
    ["Configured Routes", String(count)],
  ]) {
    const item = document.createElement("div");
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = labelText;
    const valueLabel = document.createElement("strong");
    valueLabel.textContent = value;
    item.append(label, valueLabel);
    summary.append(item);
  }
  const routeHint = document.createElement("div");
  routeHint.className = "route-hint";
  const readiness = document.createElement("span");
  readiness.className = `badge ${kind}`;
  readiness.textContent = connectivity.ready ? "Configured" : "Fallback Missing";
  const profile = document.createElement("span");
  profile.className = "badge neutral";
  profile.textContent = connectivity.active_profile;
  routeHint.append(readiness, profile);
  elements.providerRecommendation.replaceChildren(summary, routeHint);
}

function qualificationLabel(value) {
  return String(value || "unknown").replaceAll("_", " ");
}

function renderProviderQualification(payload) {
  elements.providerQualificationList.replaceChildren();
  elements.providerQualificationSummary.replaceChildren();

  const headline = document.createElement("strong");
  headline.textContent = `${payload.platform} · ${payload.active_profile}`;
  const detail = document.createElement("p");
  detail.textContent = `${payload.summary.qualified_count} qualified · ${payload.summary.configured_count} configured · ${payload.summary.action_required_count} active actions required`;
  const network = document.createElement("p");
  network.textContent = payload.network_environment.proxy_environment_configured
    ? "HTTP proxy environment detected; HTTPX will honor it."
    : "Direct network path; no HTTP proxy environment detected.";
  elements.providerQualificationSummary.append(headline, detail, network);

  for (const provider of payload.providers) {
    const card = document.createElement("article");
    card.className = "settings-check provider-qualification-card";
    const head = document.createElement("div");
    head.className = "settings-check-head";
    const label = document.createElement("strong");
    label.textContent = provider.label;
    const badge = document.createElement("span");
    const shown = providerBadge(provider);
    badge.className = `badge ${shown.tone}`;
    badge.textContent = shown.label;
    head.append(label, badge);

    const meta = document.createElement("div");
    meta.className = "meta-chips";
    const facts = [provider.kind, provider.transport, provider.support, `probe ${provider.probe_status}`];
    if (provider.package_available !== null) {
      facts.push(provider.package_available ? "package present" : "package missing");
    }
    if (provider.assets_available !== null) {
      facts.push(provider.assets_available ? "assets present" : "assets missing");
    }
    for (const role of provider.active_roles) {
      facts.push(`active ${role}`);
    }
    for (const fact of facts) {
      const chip = document.createElement("span");
      chip.textContent = qualificationLabel(fact);
      meta.append(chip);
    }

    const action = document.createElement("p");
    action.textContent = provider.action;
    const commands = document.createElement("div");
    commands.className = "provider-install-commands";
    for (const command of [provider.install_command, provider.asset_command]) {
      if (!command) continue;
      const code = document.createElement("code");
      code.textContent = command;
      commands.append(code);
    }
    card.append(head, meta, action, commands);
    elements.providerQualificationList.append(card);
  }

  const needsAction = payload.summary.action_required_count > 0;
  const routesConfigured = payload.summary.active_routes_configured;
  let statusLabel = "No Active Route";
  let statusKind = "neutral";
  if (needsAction) {
    statusLabel = `${payload.summary.action_required_count} Active Gaps`;
    statusKind = "warn";
  } else if (routesConfigured) {
    statusLabel = "Routes Configured";
    statusKind = "live";
  }
  setBadge(
    elements.providerQualificationStatus,
    statusLabel,
    statusKind,
  );
}

async function refreshProviderQualifications(options = {}) {
  if (!isTauri) return null;
  try {
    setBadge(elements.providerQualificationStatus, "Checking", "neutral");
    const payload = await invoke("get_provider_qualifications");
    renderProviderQualification(payload);
    if (!options.silent) {
      log("Refreshed provider qualification matrix.", {
        contract_version: payload.contract_version,
        platform: payload.platform,
        summary: payload.summary,
      });
    }
    return payload;
  } catch (error) {
    elements.providerQualificationList.replaceChildren();
    elements.providerQualificationSummary.textContent = `Qualification unavailable: ${String(error)}`;
    setBadge(elements.providerQualificationStatus, "Unavailable", "danger");
    if (!options.silent) {
      log("Failed to refresh provider qualification matrix.", { error: String(error) });
    }
    return null;
  }
}

function renderRecognitionEvidence(preview) {
  if (!preview) {
    elements.transcriptProvider.textContent = "pending selected route";
    elements.transcriptModel.textContent = "not invoked";
    elements.transcriptSegments.textContent = "0";
    elements.partialTranscript.textContent = "(waiting)";
    elements.finalTranscript.textContent = "(waiting)";
    setTranscriptState("Idle", "neutral");
    return;
  }

  elements.transcriptProvider.textContent = preview.provider;
  elements.transcriptModel.textContent = preview.model;
  elements.transcriptSegments.textContent = String(preview.capture_window_count);
  elements.partialTranscript.textContent = preview.status_text || "(capturing)";
  elements.finalTranscript.textContent = preview.completion_hint || "(waiting)";
  setTranscriptState("Capturing", "warn");
}

function renderOutputPolicy(policy) {
  currentOutputPolicy = policy;
  if (!policy) {
    elements.outputPolicyStrategy.textContent = "paste";
    elements.outputPolicyReason.textContent = "Low-risk targets will prefer fast paste.";
    return;
  }
  elements.outputPolicyStrategy.textContent = policy.strategy;
  elements.outputPolicyReason.textContent = policy.reason;
}

function renderSessionDigest(digest) {
  if (!digest) {
    elements.digestSummary.textContent = "(waiting)";
    elements.digestIntent.textContent = "(waiting)";
    elements.digestTags.textContent = "(waiting)";
    elements.digestEntities.textContent = "(waiting)";
    elements.digestEnhancer.textContent = "(waiting)";
    elements.digestRebuiltAt.textContent = "(waiting)";
    elements.digestExportStatus.textContent = "(waiting)";
    elements.aiMateExportStatus.textContent = "(waiting)";
    setDigestState("Idle", "neutral");
    return;
  }

  elements.digestSummary.textContent = digest.summary || "(empty)";
  elements.digestIntent.textContent = digest.intent || "(empty)";
  elements.digestTags.textContent = (digest.suggested_tags || []).join(", ") || "(empty)";
  elements.digestEntities.textContent = (digest.mentioned_entities || []).join(", ") || "(empty)";
  elements.digestEnhancer.textContent = digest.enhanced
    ? `LLM: ${digest.enhancer_provider || "unknown"}`
    : `Heuristic: ${digest.enhancer_provider || "heuristic"}`;
  elements.digestRebuiltAt.textContent = digest.updated_at
    ? new Date(digest.updated_at).toLocaleString()
    : "(unknown)";
  elements.digestExportStatus.textContent = "(ready)";
  elements.aiMateExportStatus.textContent = "(ready)";
  if (digest.digest_type === "daily_digest") {
    setDigestState("Daily Ready", "live");
    return;
  }
  if (digest.digest_type === "project_digest") {
    setDigestState("Project Ready", "live");
    return;
  }
  setDigestState("Session Ready", "live");
}

function showPolicyPreview(text) {
  showWorkspaceView("command");
  elements.policyPreviewText.textContent = text || "(empty)";
  elements.policyPreviewCard.classList.remove("hidden");
  updateOperationCounts();
  setBadge(elements.policyPreviewStatus, "Pending", "warn");
}

function hidePolicyPreview() {
  elements.policyPreviewCard.classList.add("hidden");
  elements.policyPreviewText.textContent = "";
  updateOperationCounts();
  pendingUploadPayload = null;
}

function readinessKind(status) {
  if (status === "ok") {
    return "live";
  }
  if (status === "warn") {
    return "warn";
  }
  return "danger";
}

function createReadinessCard(check, showTodoState = false) {
  const card = document.createElement("article");
  card.className = `settings-check${showTodoState && check.status !== "ok" ? " todo" : ""}`;
  const head = document.createElement("div");
  head.className = "settings-check-head";
  const label = document.createElement("strong");
  label.textContent = check.label;
  const badge = document.createElement("span");
  badge.className = `badge ${readinessKind(check.status)}`;
  badge.textContent = check.status;
  const message = document.createElement("p");
  message.textContent = check.message;
  head.append(label, badge);
  card.append(head, message);
  return card;
}

function renderSettingsChecks(payload) {
  if (!payload?.checks?.length) {
    elements.settingsChecks.innerHTML = "";
    elements.settingsChecks.classList.add("hidden");
    return;
  }

  elements.settingsChecks.innerHTML = "";
  elements.settingsChecks.classList.remove("hidden");

  for (const check of payload.checks) {
    elements.settingsChecks.append(createReadinessCard(check));
  }
}

function renderOnboardingChecks(snapshot, connectivity) {
  if (snapshot) {
    currentSettingsSnapshot = snapshot;
  }
  if (connectivity !== undefined) {
    currentConnectivity = connectivity;
  }

  const resolvedSnapshot = currentSettingsSnapshot;
  const resolvedConnectivity = currentConnectivity;
  const hasBackend = Boolean(resolvedSnapshot?.backend_base_url?.trim());
  const hasProvider = Boolean(resolvedConnectivity?.configured_provider_count || 0);
  const connectionReady = Boolean(resolvedConnectivity?.backend_url_reachable);
  const micGranted = elements.permissionStatus.textContent.includes("Granted");
  const checks = [
    {
      label: "Backend URL",
      status: hasBackend ? "ok" : "warn",
      message: hasBackend ? "Desktop shell has a backend URL configured." : "Set the backend base URL first.",
    },
    credentialStoreUnavailable()
      ? {
          label: "Provider Key",
          status: "fail",
          message: "Credential store unavailable; provider key status is unknown.",
        }
      : {
          label: "Provider Key",
          status: hasProvider ? "ok" : "fail",
          message: hasProvider ? "At least one provider key is stored securely." : "Add one provider key in Provider Settings.",
        },
    {
      label: "Runtime Response",
      status: connectionReady ? "ok" : "warn",
      message: connectionReady ? "Compatibility runtime responded behind the gateway." : "Check routing after saving your settings.",
    },
    {
      label: "Microphone Permission",
      status: micGranted ? "ok" : "warn",
      message: micGranted ? "Microphone permission granted." : "Refresh mic permission and allow access before recording.",
    },
  ];

  elements.onboardingChecks.innerHTML = "";
  for (const check of checks) {
    elements.onboardingChecks.append(createReadinessCard(check, true));
  }
}

function canCompleteOnboarding(snapshot, connectivity) {
  const resolvedSnapshot = snapshot ?? currentSettingsSnapshot;
  const resolvedConnectivity = connectivity ?? currentConnectivity;
  const hasBackend = Boolean(resolvedSnapshot?.backend_base_url?.trim());
  const hasProvider = Boolean(resolvedConnectivity?.configured_provider_count || 0);
  return hasBackend && hasProvider && Boolean(resolvedConnectivity?.backend_url_reachable);
}

function hydratePreferences() {
  elements.backendUrl.value = storage.get(STORAGE_KEYS.backendUrl, elements.backendUrl.value);
  elements.hotkeyAccelerator.value = storage.get(
    STORAGE_KEYS.hotkeyAccelerator,
    elements.hotkeyAccelerator.value || "CommandOrControl+Shift+Space",
  );
  elements.hotkeyBehavior.value = storage.get(STORAGE_KEYS.hotkeyBehavior, "hold");
  elements.hotkeyEnabled.checked = storage.getBool(STORAGE_KEYS.hotkeyEnabled, false);
  elements.targetApp.value = storage.get(STORAGE_KEYS.targetApp, elements.targetApp.value || "Cursor");
  elements.targetRisk.value = storage.get(STORAGE_KEYS.targetRisk, elements.targetRisk.value || "low");
  elements.sessionId.value = storage.get(STORAGE_KEYS.sessionId, "");
  elements.recordMode.value = storage.get(STORAGE_KEYS.recordMode, elements.recordMode.value || "normal");
  elements.retryProvider.value = storage.get(STORAGE_KEYS.retryProvider, "");
  elements.historyQuery.value = storage.get(STORAGE_KEYS.historyQuery, "");
  elements.historyAgent.dataset.persistedValue = storage.get(STORAGE_KEYS.historyAgent, "");
  elements.historySession.value = storage.get(STORAGE_KEYS.historySession, "");
  elements.historyCurrentOnly.checked = storage.getBool(STORAGE_KEYS.historyCurrentOnly, false);
  if (!elements.digestDate.value) {
    elements.digestDate.value = new Date().toISOString().slice(0, 10);
  }
  if (!elements.digestProjectKey.value.trim()) {
    elements.digestProjectKey.value = elements.targetApp.value.trim().toLowerCase();
  }
  ensureSessionId();
}

function persistPreferences() {
  storage.set(STORAGE_KEYS.backendUrl, elements.backendUrl.value.trim());
  storage.set(STORAGE_KEYS.hotkeyAccelerator, elements.hotkeyAccelerator.value.trim());
  storage.set(STORAGE_KEYS.hotkeyBehavior, elements.hotkeyBehavior.value);
  storage.set(STORAGE_KEYS.hotkeyEnabled, String(elements.hotkeyEnabled.checked));
  storage.set(STORAGE_KEYS.targetApp, elements.targetApp.value.trim());
  storage.set(STORAGE_KEYS.targetRisk, elements.targetRisk.value);
  storage.set(STORAGE_KEYS.sessionId, elements.sessionId.value.trim());
  storage.set(STORAGE_KEYS.recordMode, elements.recordMode.value);
  storage.set(STORAGE_KEYS.retryProvider, elements.retryProvider.value.trim());
  storage.set(STORAGE_KEYS.historyQuery, elements.historyQuery.value.trim());
  storage.set(STORAGE_KEYS.historyAgent, elements.historyAgent.value);
  storage.set(STORAGE_KEYS.historySession, elements.historySession.value.trim());
  storage.set(STORAGE_KEYS.historyCurrentOnly, String(elements.historyCurrentOnly.checked));
}

function getActiveHistorySessionFilter() {
  if (elements.historyCurrentOnly.checked) {
    return elements.sessionId.value.trim();
  }
  return elements.historySession.value.trim();
}

function applyHistoryItem(item) {
  elements.targetApp.value = item.target_app || elements.targetApp.value;
  elements.targetRisk.value = item.target_risk_level || elements.targetRisk.value;
  elements.sessionId.value = item.session_id || elements.sessionId.value;
  elements.recordMode.value = item.recording_mode || elements.recordMode.value;
  elements.historySession.value = item.session_id || elements.historySession.value;
  elements.dictionaryWrong.value = item.raw_text || "";
  elements.dictionaryRight.value = item.display_text || "";
  elements.retryProvider.value = item.stt_provider || "";
  elements.recentApp.textContent = item.target_app || "Unknown";
  elements.recentRisk.textContent = item.target_risk_level;
  elements.recentProvider.textContent = `${item.stt_provider} / ${item.stt_model}`;
  elements.recentConfidence.textContent = Number(item.confidence || 0).toFixed(2);
  elements.recentDisplay.textContent = item.display_text || "(empty)";
  elements.recentRaw.textContent = item.raw_text || "(empty)";
  elements.recentIds.textContent = `${item.utterance_id} / ${item.output_id}`;
  elements.recentEmpty.classList.add("hidden");
  elements.recentCard.classList.remove("hidden");
  setBadge(elements.recentStatus, "Loaded From History", "live");
  persistPreferences();
  log("Applied history item to current panel.", {
    utterance_id: item.utterance_id,
    session_id: item.session_id,
  });
}

function createSessionId() {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `voxagent-${stamp}`;
}

function ensureSessionId() {
  const existing = elements.sessionId.value.trim();
  if (existing) {
    return existing;
  }
  const nextSessionId = createSessionId();
  elements.sessionId.value = nextSessionId;
  storage.set(STORAGE_KEYS.sessionId, nextSessionId);
  return nextSessionId;
}

function handlePermissionState(state) {
  if (state === "granted") {
    setPermissionState("Mic Granted", "live");
    return;
  }
  if (state === "prompt") {
    setPermissionState("Mic Prompt", "warn");
    return;
  }
  if (state === "denied") {
    setPermissionState("Mic Denied", "warn");
    return;
  }
  setPermissionState("Mic Unknown", "neutral");
}

function buildSettingsPayload(options = {}) {
  return {
    backend_base_url: elements.backendUrl.value.trim(),
    backend_api_token: elements.backendApiToken.value.trim() || undefined,
    engine_api_token: elements.engineApiToken.value.trim() || undefined,
    active_profile: elements.activeProfile.value,
    digest_enhancement_enabled: elements.digestEnhancementEnabled.checked,
    digest_enhancement_provider: elements.digestEnhancementProvider.value,
    obsidian_vault_dir: elements.obsidianVaultDir.value.trim(),
    ai_mate_memory_dir: elements.aiMateMemoryDir.value.trim(),
    notion_sync_enabled: elements.notionSyncEnabled.checked,
    notion_data_source_id: elements.notionDataSourceId.value.trim(),
    notion_include_raw_text: elements.notionIncludeRawText.checked,
    notion_sync_direction: elements.notionSyncDirection.value,
    notion_token: elements.notionToken.value.trim() || undefined,
    git_sync_enabled: elements.gitSyncEnabled.checked,
    git_sync_repo_url: elements.gitSyncRepoUrl.value.trim(),
    git_sync_local_path: elements.gitSyncLocalPath.value.trim(),
    onboarding_completed: options.completeOnboarding ?? false,
    hotkey_accelerator: elements.hotkeyAccelerator.value.trim(),
    hotkey_behavior: elements.hotkeyBehavior.value,
    hotkey_enabled: elements.hotkeyEnabled.checked,
    app_rules: {
      never_archive_apps: parseRuleList(elements.neverArchiveApps.value),
      fast_path_only_apps: parseRuleList(elements.fastPathOnlyApps.value),
      disable_direct_typing_apps: parseRuleList(elements.disableDirectTypingApps.value),
    },
    dashscope_key_us: elements.dashscopeKeyUs.value.trim() || undefined,
    dashscope_key_cn: elements.dashscopeKeyCn.value.trim() || undefined,
    openai_key: elements.openaiKey.value.trim() || undefined,
    siliconflow_key: elements.siliconflowKey.value.trim() || undefined,
    doubao_key: elements.doubaoKey.value.trim() || undefined,
    anthropic_key: elements.anthropicKey.value.trim() || undefined,
    custom_llm_key: elements.customLlmKey.value.trim() || undefined,
    openai_llm_model: elements.openaiLlmModel.value.trim(),
    qwen_llm_model: elements.qwenLlmModel.value.trim(),
    doubao_model: elements.doubaoModel.value.trim(),
    anthropic_model: elements.anthropicModel.value.trim(),
    custom_llm_base_url: elements.customLlmBaseUrl.value.trim(),
    custom_llm_model: elements.customLlmModel.value.trim(),
    ollama_model: elements.ollamaModel.value.trim(),
    llm_main: elements.llmMainRoute.value,
    llm_fallback: elements.llmFallbackRoute.value,
    stt_main: elements.sttMainRoute.value,
    stt_fallback: elements.sttFallbackRoute.value,
  };
}

// Status after a successful save. A credential-store read failure means the
// snapshot's "present"/"configured" flags are unknown, so never report plain
// "Saved" in that case (status is shown in place, with words, per DESIGN.md).
function settingsSavedState(snapshot) {
  return snapshot.credential_store_error ? ["Saved; Credentials Not Loaded", "danger"] : ["Saved", "live"];
}

function renderAppSettings(snapshot) {
  currentSettingsSnapshot = snapshot;
  elements.backendUrl.value = snapshot.backend_base_url || elements.backendUrl.value;
  renderProfileOptions(snapshot);
  elements.activeProfile.value = snapshot.active_profile || "home";
  elements.hotkeyAccelerator.value = snapshot.hotkey_accelerator || elements.hotkeyAccelerator.value;
  elements.hotkeyBehavior.value = snapshot.hotkey_behavior || "hold";
  elements.hotkeyEnabled.checked = snapshot.hotkey_enabled;
  elements.digestEnhancementEnabled.checked = Boolean(snapshot.digest_enhancement_enabled);
  elements.digestEnhancementProvider.value = snapshot.digest_enhancement_provider || "auto";
  elements.openaiLlmModel.value = snapshot.llm_config?.openai_model || "gpt-5-mini";
  elements.qwenLlmModel.value = snapshot.llm_config?.qwen_model || "qwen3.7-plus";
  elements.doubaoModel.value = snapshot.llm_config?.doubao_model || "doubao-seed-2-0-lite-260215";
  elements.anthropicModel.value = snapshot.llm_config?.anthropic_model || "claude-sonnet-5";
  elements.customLlmBaseUrl.value = snapshot.llm_config?.custom_base_url || "";
  elements.customLlmModel.value = snapshot.llm_config?.custom_model || "";
  elements.ollamaModel.value = snapshot.llm_config?.ollama_model || "qwen2.5:7b";
  elements.obsidianVaultDir.value = snapshot.obsidian_vault_dir || "";
  elements.aiMateMemoryDir.value = snapshot.ai_mate_memory_dir || "";
  elements.notionSyncEnabled.checked = Boolean(snapshot.notion_sync_enabled);
  elements.notionDataSourceId.value = snapshot.notion_data_source_id || "";
  elements.notionIncludeRawText.checked = Boolean(snapshot.notion_include_raw_text);
  elements.notionSyncDirection.value = snapshot.notion_sync_direction || "bidirectional";
  elements.gitSyncEnabled.checked = Boolean(snapshot.git_sync_enabled);
  elements.gitSyncRepoUrl.value = snapshot.git_sync_repo_url || "";
  elements.gitSyncLocalPath.value = snapshot.git_sync_local_path || "";
  const configuredSyncs = [
    snapshot.notion_sync_enabled && snapshot.notion_data_source_id && snapshot.notion_token_present,
    snapshot.git_sync_enabled && snapshot.git_sync_repo_url && snapshot.git_sync_local_path,
  ].filter(Boolean).length;
  setBadge(
    elements.crossPlatformSyncStatus,
    configuredSyncs ? `${configuredSyncs} configured` : "Not configured",
    configuredSyncs ? "live" : "neutral",
  );
  elements.runNotionSync.disabled = !(
    snapshot.notion_sync_enabled && snapshot.notion_data_source_id && snapshot.notion_token_present
  );
  elements.runGitSync.disabled = !(
    snapshot.git_sync_enabled && snapshot.git_sync_repo_url && snapshot.git_sync_local_path
  );
  elements.neverArchiveApps.value = joinRuleList(snapshot.app_rules?.never_archive_apps);
  elements.fastPathOnlyApps.value = joinRuleList(snapshot.app_rules?.fast_path_only_apps);
  elements.disableDirectTypingApps.value = joinRuleList(snapshot.app_rules?.disable_direct_typing_apps);
  renderProviderFlags(snapshot.provider_flags);
  renderProfileSummary(snapshot);
  renderProviderRecommendation(currentConnectivity);

  if (snapshot.credential_store_error) {
    // Presence flags are unknown when the OS credential store cannot be read.
    setProviderState("Credentials Unavailable", "danger");
    setSettingsState("Credential Store Unavailable", "danger");
  } else {
    const configuredCount = Object.values(snapshot.provider_flags).filter(Boolean).length;
    setProviderState(`${configuredCount} Configured`, configuredCount > 0 ? "live" : "warn");
    const storedControlTokens = [snapshot.backend_api_token_present, snapshot.engine_api_token_present].filter(Boolean).length;
    setSettingsState(storedControlTokens ? `${storedControlTokens} Control Token${storedControlTokens === 1 ? "" : "s"}` : "Local Only", "neutral");
  }

  if (snapshot.onboarding_completed) {
    elements.onboardingPanel.classList.add("hidden");
    setOnboardingState("Complete", "live");
  } else {
    elements.onboardingPanel.classList.remove("hidden");
    setOnboardingState("Setup Needed", "warn");
  }

  elements.backendApiToken.value = "";
  elements.engineApiToken.value = "";
  elements.dashscopeKeyUs.value = "";
  elements.dashscopeKeyCn.value = "";
  elements.openaiKey.value = "";
  elements.siliconflowKey.value = "";
  elements.doubaoKey.value = "";
  elements.anthropicKey.value = "";
  elements.customLlmKey.value = "";
  elements.notionToken.value = "";
  elements.completeOnboarding.disabled = false;
  renderOnboardingChecks(snapshot, null);
}

function renderModelOptions(select, values, selectedValue) {
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Environment default";
  const options = [defaultOption];
  for (const value of values || []) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    options.push(option);
  }
  select.replaceChildren(...options);
  select.value = selectedValue || "";
}

function renderModelPolicy(policy) {
  renderModelOptions(elements.modelAnswerer, policy.availableAnswerers, policy.answererOverride);
  renderModelOptions(elements.modelAsrAdapter, policy.availableAsrAdapters, policy.asrAdapterOverride);
  const llm = policy.llmConfigured
    ? `configured${policy.llmModel ? ` (${policy.llmModel})` : ""}`
    : "not configured";
  elements.modelPolicySummary.textContent =
    `Effective answer: ${policy.answerer} · speech: ${policy.asrAdapter} · LLM: ${llm}`;
}

async function refreshModelPolicy(options = {}) {
  if (!isTauri) return null;
  setBadge(elements.modelPolicyStatus, "Loading", "neutral");
  try {
    const policy = await invoke("get_engine_model_policy");
    renderModelPolicy(policy);
    setBadge(elements.modelPolicyStatus, "Policy loaded", "live");
    if (!options.silent) {
      log("Loaded Agent Engine model policy.", {
        answerer: policy.answerer,
        asr_adapter: policy.asrAdapter,
        llm_configured: policy.llmConfigured,
      });
    }
    return policy;
  } catch (error) {
    setBadge(elements.modelPolicyStatus, "Unavailable", "danger");
    elements.modelPolicySummary.textContent = "Start the Agent Engine and verify the admin credential.";
    if (!options.silent) {
      log("Failed to load Agent Engine model policy.", { error: String(error) });
    }
    return null;
  }
}

async function applyModelPolicy() {
  if (!isTauri) return;
  setBadge(elements.modelPolicyStatus, "Applying", "neutral");
  elements.applyModelPolicy.disabled = true;
  try {
    const policy = await invoke("update_engine_model_policy", {
      input: {
        answerer: elements.modelAnswerer.value || null,
        asrAdapter: elements.modelAsrAdapter.value || null,
      },
    });
    renderModelPolicy(policy);
    setBadge(elements.modelPolicyStatus, "Policy applied", "live");
    log("Applied persistent Agent Engine model policy.", {
      answerer_override: policy.answererOverride,
      asr_adapter_override: policy.asrAdapterOverride,
    });
  } catch (error) {
    setBadge(elements.modelPolicyStatus, "Apply failed", "danger");
    log("Failed to apply Agent Engine model policy.", { error: String(error) });
  } finally {
    elements.applyModelPolicy.disabled = false;
  }
}

async function refreshEngineDiagnostics(options = {}) {
  if (!isTauri) return;
  setBadge(elements.engineDiagnosticsStatus, "Checking", "neutral");
  const [processResult, healthResult, backendResult] = await Promise.allSettled([
    invoke("get_agent_engine_process"),
    invoke("get_agent_engine_health"),
    invoke("get_backend_health"),
  ]);

  const process = processResult.status === "fulfilled" ? processResult.value : null;
  const health = healthResult.status === "fulfilled" ? healthResult.value : null;
  const backend = backendResult.status === "fulfilled" ? backendResult.value : null;
  elements.engineProcessState.textContent = process
    ? `${process.state}${process.pid ? ` · PID ${process.pid}` : ""}`
    : "Unavailable";
  elements.engineServiceState.textContent = health
    ? health.reachable
      ? health.serviceStatus || "reachable"
      : health.error || "not reachable"
    : "Unavailable";
  elements.engineVersionState.textContent = health?.version || "Not reported";
  elements.backendServiceState.textContent = backend
    ? `${backend.service || "backend"} · ${backend.version || backend.profile || backend.status}`
    : "Unavailable";
  elements.engineLogPath.textContent = process?.logPath || "Not reported";

  if (health?.reachable && backend) {
    setBadge(elements.engineDiagnosticsStatus, "All services ready", "live");
  } else if (process?.state === "stopped" && backend) {
    setBadge(elements.engineDiagnosticsStatus, "Engine stopped", "neutral");
  } else {
    setBadge(elements.engineDiagnosticsStatus, "Service unavailable", "danger");
  }
  if (!options.silent) {
    log("Refreshed local runtime diagnostics.", {
      process: process?.state || "unavailable",
      engine_reachable: Boolean(health?.reachable),
      backend_reachable: Boolean(backend),
    });
  }
}

async function startSettingsEngine() {
  if (!isTauri) return;
  setBadge(elements.engineDiagnosticsStatus, "Starting", "neutral");
  try {
    await invoke("start_agent_engine");
    await waitFor(500);
    await ensureAgentEngine();
    await refreshEngineDiagnostics();
    await refreshModelPolicy({ silent: true });
  } catch (error) {
    setBadge(elements.engineDiagnosticsStatus, "Start failed", "danger");
    log("Failed to start the local Agent Engine.", { error: String(error) });
  }
}

async function stopSettingsEngine() {
  if (!isTauri) return;
  setBadge(elements.engineDiagnosticsStatus, "Stopping", "neutral");
  try {
    await invoke("stop_engine_event_stream");
    await invoke("stop_agent_engine");
    agentEngineReady = false;
    elements.askAgent.disabled = true;
    setBadge(elements.engineStreamStatus, "Stopped", "neutral");
    setBadge(elements.contractStatus, "Agent Engine Stopped", "neutral");
    await refreshEngineDiagnostics();
    setBadge(elements.modelPolicyStatus, "Engine stopped", "neutral");
  } catch (error) {
    setBadge(elements.engineDiagnosticsStatus, "Stop failed", "danger");
    log("Failed to stop the local Agent Engine.", { error: String(error) });
  }
}

async function runObsidianSync() {
  if (!isTauri) return;
  const vaultDir = elements.obsidianVaultDir.value.trim();
  const days = Number(elements.obsidianSyncDays.value);
  if (!vaultDir) {
    setBadge(elements.obsidianSyncStatus, "Vault required", "warn");
    elements.obsidianSyncSummary.textContent = "Choose an absolute Obsidian vault path before syncing.";
    return;
  }
  if (!Number.isInteger(days) || days < 1 || days > 365) {
    setBadge(elements.obsidianSyncStatus, "Window required", "warn");
    elements.obsidianSyncSummary.textContent = "Archive window must be a whole number from 1 through 365.";
    return;
  }

  setBadge(elements.obsidianSyncStatus, "Syncing", "neutral");
  elements.runObsidianSync.disabled = true;
  try {
    const result = await invoke("sync_obsidian_archive", {
      input: { vault_dir: vaultDir, days },
    });
    const dateSummary = result.dates.length ? ` · ${result.dates.join(", ")}` : " · no new notes";
    elements.obsidianSyncSummary.textContent = `${result.output_dir}${dateSummary}`;
    setBadge(
      elements.obsidianSyncStatus,
      `${result.exported} ${result.exported === 1 ? "day" : "days"} written`,
      "live",
    );
    log("Synced normal-mode voice history to the selected Obsidian vault.", result);
  } catch (error) {
    setBadge(elements.obsidianSyncStatus, "Sync failed", "danger");
    elements.obsidianSyncSummary.textContent = String(error);
    log("Failed to sync voice history to Obsidian.", { error: String(error) });
  } finally {
    elements.runObsidianSync.disabled = false;
  }
}

async function exportMigrationBundle() {
  if (!isTauri) return;
  setBadge(elements.migrationStatus, "Exporting", "neutral");
  elements.exportMigrationBundle.disabled = true;
  try {
    const result = await invoke("export_migration_bundle");
    if (result.secrets_included) {
      throw new Error("Migration safety contract rejected a bundle containing secrets");
    }
    elements.migrationBundlePath.value = result.path;
    migrationBundleDirectory = result.directory;
    elements.openMigrationDirectory.disabled = false;
    elements.migrationSummary.textContent = `${result.history_count} history items · ${result.correction_count} corrections · ${result.preserved_term_count} protected terms · no secrets`;
    setBadge(elements.migrationStatus, "Bundle ready", "live");
    log("Exported a portable Vox Agent migration bundle without secrets.", result);
  } catch (error) {
    setBadge(elements.migrationStatus, "Export failed", "danger");
    elements.migrationSummary.textContent = String(error);
    log("Failed to export the Vox Agent migration bundle.", { error: String(error) });
  } finally {
    elements.exportMigrationBundle.disabled = false;
  }
}

async function runNotionSync() {
  if (!isTauri) return;
  setBadge(elements.crossPlatformSyncStatus, "Syncing Notion", "neutral");
  elements.runNotionSync.disabled = true;
  elements.runGitSync.disabled = true;
  try {
    const result = await invoke("sync_notion_history");
    const pending = result.pending_uploads ? ` · ${result.pending_uploads} pending next run` : "";
    elements.crossPlatformSyncSummary.textContent = `${result.uploaded} uploaded · ${result.downloaded} imported · ${result.skipped} already present${pending}`;
    setBadge(elements.crossPlatformSyncStatus, "Notion synced", "live");
    await refreshHistory({ silent: true });
    log("Synchronized Vox Agent history through Notion.", result);
  } catch (error) {
    setBadge(elements.crossPlatformSyncStatus, "Notion failed", "danger");
    elements.crossPlatformSyncSummary.textContent = String(error);
    log("Failed to synchronize Vox Agent history through Notion.", { error: String(error) });
  } finally {
    elements.runNotionSync.disabled = !(
      currentSettingsSnapshot?.notion_sync_enabled
      && currentSettingsSnapshot?.notion_data_source_id
      && currentSettingsSnapshot?.notion_token_present
    );
    elements.runGitSync.disabled = !(
      currentSettingsSnapshot?.git_sync_enabled
      && currentSettingsSnapshot?.git_sync_repo_url
      && currentSettingsSnapshot?.git_sync_local_path
    );
  }
}

async function runGitSync() {
  if (!isTauri) return;
  setBadge(elements.crossPlatformSyncStatus, "Syncing Git", "neutral");
  elements.runNotionSync.disabled = true;
  elements.runGitSync.disabled = true;
  try {
    const result = await invoke("sync_git_history");
    const publication = result.published ? "snapshot published" : "snapshot unchanged";
    elements.crossPlatformSyncSummary.textContent = `${publication} · ${result.imported_snapshots} peer snapshots · ${result.history_imported} history imported · ${result.corrections_imported} corrections imported`;
    setBadge(elements.crossPlatformSyncStatus, "Git synced", "live");
    await Promise.all([
      refreshHistory({ silent: true }),
      refreshDictionary({ silent: true }),
    ]);
    log("Synchronized portable Vox Agent data through private Git.", result);
  } catch (error) {
    setBadge(elements.crossPlatformSyncStatus, "Git failed", "danger");
    elements.crossPlatformSyncSummary.textContent = String(error);
    log("Failed to synchronize portable Vox Agent data through private Git.", { error: String(error) });
  } finally {
    elements.runNotionSync.disabled = !(
      currentSettingsSnapshot?.notion_sync_enabled
      && currentSettingsSnapshot?.notion_data_source_id
      && currentSettingsSnapshot?.notion_token_present
    );
    elements.runGitSync.disabled = !(
      currentSettingsSnapshot?.git_sync_enabled
      && currentSettingsSnapshot?.git_sync_repo_url
      && currentSettingsSnapshot?.git_sync_local_path
    );
  }
}

async function importMigrationBundle() {
  if (!isTauri) return;
  const path = elements.migrationBundlePath.value.trim();
  if (!path.endsWith(".voxagent.json")) {
    setBadge(elements.migrationStatus, "Bundle required", "warn");
    elements.migrationSummary.textContent = "Paste the absolute path to a .voxagent.json bundle.";
    return;
  }
  setBadge(elements.migrationStatus, "Importing", "neutral");
  elements.importMigrationBundle.disabled = true;
  try {
    const result = await invoke("import_migration_bundle", { input: { path } });
    if (result.secrets_imported) {
      throw new Error("Migration safety contract reported imported secrets");
    }
    const reentry = result.reentry_required?.length
      ? ` Re-enter ${result.reentry_required.length} credential types in secure settings.`
      : "";
    elements.migrationSummary.textContent = `${result.history_imported} history imported · ${result.history_skipped} already present · ${result.corrections_imported} corrections imported.${reentry}`;
    setBadge(elements.migrationStatus, "Import complete", "live");
    const settings = await invoke("get_app_settings");
    renderAppSettings(settings);
    await Promise.all([
      refreshHistory({ silent: true }),
      refreshDictionary({ silent: true }),
      refreshNamedModes({ silent: true }),
    ]);
    log("Imported a portable Vox Agent migration bundle; secrets remain local-only.", result);
  } catch (error) {
    setBadge(elements.migrationStatus, "Import failed", "danger");
    elements.migrationSummary.textContent = String(error);
    log("Failed to import the Vox Agent migration bundle.", { error: String(error) });
  } finally {
    elements.importMigrationBundle.disabled = false;
  }
}

async function importLegacyVoxlog() {
  if (!isTauri) return;
  setBadge(elements.migrationStatus, "Scanning legacy data", "neutral");
  elements.importLegacyVoxlog.disabled = true;
  try {
    const result = await invoke("import_legacy_voxlog");
    const errors = result.sources.filter((source) => source.error).length;
    if (!result.sources.length) {
      elements.migrationSummary.textContent = "No preserved previous-app database was found. Nothing was changed.";
      setBadge(elements.migrationStatus, "No previous data", "neutral");
      return;
    }
    const errorSummary = errors ? ` · ${errors} source${errors === 1 ? "" : "s"} need review` : "";
    elements.migrationSummary.textContent = `${result.records_found} legacy records found · ${result.imported} imported · ${result.skipped} already present · ${result.invalid} invalid${errorSummary}`;
    setBadge(elements.migrationStatus, errors ? "Imported with review" : "Previous data imported", errors ? "warn" : "live");
    await refreshHistory({ silent: true });
    log("Imported preserved previous-app history into the Vox Agent data model without modifying its source.", result);
  } catch (error) {
    setBadge(elements.migrationStatus, "Legacy import failed", "danger");
    elements.migrationSummary.textContent = String(error);
    log("Failed to import preserved previous-app history.", { error: String(error) });
  } finally {
    elements.importLegacyVoxlog.disabled = false;
  }
}

async function openMigrationDirectory() {
  if (!isTauri || !migrationBundleDirectory) return;
  try {
    await invoke("open_system_path", { path: migrationBundleDirectory });
  } catch (error) {
    setBadge(elements.migrationStatus, "Open failed", "danger");
    log("Failed to open the migration bundle directory.", { error: String(error) });
  }
}

async function refreshSettingsControlPlane() {
  await Promise.all([
    refreshModelPolicy({ silent: true }),
    refreshEngineDiagnostics({ silent: true }),
    refreshProviderQualifications({ silent: true }),
  ]);
}

async function testProviderSettings(options = {}) {
  if (!isTauri) {
    return null;
  }

  try {
    setSettingsState("Testing", "warn");
    const payload = await invoke("test_provider_settings");
    renderSettingsChecks(payload);
    renderOnboardingChecks(null, payload);
    renderProviderRecommendation(payload);
    setProviderState(
      payload.ready ? `${payload.configured_provider_count} Configured` : "Setup Needed",
      payload.ready ? "live" : "warn",
    );
    setSettingsState(payload.ready ? "Routing Configured" : "Needs Setup", payload.ready ? "live" : "warn");
    if (!options.silent) {
      log("Checked provider routing configuration.", payload);
    }
    return payload;
  } catch (error) {
    renderSettingsChecks({
      checks: [
        {
          label: "Backend Bridge",
          status: "fail",
          message: `Connection test failed: ${String(error)}`,
        },
      ],
    });
    setProviderState("Connection Failed", "danger");
    setSettingsState("Test Failed", "danger");
    renderOnboardingChecks(null, null);
    renderProviderRecommendation(null);
    if (!options.silent) {
      log("Failed to test provider settings.", { error: String(error) });
    }
    return null;
  }
}

async function refreshMicrophonePermission() {
  if (!navigator.mediaDevices?.getUserMedia) {
    setPermissionState("Mic Unsupported", "warn");
    log("Media devices API unavailable.");
    return;
  }

  if (!navigator.permissions?.query) {
    setPermissionState("Mic Ready", "neutral");
    log("Permissions API unavailable; mic status will update on capture.");
    return;
  }

  try {
    const status = await navigator.permissions.query({ name: "microphone" });
    microphonePermissionStatus = status;
    handlePermissionState(status.state);
    renderOnboardingChecks(null, null);
    status.onchange = () => {
      handlePermissionState(status.state);
      renderOnboardingChecks(null, null);
    };
    log("Refreshed microphone permission.", { state: status.state });
  } catch (error) {
    setPermissionState("Mic Unknown", "neutral");
    renderOnboardingChecks(null, null);
    log("Failed to query microphone permission.", { error: String(error) });
  }
}

function scheduleEngineRefresh() {
  if (engineRefreshTimer) {
    clearTimeout(engineRefreshTimer);
  }
  engineRefreshTimer = window.setTimeout(() => {
    refreshEngineOperations({ silent: true });
  }, 120);
}

async function refreshEngineOperations(options = {}) {
  if (!isTauri) return null;
  try {
    const snapshot = await invoke("get_engine_operations");
    renderEngineOperations(snapshot);
    agentEngineReady = true;
    elements.askAgent.disabled = false;
    if (!options.silent) {
      log("Reconciled Agent Engine operations.", {
        agents: snapshot.agents?.length || 0,
        tasks: snapshot.tasks?.length || 0,
        approvals: snapshot.pendingApprovals?.length || 0,
      });
    }
    return snapshot;
  } catch (error) {
    agentEngineReady = false;
    elements.askAgent.disabled = true;
    setBadge(elements.engineStreamStatus, "Engine blocked", "danger");
    if (!options.silent) {
      log("Agent Engine operations unavailable.", { error: String(error) });
    }
    return null;
  }
}

function waitFor(delayMs) {
  return new Promise((resolve) => window.setTimeout(resolve, delayMs));
}

async function ensureAgentEngine() {
  let health = await invoke("get_agent_engine_health");
  if (!health.reachable) {
    setBadge(elements.engineStreamStatus, "Starting engine", "neutral");
    await invoke("start_agent_engine");
    for (let attempt = 0; attempt < 16; attempt += 1) {
      await waitFor(250);
      health = await invoke("get_agent_engine_health");
      if (health.reachable) break;
    }
  }
  if (!health.reachable) {
    throw new Error(health.error || "Local Agent Engine did not become ready");
  }
  const snapshot = await refreshEngineOperations();
  if (!snapshot) {
    throw new Error("Agent Engine authentication failed; check the admin token in Settings");
  }
  await invoke("start_engine_event_stream");
  setBadge(elements.contractStatus, "Agent Engine Ready", "live");
}

async function initializeEngineOperations() {
  engineEventUnlisten = await listen("vox-agent://engine-event", (event) => {
    const envelope = event.payload || {};
    const eventType = envelope.type || "unknown";
    if (eventType !== "task:message") {
      log("Agent Engine event.", { type: eventType });
    }
    scheduleEngineRefresh();
  });
  engineStreamUnlisten = await listen("vox-agent://engine-stream", (event) => {
    const status = event.payload || {};
    const label = status.state === "connected" ? "Events live" : status.state || "Engine events";
    setBadge(elements.engineStreamStatus, label, statusKind(status.state));
    if (["blocked", "reconnecting"].includes(status.state)) {
      log("Agent Engine stream state changed.", status);
    }
  });
  try {
    await ensureAgentEngine();
  } catch (error) {
    agentEngineReady = false;
    elements.askAgent.disabled = true;
    setBadge(elements.contractStatus, "Agent Engine Unavailable", "danger");
    setBadge(elements.engineStreamStatus, "Offline", "danger");
    log("Failed to initialize Agent Engine operations.", { error: String(error) });
  }
}

async function bootstrap() {
  migrateLegacyPreferences();
  hydratePreferences();
  renderRecognitionEvidence(null);
  renderOutputPolicy(null);
  renderSessionDigest(null);

  if (!isTauri) {
    setBadge(elements.bridgeStatus, "Browser Mode", "warn");
    setBadge(elements.contractStatus, "Tauri Runtime Required", "neutral");
    log("Tauri runtime not available; commands disabled.");
    return;
  }

  try {
    const contract = await invoke("get_desktop_contract");
    desktopContractReady = true;
    setBadge(elements.contractStatus, `API ${contract.api_version}`, "live");
    setBridgeState("Vox Agent Ready", "live");
    log("Verified Vox Agent desktop contract.", contract);
  } catch (error) {
    desktopContractReady = false;
    setBadge(elements.contractStatus, "Voice Bridge Offline", "warn");
    setBridgeState("Voice Bridge Offline", "warn");
    log("Voice-memory bridge contract verification failed; Agent Engine startup will continue.", {
      error: String(error),
    });
  }

  try {
    const url = await invoke("get_backend_base_url");
    if (!storage.get(STORAGE_KEYS.backendUrl)) {
      elements.backendUrl.value = url;
    }
    persistPreferences();
    if (desktopContractReady) {
      setBridgeState("Vox Agent Ready", "live");
    }
    log("Loaded backend base URL.", { url: elements.backendUrl.value });
  } catch (error) {
    setBridgeState("Bridge Error", "warn");
    log("Failed to load backend base URL.", { error: String(error) });
  }

  try {
    const settings = await invoke("get_app_settings");
    renderAppSettings(settings);
    if (!storage.get(STORAGE_KEYS.backendUrl)) {
      elements.backendUrl.value = settings.backend_base_url;
    }
    persistPreferences();
    log("Loaded app settings.", settings);
    const connectivity = await testProviderSettings({ silent: true });
    await refreshProviderQualifications({ silent: true });
    elements.completeOnboarding.disabled = !canCompleteOnboarding(settings, connectivity);
  } catch (error) {
    setSettingsState("Load Failed", "danger");
    setHotkeyState("Hotkey Error", "warn");
    setProviderState("Providers Unknown", "warn");
    log("Failed to load app settings.", { error: String(error) });
  }

  globalHotkeyUnlisten = await listen("vox-agent://global-hotkey", async (event) => {
    const payload = event.payload || {};
    log("Global hotkey event.", payload);
    if (["escape", "esc"].includes(String(payload.accelerator || "").toLowerCase())) {
      if (payload.phase === "pressed") {
        await cancelRecording();
      }
      return;
    }
    const namedMode = findNamedModeForHotkey(payload.accelerator);
    if (namedMode && payload.phase === "pressed") {
      applyNamedMode(namedMode);
    }
    const behavior = elements.hotkeyBehavior.value;
    if (behavior === "toggle") {
      if (payload.phase !== "pressed") {
        return;
      }
      if (capture.phase() !== "idle") {
        setHotkeyState("Hotkey Ready", "live");
        await stopRecording({ owner: "hotkey" });
      } else {
        setHotkeyState("Hotkey Pressed", "warn");
        await startRecording({ owner: "hotkey", injectAfterUpload: true });
      }
      return;
    }
    if (payload.phase === "pressed") {
      setHotkeyState("Hotkey Pressed", "warn");
      await startRecording({ owner: "hotkey", injectAfterUpload: true });
      return;
    }
    if (payload.phase === "released") {
      setHotkeyState("Hotkey Ready", "live");
      await stopRecording({ owner: "hotkey" });
    }
  });

  await initializeEngineOperations();

  await refreshMicrophonePermission();

  if (elements.hotkeyEnabled.checked) {
    await saveHotkeyConfig({ silent: true });
  } else {
    setHotkeyState("Hotkey Off", "neutral");
  }

  await refreshBackendHealth({ silent: true });
  await refreshOutputPolicyPreview();
  await refreshRecent();
  await refreshSessionDigest({ silent: true });
  await refreshAgents();
  await refreshHistory({ silent: true });
  await refreshDictionary({ silent: true });
  await refreshNamedModes({ silent: true });
  await refreshSettingsControlPlane();
  startPolling();
}

let agentCommandInFlight = false;

async function askAgent() {
  // Agent commands only ever go to an agent target; group text is Context.
  if (contextUi.currentTarget().kind !== "agent" || agentCommandInFlight) {
    return;
  }
  const submittedTarget = contextUi.currentTarget();
  const submittedText = elements.agentQuery.value;
  const query = submittedText.trim();
  if (!agentEngineReady) {
    setBadge(elements.contractStatus, "Agent Engine Required", "warn");
    return;
  }
  if (!query) {
    elements.agentQuery.focus();
    return;
  }

  agentCommandInFlight = true;
  elements.askAgent.disabled = true;
  elements.askAgent.textContent = "Running…";
  setBadge(elements.contractStatus, "Agent Running", "running");
  try {
    const target = contextUi.currentTarget();
    const result = await invoke("run_engine_command", {
      input: { text: query, agentId: target.kind === "agent" ? target.id : null, provider: "claude" },
    });
    elements.agentAnswer.textContent = result.message || (result.taskId ? "Task accepted by the Agent Engine." : "Command accepted.");
    elements.agentAnswerer.textContent = result.taskId ? "Vox Agent Coordinator" : "Vox Agent";
    elements.agentContext.textContent = result.taskId
      ? `Traceable task ${result.taskId} · live status follows below.`
      : `Engine action: ${result.action || "respond"}`;
    elements.agentAnswerCard.classList.remove("hidden");
    // Clear only the draft this command came from, if it is unchanged.
    contextUi.finishAgentCommand(submittedTarget, submittedText);
    setBadge(elements.contractStatus, result.taskId ? "Task Dispatched" : "Agent Complete", "live");
    log("Accepted Agent Engine command.", {
      action: result.action,
      task_id: result.taskId || null,
    });
    await refreshEngineOperations({ silent: true });
  } catch (error) {
    setBadge(elements.contractStatus, "Agent Failed", "danger");
    elements.agentAnswer.textContent = String(error);
    elements.agentAnswerer.textContent = "Action failed";
    elements.agentContext.textContent = "Check the local Agent Engine, its admin credential, and provider configuration.";
    elements.agentAnswerCard.classList.remove("hidden");
    log("Agent command failed.", { error: String(error) });
  } finally {
    agentCommandInFlight = false;
    elements.askAgent.disabled = !agentEngineReady;
    elements.askAgent.textContent = "↵";
  }
}

async function decideEnginePermission(approvalId, approved, rememberScope, card) {
  const buttons = [...card.querySelectorAll("button")];
  for (const button of buttons) button.disabled = true;
  try {
    const result = await invoke("resolve_engine_permission", {
      approvalId,
      approved,
      rememberScope,
    });
    log("Recorded Agent tool decision.", { approval_id: approvalId, decision: result.decision });
    await refreshEngineOperations({ silent: true });
  } catch (error) {
    for (const button of buttons) button.disabled = false;
    log("Agent tool decision failed.", { approval_id: approvalId, error: String(error) });
  }
}

async function refreshRecent() {
  if (!isTauri) return;

  try {
    const payload = await invoke("get_recent_utterance");
    renderRecent(payload.recent);
    log("Fetched recent utterance.", { found: Boolean(payload.recent) });
  } catch (error) {
    renderRecent(null);
    setBadge(elements.recentStatus, "Unavailable", "warn");
    log("Failed to fetch recent utterance.", { error: String(error) });
  }
}

async function refreshBackendHealth(options = {}) {
  if (!isTauri) return;

  try {
    const payload = await invoke("get_backend_health");
    const service = payload.service || "backend";
    const version = payload.version || payload.profile || "ok";
    setBridgeState(`${service} ${version}`, "live");
    if (!options.silent) {
      log("Fetched backend health.", payload);
    }
  } catch (error) {
    setBridgeState("Backend Down", "warn");
    if (!options.silent) {
      log("Failed to fetch backend health.", { error: String(error) });
    }
  }
}

async function refreshOutputPolicyPreview() {
  if (!isTauri) {
    return;
  }
  try {
    const policy = await invoke("preview_output_policy", {
      targetApp: elements.targetApp.value.trim(),
      riskLevel: elements.targetRisk.value,
      requestedMode: "paste",
      appRules: {
        never_archive_apps: parseRuleList(elements.neverArchiveApps.value),
        fast_path_only_apps: parseRuleList(elements.fastPathOnlyApps.value),
        disable_direct_typing_apps: parseRuleList(elements.disableDirectTypingApps.value),
      },
    });
    renderOutputPolicy(policy);
    if (!policy.should_confirm) {
      hidePolicyPreview();
    }
  } catch (error) {
    log("Failed to preview output policy.", { error: String(error) });
  }
}

async function refreshCaptureStatus(options = {}) {
  if (!isTauri || !isRecording) {
    return;
  }
  try {
    const durationMs = Math.max(250, Date.now() - recordingStartedAt);
    const preview = await invoke("preview_capture_status", {
      durationMs,
      targetApp: elements.targetApp.value.trim(),
    });
    renderRecognitionEvidence(preview);
    if (!options.silent) {
      log("Updated capture status.", {
        duration_ms: durationMs,
        capture_window_count: preview.capture_window_count,
      });
    }
  } catch (error) {
    setTranscriptState("Preview Error", "warn");
    if (!options.silent) {
      log("Failed to refresh capture status.", { error: String(error) });
    }
  }
}

async function refreshSessionDigest(options = {}) {
  if (!isTauri) {
    return;
  }

  const digestType = elements.digestType.value;
  const sessionId = elements.sessionId.value.trim();
  const digestDate = elements.digestDate.value.trim() || new Date().toISOString().slice(0, 10);
  const projectKey = elements.digestProjectKey.value.trim().toLowerCase();
  if (digestType === "session" && !sessionId) {
    renderSessionDigest(null);
    return;
  }
  if (digestType === "project" && !projectKey) {
    renderSessionDigest(null);
    return;
  }

  try {
    setDigestState("Loading", "warn");
    elements.digestExportStatus.textContent = "(waiting)";
    elements.aiMateExportStatus.textContent = "(waiting)";
    const digest =
      digestType === "daily"
        ? await invoke("get_daily_digest", { date: digestDate })
        : digestType === "project"
          ? await invoke("get_project_digest", { projectKey })
        : await invoke("get_session_digest", { sessionId });
    renderSessionDigest(digest);
    if (!options.silent) {
      log("Fetched digest.", {
        digest_type: digest.digest_type,
        session_id: sessionId,
        digest_date: digest.digest_date,
        project_key: digest.project_key,
        intent: digest.intent,
      });
    }
  } catch (error) {
    setDigestState("Missing", "warn");
    elements.digestExportStatus.textContent = "(waiting)";
    elements.aiMateExportStatus.textContent = "(waiting)";
    if (!options.silent) {
      log("Digest unavailable.", {
        digest_type: digestType,
        session_id: sessionId,
        digest_date: digestDate,
        project_key: projectKey,
        error: String(error),
      });
    }
  }
}

async function rebuildDigest() {
  if (!isTauri) {
    return;
  }

  const scope = elements.digestType.value;
  const payload = {
    scope,
    session_id: scope === "session" ? elements.sessionId.value.trim() : undefined,
    date: scope === "daily" ? elements.digestDate.value.trim() : undefined,
    project_key: scope === "project" ? elements.digestProjectKey.value.trim().toLowerCase() : undefined,
  };

  try {
    setDigestState("Rebuilding", "warn");
    elements.digestExportStatus.textContent = "(waiting)";
    elements.aiMateExportStatus.textContent = "(waiting)";
    const digest = await invoke("rebuild_digest", { input: payload });
    renderSessionDigest(digest);
    setDigestState("Rebuilt", "live");
    log("Rebuilt digest.", {
      scope,
      session_id: payload.session_id,
      date: payload.date,
      project_key: payload.project_key,
    });
  } catch (error) {
    setDigestState("Rebuild Failed", "warn");
    log("Failed to rebuild digest.", { scope, error: String(error) });
  }
}

async function exportDigestToObsidian() {
  if (!isTauri) {
    return;
  }

  const vaultDir = elements.obsidianVaultDir.value.trim();
  if (!vaultDir) {
    elements.digestExportStatus.textContent = "Set Obsidian vault path first.";
    setDigestState("Export Blocked", "warn");
    return;
  }

  const scope = elements.digestType.value;
  const payload = {
    scope,
    vault_dir: vaultDir,
    session_id: scope === "session" ? elements.sessionId.value.trim() : undefined,
    date: scope === "daily" ? elements.digestDate.value.trim() : undefined,
    project_key: scope === "project" ? elements.digestProjectKey.value.trim().toLowerCase() : undefined,
  };

  try {
    setDigestState("Exporting", "warn");
    const result = await invoke("export_digest_to_obsidian", { input: payload });
    elements.digestExportStatus.textContent = `${result.note_path} (${result.bytes_written} bytes)`;
    setDigestState("Exported", "live");
    log("Exported digest to Obsidian.", result);
  } catch (error) {
    elements.digestExportStatus.textContent = String(error);
    setDigestState("Export Failed", "warn");
    log("Failed to export digest to Obsidian.", { error: String(error) });
  }
}

async function exportDigestToAiMateMemory() {
  if (!isTauri) {
    return;
  }

  const baseDir = elements.aiMateMemoryDir.value.trim();
  if (!baseDir) {
    elements.aiMateExportStatus.textContent = "Set AI Mate Memory path first.";
    setDigestState("Export Blocked", "warn");
    return;
  }

  const scope = elements.digestType.value;
  const payload = {
    scope,
    base_dir: baseDir,
    session_id: scope === "session" ? elements.sessionId.value.trim() : undefined,
    date: scope === "daily" ? elements.digestDate.value.trim() : undefined,
    project_key: scope === "project" ? elements.digestProjectKey.value.trim().toLowerCase() : undefined,
  };

  try {
    setDigestState("Exporting", "warn");
    const result = await invoke("export_digest_to_ai_mate_memory", { input: payload });
    elements.aiMateExportStatus.textContent = `${result.record_path} (${result.bytes_written} bytes)`;
    setDigestState("Exported", "live");
    log("Exported digest to AI Mate Memory.", result);
  } catch (error) {
    elements.aiMateExportStatus.textContent = String(error);
    setDigestState("Export Failed", "warn");
    log("Failed to export digest to AI Mate Memory.", { error: String(error) });
  }
}

function startPolling() {
  if (healthPollTimer) {
    clearInterval(healthPollTimer);
  }
  if (recentPollTimer) {
    clearInterval(recentPollTimer);
  }
  if (historyPollTimer) {
    clearInterval(historyPollTimer);
  }
  if (enginePollTimer) {
    clearInterval(enginePollTimer);
  }

  healthPollTimer = window.setInterval(() => {
    refreshBackendHealth({ silent: true });
  }, 15000);
  recentPollTimer = window.setInterval(() => {
    if (!isRecording) {
      refreshRecent();
    }
  }, 10000);
  historyPollTimer = window.setInterval(() => {
    if (!isRecording) {
      refreshHistory({ silent: true });
    }
  }, 15000);
  enginePollTimer = window.setInterval(() => {
    refreshEngineOperations({ silent: true });
  }, 5000);
}

async function refreshAgents() {
  if (!isTauri) return;

  try {
    const agents = await invoke("get_agents");
    const persistedValue = elements.historyAgent.dataset.persistedValue || "";
    const currentValue = elements.historyAgent.value || persistedValue;
    elements.historyAgent.innerHTML = '<option value="">all agents</option>';
    for (const item of agents) {
      const option = document.createElement("option");
      option.value = item.agent;
      option.textContent = `${item.agent} (${item.count})`;
      elements.historyAgent.append(option);
    }
    if ([...elements.historyAgent.options].some((option) => option.value === currentValue)) {
      elements.historyAgent.value = currentValue;
    }
    elements.historyAgent.dataset.persistedValue = "";
    persistPreferences();
  } catch (error) {
    log("Failed to fetch agent list.", { error: String(error) });
  }
}

async function refreshHistory(options = {}) {
  if (!isTauri) return;

  try {
    setHistoryState("Loading", "neutral");
    const query = elements.historyQuery.value.trim();
    const agent = elements.historyAgent.value;
    const sessionFilter = getActiveHistorySessionFilter();
    const rawItems = agent
      ? await invoke("get_agent_history", { agent, limit: 20 })
      : await invoke("get_history", { q: query, limit: 20 });
    const items = sessionFilter
      ? rawItems.filter((item) => item.session_id === sessionFilter)
      : rawItems;
    renderHistory(items);
    if (!options.silent) {
      log("Fetched history items.", { count: items.length, query, agent, sessionFilter });
    }
  } catch (error) {
    setHistoryState("Unavailable", "danger");
    elements.historyEmpty.classList.remove("hidden");
    elements.historyList.classList.add("hidden");
    if (!options.silent) {
      log("Failed to fetch history items.", { error: String(error) });
    }
  }
}

async function exportHistory(format) {
  if (!isTauri) return;
  const input = {
    q: elements.historyQuery.value.trim(),
    agent: elements.historyAgent.value,
    sessionId: getActiveHistorySessionFilter(),
    format,
  };
  try {
    elements.exportHistoryMarkdown.disabled = true;
    elements.exportHistoryJson.disabled = true;
    elements.historyExportStatus.textContent = `Exporting ${format} history…`;
    const response = await invoke("export_history", { input });
    historyExportDirectory = response.directory;
    elements.openHistoryExports.disabled = false;
    elements.historyExportStatus.textContent = `Exported ${response.itemCount} items to ${response.path}`;
    log("Exported history.", response);
  } catch (error) {
    elements.historyExportStatus.textContent = `History export failed: ${String(error)}`;
    log("Failed to export history.", { format, error: String(error) });
  } finally {
    elements.exportHistoryMarkdown.disabled = false;
    elements.exportHistoryJson.disabled = false;
  }
}

async function openHistoryExportDirectory() {
  if (!isTauri || !historyExportDirectory) return;
  try {
    await invoke("open_system_path", { path: historyExportDirectory });
    log("Opened history export directory.", { path: historyExportDirectory });
  } catch (error) {
    elements.historyExportStatus.textContent = `Could not open export directory: ${String(error)}`;
    log("Failed to open history export directory.", { error: String(error) });
  }
}

async function saveBackendUrl() {
  if (!isTauri) return;

  try {
    const url = await invoke("set_backend_base_url", {
      backendBaseUrl: elements.backendUrl.value,
    });
    elements.backendUrl.value = url;
    persistPreferences();
    setBridgeState("Saved", "live");
    log("Updated backend base URL.", { url });
    await refreshBackendHealth({ silent: true });
  } catch (error) {
    setBridgeState("Save Failed", "warn");
    log("Failed to update backend base URL.", { error: String(error) });
  }
}

async function saveAppSettings(options = {}) {
  if (!isTauri) return;

  try {
    setSettingsState("Saving", "warn");
    const snapshot = await invoke("save_app_settings", {
      input: buildSettingsPayload(options),
    });
    renderAppSettings(snapshot);
    persistPreferences();
    setSettingsState(...settingsSavedState(snapshot));
    setHotkeyState(snapshot.hotkey_enabled ? "Hotkey Ready" : "Hotkey Off", snapshot.hotkey_enabled ? "live" : "neutral");
    await refreshBackendHealth({ silent: true });
    const connectivity = await testProviderSettings({ silent: true });
    elements.completeOnboarding.disabled = !canCompleteOnboarding(snapshot, connectivity);
    await refreshAgents();
    await refreshHistory({ silent: true });
    try {
      await ensureAgentEngine();
    } catch (engineError) {
      setBadge(elements.engineStreamStatus, "Credential required", "danger");
      log("Saved settings, but Agent Engine reconnect failed.", { error: String(engineError) });
    }
    log("Saved app settings.", {
      active_profile: snapshot.active_profile,
      onboarding_completed: snapshot.onboarding_completed,
    });
  } catch (error) {
    setSettingsState("Save Failed", "danger");
    log("Failed to save app settings.", { error: String(error) });
  }
}

async function completeOnboarding() {
  const preview = buildSettingsPayload({ completeOnboarding: false });
  if (!preview.backend_base_url.trim()) {
    setOnboardingState("Backend Required", "warn");
    setSettingsState("Add Backend URL", "warn");
    return;
  }
  const saved = await invoke("save_app_settings", {
    input: preview,
  });
  renderAppSettings(saved);
  const connectivity = await testProviderSettings({ silent: false });
  if (!canCompleteOnboarding(saved, connectivity)) {
    setOnboardingState("Add Provider Key", "warn");
    elements.completeOnboarding.disabled = false;
    return;
  }
  await saveAppSettings({ completeOnboarding: true });
  setOnboardingState("Complete", "live");
}

async function saveHotkeyConfig(options = {}) {
  if (!isTauri) return;

  try {
    const response = await invoke("set_hotkey_config", {
      accelerator: elements.hotkeyAccelerator.value.trim(),
      behavior: elements.hotkeyBehavior.value,
      enabled: elements.hotkeyEnabled.checked,
    });
    elements.hotkeyAccelerator.value = response.accelerator;
    elements.hotkeyEnabled.checked = response.enabled;
    persistPreferences();
    setHotkeyState(response.registered ? "Hotkey Ready" : "Hotkey Off", response.registered ? "live" : "neutral");
    if (!options.silent) {
      log("Updated hotkey config.", response);
    }
  } catch (error) {
    setHotkeyState("Hotkey Error", "warn");
    log("Failed to update hotkey config.", { error: String(error) });
  }
}

async function dismissRecent() {
  if (!isTauri || !currentRecent) return;

  try {
    await invoke("dismiss_recent_utterance", {
      utteranceId: currentRecent.utterance_id,
    });
    log("Dismissed recent utterance.", { utterance_id: currentRecent.utterance_id });
    renderRecent(null);
  } catch (error) {
    log("Failed to dismiss recent utterance.", { error: String(error) });
  }
}

async function undoOutput() {
  if (!isTauri || !currentRecent) return;

  try {
    const response = await invoke("undo_output", {
      outputId: currentRecent.output_id,
    });
    log("Undo output requested.", response);
    if (response.ui_undo_required) {
      setBadge(elements.recentStatus, "UI Undo Needed", "warn");
    }
  } catch (error) {
    log("Failed to request output undo.", { error: String(error) });
  }
}

async function retryRecent() {
  if (!isTauri || !currentRecent) return;

  try {
    const response = await invoke("retry_recent_utterance", {
      utteranceId: currentRecent.utterance_id,
      provider: elements.retryProvider.value.trim(),
    });
    log("Retried recent utterance.", {
      utterance_id: response.utterance_id,
      retried_from: response.retried_from,
      provider: response.stt_provider,
    });
    await refreshRecent();
  } catch (error) {
    log("Failed to retry recent utterance.", { error: String(error) });
  }
}

async function applyRecentMode() {
  if (!isTauri || !currentRecent) return;

  try {
    const response = await invoke("set_recent_mode", {
      utteranceId: currentRecent.utterance_id,
      mode: elements.recentModeSelect.value,
    });
    log("Updated recent mode.", response);
    await refreshRecent();
  } catch (error) {
    log("Failed to update recent mode.", { error: String(error) });
  }
}

async function addRecentDictionary() {
  if (!isTauri || !currentRecent) return;

  const wrong = elements.dictionaryWrong.value.trim();
  const right = elements.dictionaryRight.value.trim();
  if (!wrong || !right) {
    log("Dictionary update skipped.", { reason: "wrong/right required" });
    return;
  }

  try {
    const response = await invoke("add_recent_dictionary_term", {
      utteranceId: currentRecent.utterance_id,
      wrong,
      right,
    });
    log("Added recent dictionary mapping.", {
      utterance_id: response.utterance_id,
      wrong: response.wrong,
      right: response.right,
    });
    await refreshDictionary({ silent: true });
  } catch (error) {
    log("Failed to add recent dictionary mapping.", { error: String(error) });
  }
}

async function ensureMediaRecorder() {
  if (mediaRecorder) {
    return mediaRecorder;
  }

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaStream = stream;
  handlePermissionState("granted");

  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : "audio/webm";
  mediaRecorder = new MediaRecorder(stream, { mimeType });

  mediaRecorder.addEventListener("dataavailable", (event) => {
    if (event.data && event.data.size > 0) {
      mediaChunks.push(event.data);
    }
  });

  return mediaRecorder;
}

// WebView fallback recorder for the capture controller; used only when the
// native recorder is unavailable (never when it is merely busy).
const webviewRecorder = {
  async start() {
    const recorder = await ensureMediaRecorder();
    mediaChunks = [];
    recorder.start();
  },
  stop() {
    const recorder = mediaRecorder;
    return new Promise((resolve) => {
      recorder.addEventListener(
        "stop",
        () => resolve({ chunks: mediaChunks.splice(0), mimeType: recorder.mimeType || "audio/webm" }),
        { once: true },
      );
      recorder.stop();
    });
  },
  async cancel() {
    await webviewRecorder.stop();
    mediaChunks = [];
  },
};

const capture = createCaptureController({ invoke, fallback: webviewRecorder });

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const value = reader.result;
      if (typeof value !== "string") {
        reject(new Error("Unexpected FileReader result"));
        return;
      }
      const [, base64] = value.split(",", 2);
      resolve(base64);
    };
    reader.onerror = () => reject(reader.error || new Error("FileReader failed"));
    reader.readAsDataURL(blob);
  });
}

function markRecordingStarted(source, details = {}) {
  isRecording = true;
  recordingStartedAt = Date.now();
  setRecordingState("Recording", "warn");
  setTranscriptState("Capturing", "warn");
  renderRecognitionEvidence({
    provider: "pending selected route",
    model: source === "native" ? "cpal / system input" : "webview audio buffer",
    status_text: "Listening for your command...",
    completion_hint: "Vox Agent will transcribe through the selected route after capture stops.",
    capture_window_count: 0,
  });
  if (captureStatusPollTimer) {
    clearInterval(captureStatusPollTimer);
  }
  captureStatusPollTimer = window.setInterval(() => {
    refreshCaptureStatus({ silent: true });
  }, 700);
  void updateVoiceHud({
    phase: "listening",
    instruction: "Release to send · Esc to cancel",
    elapsedMs: 0,
    rmsLevel: 0,
    peakLevel: 0,
  });
  startVoiceHudLevelPolling();
  log("Recording started.", { source, ...details });
}

async function setCaptureAbortHotkey(enabled) {
  try {
    await invoke("set_capture_abort_hotkey", { enabled });
  } catch (error) {
    log("Capture cancel shortcut unavailable.", { enabled, error: String(error) });
  }
}

async function routeCapturedAudio({ audioBase64, mimeType, source, injectAfterUpload = false }) {
  const sessionId = ensureSessionId();
  const targetApp = elements.targetApp.value.trim();
  const mode = elements.recordMode.value;
  persistPreferences();

  const payload = {
    audioBase64,
    mimeType,
    source,
    targetApp,
    sessionId,
    mode,
    injectAfterUpload,
  };
  const effectivePolicy = currentOutputPolicy;
  if (effectivePolicy?.should_confirm) {
    pendingUploadPayload = payload;
    setRecordingState("Preview Required", "warn");
    setTranscriptState("Preview Required", "warn");
    showPolicyPreview(elements.finalTranscript.textContent || elements.partialTranscript.textContent);
    void updateVoiceHud({
      phase: "processing",
      instruction: "Approval required in Vox Agent before output",
    });
    scheduleVoiceHudHide(2_400);
    log("Preview required before upload.", {
      strategy: effectivePolicy.strategy,
      target_app: targetApp,
      risk: elements.targetRisk.value,
    });
    return;
  }

  await sendVoicePayload(payload);
}

async function uploadRecordedChunks({ chunks, mimeType, injectAfterUpload = false }) {
  if (!chunks.length) {
    log("No audio captured; skipping upload.");
    setRecordingState("Idle", "neutral");
    void updateVoiceHud({ phase: "hidden" }, { silent: true });
    return;
  }

  const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
  const audioBase64 = await blobToBase64(blob);
  await routeCapturedAudio({
    audioBase64,
    mimeType: blob.type || "audio/webm",
    source: "desktop-tauri-webview-fallback",
    injectAfterUpload,
  });
}

async function sendVoicePayload(payload) {
  clearVoiceHudHideTimer();
  setRecordingState("Uploading", "warn");
  void updateVoiceHud({
    phase: "processing",
    instruction: "Transcribing and routing your command…",
    elapsedMs: Date.now() - recordingStartedAt,
  });

  const response = await invoke("upload_voice", {
    audioBase64: payload.audioBase64,
    mimeType: payload.mimeType,
    source: payload.source,
    targetApp: payload.targetApp,
    sessionId: payload.sessionId,
    mode: payload.mode,
  });

  log("Uploaded utterance to backend.", {
    id: response.id,
    utterance_id: response.utterance_id,
    latency_ms: response.latency_ms,
  });

  setRecordingState("Uploaded", "live");
  setTranscriptState("Final", "live");
  elements.partialTranscript.textContent = response.raw_text || "(empty)";
  elements.finalTranscript.textContent = response.display_text || "(empty)";
  elements.transcriptProvider.textContent = response.stt_provider || "unknown";
  elements.transcriptModel.textContent = response.stt_model || "unknown";
  if (
    payload.injectAfterUpload &&
    response.should_autopaste &&
    response.display_text &&
    ["paste", "direct_typing"].includes(response.output_mode)
  ) {
    try {
      const injection = await invoke("inject_text", {
        text: response.display_text,
        method: response.output_mode,
      });
      setRecordingState(injection.method === "paste" ? "Pasted" : "Typed", "live");
      log("Routed transcript to the active app.", injection);
    } catch (error) {
      setRecordingState("Output Failed", "warn");
      showVoiceHudError("Transcript is ready, but output to the active application failed");
      log("Transcript is ready but text injection failed.", { error: String(error) });
    }
  }
  if (elements.recordingStatus.textContent !== "Output Failed") {
    void updateVoiceHud({
      phase: "sent",
      instruction: "Command completed",
      transcript: response.display_text || response.raw_text || "Voice command sent",
      elapsedMs: Date.now() - recordingStartedAt,
    });
    scheduleVoiceHudHide(1_800);
  }
  hidePolicyPreview();
  await refreshRecent();
  await refreshSessionDigest({ silent: true });
}

/**
 * Start a capture owned by `owner` ("hotkey" or "ui"); T26. The controller
 * claims the capture before anything is awaited, so a concurrent start is
 * refused and cannot change this session's output options.
 */
async function startRecording({ owner = "ui", injectAfterUpload = false } = {}) {
  if (!isTauri) {
    log("Recording unavailable outside Tauri runtime.");
    return;
  }
  const starting = capture.start(owner, { injectAfterUpload: Boolean(injectAfterUpload) });
  if (capture.phase() !== "starting") {
    log("A capture is already running; this start was ignored.", { owner });
    return;
  }
  clearVoiceHudHideTimer();
  void updateVoiceHud({
    phase: "preparing",
    instruction: "Preparing microphone · Esc to cancel",
  });
  const result = await starting;
  if (!result.ok) {
    if (result.reason === "busy") {
      log("A capture is already running; this start was ignored.", { owner });
      return;
    }
    if (result.reason === "released" || result.reason === "cancelled") {
      setRecordingState("Cancelled", "neutral");
      void updateVoiceHud({ phase: "hidden" }, { silent: true });
      log("Capture ended before the microphone was ready; nothing was kept.", { owner, reason: result.reason });
      return;
    }
    handlePermissionState("denied");
    setRecordingState("Mic Error", "warn");
    showVoiceHudError("Microphone access failed · check Vox Agent Settings");
    log("Failed to start recording.", { owner });
    return;
  }
  handlePermissionState("granted");
  markRecordingStarted(result.mode === "native" ? "native" : "webview-fallback", {
    owner,
    device: result.status?.deviceName,
    sampleRate: result.status?.sampleRate,
    channels: result.status?.channels,
  });
  await setCaptureAbortHotkey(true);
}

/** Leave the listening state: timers, level polling, cancel shortcut. */
async function leaveListening(instruction) {
  if (captureStatusPollTimer) {
    clearInterval(captureStatusPollTimer);
    captureStatusPollTimer = null;
  }
  stopVoiceHudLevelPolling();
  void updateVoiceHud({
    phase: "processing",
    instruction,
    elapsedMs: Date.now() - recordingStartedAt,
  });
  await setCaptureAbortHotkey(false);
}

/**
 * Stop the capture if it belongs to `owner` ("any" is the explicit Stop
 * control). The controller claims the exact session at the call, before any
 * await; a release while the microphone is still starting is remembered.
 */
async function stopRecording({ owner = "ui" } = {}) {
  const wasRunning = capture.phase() === "running";
  const stopping = capture.stop(owner);
  if (!wasRunning || capture.phase() !== "finishing") {
    await stopping;
    return;
  }
  isRecording = false;
  try {
    await leaveListening("Transcribing and routing…");
    const audio = await stopping;
    if (!audio || audio.cancelled) {
      return;
    }
    const injectAfterUpload = Boolean(audio.options?.injectAfterUpload);
    if (audio.mode === "native") {
      await routeCapturedAudio({
        audioBase64: audio.audioBase64,
        mimeType: audio.mimeType,
        source: "desktop-tauri-native",
        injectAfterUpload,
      });
    } else {
      await uploadRecordedChunks({ ...audio, injectAfterUpload });
    }
  } catch (error) {
    setRecordingState("Capture Failed", "warn");
    showVoiceHudError("Vox Agent could not finalize the captured audio");
    log("Failed to finalize recording.", { error: String(error) });
  }
}

/**
 * Esc: discard the capture. While starting, the session is cancelled as soon
 * as it exists; while finishing, its audio is never used; while running, the
 * audio is dropped in Rust.
 */
async function cancelRecording() {
  const phase = capture.phase();
  const cancelling = capture.cancel();
  if (phase === "idle") {
    await cancelling;
    return;
  }
  isRecording = false;
  if (phase === "running") {
    await leaveListening("Cancelling voice capture…");
  }
  try {
    await cancelling;
  } catch (error) {
    log("Capture cancel failed.", { error: String(error) });
  }
  setRecordingState("Cancelled", "neutral");
  setTranscriptState("Cancelled", "neutral");
  void updateVoiceHud({ phase: "hidden" }, { silent: true });
  log("Discarded capture.", { phase });
}

/** The native session ended on its own (e.g. the microphone was unplugged). */
function handleCaptureEnded(reason) {
  if (captureStatusPollTimer) {
    clearInterval(captureStatusPollTimer);
    captureStatusPollTimer = null;
  }
  stopVoiceHudLevelPolling();
  void setCaptureAbortHotkey(false);
  isRecording = false;
  setRecordingState("Capture Failed", "warn");
  setTranscriptState("Discarded", "neutral");
  showVoiceHudError(
    reason === "device-lost"
      ? "Microphone disconnected · the capture was discarded"
      : "Voice capture ended · nothing was sent",
  );
  log("Capture ended.", { reason });
}

async function confirmPolicyPreview() {
  if (!pendingUploadPayload) {
    return;
  }
  try {
    setBadge(elements.policyPreviewStatus, "Sending", "warn");
    await sendVoicePayload(pendingUploadPayload);
    setBadge(elements.policyPreviewStatus, "Sent", "live");
  } catch (error) {
    setBadge(elements.policyPreviewStatus, "Failed", "warn");
    setRecordingState("Upload Failed", "warn");
    showVoiceHudError("Vox Agent could not process the approved voice command");
    log("Failed to send preview payload.", { error: String(error) });
  }
}

function cancelPolicyPreview() {
  hidePolicyPreview();
  clearVoiceHudHideTimer();
  void updateVoiceHud({ phase: "hidden" }, { silent: true });
  setRecordingState("Cancelled", "neutral");
  setTranscriptState("Preview Cancelled", "warn");
  log("Cancelled preview-gated output.");
}

function shouldIgnoreSpaceHotkey(event) {
  const tagName = event.target?.tagName;
  return tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT";
}

function resetSession() {
  const nextSessionId = createSessionId();
  elements.sessionId.value = nextSessionId;
  persistPreferences();
  log("Created new session.", { session_id: nextSessionId });
}

for (const item of elements.workspaceNav) {
  item.addEventListener("click", () => {
    const viewName = item.dataset.viewTarget;
    showWorkspaceView(viewName);
    if (viewName === "vocabulary") {
      refreshDictionary({ silent: true });
    }
    if (viewName === "modes") {
      refreshNamedModes({ silent: true });
    }
    if (viewName === "settings") {
      refreshSettingsControlPlane();
    }
  });
}

window.addEventListener("keydown", (event) => {
  if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) {
    return;
  }
  const viewName = {
    Digit1: "command",
    Digit2: "sessions",
    Digit3: "history",
    Digit4: "settings",
    Digit5: "vocabulary",
    Digit6: "modes",
  }[event.code];
  if (!viewName) {
    return;
  }
  event.preventDefault();
  showWorkspaceView(viewName);
  if (viewName === "vocabulary") {
    refreshDictionary({ silent: true });
  }
  if (viewName === "modes") {
    refreshNamedModes({ silent: true });
  }
  if (viewName === "settings") {
    refreshSettingsControlPlane();
  }
});

window.addEventListener("keydown", (event) => {
  if (event.code === "Escape" && capture.phase() !== "idle") {
    event.preventDefault();
    cancelRecording();
    return;
  }
  if (event.code !== "Space" || event.repeat || shouldIgnoreSpaceHotkey(event)) {
    return;
  }
  event.preventDefault();
  startRecording({ owner: "ui" });
});

window.addEventListener("keyup", (event) => {
  if (event.code !== "Space" || shouldIgnoreSpaceHotkey(event)) {
    return;
  }
  event.preventDefault();
  stopRecording({ owner: "ui" });
});

elements.saveBackend.addEventListener("click", saveBackendUrl);
elements.saveSettings.addEventListener("click", () => saveAppSettings({ completeOnboarding: false }));
elements.testSettings.addEventListener("click", () => testProviderSettings());
elements.refreshProviderQualification.addEventListener("click", () => refreshProviderQualifications());
elements.applyModelPolicy.addEventListener("click", applyModelPolicy);
elements.refreshModelPolicy.addEventListener("click", () => refreshModelPolicy());
elements.refreshEngineDiagnostics.addEventListener("click", () => refreshEngineDiagnostics());
elements.startSettingsEngine.addEventListener("click", startSettingsEngine);
elements.stopSettingsEngine.addEventListener("click", stopSettingsEngine);
elements.runObsidianSync.addEventListener("click", runObsidianSync);
elements.runNotionSync.addEventListener("click", runNotionSync);
elements.runGitSync.addEventListener("click", runGitSync);
elements.importLegacyVoxlog.addEventListener("click", importLegacyVoxlog);
elements.exportMigrationBundle.addEventListener("click", exportMigrationBundle);
elements.importMigrationBundle.addEventListener("click", importMigrationBundle);
elements.openMigrationDirectory.addEventListener("click", openMigrationDirectory);
elements.rebuildDigest.addEventListener("click", rebuildDigest);
elements.exportObsidian.addEventListener("click", exportDigestToObsidian);
elements.exportAiMateMemory.addEventListener("click", exportDigestToAiMateMemory);
elements.completeOnboarding.addEventListener("click", completeOnboarding);
elements.saveHotkey.addEventListener("click", saveHotkeyConfig);
elements.refreshPermissions.addEventListener("click", refreshMicrophonePermission);
elements.refreshRecent.addEventListener("click", refreshRecent);
elements.refreshHealth.addEventListener("click", () => refreshBackendHealth());
elements.refreshDigest.addEventListener("click", () => refreshSessionDigest());
elements.refreshHistory.addEventListener("click", () => refreshHistory());
elements.exportHistoryMarkdown.addEventListener("click", () => exportHistory("markdown"));
elements.exportHistoryJson.addEventListener("click", () => exportHistory("json"));
elements.openHistoryExports.addEventListener("click", openHistoryExportDirectory);
elements.dismissRecent.addEventListener("click", dismissRecent);
elements.undoOutput.addEventListener("click", undoOutput);
elements.retryRecent.addEventListener("click", retryRecent);
elements.applyRecentMode.addEventListener("click", applyRecentMode);
elements.addDictionary.addEventListener("click", addRecentDictionary);
elements.dictionaryForm.addEventListener("submit", saveDictionaryTerm);
elements.refreshDictionary.addEventListener("click", () => refreshDictionary());
elements.namedModeForm.addEventListener("submit", createNamedMode);
elements.refreshNamedModes.addEventListener("click", () => refreshNamedModes());
elements.startRecording.addEventListener("click", () => startRecording({ owner: "ui" }));
elements.stopRecording.addEventListener("click", () => stopRecording({ owner: "any" }));
elements.newSession.addEventListener("click", resetSession);
elements.activeProfile.addEventListener("change", () => {
  renderProfileSummary(currentSettingsSnapshot);
});
elements.backendUrl.addEventListener("change", persistPreferences);
elements.hotkeyAccelerator.addEventListener("change", persistPreferences);
elements.hotkeyBehavior.addEventListener("change", persistPreferences);
elements.hotkeyEnabled.addEventListener("change", persistPreferences);
elements.targetApp.addEventListener("change", persistPreferences);
elements.targetApp.addEventListener("change", () => {
  if (!elements.digestProjectKey.value.trim()) {
    elements.digestProjectKey.value = elements.targetApp.value.trim().toLowerCase();
  }
});
elements.targetApp.addEventListener("change", refreshOutputPolicyPreview);
elements.targetRisk.addEventListener("change", () => {
  persistPreferences();
  refreshOutputPolicyPreview();
});
elements.askAgent.addEventListener("click", askAgent);
// Ctrl/⌘+Enter in the composer is handled by the Context UI (IME-safe routing).
elements.neverArchiveApps.addEventListener("change", refreshOutputPolicyPreview);
elements.fastPathOnlyApps.addEventListener("change", refreshOutputPolicyPreview);
elements.disableDirectTypingApps.addEventListener("change", refreshOutputPolicyPreview);
elements.policyPreviewSend.addEventListener("click", confirmPolicyPreview);
elements.policyPreviewCancel.addEventListener("click", cancelPolicyPreview);
elements.sessionId.addEventListener("change", persistPreferences);
elements.sessionId.addEventListener("change", () => refreshSessionDigest({ silent: true }));
elements.digestType.addEventListener("change", () => refreshSessionDigest({ silent: true }));
elements.digestDate.addEventListener("change", () => refreshSessionDigest({ silent: true }));
elements.digestProjectKey.addEventListener("change", () => refreshSessionDigest({ silent: true }));
elements.recordMode.addEventListener("change", persistPreferences);
elements.retryProvider.addEventListener("change", persistPreferences);
elements.historyQuery.addEventListener("change", () => {
  persistPreferences();
  refreshHistory();
});
elements.historyQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    persistPreferences();
    refreshHistory();
  }
});
elements.historyAgent.addEventListener("change", () => {
  persistPreferences();
  refreshHistory();
});
elements.historySession.addEventListener("change", () => {
  persistPreferences();
  refreshHistory();
});
elements.historyCurrentOnly.addEventListener("change", () => {
  if (elements.historyCurrentOnly.checked) {
    elements.historySession.value = elements.sessionId.value.trim();
  }
  persistPreferences();
  refreshHistory();
});
elements.useCurrentSession.addEventListener("click", () => {
  elements.historySession.value = elements.sessionId.value.trim();
  elements.historyCurrentOnly.checked = false;
  persistPreferences();
  refreshHistory();
});
window.addEventListener("beforeunload", () => {
  if (globalHotkeyUnlisten) {
    Promise.resolve(globalHotkeyUnlisten()).catch(() => {});
  }
  if (healthPollTimer) {
    clearInterval(healthPollTimer);
  }
  if (recentPollTimer) {
    clearInterval(recentPollTimer);
  }
  if (historyPollTimer) {
    clearInterval(historyPollTimer);
  }
  if (enginePollTimer) {
    clearInterval(enginePollTimer);
  }
  if (engineRefreshTimer) {
    clearTimeout(engineRefreshTimer);
  }
  if (engineEventUnlisten) {
    Promise.resolve(engineEventUnlisten()).catch(() => {});
  }
  if (engineStreamUnlisten) {
    Promise.resolve(engineStreamUnlisten()).catch(() => {});
  }
  if (isTauri) {
    invoke("stop_engine_event_stream").catch(() => {});
  }
  if (captureStatusPollTimer) {
    clearInterval(captureStatusPollTimer);
  }
});

bootstrap();
