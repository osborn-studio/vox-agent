// T27: conversation voice drafts follow the macOS FeishuVoiceDraftHarness rules.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createCaptureController } from "../src/capture/controller.js";
import { createConversationDictation, createConversationDraftBox } from "../src/context/conversation-draft.js";

const alice = { origin: "https://fixture.test", appId: "cli_test", tenantKey: "team", userId: "alice" };
const bob = { ...alice, userId: "bob" };
const one = { id: "fc_one", name: "One", canSend: true };
const two = { id: "fc_two", name: "Two", canSend: true };

test("the macOS voice-draft sequence: frozen destination, draft only, late and cancelled transcripts refused", () => {
  const cancelled = [];
  const box = createConversationDraftBox({ onVoiceDraftCancelled: (id) => cancelled.push(id) });
  box.bind(alice);
  box.select(one);
  box.draft = "typed";
  const first = box.beginVoiceDraft();
  assert.equal(box.beginVoiceDraft(), null, "one voice draft at a time");
  assert.equal(box.finishVoiceDraft(first, "spoken"), true);
  assert.equal(box.draft, "typed\nspoken");
  assert.equal(box.finishVoiceDraft(first, "duplicate"), false);

  const switched = box.beginVoiceDraft();
  box.select(two);
  box.draft = "second";
  assert.equal(box.finishVoiceDraft(switched, "frozen one"), true);
  assert.equal(box.draft, "second", "never into another conversation's draft");
  assert.deepEqual(box.recoveredDrafts.at(-1), { id: 1, conversationId: "fc_one", name: "One", text: "frozen one" });

  const cancel = box.beginVoiceDraft();
  box.cancelVoiceDraft();
  assert.equal(box.finishVoiceDraft(cancel, "late"), false);
  assert.deepEqual(cancelled, [cancel]);

  const logout = box.beginVoiceDraft();
  box.bind(null);
  box.bind(alice);
  box.select(one);
  assert.equal(box.finishVoiceDraft(logout, "previous session"), false, "sign-out, even back to the same account");
  assert.equal(cancelled.at(-1), logout);

  const changed = box.beginVoiceDraft();
  box.bind(bob);
  box.select(one);
  assert.equal(box.finishVoiceDraft(changed, "alice"), false, "another account never receives it");

  const revoked = box.beginVoiceDraft();
  box.revoke("fc_one");
  assert.equal(box.finishVoiceDraft(revoked, "recover only"), true);
  assert.equal(box.draft, "");
  assert.equal(box.recoveredDrafts.at(-1).text, "recover only");

  box.select(one);
  const renewed = box.beginVoiceDraft();
  box.bind({ ...bob }); // the same account again (token renewal)
  assert.equal(box.finishVoiceDraft(renewed, "same identity survives renewal"), true);

  const empty = box.beginVoiceDraft();
  const before = box.draft;
  assert.equal(box.finishVoiceDraft(empty, "   "), true);
  assert.equal(box.voiceDraftId, null);
  assert.equal(box.draft, before);
});

test("no voice draft without an account or a conversation that can send", () => {
  const box = createConversationDraftBox();
  assert.equal(box.beginVoiceDraft(), null);
  box.bind(alice);
  assert.equal(box.beginVoiceDraft(), null);
  box.select({ id: "fc_view", name: "View", canSend: false });
  assert.equal(box.beginVoiceDraft(), null);
});

function fakeCapture(result) {
  const calls = [];
  return {
    calls,
    start: async (owner) => { calls.push(["start", owner]); return { ok: true }; },
    stop: async (owner) => { calls.push(["stop", owner]); return typeof result === "function" ? result() : result; },
    cancel: async () => { calls.push(["cancel"]); return true; },
    active: () => ({ id: "s1", owner: "ui" }),
    phase: () => "running",
  };
}

