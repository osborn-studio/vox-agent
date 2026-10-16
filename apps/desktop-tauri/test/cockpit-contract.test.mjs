import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("../src/index.html", import.meta.url), "utf8");
const javascript = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

function matches(source, pattern) {
  return [...source.matchAll(pattern)].map((match) => match[1]);
}

test("every desktop bridge selector resolves to exactly one DOM element", () => {
  const selectorIds = new Set(matches(javascript, /querySelector\("#([^"\n]+)"\)/g));
  const htmlIds = matches(html, /\bid="([^"\n]+)"/g);
  const counts = new Map();
  for (const id of htmlIds) {
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }

  assert.deepEqual(
    [...selectorIds].filter((id) => counts.get(id) !== 1),
    [],
    "a main.js selector is missing or duplicated in index.html",
  );
  assert.deepEqual(
    [...counts].filter(([, count]) => count !== 1),
    [],
    "index.html contains duplicate ids",
  );
});

test("the Vox Agent cockpit exposes the canonical workspace views", () => {
  const views = matches(html, /\bdata-view="([^"\n]+)"/g).sort();
  const targets = matches(html, /\bdata-view-target="([^"\n]+)"/g).sort();
  const expected = ["command", "history", "modes", "sessions", "settings", "vocabulary"];

  assert.deepEqual(views, expected);
  assert.deepEqual(targets, expected);
  assert.match(html, /class="cockpit"/);
  assert.match(html, /class="voice-stage"/);
  assert.match(html, /class="inline-card permission-card hidden"/);
  assert.match(html, /id="engine-task-timeline"/);
  assert.match(html, /id="engine-permission-list"/);
  assert.match(html, /id="engine-stream-status"/);
});

