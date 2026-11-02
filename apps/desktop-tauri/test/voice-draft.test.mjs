import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { composeDraft, insertVoiceDraft, voiceDraftFromResponse } from "../src/capture/voice-draft.js";

const javascript = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");

test("a polished response fills the draft with the polished text", () => {
  const draft = voiceDraftFromResponse({
    raw_text: "嗯 明天 开会",
    display_text: "嗯 明天开会",
    polished_text: "明天开会。",
    polish_status: "ok",
  });
  assert.deepEqual(draft, { text: "明天开会。", polished: true, note: null });
});

test("a failed polish keeps the plain transcript and explains why inline", () => {
  const draft = voiceDraftFromResponse({
    display_text: "plain words",
    polished_text: "plain words",
    polish_status: "failed",
    polish_error: "Qwen: timeout",
  });
  assert.equal(draft.text, "plain words");
  assert.equal(draft.polished, false);
  assert.match(draft.note, /Polish failed \(Qwen: timeout\)/);
});

test("skipped or legacy responses never claim a polish, even if texts differ", () => {
  for (const response of [
    { display_text: "a", polished_text: "b", polish_status: "skipped" },
    { display_text: "a", polished_text: "b" },
  ]) {
    assert.deepEqual(voiceDraftFromResponse(response), { text: "a", polished: false, note: null });
  }
  assert.equal(voiceDraftFromResponse({ raw_text: "raw only" }).text, "raw only");
  assert.equal(voiceDraftFromResponse(null).text, "");
});

test("voice text is appended after a typed draft on its own line", () => {
  assert.equal(composeDraft("", "hello"), "hello");
  assert.equal(composeDraft("typed  \n", " hello "), "typed\nhello");
  assert.equal(composeDraft("typed", "   "), "typed");
});

test("inserting a draft notifies the composer owners and keeps the caret at the end", () => {
  const events = [];
  const textarea = {
    value: "typed",
    dispatchEvent: (event) => events.push(event.type),
    focus: () => events.push("focus"),
    setSelectionRange: (start, end) => events.push(`caret:${start}-${end}`),
  };
  assert.equal(insertVoiceDraft(textarea, "spoken"), true);
  assert.equal(textarea.value, "typed\nspoken");
  assert.deepEqual(events, ["input", "focus", "caret:12-12"]);
  assert.equal(insertVoiceDraft(textarea, ""), false);
});

test("composer captures ask the backend to polish; hotkey output to other apps does not", () => {
  assert.match(javascript, /invoke\("upload_voice",\s*\{[^}]*polish: !payload\.injectAfterUpload/s);
  assert.match(javascript, /insertVoiceDraft\(elements\.agentQuery/);
});
