import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const config = JSON.parse(read("../src-tauri/tauri.conf.json"));
const html = read("../src/voice-hud.html");
const javascript = read("../src/voice-hud.js");
const styles = read("../src/voice-hud.css");
const cockpit = read("../src/main.js");
const rust = read("../src-tauri/src/lib.rs");
const voiceHudRust = read("../src-tauri/src/voice_hud.rs");
const audio = read("../src-tauri/src/audio.rs");

test("the global voice HUD is a hidden non-focusable always-on-top window", () => {
  const hud = config.app.windows.find((window) => window.label === "voice-hud");

  assert.ok(hud, "tauri.conf.json must declare the voice-hud window");
  assert.equal(hud.url, "voice-hud.html");
  assert.equal(hud.visible, false);
  assert.equal(hud.focus, false);
  assert.equal(hud.alwaysOnTop, true);
  assert.equal(hud.skipTaskbar, true);
  assert.equal(hud.decorations, false);
  assert.equal(hud.resizable, false);
});

test("the HUD exposes status, target Agent, instructions, and real level meters", () => {
  assert.match(html, /id="voice-hud"/);
  assert.match(html, /id="hud-status"[^>]*aria-live="polite"/);
  assert.match(html, /id="hud-target-agent"/);
  assert.match(html, /id="hud-instruction"/);
  assert.match(html, /id="hud-elapsed"/);
  assert.match(html, /id="hud-rms-meter"/);
  assert.match(html, /id="hud-peak-meter"/);

  assert.match(javascript, /listen\("vox-agent:\/\/voice-hud"/);
  assert.match(javascript, /payload\.rmsLevel/);
  assert.match(javascript, /payload\.peakLevel/);
  assert.doesNotMatch(javascript, /Math\.random|AudioContext|requestAnimationFrame/);

  assert.match(styles, /prefers-reduced-motion:\s*reduce/);
  assert.match(styles, /\.voice-copy b[\s\S]*color:\s*var\(--amber\)/);
  assert.match(styles, /animation:\s*hud-breathe 2\.2s/);
  assert.doesNotMatch(styles, /(?:linear|radial)-gradient\s*\(/);
});

test("the cockpit drives every HUD phase and polls native capture levels", () => {
  for (const phase of ["preparing", "listening", "processing", "sent", "error", "hidden"]) {
    assert.match(cockpit, new RegExp(`phase:\\s*"${phase}"`));
  }
  assert.match(cockpit, /invoke\("update_voice_hud"/);
  assert.match(cockpit, /invoke\("get_native_capture_status"/);
  assert.match(cockpit, /status\.rmsLevel/);
  assert.match(cockpit, /status\.peakLevel/);
  assert.match(cockpit, /currentVoiceTargetAgent/);
  assert.match(cockpit, /generation !== voiceHudPollGeneration/);
});

test("the Rust shell owns HUD visibility and publishes measured audio levels", () => {
  assert.match(rust, /mod voice_hud;/);
  assert.match(rust, /voice_hud::update_voice_hud/);
  assert.match(voiceHudRust, /window\.is_visible\(\)/);
  assert.match(audio, /pub rms_level: f32/);
  assert.match(audio, /pub peak_level: f32/);
});