test("named modes drive real capture and output controls", () => {
  for (const id of [
    "named-mode-form",
    "named-mode-name",
    "named-mode-target-app",
    "named-mode-risk",
    "named-mode-recording",
    "named-mode-hotkey",
    "named-mode-list",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }

  for (const command of ["get_named_modes", "create_named_mode", "delete_named_mode"]) {
    assert.match(javascript, new RegExp(`invoke\\("${command}"`));
  }
  assert.match(javascript, /elements\.recordMode\.value = mode\.recording_mode/);
  assert.match(javascript, /elements\.targetRisk\.value = mode\.target_risk_level/);
  assert.match(javascript, /elements\.targetApp\.value = mode\.target_app/);
  assert.match(javascript, /hotkeyAccelerator: elements\.namedModeHotkey\.value\.trim\(\)/);
  assert.match(javascript, /mode\.hotkey_accelerator/);
  assert.match(javascript, /invoke\("sync_named_mode_hotkeys"/);
  assert.match(javascript, /findNamedModeForHotkey\(payload\.accelerator\)/);
  assert.match(
    javascript,
    /invoke\("set_hotkey_config",\s*\{[\s\S]*?behavior: elements\.hotkeyBehavior\.value/,
  );
});

test("the vocabulary workspace manages real corrections without an HTML sink", () => {
  for (const id of [
    "dictionary-status",
    "dictionary-wrong-input",
    "dictionary-right-input",
    "save-dictionary-term",
    "dictionary-corrections",
    "dictionary-preserve",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }

  assert.match(javascript, /invoke\("get_dictionary"/);
  assert.match(javascript, /invoke\("mutate_dictionary"/);

  const start = javascript.indexOf("function renderDictionary");
  const end = javascript.indexOf("function setRecordingState", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const dictionaryRenderer = javascript.slice(start, end);
  assert.doesNotMatch(dictionaryRenderer, /innerHTML\s*=/);
  assert.match(dictionaryRenderer, /textContent\s*=/);
});

test("desktop invoke calls do not regress to snake_case top-level arguments", () => {
  const formerlyBroken = [
    ["preview_output_policy", "target_app"],
    ["preview_capture_status", "duration_ms"],
    ["get_project_digest", "project_key"],
    ["get_session_digest", "session_id"],
    ["set_backend_base_url", "backend_base_url"],
    ["dismiss_recent_utterance", "utterance_id"],
    ["undo_output", "output_id"],
    ["retry_recent_utterance", "utterance_id"],
    ["set_recent_mode", "utterance_id"],
    ["add_recent_dictionary_term", "utterance_id"],
    ["upload_voice", "audio_base64"],
    ["start_native_capture", "device_name"],
  ];

  for (const [command, key] of formerlyBroken) {
    const pattern = new RegExp(`invoke\\("${command}",\\s*\\{\\s*${key}\\b`);
    assert.doesNotMatch(javascript, pattern, `${command} must use Tauri's camelCase argument contract`);
  }
});

test("the cross-platform design contract stays explicit", () => {
  assert.doesNotMatch(styles, /(?:linear|radial)-gradient\s*\(/);
  assert.match(styles, /@keyframes voice-breathe/);
  assert.equal(matches(styles, /@keyframes\s+([\w-]+)/g).length, 1, "only voice surfaces may animate");
  assert.match(html, /id="approval-count"[^>]*>0<\/b> awaiting approval/);
  assert.match(html, /id="policy-preview-send"[^>]*>Approve &amp; send<\/button>/);
  for (const id of ["history-result-count", "dictionary-preserve-count", "named-mode-count"]) {
    assert.match(html, new RegExp(`id="${id}" class="count-label"`));
  }
});

test("Agent commands and approvals use the authenticated engine bridge", () => {
  for (const command of [
    "get_agent_engine_health",
    "get_engine_operations",
    "run_engine_command",
    "resolve_engine_permission",
    "start_engine_event_stream",
  ]) {
    assert.match(javascript, new RegExp(`invoke\\("${command}"`));
  }
  assert.match(javascript, /listen\("vox-agent:\/\/engine-event"/);
  assert.match(javascript, /listen\("vox-agent:\/\/engine-stream"/);
  assert.doesNotMatch(javascript, /storage\.(?:set|get)\([^\n]*engine[_-](?:admin[_-])?token/i);
  assert.match(html, /id="engine-api-token"[^>]*type="password"/);
});

test("history renders backend-controlled fields without an HTML injection sink", () => {
  const start = javascript.indexOf("function createTextElement");
  const end = javascript.indexOf("function setRecordingState", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const renderHistory = javascript.slice(start, end);

  assert.doesNotMatch(renderHistory, /innerHTML\s*=/);
  assert.match(renderHistory, /element\.textContent\s*=\s*String\(text \?\? ""\)/);
  for (const field of [
    "item.target_app",
    "item.agent",
    "item.recording_mode",
    "item.stt_provider",
    "item.display_text",
    "item.created_at",
    "item.utterance_id",
  ]) {
    assert.match(renderHistory, new RegExp(field.replace(".", "\\.")));
  }

  assert.match(javascript, /invoke\("export_history"/);
  assert.match(javascript, /invoke\("copy_text"/);
  assert.match(html, /id="export-history-markdown"/);
  assert.match(html, /id="export-history-json"/);
  assert.match(html, /id="open-history-exports"/);
});

test("settings exposes real model policy, engine diagnostics, and vault sync", () => {
  for (const id of [
    "model-policy-status",
    "model-answerer",
    "model-asr-adapter",
    "apply-model-policy",
    "engine-diagnostics-status",
    "engine-process-state",
    "engine-service-state",
    "refresh-engine-diagnostics",
    "start-settings-engine",
    "stop-settings-engine",
    "obsidian-sync-days",
    "obsidian-sync-status",
    "run-obsidian-sync",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }

  for (const command of [
    "get_engine_model_policy",
    "update_engine_model_policy",
    "get_agent_engine_process",
    "get_agent_engine_health",
    "get_backend_health",
    "sync_obsidian_archive",
  ]) {
    assert.match(javascript, new RegExp(`invoke\\("${command}"`));
  }

  assert.doesNotMatch(javascript, /elements\.syncSettings\.addEventListener\([^\n]+saveAppSettings/);
  assert.match(html, />Run archive sync</);
});

test("settings exposes the Vox Agent portable migration and cross-platform sync contract", () => {
  for (const id of [
    "migration-status",
    "migration-bundle-path",
    "export-migration-bundle",
    "import-migration-bundle",
    "migration-summary",
    "notion-sync-enabled",
    "notion-token",
    "notion-data-source-id",
    "notion-sync-direction",
    "notion-include-raw-text",
    "git-sync-enabled",
    "git-sync-repo-url",
    "git-sync-local-path",
    "run-notion-sync",
    "run-git-sync",
    "cross-platform-sync-summary",
    "import-previous-data",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }

  assert.match(javascript, /invoke\("export_migration_bundle"/);
  assert.match(javascript, /invoke\("import_migration_bundle"/);
  assert.match(javascript, /invoke\("sync_notion_history"/);
  assert.match(javascript, /invoke\("sync_git_history"/);
  assert.match(javascript, /invoke\("import_legacy_voxlog"/);
  assert.match(javascript, /notion_token:/);
  assert.match(javascript, /git_sync_repo_url:/);
  assert.match(javascript, /secrets_imported/);
  assert.match(html, /Secrets are never written to the migration bundle/);
});

test("provider qualification distinguishes configured routes from verified evidence", () => {
  for (const id of [
    "provider-qualification-status",
    "provider-qualification-summary",
    "provider-qualification-list",
    "refresh-provider-qualification",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(javascript, /invoke\("get_provider_qualifications"/);
  assert.match(javascript, /provider\.probe_status/);
  assert.match(javascript, /provider\.package_available/);
  assert.match(javascript, /provider\.assets_available/);
  assert.match(javascript, /provider\.install_command/);
  assert.match(javascript, /provider\.active_roles/);
  assert.match(javascript, /payload\.summary\.active_routes_configured/);
  assert.match(javascript, /No Active Route/);

  const start = javascript.indexOf("function renderProviderQualification");
  const end = javascript.indexOf("function renderRecognitionEvidence", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  assert.doesNotMatch(javascript.slice(start, end), /innerHTML\s*=/);
});

test("capture status never masquerades as a local ASR transcript", () => {
  assert.match(javascript, /invoke\("preview_capture_status"/);
  assert.match(javascript, /not invoked/);
  assert.doesNotMatch(javascript, /preview_local_transcript/);
  assert.doesNotMatch(javascript, /using whisper\.cpp local preview/);
  assert.doesNotMatch(html, />whispercpp-local<\/dd>/);
});

test("Windows provider routes share secure settings without HTML sinks", () => {
  for (const id of [
    "stt-main-route",
    "stt-fallback-route",
    "llm-main-route",
    "llm-fallback-route",
    "doubao-key",
    "anthropic-key",
    "custom-llm-key",
    "openai-llm-model",
    "qwen-llm-model",
    "doubao-model",
    "anthropic-model",
    "custom-llm-base-url",
    "custom-llm-model",
    "ollama-model",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const field of [
    "stt_main",
    "stt_fallback",
    "doubao_key",
    "anthropic_key",
    "custom_llm_key",
    "openai_llm_model",
    "custom_llm_base_url",
    "llm_main",
    "llm_fallback",
  ]) {
    assert.match(javascript, new RegExp(`${field}:`));
  }

  const start = javascript.indexOf("function renderProviderFlags");
  const end = javascript.indexOf("function qualificationLabel", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  assert.doesNotMatch(javascript.slice(start, end), /innerHTML\s*=/);
});

test("desktop preferences use the Vox Agent namespace with a legacy migration", () => {
  assert.match(javascript, /backendUrl: "voxagent\.backendUrl"/);
  assert.match(javascript, /function migrateLegacyPreferences\(\)/);
  assert.match(javascript, /key\.replace\("voxagent\.", "voxlog2\."\)/);
  assert.doesNotMatch(html, /VoxLog/i);
});
