const listen = window.__TAURI__?.event?.listen;

const elements = {
  hud: document.querySelector("#voice-hud"),
  status: document.querySelector("#hud-status"),
  targetAgent: document.querySelector("#hud-target-agent"),
  instruction: document.querySelector("#hud-instruction"),
  elapsed: document.querySelector("#hud-elapsed"),
  rms: document.querySelector("#hud-rms-meter"),
  peak: document.querySelector("#hud-peak-meter"),
};

const phaseLabels = {
  preparing: "Preparing microphone",
  listening: "Listening",
  processing: "Transcribing and routing",
  sent: "Sent",
  error: "Voice command failed",
};

function boundedLevel(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.min(1, Math.max(0, numeric)) : 0;
}

function setMeter(element, value) {
  const level = boundedLevel(value);
  const percentage = Math.round(level * 100);
  element.style.setProperty("--level", `${percentage}%`);
  element.setAttribute("aria-valuenow", String(percentage));
}

function formatElapsed(elapsedMs) {
  const seconds = Math.max(0, Math.floor(Number(elapsedMs || 0) / 1_000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function render(payload = {}) {
  const phase = phaseLabels[payload.phase] ? payload.phase : "error";
  const elapsed = formatElapsed(payload.elapsedMs);
  elements.hud.dataset.phase = phase;
  elements.status.textContent = phaseLabels[phase];
  elements.targetAgent.textContent = payload.targetAgent || "Vox Agent Coordinator";
  elements.instruction.textContent = payload.transcript || payload.instruction || "Esc to cancel";
  elements.elapsed.textContent = elapsed;
  elements.elapsed.dateTime = `PT${Math.max(0, Math.floor(Number(payload.elapsedMs || 0) / 1_000))}S`;
  setMeter(elements.rms, payload.rmsLevel);
  setMeter(elements.peak, payload.peakLevel);
}

if (typeof listen === "function") {
  await listen("vox-agent://voice-hud", (event) => render(event.payload));
} else if (new URLSearchParams(window.location.search).has("preview")) {
  render({
    phase: "listening",
    targetAgent: "Vox Agent Coordinator",
    instruction: "Release to send · Esc to cancel",
    elapsedMs: 12_000,
    rmsLevel: 0.46,
    peakLevel: 0.78,
  });
}
