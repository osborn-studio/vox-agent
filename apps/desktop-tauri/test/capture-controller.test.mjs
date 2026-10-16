// T26: one owner for capture. The hotkey and the window share one controller;
// a capture is stopped only by its own owner (or the explicit Stop control),
// a busy native recorder never opens a second (WebView) recorder, and cancel
// never brings audio back from Rust.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createCaptureController } from "../src/capture/controller.js";

function harness({ startError = null, fallbackFails = false } = {}) {
  const calls = [];
  let nextId = 1;
  let release;
  const gate = { held: false };
  const invoke = async (command, args) => {
    calls.push([command, args]);
    if (command === "start_native_capture") {
      if (gate.held) await new Promise((resolve) => (release = resolve));
      if (startError) throw startError;
      return { recording: true, sessionId: nextId++, owner: args.owner, deviceName: "Mic" };
    }
    if (command === "stop_native_capture") return { audioBase64: "UklGRg==", mimeType: "audio/wav" };
    if (command === "cancel_native_capture") return null;
    throw new Error(`unexpected ${command}`);
  };
  const fallbackCalls = [];
  const fallback = {
    start: async () => {
      fallbackCalls.push("start");
      if (fallbackFails) throw new Error("no media");
    },
    stop: async () => (fallbackCalls.push("stop"), { blob: "webm" }),
    cancel: async () => fallbackCalls.push("cancel"),
  };
  return { calls, fallbackCalls, gate, release: () => release?.(), controller: createCaptureController({ invoke, fallback }) };
}

test("a capture belongs to its owner; the other owner cannot stop it", async () => {
  const h = harness();
  const started = await h.controller.start("hotkey");
  assert.equal(started.ok, true);
  assert.deepEqual(h.calls[0], ["start_native_capture", { deviceName: null, owner: "hotkey" }]);
  assert.equal(await h.controller.stop("ui"), null, "a Space keyup cannot end the hotkey capture");
  assert.equal(h.calls.length, 1);
  const audio = await h.controller.stop("hotkey");
  assert.equal(audio.mode, "native");
  assert.deepEqual(h.calls[1], ["stop_native_capture", { sessionId: 1 }]);
  assert.equal(h.controller.active(), null);
});

test("the explicit Stop control may stop any capture", async () => {
  const h = harness();
  await h.controller.start("hotkey");
  assert.ok(await h.controller.stop("any"));
});

test("a second start while one is starting or running is refused, never a second recorder", async () => {
  const h = harness();
  h.gate.held = true;
  const first = h.controller.start("hotkey");
  const second = await h.controller.start("ui");
  assert.deepEqual(second, { ok: false, reason: "busy" });
  h.release();
  assert.equal((await first).ok, true);
  assert.deepEqual(await h.controller.start("ui"), { ok: false, reason: "busy" });
  assert.equal(h.calls.filter(([c]) => c === "start_native_capture").length, 1);
  assert.deepEqual(h.fallbackCalls, []);
});

test("a busy native recorder does not fall back to the WebView recorder", async () => {
  const h = harness({ startError: "capture-busy:hotkey" });
  assert.deepEqual(await h.controller.start("ui"), { ok: false, reason: "busy" });
  assert.deepEqual(h.fallbackCalls, []);
  assert.equal(h.controller.active(), null);
});

test("an unavailable native recorder falls back once, as the same owner", async () => {
  const h = harness({ startError: "capture-open-failed: no device" });
  const started = await h.controller.start("ui");
  assert.equal(started.ok, true);
  assert.equal(started.mode, "webview");
  assert.equal(h.controller.active().owner, "ui");
  assert.equal(await h.controller.stop("hotkey"), null);
  const audio = await h.controller.stop("ui");
  assert.deepEqual(audio, { mode: "webview", cancelled: false, options: {}, blob: "webm" });
  assert.deepEqual(h.fallbackCalls, ["start", "stop"]);
});

test("when both recorders fail nothing stays active", async () => {
  const h = harness({ startError: "capture-open-failed: x", fallbackFails: true });
  assert.deepEqual(await h.controller.start("ui"), { ok: false, reason: "failed" });
  assert.equal(h.controller.active(), null);
});

test("cancel discards in Rust and returns no audio", async () => {
  const h = harness();
  await h.controller.start("hotkey");
  assert.equal(await h.controller.cancel(), true);
  assert.deepEqual(h.calls.at(-1), ["cancel_native_capture", { sessionId: 1 }]);
  assert.ok(!h.calls.some(([c]) => c === "stop_native_capture"), "cancel never collects audio");
  assert.equal(await h.controller.cancel(), false, "nothing left to cancel");
});

test("stop and cancel never both reach Rust for one session", async () => {
  const h = harness();
  await h.controller.start("ui");
  const stopping = h.controller.stop("ui");
  assert.equal(await h.controller.cancel(), true, "cancel marks the finishing result");
  const result = await stopping;
  assert.equal(result.cancelled, true);
  assert.equal(h.calls.filter(([c]) => c !== "start_native_capture").length, 1, "only the stop went to Rust");
});

