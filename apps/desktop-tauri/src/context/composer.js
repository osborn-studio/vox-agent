// Context composer transaction (T13).
//
// * "Saved on this device" is reported only after the Rust journal commit
//   returns; until then the state is "saving".
// * The limit is counted in UTF-8 bytes (owner decision D4), matching the
//   Rust payload check.
// * Enter while an IME composition is active never submits.
// * Drafts live in memory per target. They are not written to WebView storage:
//   that would put plaintext on disk outside the encrypted journal.
// * A retry of the same unchanged draft reuses its eventId; a late reply only
//   clears the draft if the user has not changed it since submitting.
// * Project attribution (T15) is frozen when a draft starts, including "no
//   project", and stays with that draft: catalog refreshes or revoked access
//   never change it. It can only be changed while the draft is empty.
import { routeSubmission, targetKey } from "./targets.js";

export const MAX_TEXT_BYTES = 8192;

const encoder = new TextEncoder();

export function utf8Bytes(text) {
  return encoder.encode(text).length;
}

export function isComposingEvent(event) {
  return Boolean(event.isComposing) || event.keyCode === 229 || event.key === "Process";
}

/** Ctrl/⌘ + Enter submits; plain Enter inserts a newline. */
export function isSubmitShortcut(event) {
  return (
    event.key === "Enter" &&
    Boolean(event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.shiftKey &&
    !isComposingEvent(event)
  );
}

export function validateText(text) {
  const bytes = utf8Bytes(text);
  if (!text.trim()) return { ok: false, reason: "empty", bytes };
  if (bytes > MAX_TEXT_BYTES) return { ok: false, reason: "too-large", bytes };
  return { ok: true, bytes };
}

// Errors from `context_submit_record` mean nothing was saved, so retrying the
// same text may reuse the id, except where the id itself is the problem.
const NEW_ID_AFTER = new Set(["conflict", "tombstoned", "invalid-input"]);

export function errorCode(error) {
  return typeof error === "string" && /^[a-z-]{1,40}$/.test(error) ? error : "internal";
}

export function newEventId(random = globalThis.crypto) {
  return `ctx-${random.randomUUID()}`;
}

function validProject(project) {
  return (
    project === null ||
    (project &&
      typeof project.id === "string" &&
      typeof project.repository === "string" &&
      project.id.length > 0 &&
      project.repository.length > 0)
  );
}

export function createComposer({ invoke, makeEventId = newEventId }) {
  const drafts = new Map();
  // Per target: a revision bumped on every draft change, so a late reply can
  // tell "unchanged since I submitted" from "cleared and retyped the same".
  const revisions = new Map();
  let generation = 0;
  // Per target: the selected project and whether the current draft froze it.
  const attribution = new Map();
  const stateFor = (key) => {
    if (!attribution.has(key)) attribution.set(key, { project: null, frozen: false });
    return attribution.get(key);
  };
  const writeDraft = (key, text) => {
    if (drafts.get(key) !== text) revisions.set(key, (revisions.get(key) ?? 0) + 1);
    drafts.set(key, text);
    const state = stateFor(key);
    if (text.trim() && !state.frozen) state.frozen = true;
    if (!text.trim()) state.frozen = false;
  };
  let retry = null; // { key, text, eventId }
  let epoch = 0;
  let inFlight = false;

  return {
    draft(target) {
      return drafts.get(targetKey(target)) ?? "";
    },
    setDraft(target, text) {
      writeDraft(targetKey(target), text);
    },
    /** The project this target's draft is (or will be) attributed to. */
    project(target) {
      return stateFor(targetKey(target)).project;
    },
    isFrozen(target) {
      return stateFor(targetKey(target)).frozen;
    },
    /** Change the project; refused while a draft has frozen the attribution. */
    selectProject(target, project) {
      const state = stateFor(targetKey(target));
      if (state.frozen || !validProject(project)) return false;
      state.project = project ? { id: project.id, repository: project.repository } : null;
      return true;
    },
    /**
     * Drop an unfrozen selection that the latest catalog no longer contains.
     * A frozen draft keeps its project (shown as the original project).
     */
    revalidateProject(target, allowedIds) {
      const state = stateFor(targetKey(target));
      if (!state.frozen && state.project && !allowedIds.includes(state.project.id)) {
        state.project = null;
      }
    },
    /** Clear every draft and attribution, e.g. on sign-out or identity change. */
    clearAll() {
      drafts.clear();
      attribution.clear();
      retry = null;
      generation += 1;
    },
    async submitContext(target, text, { destination } = {}) {
      const key = targetKey(target);
      const valid = validateText(text);
      if (!valid.ok) return { status: valid.reason, bytes: valid.bytes };
      // Submitting starts the draft too: freeze the attribution if not yet.
      writeDraft(key, drafts.get(key) || text);
      const project = stateFor(key).project;
      const projectKey = project ? `${project.id}|${project.repository}` : "";
      const eventId =
        retry && retry.key === key && retry.text === text && retry.projectKey === projectKey
          ? retry.eventId
          : makeEventId();
      const route = routeSubmission(target, { text, eventId, destination, project });
      if (route.error) return { status: route.error };
      if (route.command !== "context_submit_record") return { status: "unknown-target" };
      // One save at a time: a repeated shortcut must not create a second
      // record with a new id while the first is still being committed.
      if (inFlight) return { status: "in-flight" };
      inFlight = true;
      const mine = ++epoch;
      const submittedRevision = revisions.get(key) ?? 0;
      const submittedGeneration = generation;
      let record;
      try {
        record = await invoke(route.command, route.args);
      } catch (error) {
        const code = errorCode(error);
        retry = NEW_ID_AFTER.has(code) ? null : { key, text, eventId, projectKey };
        return { status: "failed", code, eventId };
      } finally {
        inFlight = false;
      }
      retry = null;
      const unchanged =
        submittedGeneration === generation &&
        (revisions.get(key) ?? 0) === submittedRevision &&
        (drafts.get(key) ?? "") === text;
      if (unchanged) writeDraft(key, "");
      return { status: "saved-locally", record, eventId, cleared: unchanged, stale: mine !== epoch };
    },
  };
}
