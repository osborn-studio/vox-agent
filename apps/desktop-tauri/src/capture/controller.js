// One owner for microphone capture (T26). The global hotkey and the window
// UI both go through this controller. It owns the whole lifecycle:
//
//   idle -> starting -> running -> finishing -> idle
//
// - start/stop/cancel take effect synchronously when called (before the
//   caller awaits anything), so a late operation can never act on a newer
//   session;
// - a capture belongs to its owner ("hotkey" or "ui"); only that owner, or
//   the explicit Stop control ("any"), can stop it;
// - a release or Esc while the microphone is still starting is remembered,
//   and the session is cancelled in Rust as soon as it exists;
// - Esc while the audio is being finalized marks the result cancelled: the
//   caller gets no audio and must not upload, preview or inject anything;
// - a busy native recorder is never a reason to open the WebView recorder;
// - output options (inject after upload) belong to the session.
// Rust enforces ownership again by session number.

export function createCaptureController({ invoke, fallback }) {
  let phase = "idle";
  let session = null; // { id, owner, mode, options, intent, cancelled }
  let localId = 0;

  async function discard(current) {
    if (current.mode === "native") {
      await invoke("cancel_native_capture", { sessionId: current.id });
    } else if (current.mode === "webview") {
      await fallback.cancel();
    }
  }

  function start(owner, options = {}) {
    if (phase !== "idle") return Promise.resolve({ ok: false, reason: "busy" });
    phase = "starting";
    const current = { id: null, owner, mode: null, options: { ...options }, intent: null, cancelled: false };
    session = current;
    return (async () => {
      let status = null;
      try {
        status = await invoke("start_native_capture", { deviceName: options.deviceName ?? null, owner });
        current.id = status.sessionId;
        current.mode = "native";
      } catch (error) {
        if (String(error).startsWith("capture-busy")) {
          phase = "idle";
          session = null;
          return { ok: false, reason: "busy" };
        }
        try {
          await fallback.start();
          localId += 1;
          current.id = `webview-${localId}`;
          current.mode = "webview";
        } catch {
          phase = "idle";
          session = null;
          return { ok: false, reason: "failed" };
        }
      }
      if (current.intent) {
        // Released or cancelled while starting: nothing is kept.
        const reason = current.intent === "cancel" ? "cancelled" : "released";
        phase = "idle";
        session = null;
        await discard(current);
        return { ok: false, reason };
      }
      phase = "running";
      return { ok: true, mode: current.mode, status };
    })();
  }

  function owns(owner) {
    return session && (owner === "any" || session.owner === owner);
  }

  function stop(owner) {
    if (!owns(owner)) return Promise.resolve(null);
    if (phase === "starting") {
      session.intent = session.intent || "stop";
      return Promise.resolve(null);
    }
    if (phase !== "running") return Promise.resolve(null);
    phase = "finishing";
    const current = session;
    return (async () => {
      try {
        const audio =
          current.mode === "native"
            ? await invoke("stop_native_capture", { sessionId: current.id })
            : await fallback.stop();
        if (current.cancelled) return { mode: current.mode, cancelled: true, options: current.options };
        return { mode: current.mode, cancelled: false, options: current.options, ...audio };
      } finally {
        if (session === current) {
          phase = "idle";
          session = null;
        }
      }
    })();
  }

  function cancel() {
    if (!session) return Promise.resolve(false);
    if (phase === "starting") {
      session.intent = "cancel";
      return Promise.resolve(true);
    }
    if (phase === "finishing") {
      session.cancelled = true;
      return Promise.resolve(true);
    }
    const current = session;
    phase = "idle";
    session = null;
    return discard(current).then(() => true);
  }

  /**
   * A native status poll. Returns why our running session is gone
   * ("device-lost", "ended", "replaced"), or null when nothing changed.
   */
  function observe(status) {
    if (phase !== "running" || session?.mode !== "native") return null;
    if (status.recording && status.sessionId === session.id) return null;
    phase = "idle";
    session = null;
    if (status.recording) return "replaced";
    return status.endedReason || "ended";
  }

  return {
    start,
    stop,
    cancel,
    observe,
    phase: () => phase,
    active: () => (session && phase === "running" ? { id: session.id, owner: session.owner, mode: session.mode, options: { ...session.options } } : null),
    starting: () => phase === "starting",
  };
}
