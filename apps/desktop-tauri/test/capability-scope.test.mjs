import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const lib = read("../src-tauri/src/lib.rs");
const build = read("../src-tauri/build.rs");
const main = JSON.parse(read("../src-tauri/capabilities/main.json"));
const hud = JSON.parse(read("../src-tauri/capabilities/voice-hud.json"));
const config = JSON.parse(read("../src-tauri/tauri.conf.json"));
const rustSources = readdirSync(new URL("../src-tauri/src/", import.meta.url), { recursive: true })
  .filter((name) => name.endsWith(".rs"))
  .map((name) => read(`../src-tauri/src/${name}`));

// What the HUD may ever be granted (plan C-03): its own events and two
// read-only/HUD commands. Everything else belongs to the main window only.
const HUD_WHITELIST = new Set([
  "core:event:allow-listen",
  "core:event:allow-unlisten",
  "allow-update-voice-hud",
  "allow-get-native-capture-status",
]);

function registered() {
  const block = lib.slice(lib.indexOf("generate_handler!["), lib.indexOf("])", lib.indexOf("generate_handler![")));
  return [...block.matchAll(/[a-z_]+::([a-z_]+)/g)].map((m) => m[1]);
}

function manifest() {
  const block = build.slice(build.indexOf("APP_COMMANDS"), build.indexOf("];"));
  return [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

const permission = (command) => `allow-${command.replaceAll("_", "-")}`;

test("every registered command is in the app ACL manifest and nothing else is", () => {
  const commands = registered();
  assert.ok(commands.length > 60);
  assert.deepEqual([...manifest()].sort(), [...commands].sort());
});

test("the main window is granted every app command", () => {
  assert.deepEqual(main.windows, ["main"]);
  for (const command of registered()) {
    assert.ok(main.permissions.includes(permission(command)), command);
  }
});

test("the voice HUD gets only whitelisted permissions and no Context or credential command", () => {
  assert.deepEqual(hud.windows, ["voice-hud"]);
  for (const granted of hud.permissions) {
    assert.ok(HUD_WHITELIST.has(granted), `HUD must not get ${granted}`);
  }
  for (const forbidden of [
    "allow-context-submit-record",
    "allow-context-list-records",
    "allow-context-sign-in",
    "allow-context-conversations",
    "allow-context-private-create",
    "allow-context-private-step",
    "allow-context-private-list",
    "allow-context-private-get",
    "allow-context-private-edit",
    "allow-context-private-withdraw",
    "allow-context-private-kept",
    "allow-context-private-dismiss",
    "allow-context-private-clear-view",
    "allow-context-weekly-load",
    "allow-context-weekly-exclude",
    "allow-context-weekly-compose",
    "allow-context-weekly-edit",
    "allow-context-weekly-copy",
    "allow-context-weekly-clear",
    "allow-save-app-settings",
    "allow-test-provider-settings",
    "allow-set-backend-base-url",
    "allow-inject-text",
  ]) {
    assert.ok(!hud.permissions.includes(forbidden), forbidden);
  }
});

test("no capability is granted to every window or to remote URLs", () => {
  for (const capability of [main, hud]) {
    assert.ok(!capability.windows.includes("*"));
    assert.equal(capability.remote, undefined);
  }
});

test("the CSP is fixed and forbids inline or eval'd script and foreign connections", () => {
  const csp = config.app.security.csp;
  const directives = Object.fromEntries(
    csp.split(";").map((d) => d.trim().split(/\s+/)).map(([name, ...values]) => [name, values]),
  );
  assert.deepEqual(directives["default-src"], ["'self'"]);
  assert.deepEqual(directives["script-src"], ["'self'"]);
  assert.deepEqual(directives["object-src"], ["'none'"]);
  assert.deepEqual(directives["base-uri"], ["'none'"]);
  assert.deepEqual(directives["frame-ancestors"], ["'none'"]);
  assert.deepEqual(directives["connect-src"], ["'self'", "ipc:", "http://ipc.localhost"]);
  assert.ok(!csp.includes("unsafe-eval"));
});

test("no event is broadcast to every window: the HUD can listen, so events are targeted", () => {
  for (const source of rustSources) {
    assert.ok(!/\.emit\(/.test(source), "use emit_to with an explicit window label");
  }
  const targets = rustSources.flatMap((source) => [...source.matchAll(/\.emit_to\(\s*("[^"]+"|[A-Z_]+)/g)].map((m) => m[1]));
  assert.ok(targets.length >= 6);
  for (const target of targets) {
    assert.ok(['"main"', "HUD_LABEL"].includes(target), target);
  }
});
