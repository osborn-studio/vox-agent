// T06a: a credential-store failure must render as "unavailable", never as
// "not configured", on every status path until a healthy snapshot arrives.
// Runs the real main.js functions against a stub DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");

function extract(name) {
  const marker = source.indexOf(`function ${name}(`);
  assert.ok(marker >= 0, `could not locate ${name} in main.js`);
  const start = source.startsWith("async ", marker - 6) ? marker - 6 : marker;
  const end = source.indexOf("\n}\n", marker);
  assert.ok(end > marker, `could not find the end of ${name}`);
  return source.slice(start, end + 3);
}

const FUNCTIONS = [
  "setBadge", "credentialAwareLabel", "setProviderState", "setSettingsState", "credentialStoreUnavailable",
  "renderProviderFlags", "settingsSavedState", "renderAppSettings", "testProviderSettings",
];

function harness(connectivity = { ready: true, configured_provider_count: 1 }, { reject = false } = {}) {
  const nodes = {};
  const node = () => ({
    value: "", checked: false, disabled: false, textContent: "", className: "", children: [],
    classList: { add() {}, remove() {} },
    replaceChildren(...items) { this.children = items; },
    append(item) { this.children.push(item); },
  });
  const elements = new Proxy({}, { get: (_, key) => (nodes[key] ??= node()) });
  const context = {
    elements, isTauri: true, currentConnectivity: null,
    document: { createElement: () => node() },
    renderProfileOptions() {}, renderProfileSummary() {}, renderProviderRecommendation() {},
    renderOnboardingChecks() {}, renderSettingsChecks() {}, joinRuleList() { return ""; },
    setOnboardingState() {}, log() {},
    invoke: async () => {
      if (reject) throw new Error("bridge down");
      return connectivity;
    },
  };
  vm.createContext(context);
  vm.runInContext("var currentSettingsSnapshot = null;", context);
  for (const name of FUNCTIONS) vm.runInContext(extract(name), context);
  const badge = (key) => [nodes[key].textContent, nodes[key].className.replace("badge ", "")];
  const pills = () => nodes.providerFlags.children.map((pill) => pill.textContent);
  return { context, badge, pills };
}

const baseSnapshot = {
  provider_flags: { openai: false, doubao: false },
  backend_api_token_present: false,
  engine_api_token_present: false,
  notion_sync_enabled: true,
  notion_data_source_id: "source",
  notion_token_present: false,
  onboarding_completed: true,
};
const failing = { ...baseSnapshot, credential_store_error: "credential store locked or access denied" };

test("credential store failure is shown as unavailable, not as unconfigured", () => {
  const { context, badge, pills } = harness();
  context.renderAppSettings(failing);
  assert.deepEqual(badge("providerStatus"), ["Credentials Unavailable", "danger"]);
  assert.deepEqual(badge("settingsStatus"), ["Credential Store Unavailable", "danger"]);
  assert.ok(pills().every((text) => text.endsWith(": unknown")), pills().join(","));
});

test("a later connectivity check cannot overwrite the unavailable state", async () => {
  const { context, badge } = harness({ ready: true, configured_provider_count: 1 });
  context.renderAppSettings(failing);
  await context.testProviderSettings({ silent: true });
  assert.deepEqual(badge("providerStatus"), ["Credentials Unavailable", "danger"]);
  assert.deepEqual(badge("settingsStatus"), ["Credential Store Unavailable", "danger"]);
});

test("a healthy snapshot clears the unavailable state", async () => {
  const { context, badge, pills } = harness({ ready: true, configured_provider_count: 1 });
  context.renderAppSettings(failing);
  context.renderAppSettings({ ...baseSnapshot, provider_flags: { openai: true, doubao: false } });
  assert.deepEqual(badge("providerStatus"), ["1 Configured", "live"]);
  assert.deepEqual(badge("settingsStatus"), ["Local Only", "neutral"]);
  assert.ok(pills().includes("openai: configured"), pills().join(","));
  assert.ok(pills().includes("doubao: missing"), pills().join(","));
  assert.ok(!pills().some((text) => text.endsWith(": unknown")), pills().join(","));
  await context.testProviderSettings({ silent: true });
  assert.deepEqual(badge("settingsStatus"), ["Routing Configured", "live"]);
});

test("a save never reports plain Saved while credentials could not be read", () => {
  const { context } = harness();
  assert.deepEqual(
    [...context.settingsSavedState({ credential_store_error: "credential store backend failure" })],
    ["Saved; Credentials Not Loaded", "danger"],
  );
  assert.deepEqual([...context.settingsSavedState({})], ["Saved", "live"]);
});

test("operation failures stay visible while the credential store is unavailable", async () => {
  const { context, badge } = harness(undefined, { reject: true });
  context.renderAppSettings(failing);
  await context.testProviderSettings({ silent: true });
  assert.deepEqual(badge("providerStatus"), ["Connection Failed; Credentials Unavailable", "danger"]);
  assert.deepEqual(badge("settingsStatus"), ["Test Failed; Credentials Unavailable", "danger"]);
  context.setSettingsState("Save Failed", "danger");
  assert.deepEqual(badge("settingsStatus"), ["Save Failed; Credentials Unavailable", "danger"]);
});

test("operation failures use the failed status form when the store is healthy", async () => {
  const { context, badge } = harness(undefined, { reject: true });
  context.renderAppSettings(baseSnapshot);
  await context.testProviderSettings({ silent: true });
  assert.deepEqual(badge("providerStatus"), ["Connection Failed", "danger"]);
  assert.deepEqual(badge("settingsStatus"), ["Test Failed", "danger"]);
});

test("onboarding never claims provider keys are stored when the store is unreadable", () => {
  const cards = [];
  const context = {
    elements: new Proxy({}, { get: (_, key) => ({ textContent: key === "permissionStatus" ? "Granted" : "", innerHTML: "", append() {} }) }),
    createReadinessCard(check) { cards.push(check); return {}; },
    currentConnectivity: null,
  };
  vm.createContext(context);
  vm.runInContext("var currentSettingsSnapshot = null;", context);
  for (const name of ["credentialStoreUnavailable", "renderOnboardingChecks"]) vm.runInContext(extract(name), context);
  context.renderOnboardingChecks(failing, { configured_provider_count: 1, backend_url_reachable: true });
  const provider = cards.find((card) => card.label === "Provider Key");
  assert.equal(provider.status, "fail");
  assert.match(provider.message, /unavailable/i);

  cards.length = 0;
  context.renderOnboardingChecks({ ...baseSnapshot }, { configured_provider_count: 1, backend_url_reachable: true });
  assert.equal(cards.find((card) => card.label === "Provider Key").status, "ok");
});