test("a status showing the session ended (device lost) clears it", async () => {
  const h = harness();
  await h.controller.start("hotkey");
  assert.equal(h.controller.observe({ recording: false, endedReason: "device-lost" }), "device-lost");
  assert.equal(h.controller.active(), null);
  assert.equal(h.controller.observe({ recording: false }), null, "nothing to clear twice");
  await h.controller.start("ui");
  assert.equal(h.controller.observe({ recording: true, sessionId: 99 }), "replaced", "another session is not ours");
});

test("a release during startup cancels that session once it starts; nothing is captured", async () => {
  const h = harness();
  h.gate.held = true;
  const starting = h.controller.start("hotkey");
  assert.equal(await h.controller.stop("hotkey"), null, "recorded as an intent");
  h.release();
  assert.deepEqual(await starting, { ok: false, reason: "released" });
  assert.deepEqual(h.calls.at(-1), ["cancel_native_capture", { sessionId: 1 }]);
  assert.equal(h.controller.active(), null);
});

test("Esc during startup cancels the session once it starts", async () => {
  const h = harness();
  h.gate.held = true;
  const starting = h.controller.start("ui");
  assert.equal(await h.controller.cancel(), true);
  h.release();
  assert.deepEqual(await starting, { ok: false, reason: "cancelled" });
  assert.deepEqual(h.calls.at(-1), ["cancel_native_capture", { sessionId: 1 }]);
});

test("Esc while the audio is being finalized marks it cancelled so it is never used", async () => {
  const calls = [];
  let finish;
  const invoke = async (command, args) => {
    calls.push(command);
    if (command === "start_native_capture") return { sessionId: 7, owner: args.owner };
    if (command === "stop_native_capture") {
      await new Promise((resolve) => (finish = resolve));
      return { audioBase64: "UklGRg==", mimeType: "audio/wav" };
    }
    return null;
  };
  const controller = createCaptureController({ invoke, fallback: {} });
  await controller.start("ui", { injectAfterUpload: true });
  const stopping = controller.stop("ui");
  assert.equal(await controller.cancel(), true, "cancel is honoured while finishing");
  finish();
  const result = await stopping;
  assert.equal(result.cancelled, true);
  assert.equal(result.audioBase64, undefined, "a cancelled result carries no audio");
});

test("a stop issued before a restart can never act on the new session", async () => {
  const calls = [];
  let finish;
  const invoke = async (command, args) => {
    calls.push([command, args]);
    if (command === "start_native_capture") return { sessionId: calls.filter(([c]) => c === command).length, owner: args.owner };
    if (command === "stop_native_capture") {
      await new Promise((resolve) => (finish = resolve));
      return { audioBase64: "UklGRg==", mimeType: "audio/wav" };
    }
    return null;
  };
  const controller = createCaptureController({ invoke, fallback: {} });
  await controller.start("ui");
  const firstStop = controller.stop("ui"); // claims session 1 now
  await controller.cancel(); // cancels the finishing session 1
  finish();
  await firstStop;
  await controller.start("ui"); // session 2
  assert.equal(controller.active().id, 2);
  const stops = calls.filter(([c]) => c === "stop_native_capture").map(([, a]) => a.sessionId);
  assert.deepEqual(stops, [1], "session 2 was never stopped by the old operation");
});

test("output options belong to the session; a losing start cannot change them", async () => {
  const h = harness();
  h.gate.held = true;
  const winner = h.controller.start("hotkey", { injectAfterUpload: true });
  assert.deepEqual(await h.controller.start("ui", { injectAfterUpload: false }), { ok: false, reason: "busy" });
  h.release();
  await winner;
  assert.equal(h.controller.active().options.injectAfterUpload, true);
  const audio = await h.controller.stop("hotkey");
  assert.equal(audio.options.injectAfterUpload, true);
});

test("main.js drives capture only through the controller with owners", () => {
  const main = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
  assert.match(main, /createCaptureController/);
  assert.doesNotMatch(main, /invoke\("start_native_capture"/, "only the controller starts native capture");
  assert.doesNotMatch(main, /invoke\("stop_native_capture"/);
  assert.doesNotMatch(main, /captureOutputRequested/, "output options are bound to the capture session");
  const start = main.slice(main.indexOf("async function startRecording"), main.indexOf("async function leaveListening"));
  assert.ok(start.indexOf("capture.start(") < start.indexOf("await"), "the controller claims before any await");
  const stop = main.slice(main.indexOf("async function stopRecording"), main.indexOf("async function cancelRecording"));
  assert.ok(stop.indexOf("capture.stop(") < stop.indexOf("await"), "stop claims before any await");
  assert.match(main, /if \(capture\.phase\(\) !== "idle"\) \{\s*setHotkeyState\("Hotkey Ready", "live"\);\s*await stopRecording\(\{ owner: "hotkey" \}\)/,
    "toggle: a second press during startup is a stop");
  assert.match(main, /event\.code === "Escape" && capture\.phase\(\) !== "idle"/, "Esc works during startup");
  assert.match(main, /startRecording\(\{ owner: "hotkey"/);
  assert.match(main, /stopRecording\(\{ owner: "hotkey" \}\)/);
  assert.match(main, /startRecording\(\{ owner: "ui" \}\)/);
  assert.match(main, /stopRecording\(\{ owner: "ui" \}\)/);
});