test("dictation fills the draft for the frozen conversation and never sends", async () => {
  const box = createConversationDraftBox();
  box.bind(alice);
  box.select(one);
  const capture = fakeCapture({ mode: "native", cancelled: false, audio: "pcm" });
  const dictation = createConversationDictation({ box, capture, transcribe: async (audio) => `heard ${audio.audio}` });
  assert.deepEqual(await dictation.start(), { ok: true });
  assert.deepEqual(await dictation.start(), { ok: false, reason: "busy" });
  assert.deepEqual(await dictation.stop(), { ok: true, reason: null });
  assert.equal(box.draft, "heard pcm");
  assert.deepEqual(capture.calls, [["start", "ui"], ["stop", "ui"]]);
  const source = readFileSync(new URL("../src/context/conversation-draft.js", import.meta.url), "utf8");
  // The module has no IPC of its own: it cannot send, enqueue or persist.
  assert.doesNotMatch(source, /invoke\(|post_text|context_conversation|\/messages/);
});

test("a cancelled capture, a failed transcription or a sign-out during it leave the draft untouched", async () => {
  const box = createConversationDraftBox();
  box.bind(alice);
  box.select(one);
  box.draft = "kept";
  const cancelledCapture = createConversationDictation({ box, capture: fakeCapture({ cancelled: true }), transcribe: async () => "x" });
  await cancelledCapture.start();
  assert.deepEqual(await cancelledCapture.stop(), { ok: false, reason: "cancelled" });

  const failing = createConversationDictation({ box, capture: fakeCapture({ cancelled: false, audio: "a" }), transcribe: async () => { throw new Error("asr"); } });
  await failing.start();
  assert.deepEqual(await failing.stop(), { ok: false, reason: "transcription-failed" });

  let release;
  const slow = createConversationDictation({
    box,
    capture: fakeCapture({ cancelled: false, audio: "a" }),
    transcribe: () => new Promise((r) => { release = r; }),
  });
  await slow.start();
  const pending = slow.stop();
  await new Promise((r) => setImmediate(r));
  box.bind(null); // signed out while transcribing
  release("late words");
  assert.deepEqual(await pending, { ok: false, reason: null });
  assert.equal(box.draft, "");
  assert.equal(box.voiceDraftId, null);

  box.bind(alice);
  box.select(one);
  box.draft = "kept";
  const cancelled = fakeCapture({ cancelled: false, audio: "a" });
  const user = createConversationDictation({ box, capture: cancelled, transcribe: async () => "x" });
  await user.start();
  await user.cancel();
  assert.deepEqual(cancelled.calls.at(-1), ["cancel"]);
  assert.equal(box.draft, "kept");
  assert.equal(box.voiceDraftId, null);
});


test("a late failure of an earlier dictation never cancels a newer one", async () => {
  const box = createConversationDraftBox();
  box.bind(alice);
  box.select(one);
  let failA;
  let n = 0;
  const capture = fakeCapture({ cancelled: false, audio: "a" });
  const transcribe = () => {
    n += 1;
    return n === 1 ? new Promise((_, reject) => { failA = reject; }) : Promise.resolve("B words");
  };
  const dictation = createConversationDictation({ box, capture, transcribe });
  await dictation.start();
  const a = dictation.stop(); // A is transcribing
  await new Promise((r) => setImmediate(r));
  await dictation.cancel(); // the user drops A
  assert.deepEqual(await dictation.start(), { ok: true }); // B starts
  const bId = box.voiceDraftId;
  failA(new Error("asr"));
  assert.deepEqual(await a, { ok: false, reason: "transcription-failed" });
  assert.equal(box.voiceDraftId, bId, "B is still the active voice draft");
  assert.deepEqual(await dictation.stop(), { ok: true, reason: null });
  assert.equal(box.draft, "B words");
});

test("cancel never stops another owner's recording after a busy start, and a stop error frees the dictation", async () => {
  const box = createConversationDraftBox();
  box.bind(alice);
  box.select(one);
  let releaseStart;
  const calls = [];
  const capture = {
    start: () => new Promise((r) => { releaseStart = r; }),
    stop: async () => { throw new Error("device gone"); },
    cancel: async () => { calls.push("cancel"); return true; },
    active: () => ({ id: "ours", owner: "ui" }),
    phase: () => "running",
  };
  const dictation = createConversationDictation({ box, capture, transcribe: async () => "x" });
  const starting = dictation.start();
  await dictation.cancel(); // the hotkey owns the microphone; our start will be refused
  releaseStart({ ok: false, reason: "busy" });
  assert.deepEqual(await starting, { ok: false, reason: "busy" });
  assert.deepEqual(calls, [], "the hotkey's capture is untouched");

  // A cancel during a start that then succeeds stops our own capture.
  const ours = dictation.start();
  await dictation.cancel();
  releaseStart({ ok: true });
  assert.deepEqual(await ours, { ok: false, reason: "cancelled" });
  assert.deepEqual(calls, ["cancel"]);

  // A throwing stop frees the dictation for a new start.
  const again = dictation.start();
  releaseStart({ ok: true });
  await again;
  assert.deepEqual(await dictation.stop(), { ok: false, reason: "failed" });
  assert.equal(box.voiceDraftId, null);
  const next = dictation.start();
  releaseStart({ ok: true });
  assert.deepEqual(await next, { ok: true });
});


test("with the real capture controller, an old dictation never cancels a newer owner's session", async () => {
  const box = createConversationDraftBox();
  box.bind(alice);
  box.select(one);
  let session = 0;
  const cancelled = [];
  const invoke = async (command, args) => {
    if (command === "start_native_capture") return { sessionId: ++session, recording: true };
    if (command === "cancel_native_capture") { cancelled.push(args.sessionId); return null; }
    return null;
  };
  const capture = createCaptureController({ invoke, fallback: {} });
  const dictation = createConversationDictation({ box, capture, transcribe: async () => "x" });
  assert.deepEqual(await dictation.start(), { ok: true }); // session 1 is ours
  await capture.cancel(); // the shared controller ends it (e.g. Esc)
  assert.equal(cancelled.at(-1), 1);
  assert.equal((await capture.start("hotkey")).ok, true); // session 2 is the hotkey's
  await dictation.cancel();
  assert.ok(!cancelled.includes(2), "the hotkey's session 2 is untouched");
});


test("an old dictation never stops or cancels a session that replaced its own", async () => {
  const box = createConversationDraftBox();
  box.bind(alice);
  box.select(one);
  let session = 0;
  const calls = [];
  let releaseStop;
  const invoke = async (command, args) => {
    calls.push([command, args?.sessionId]);
    if (command === "start_native_capture") return { sessionId: ++session, recording: true };
    if (command === "stop_native_capture") return new Promise((r) => { releaseStop = () => r({ audio: "a" }); });
    return null;
  };
  const capture = createCaptureController({ invoke, fallback: {} });
  const dictation = createConversationDictation({ box, capture, transcribe: async () => "x" });
  await dictation.start(); // session 1
  await capture.cancel();
  await capture.start("ui"); // session 2 belongs to someone else in the window
  assert.deepEqual(await dictation.stop(), { ok: false, reason: "stale" });
  assert.ok(!calls.some(([c, id]) => c === "stop_native_capture" && id === 2), "session 2 is not stopped");
  assert.equal(box.voiceDraftId, null);

  // A hotkey session finishing: an old dictation's stop/cancel leave it alone.
  await capture.cancel();
  await dictation.start(); // session 3, ours
  await capture.cancel();
  await capture.start("hotkey"); // session 4
  const finishing = capture.stop("hotkey");
  assert.equal(capture.phase(), "finishing");
  assert.deepEqual(await dictation.stop(), { ok: false, reason: "stale" });
  await dictation.cancel();
  releaseStop();
  const hotkeyResult = await finishing;
  assert.equal(hotkeyResult.cancelled, false, "the hotkey's result is not marked cancelled");
});
