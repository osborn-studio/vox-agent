// Feishu conversation draft and voice drafts (T27), the same rules as macOS
// FeishuConversationOutbox (beginVoiceDraft / finishVoiceDraft):
//
// - one voice draft at a time, only for a selected conversation that can
//   send text, only while an account is bound;
// - the destination is frozen when dictation starts: if the user switches
//   conversation (or the conversation is revoked) before the transcript
//   arrives, the text goes to "recovered drafts" for the original
//   conversation, never into another conversation's draft;
// - cancel, sign-out (bind(null), even followed by the same account) or an
//   account change rejects the late transcript; re-binding the identical
//   account (token renewal) keeps it;
// - an empty transcript changes nothing;
// - nothing is ever sent or persisted here: the transcript only fills the
//   draft, and the user decides whether to send it.

function sameIdentity(a, b) {
  if (!a || !b) return a === b;
  return a.origin === b.origin && a.appId === b.appId && a.tenantKey === b.tenantKey && a.userId === b.userId;
}

function sameDestination(a, b) {
  return Boolean(a && b && a.id === b.id && a.canSend === b.canSend);
}

export function createConversationDraftBox({ onVoiceDraftCancelled } = {}) {
  let identity = null;
  let destination = null;
  let draft = "";
  let recovered = [];
  let voiceId = null;
  let voiceDestination = null;
  let nextVoice = 0;
  let nextRecovery = 0;

  /** Cancel the voice draft; with `onlyId`, only if it is still that one. */
  function cancelVoiceDraft(onlyId = null) {
    if (onlyId !== null && onlyId !== voiceId) return;
    const id = voiceId;
    voiceId = null;
    voiceDestination = null;
    if (id !== null) onVoiceDraftCancelled?.(id);
  }

  return {
    get identity() {
      return identity;
    },
    get destination() {
      return destination;
    },
    get draft() {
      return draft;
    },
    set draft(value) {
      draft = String(value);
    },
    get recoveredDrafts() {
      return recovered.map((r) => ({ ...r }));
    },
    get voiceDraftId() {
      return voiceId;
    },

    /** Bind the signed-in Feishu identity (null on sign-out). */
    bind(value) {
      if (sameIdentity(identity, value)) return;
      cancelVoiceDraft();
      identity = value ? { ...value } : null;
      destination = null;
      draft = "";
      recovered = [];
    },

    /** Select a conversation `{ id, name, canSend }` (null for none). */
    select(value) {
      const next = value ? { id: value.id, name: value.name, canSend: Boolean(value.canSend) } : null;
      if (sameDestination(destination, next) && destination?.name === next?.name) return;
      const changed = !sameDestination(destination, next);
      destination = next;
      if (changed) draft = "";
    },

    /** The conversation is no longer allowed: keep its draft as a recovery. */
    revoke(id) {
      if (destination?.id === id && draft !== "") {
        recovered.push({ id: ++nextRecovery, conversationId: id, name: destination.name, text: draft });
        draft = "";
      }
      if (destination?.id === id) destination = null;
    },

    discardRecovery(id) {
      recovered = recovered.filter((r) => r.id !== id);
    },

    /** Start a voice draft for the selected conversation; its id, or null. */
    beginVoiceDraft() {
      if (voiceId !== null || !identity || !destination?.canSend) return null;
      nextVoice += 1;
      voiceId = nextVoice;
      voiceDestination = { ...destination };
      return voiceId;
    },

    cancelVoiceDraft,

    /** Apply a transcript to the draft it was started for. Never sends. */
    finishVoiceDraft(id, text) {
      if (voiceId !== id || !identity || !voiceDestination) return false;
      const frozen = voiceDestination;
      voiceId = null;
      voiceDestination = null;
      if (!String(text).trim()) return true;
      if (sameDestination(destination, frozen)) {
        draft += draft === "" ? text : `\n${text}`;
      } else {
        recovered.push({ id: ++nextRecovery, conversationId: frozen.id, name: frozen.name, text });
      }
      return true;
    },
  };
}

