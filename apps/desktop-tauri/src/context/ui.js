// Page wiring for the Context flow (T11–T14). Logic lives in the sibling
// modules; this file only connects them to elements and IPC.
import { ENDS_ACCOUNT, accountError, accountView, renderConversations, signOutMessage } from "./account.js";
import { createComposer, isSubmitShortcut, utf8Bytes, MAX_TEXT_BYTES } from "./composer.js";
import { createPaneController, paneForShortcut } from "./layout.js";
import { readLanguage, t, translateStatic, writeLanguage } from "./i18n.js";
import { createPrivatePanel } from "./private-panel.js";
import { createWeeklyPanel } from "./weekly-panel.js";
import { composerMessage } from "./status-copy.js";
import { COORDINATOR_TARGET, GROUP_TARGET, agentTarget, sameTarget } from "./targets.js";
import {
  announcement,
  beginReveal,
  hideAllOriginals,
  revealCurrent,
  renderRecords,
  showOriginal,
  showOriginalError,
  upsertRecord,
} from "./timeline.js";

export const RECORD_EVENT = "vox-agent://context-record";

const byId = (doc, id) => doc.getElementById(id);
const EVENT_ID = /^[A-Za-z0-9_-]{1,128}$/;

export function initContextUi({ document: doc, invoke, listen, storage, onAgentSubmit }) {
  const el = {
    root: doc.querySelector(".cockpit"),
    sidebar: doc.querySelector(".sidebar"),
    inspector: doc.querySelector(".inspector"),
    toggleSidebar: byId(doc, "toggle-sidebar"),
    toggleInspector: byId(doc, "toggle-inspector"),
    groupButton: byId(doc, "target-group"),
    coordinatorButton: byId(doc, "target-coordinator"),
    agentsToggle: byId(doc, "toggle-agents"),
    agentsList: byId(doc, "agent-targets"),
    timelineTitle: byId(doc, "timeline-title"),
    timelineScope: byId(doc, "timeline-scope"),
    contextFlow: byId(doc, "context-flow"),
    agentFlow: byId(doc, "agent-flow"),
    records: byId(doc, "context-records"),
    recordsEmpty: byId(doc, "context-empty"),
    loadOlder: byId(doc, "context-load-older"),
    live: byId(doc, "context-live"),
    inspectorTitle: byId(doc, "inspector-title"),
    contextPanel: byId(doc, "context-panel"),
    operationsPanel: byId(doc, "operations-panel"),
    sessionStatus: byId(doc, "context-session-status"),
    destination: byId(doc, "context-destination"),
    quota: byId(doc, "context-quota"),
    project: byId(doc, "context-project"),
    refreshProjects: byId(doc, "context-refresh-projects"),
    inspectRepository: byId(doc, "context-inspect-repository"),
    projectNote: byId(doc, "context-project-note"),
    repositoryNote: byId(doc, "context-repository-note"),
    composer: byId(doc, "agent-query"),
    composerLabel: byId(doc, "composer-label"),
    composerStatus: byId(doc, "composer-status"),
    composerBytes: byId(doc, "composer-bytes"),
    recordButton: byId(doc, "record-context"),
    askButton: byId(doc, "ask-agent"),
    language: byId(doc, "context-language"),
    diagnostics: byId(doc, "context-diagnostics"),
    diagnosticsStatus: byId(doc, "context-diagnostics-status"),
    refreshDiagnostics: byId(doc, "context-refresh-diagnostics"),
    accountLine: byId(doc, "context-account-line"),
    signIn: byId(doc, "context-sign-in"),
    signOut: byId(doc, "context-sign-out"),
    accountNote: byId(doc, "context-account-note"),
    conversations: byId(doc, "context-conversations"),
    refreshConversations: byId(doc, "context-refresh-conversations"),
    conversationsNote: byId(doc, "context-conversations-note"),
  };
  let lang = readLanguage(storage);

  // T23a: revealed originals are team-side plaintext. They are cleared
  // whenever the user steps away (pane collapse, window blur or hidden, e.g.
  // lock or minimise, conversation switch, sign-out). Team drafts and stored
  // records are never touched by this; drafts clear only on sign-out.
  function hideRevealed() {
    hideAllOriginals(el.records);
  }

  // T23b: private notes and the weekly draft are cleared, in the page and in
  // Rust, when the panel collapses, the window is hidden or locked, or the
  // account ends (product design 3.2: not on a mere window switch). Saved
  // private mutations stay.
  let privatePanel = null;
  let weeklyPanel = null;
  function clearPrivateSide() {
    privatePanel?.clear();
    weeklyPanel?.clear();
  }

  const panes = createPaneController({
    root: el.root,
    panes: {
      sidebar: { panel: el.sidebar, toggle: el.toggleSidebar },
      inspector: { panel: el.inspector, toggle: el.toggleInspector },
    },
    storage,
    onChange: (name, collapsed) => {
      if (collapsed) hideRevealed();
      if (collapsed && name === "inspector") clearPrivateSide();
    },
  });
  const composer = createComposer({ invoke });
  privatePanel = createPrivatePanel({
    doc,
    invoke,
    lang: () => lang,
    el: {
      text: byId(doc, "private-text"),
      tags: [...(doc.querySelectorAll?.("[data-private-tag]") ?? [])].map((box) => {
        box.dataset.tag = box.getAttribute("data-private-tag");
        return box;
      }),
      save: byId(doc, "private-save"),
      refresh: byId(doc, "private-refresh"),
      status: byId(doc, "private-status"),
      list: byId(doc, "private-list"),
      empty: byId(doc, "private-empty"),
      omitted: byId(doc, "private-omitted"),
      unavailable: byId(doc, "private-unavailable"),
    },
  });
  weeklyPanel = createWeeklyPanel({
    doc,
    invoke,
    lang: () => lang,
    privatePicks: () => privatePanel.picks(),
    el: {
      week: byId(doc, "weekly-week"),
      load: byId(doc, "weekly-load"),
      coverage: byId(doc, "weekly-coverage"),
      list: byId(doc, "weekly-sources"),
      compose: byId(doc, "weekly-compose"),
      draft: byId(doc, "weekly-draft"),
      draftField: byId(doc, "weekly-draft-field"),
      copy: byId(doc, "weekly-copy"),
      status: byId(doc, "weekly-status"),
    },
  });
  let target = GROUP_TARGET;
  let records = [];
  let summary = { configured: false, signedIn: false, destinations: [] };
  let submitting = false;
  // Bumped by clear() (sign-out, identity change): replies to requests made
  // before it are dropped.
  let viewEpoch = 0;
  let nextCursor = null;
  let projects = [];
  // Last rendered inputs, so a language switch redraws without reloading.
  let quota = null;
  let composerResult = null;
  let repositoryNote = null;
  let diagnostics = null;
  // Notes are kept as functions of the language so a switch redraws them.
  let accountNote = null;
  let conversationsNote = null;
  let conversations = [];
  let conversationTicket = 0;
  let signingIn = false;
  let catalogTicket = 0;
  let inspectTicket = 0;
  // Per record, only the most recently issued status request may apply, so
  // an older reply can never overwrite a newer state.
  const latest = new Map();
  let requestCounter = 0;
  const issue = (eventId) => {
    const ticket = ++requestCounter;
    latest.set(eventId, ticket);
    return ticket;
  };
  const current = (eventId, ticket) => latest.get(eventId) === ticket;

  /** Composer status from a result DTO (or null to clear), kept for redraws. */
  function setComposerStatus(result) {
    composerResult = result;
    renderComposerStatus();
  }

  function renderComposerStatus() {
    el.composerStatus.textContent = composerResult
      ? composerMessage(composerResult, { maxBytes: MAX_TEXT_BYTES, lang })
      : "";
  }

  function setRepositoryNote(key, vars = {}) {
    repositoryNote = key ? { key, vars } : null;
    el.repositoryNote.textContent = repositoryNote ? t(key, lang, vars) : "";
  }

  function say(text) {
    if (text) el.live.textContent = text;
  }

  function showRecords() {
    renderRecords(doc, el.records, records, { onCheck: checkRecord, onView: viewOriginal, lang });
    el.recordsEmpty.hidden = records.length > 0;
    el.loadOlder.hidden = !nextCursor;
  }

  const ORIGINAL_ERRORS = {
    "integrity-failed": "original.integrity",
    "verify-only": "original.verifyOnly",
    "not-found": "original.notFound",
  };

  /** Whether the record is still shown and may still offer its original. */
  const originalAllowed = (eventId, epoch) =>
    epoch === viewEpoch && records.some((r) => r.eventId === eventId && r.action !== "quarantine");

  /**
   * Copy re-reads and re-verifies the original instead of trusting what is on
   * screen, and refuses once this revealed box is gone. Defined outside the
   * reveal so the handler never closes over the shown text.
   */
  function copier(eventId, epoch, item, shown) {
    const stillShown = () =>
      item.parts.original === shown.box && el.records.contains(item) && originalAllowed(eventId, epoch);
    return async () => {
      if (!stillShown()) return false;
      try {
        const fresh = await invoke("context_read_original", { eventId });
        if (!stillShown()) return false;
        await doc.defaultView.navigator.clipboard.writeText(fresh.text);
        return true;
      } catch {
        return false;
      }
    };
  }

  /**
   * Explicit user action: fetch the verified original and show it in place.
   * A reply is dropped once the card was hidden (any lifecycle trigger or
   * its own Hide), re-rendered away, or the record is no longer offered.
   */
  async function viewOriginal(eventId, item) {
    const epoch = viewEpoch;
    const reveal = beginReveal(item);
    const stillWanted = () => originalAllowed(eventId, epoch) && revealCurrent(el.records, reveal);
    try {
      const original = await invoke("context_read_original", { eventId });
      if (!stillWanted()) return;
      const shown = { box: null };
      shown.box = showOriginal(doc, item, original.text, { lang, onCopy: copier(eventId, epoch, item, shown) });
    } catch (error) {
      if (stillWanted()) {
        showOriginalError(doc, item, t(ORIGINAL_ERRORS[error] ?? "original.failed", lang));
      }
    }
  }

  async function loadPage(cursor) {
    const epoch = viewEpoch;
    const page = await invoke("context_page_records", { cursor, limit: 20 });
    if (epoch !== viewEpoch) return;
    for (const dto of page.records) {
      // Older pages never overwrite records already shown.
      if (!records.some((r) => r.eventId === dto.eventId)) records = [...records, dto];
    }
    // Reloading the first page keeps a cursor that already reaches further.
    if (cursor !== null || nextCursor === null) nextCursor = page.nextCursor ?? null;
    showRecords();
  }

  function applyRecord(dto) {
    const previous = records.find((r) => r.eventId === dto.eventId) ?? null;
    records = upsertRecord(records, dto);
    if (records.includes(dto)) say(announcement(previous, dto, lang));
    showRecords();
  }

  /** Event payloads are only hints: the status itself always comes from Rust. */
  async function refreshRecord(eventId) {
    if (typeof eventId !== "string" || !EVENT_ID.test(eventId)) return;
    const ticket = issue(eventId);
    const epoch = viewEpoch;
    try {
      const dto = await invoke("context_record_status", { eventId });
      if (epoch === viewEpoch && current(eventId, ticket)) applyRecord(dto);
    } catch {
      // Keep the last known state; the next event or check retries.
    }
  }

  async function checkRecord(eventId, button) {
    button.disabled = true;
    const ticket = issue(eventId);
    const epoch = viewEpoch;
    try {
      const dto = await invoke("context_check_record", { eventId });
      if (epoch === viewEpoch && current(eventId, ticket)) applyRecord(dto);
    } catch (error) {
      setComposerStatus({ status: "failed", code: String(error) });
    } finally {
      button.disabled = false;
    }
  }

  function selectTarget(next) {
    if (sameTarget(next, target)) return;
    hideRevealed();
    composer.setDraft(target, el.composer.value);
    target = next;
    const group = target.kind === "group";
    el.composer.value = composer.draft(target);
    el.groupButton.setAttribute("aria-pressed", String(group));
    el.coordinatorButton.setAttribute("aria-pressed", String(!group && target.id === null));
    for (const button of el.agentsList.querySelectorAll("[data-agent-id]")) {
      button.setAttribute("aria-pressed", String(!group && button.dataset.agentId === target.id));
    }
    el.contextFlow.hidden = !group;
    el.agentFlow.hidden = group;
    el.contextPanel.hidden = !group;
    el.operationsPanel.hidden = group;
    el.recordButton.hidden = !group;
    el.askButton.hidden = group;
    setComposerStatus(null);
    applyLabels();
  }

  /** Target- and language-dependent labels, applied in place. */
  function applyLabels() {
    const group = target.kind === "group";
    el.timelineTitle.textContent = group ? GROUP_TARGET.label : target.label;
    el.timelineScope.textContent = t(group ? "scope.group" : "scope.session", lang);
    el.inspectorTitle.textContent = t(group ? "panel.context" : "panel.operations", lang);
    el.composerLabel.textContent = group
      ? t("composer.labelGroup", lang)
      : t("composer.labelAgent", lang, { name: target.label });
    el.composer.placeholder = t(group ? "composer.placeholderGroup" : "composer.placeholderAgent", lang);
    updateBytes();
  }

  function option(value, label) {
    const node = doc.createElement("option");
    node.value = value;
    node.textContent = label;
    return node;
  }

  /** The project menu always reflects the draft's frozen attribution. */
  function renderProjects() {
    const frozen = composer.isFrozen(GROUP_TARGET);
    const current = composer.project(GROUP_TARGET);
    const options = [option("", t("project.none", lang))];
    for (const project of projects) options.push(option(project.id, `${project.name} · ${project.repository}`));
    // Only a frozen draft can keep a project the catalog no longer lists.
    if (frozen && current && !projects.some((p) => p.id === current.id)) {
      options.push(option(current.id, t("project.original", lang, { repository: current.repository })));
    }
    el.project.replaceChildren(...options);
    el.project.value = current?.id ?? "";
    el.project.disabled = frozen || !summary.configured;
    el.inspectRepository.disabled = frozen || !summary.configured;
    el.projectNote.textContent = frozen
      ? t("project.fixed", lang, { repository: current ? current.repository : t("project.unspecified", lang) })
      : t("project.help", lang);
  }

  async function refreshProjects() {
    const ticket = ++catalogTicket;
    const epoch = viewEpoch;
    if (!summary.configured || !summary.signedIn) {
      projects = [];
      composer.revalidateProject(GROUP_TARGET, []);
      renderProjects();
      return;
    }
    try {
      const catalog = await invoke("context_list_projects");
      if (ticket !== catalogTicket || epoch !== viewEpoch) return;
      projects = catalog.projects;
      composer.revalidateProject(
        GROUP_TARGET,
        projects.map((p) => p.id),
      );
      setRepositoryNote(catalog.omitted ? "project.omitted" : null, { count: catalog.omitted });
    } catch {
      if (ticket !== catalogTicket || epoch !== viewEpoch) return;
      // Without a fresh catalog no new draft may use a possibly revoked project.
      projects = [];
      composer.revalidateProject(GROUP_TARGET, []);
      setRepositoryNote("project.loadFailed");
    }
    renderProjects();
  }

  async function inspectRepository() {
    if (composer.isFrozen(GROUP_TARGET)) return;
    const ticket = ++inspectTicket;
    el.inspectRepository.disabled = true;
    try {
      const result = await invoke("context_inspect_repository");
      if (ticket !== inspectTicket || result.cancelled) return;
      const matches = projects.filter((p) => result.repositories.includes(p.repository));
      if (matches.length) {
        setRepositoryNote("repository.matches", { list: matches.map((p) => `${p.name} · ${p.repository}`).join("; ") });
      } else {
        setRepositoryNote("repository.none");
      }
    } catch {
      if (ticket === inspectTicket) {
        setRepositoryNote("repository.failed");
      }
    } finally {
      renderProjects();
    }
  }

  function updateBytes() {
    const bytes = utf8Bytes(el.composer.value);
    el.composerBytes.textContent =
      target.kind === "group" ? t("composer.bytes", lang, { bytes, max: MAX_TEXT_BYTES }) : "";
    el.composerBytes.dataset.over = String(bytes > MAX_TEXT_BYTES);
  }

  async function submit() {
    // One submission at a time, for every entry point (button or shortcut).
    if (submitting) return;
    submitting = true;
    try {
      if (target.kind === "group") await submitContext();
      else await onAgentSubmit(target);
    } finally {
      submitting = false;
    }
  }

  async function submitContext() {
    const text = el.composer.value;
    composer.setDraft(target, text);
    el.recordButton.disabled = true;
    setComposerStatus({ status: "saving" });
    const submittedTo = target;
    const epoch = viewEpoch;
    const result = await composer.submitContext(submittedTo, text, { destination: el.destination.value });
    el.recordButton.disabled = false;
    renderProjects();
    if (epoch !== viewEpoch) return;
    if (result.record && EVENT_ID.test(result.record.eventId ?? "")) {
      // The reply is the state at commit time; anything newer wins.
      if (!latest.has(result.record.eventId)) applyRecord(result.record);
      refreshRecord(result.record.eventId);
    }
    // A late reply only updates the composer the user is still looking at.
    if (sameTarget(submittedTo, target)) {
      if (result.cleared && el.composer.value === text) el.composer.value = "";
      setComposerStatus(result);
      updateBytes();
    }
  }

  let summaryTicket = 0;

  /** Only the latest summary request, made in the current view, applies. */
  async function refreshSummary() {
    const ticket = ++summaryTicket;
    const epoch = viewEpoch;
    let next;
    try {
      next = await invoke("context_session_summary");
    } catch {
      next = { configured: false, signedIn: false, destinations: [] };
    }
    if (ticket !== summaryTicket || epoch !== viewEpoch) return;
    summary = next;
    renderSummary();
    refreshProjects();
    if (!summary.configured) return;
    try {
      await loadPage(null);
      quota = await invoke("context_quota");
    } catch {
      quota = null;
    }
    renderQuota();
  }

  /** Session line and destinations from the cached summary; keeps the choice. */
  function renderSummary() {
    el.sessionStatus.textContent = t(
      !summary.configured ? "session.notSetUp" : summary.signedIn ? "session.signedIn" : "session.signedOut",
      lang,
    );
    el.sessionStatus.dataset.tone = summary.configured && summary.signedIn ? "live" : "neutral";
    const chosen = el.destination.value;
    el.destination.replaceChildren(...summary.destinations.map((name) => option(name, name)));
    if (summary.destinations.includes(chosen)) el.destination.value = chosen;
    el.destination.disabled = summary.destinations.length === 0;
    renderAccount();
  }

  // ---------- T24: Feishu account and conversation discovery ----------

  function setAccountNote(note) {
    accountNote = note;
    el.accountNote.textContent = note ? note(lang) : "";
  }

  function setConversationsNote(note) {
    conversationsNote = note;
    el.conversationsNote.textContent = note ? note(lang) : "";
  }

  function renderAccount() {
    const view = accountView(summary, lang);
    el.accountLine.textContent = view.line;
    el.signIn.hidden = !view.signIn;
    el.signOut.hidden = !view.signOut;
    el.refreshConversations.disabled = !summary.signedIn;
  }

  function renderConversationList() {
    renderConversations(doc, el.conversations, conversations, lang);
  }

  /** Rust opens the browser and owns the result; the page passes nothing. */
  async function signIn() {
    if (signingIn) return;
    signingIn = true;
    el.signIn.disabled = true;
    const epoch = viewEpoch;
    setAccountNote((l) => t("account.signingIn", l));
    try {
      await invoke("context_sign_in");
      if (epoch !== viewEpoch) return;
      setAccountNote(null);
      await refreshSummary();
      refreshConversations();
    } catch (error) {
      if (epoch === viewEpoch) setAccountNote((l) => accountError(String(error), l));
    } finally {
      signingIn = false;
      el.signIn.disabled = false;
    }
  }

  /**
   * The view is cleared at the click, before Rust is asked: revealed
   * originals, drafts and pending replies never outlive the request, and the
   * server logout (network) cannot delay it.
   */
  async function signOut() {
    el.signOut.disabled = true;
    resetView();
    summary = { ...summary, signedIn: false, displayName: null, destinations: [] };
    renderSummary();
    const epoch = viewEpoch;
    try {
      const result = await invoke("context_sign_out");
      if (epoch === viewEpoch) setAccountNote((l) => signOutMessage(result, l));
    } catch (error) {
      if (epoch === viewEpoch) setAccountNote((l) => accountError(String(error), l));
    } finally {
      el.signOut.disabled = false;
      await refreshSummary();
    }
  }

  async function refreshConversations() {
    const ticket = ++conversationTicket;
    const epoch = viewEpoch;
    if (!summary.signedIn) {
      conversations = [];
      setConversationsNote(null);
      renderConversationList();
      return;
    }
    setConversationsNote((l) => t("conversations.loading", l));
    try {
      const found = await invoke("context_conversations");
      if (ticket !== conversationTicket || epoch !== viewEpoch) return;
      conversations = found.conversations;
      const omitted = found.omitted;
      setConversationsNote(
        conversations.length === 0
          ? (l) => t("conversations.empty", l)
          : omitted
            ? (l) => t("conversations.omitted", l, { count: omitted })
            : null,
      );
    } catch (error) {
      if (ticket !== conversationTicket || epoch !== viewEpoch) return;
      const code = String(error);
      conversations = [];
      if (ENDS_ACCOUNT.has(code)) {
        // Rust has already ended the account; clear this view to match.
        resetView();
        setConversationsNote(null);
        setAccountNote((l) => accountError(code, l));
        refreshSummary();
      } else {
        setConversationsNote((l) => accountError(code, l));
      }
    }
    renderConversationList();
  }

  function renderQuota() {
    if (!summary.configured) return;
    el.quota.textContent = quota
      ? t("quota.line", lang, { count: quota.activeRecords, max: quota.maxActiveRecords })
      : t("quota.unavailable", lang);
  }

  el.groupButton.addEventListener("click", () => selectTarget(GROUP_TARGET));
  el.coordinatorButton.addEventListener("click", () => selectTarget(COORDINATOR_TARGET));
  el.agentsToggle.addEventListener("click", () => {
    const expanded = el.agentsToggle.getAttribute("aria-expanded") === "true";
    el.agentsToggle.setAttribute("aria-expanded", String(!expanded));
    el.agentsList.hidden = expanded;
  });
  el.recordButton.addEventListener("click", submit);
  el.loadOlder.addEventListener("click", () => loadPage(nextCursor).catch(() => {}));
  el.composer.addEventListener("input", () => {
    composer.setDraft(target, el.composer.value);
    updateBytes();
    if (target.kind === "group") renderProjects();
  });
  el.project.addEventListener("change", () => {
    const chosen = projects.find((p) => p.id === el.project.value) ?? null;
    composer.selectProject(GROUP_TARGET, chosen);
    renderProjects(); // reverts the menu if the change was refused
  });
  el.refreshProjects.addEventListener("click", refreshProjects);
  el.inspectRepository.addEventListener("click", inspectRepository);
  el.composer.addEventListener("keydown", (event) => {
    if (isSubmitShortcut(event)) {
      event.preventDefault();
      submit();
    }
  });
  doc.defaultView?.addEventListener("keydown", (event) => {
    const pane = paneForShortcut(event);
    if (pane) {
      event.preventDefault();
      panes.toggle(pane);
    }
  });
  listen(RECORD_EVENT, (event) => refreshRecord(event?.payload?.eventId));
  doc.defaultView?.addEventListener("blur", hideRevealed);
  doc.addEventListener?.("visibilitychange", () => {
    if (doc.visibilityState === "hidden") {
      hideRevealed();
      clearPrivateSide();
    }
  });

  /** Diagnostics labels: never paths, tokens, text or server bodies. */
  async function refreshDiagnostics() {
    try {
      diagnostics = await invoke("context_diagnostics");
    } catch {
      diagnostics = null;
    }
    renderDiagnostics();
  }

  function renderDiagnostics() {
    if (!diagnostics) {
      el.diagnostics.replaceChildren();
      el.diagnosticsStatus.textContent = t("diagnostics.error", lang);
      return;
    }
    const d = diagnostics;
    const rows = [
      ["diagnostics.version", d.appVersion],
      ["diagnostics.platform", `${d.os} · ${d.arch}`],
      ["diagnostics.setup", t(d.configured ? "diagnostics.ready" : "diagnostics.notSetUp", lang)],
      ["diagnostics.signIn", t(d.signedIn ? "diagnostics.yes" : "diagnostics.no", lang)],
      ["diagnostics.keyStore", t(d.keyStore === "available" ? "diagnostics.ready" : "diagnostics.notAvailable", lang)],
      [
        "diagnostics.journal",
        t({ normal: "diagnostics.normal", "verify-only": "diagnostics.verifyOnly" }[d.journal] ?? "diagnostics.unavailable", lang),
      ],
      ["diagnostics.records", d.activeRecords ?? t("diagnostics.none", lang)],
    ];
    el.diagnostics.replaceChildren(
      ...rows.map(([key, value]) => {
        const row = doc.createElement("div");
        const term = doc.createElement("dt");
        term.textContent = t(key, lang);
        const detail = doc.createElement("dd");
        detail.textContent = String(value);
        row.append(term, detail);
        return row;
      }),
    );
    el.diagnosticsStatus.textContent = "";
  }

  /**
   * Redraw everything already on screen in the new language. Nothing is
   * reloaded: the destination choice, loaded pages and composer state stay.
   * Revealed originals are hidden, like any other change of view.
   */
  function setLanguage(next) {
    hideRevealed();
    lang = next === "zh" ? "zh" : "en";
    writeLanguage(storage, lang);
    el.language.value = lang;
    doc.documentElement?.setAttribute?.("lang", lang === "zh" ? "zh-CN" : "en");
    translateStatic(doc, lang);
    applyLabels();
    showRecords();
    renderProjects();
    renderSummary();
    renderQuota();
    renderComposerStatus();
    if (repositoryNote) setRepositoryNote(repositoryNote.key, repositoryNote.vars);
    if (diagnostics) renderDiagnostics();
    renderConversationList();
    setAccountNote(accountNote);
    setConversationsNote(conversationsNote);
    privatePanel.render();
    weeklyPanel.render();
  }

  el.language.addEventListener("change", () => setLanguage(el.language.value));
  el.signIn.addEventListener("click", signIn);
  el.signOut.addEventListener("click", signOut);
  el.refreshConversations.addEventListener("click", refreshConversations);
  el.refreshDiagnostics.addEventListener("click", refreshDiagnostics);

  const initial = target;
  target = COORDINATOR_TARGET; // force the first selectTarget to apply the group view
  selectTarget(initial);
  setLanguage(lang);
  refreshSummary().then(refreshConversations);
  refreshDiagnostics();

  /** Clear drafts and visible records (sign-out or identity change). */
  function resetView() {
    hideRevealed();
    clearPrivateSide();
    viewEpoch += 1;
    latest.clear();
    catalogTicket += 1;
    inspectTicket += 1;
    conversationTicket += 1;
    nextCursor = null;
    composer.clearAll();
    records = [];
    projects = [];
    conversations = [];
    quota = null;
    setComposerStatus(null);
    setRepositoryNote(null);
    setConversationsNote(null);
    el.composer.value = "";
    showRecords();
    renderProjects();
    renderConversationList();
  }

  return {
    currentTarget: () => target,
    /**
     * After an agent command succeeded: clear the draft it was sent from,
     * only if that draft is unchanged, and the field only if it still shows it.
     */
    finishAgentCommand(submittedTarget, text) {
      if (sameTarget(submittedTarget, target)) {
        if (el.composer.value === text) {
          el.composer.value = "";
          composer.setDraft(target, "");
          updateBytes();
        }
      } else if (composer.draft(submittedTarget) === text) {
        composer.setDraft(submittedTarget, "");
      }
    },
    selectAgent: (agent) => selectTarget(agentTarget(agent)),
    /** Clear drafts and visible records (sign-out or identity change). */
    clear: resetView,
  };
}
