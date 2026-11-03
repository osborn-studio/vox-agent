// Composer voice turn (PR-E, spec §3.4), the Windows counterpart of the macOS
// ChatInputBar flow: capture plain ASR, polish it with the selected LLM, and
// put the result into the composer as an editable draft. Nothing is sent here.
//
// * Decisions key on `polish_status`, never on text equality (PR-D contract):
//   "ok" uses `polished_text`; "failed" keeps the plain transcript and says
//   why inline; "skipped" or an older backend without the field keeps the
//   plain transcript silently.
// * A draft the user already typed is kept: the voice text is appended after
//   it on a new line, as macOS composes its voice prefix.

/** Draft text and inline note for a `/v1/voice` response. */
export function voiceDraftFromResponse(response) {
  const plain = String(response?.display_text || response?.raw_text || "").trim();
  const status = response?.polish_status;
  if (status === "ok") {
    const polished = String(response?.polished_text || "").trim();
    return { text: polished || plain, polished: Boolean(polished), note: null };
  }
  if (status === "failed") {
    const reason = String(response?.polish_error || "").trim();
    return {
      text: plain,
      polished: false,
      note: reason ? `Polish failed (${reason}); kept the plain transcript.` : "Polish failed; kept the plain transcript.",
    };
  }
  return { text: plain, polished: false, note: null };
}

/** Existing draft followed by the voice text. */
export function composeDraft(existing, voiceText) {
  const prefix = String(existing || "").replace(/\s+$/, "");
  const addition = String(voiceText || "").trim();
  if (!addition) return String(existing || "");
  return prefix ? `${prefix}\n${addition}` : addition;
}

/**
 * Put the voice text into the composer textarea and let its owners (the
 * Context composer keeps per-target drafts on `input`) see the change.
 * Returns false when there was nothing to insert.
 */
export function insertVoiceDraft(textarea, voiceText) {
  const next = composeDraft(textarea.value, voiceText);
  if (next === textarea.value) return false;
  textarea.value = next;
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.focus();
  if (typeof textarea.setSelectionRange === "function") {
    textarea.setSelectionRange(next.length, next.length);
  }
  return true;
}