/**
 * Dictate into the conversation draft: freeze the destination, capture
 * through the shared capture controller (owner "ui"), transcribe, and apply.
 * `transcribe(audio)` returns the text. Nothing is sent.
 *
 * Every cleanup is scoped to its own operation: a late failure of an earlier
 * dictation never cancels a newer one, and cancel() only stops a capture this
 * dictation actually started (never another owner's after a busy refusal).
 */
export function createConversationDictation({ box, capture, transcribe }) {
  // { id, phase: "starting" | "running" | "finishing" | "transcribing",
  //   cancelRequested, captureId }
  let op = null;

  /** Cancel the capture only while it is still this dictation's session. */
  async function cancelOwnCapture(current) {
    const ours =
      (current.phase === "finishing" && capture.phase?.() === "finishing") ||
      (current.captureId !== null && capture.active?.()?.id === current.captureId);
    if (ours) await capture.cancel();
  }

  function end(current) {
    if (op === current) op = null;
    box.cancelVoiceDraft(current.id);
  }

  async function start() {
    if (op) return { ok: false, reason: "busy" };
    const id = box.beginVoiceDraft();
    if (id === null) return { ok: false, reason: "no-destination" };
    const current = { id, phase: "starting", cancelRequested: false, captureId: null };
    op = current;
    let started;
    try {
      started = await capture.start("ui");
    } catch {
      started = null;
    }
    if (!started?.ok) {
      // Busy (another owner records) or failed: nothing of ours to stop.
      end(current);
      return { ok: false, reason: started?.reason ?? "failed" };
    }
    current.captureId = capture.active?.()?.id ?? null;
    if (current.cancelRequested || op !== current) {
      end(current);
      current.phase = "running";
      await cancelOwnCapture(current);
      return { ok: false, reason: "cancelled" };
    }
    current.phase = "running";
    return { ok: true };
  }

  async function stop() {
    const current = op;
    if (!current || current.phase !== "running") return { ok: false, reason: "idle" };
    // Only this dictation's own session: if the shared controller ended it and
    // another capture runs now, that one is never stopped from here.
    if (current.captureId === null || capture.active?.()?.id !== current.captureId) {
      end(current);
      return { ok: false, reason: "stale" };
    }
    // "finishing" is entered only after the check above, so a cancel while
    // finishing always concerns this dictation's own stop.
    current.phase = "finishing";
    let result;
    try {
      result = await capture.stop("ui");
    } catch {
      end(current);
      return { ok: false, reason: "failed" };
    }
    if (op !== current) {
      box.cancelVoiceDraft(current.id);
      return { ok: false, reason: "stale" };
    }
    // The controller returns the captured audio, null (nothing captured) or
    // { cancelled: true } (Esc while finishing): no audio, no transcription.
    if (!result || result.cancelled) {
      end(current);
      return { ok: false, reason: result?.cancelled ? "cancelled" : "no-audio" };
    }
    // Still this dictation's while transcribing: cancel() can drop it.
    current.phase = "transcribing";
    let text;
    try {
      text = await transcribe(result);
    } catch {
      end(current);
      return { ok: false, reason: "transcription-failed" };
    }
    if (op === current) op = null;
    // A late transcript (cancelled, signed out, account changed) is refused here.
    return { ok: box.finishVoiceDraft(current.id, text), reason: null };
  }

  async function cancel() {
    const current = op;
    if (!current) return;
    if (current.phase === "starting") {
      // The capture is not ours yet: cancel it once its start succeeds.
      current.cancelRequested = true;
      box.cancelVoiceDraft(current.id);
      op = null;
      return;
    }
    op = null;
    box.cancelVoiceDraft(current.id);
    // Transcribing: the audio is already captured; dropping the draft is enough.
    if (current.phase === "running" || current.phase === "finishing") await cancelOwnCapture(current);
  }

  return { start, stop, cancel };
}
