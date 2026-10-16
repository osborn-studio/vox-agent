// T28a: a provider is shown as qualified (green) only with probe evidence.
// An installed local runtime or a stored credential is "not verified".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { providerBadge } from "../src/settings/provider-badge.js";

const base = { readiness: "missing_runtime", probe_status: "not_run", active_roles: [], transport: "local" };

test("only a passed probe is green and says qualified", () => {
  assert.deepEqual(providerBadge({ ...base, readiness: "ready", probe_status: "passed" }), {
    tone: "live",
    label: "qualified",
  });
  assert.deepEqual(providerBadge({ ...base, readiness: "configured_unverified", probe_status: "passed", transport: "cloud" }), {
    tone: "live",
    label: "qualified",
  });
});

test("an installed runtime or stored credential without a probe is not verified", () => {
  assert.deepEqual(providerBadge({ ...base, readiness: "ready" }), { tone: "warn", label: "installed · not verified" });
  assert.deepEqual(providerBadge({ ...base, readiness: "configured_unverified", transport: "cloud" }), {
    tone: "warn",
    label: "configured · not verified",
  });
});

test("a failed probe is red and says so", () => {
  assert.deepEqual(providerBadge({ ...base, readiness: "ready", probe_status: "failed" }), {
    tone: "danger",
    label: "probe failed",
  });
});

test("deferred and missing states keep their words", () => {
  assert.deepEqual(providerBadge({ ...base, readiness: "not_implemented" }), { tone: "neutral", label: "not implemented" });
  assert.deepEqual(providerBadge({ ...base, readiness: "missing_credential", transport: "cloud" }), {
    tone: "danger",
    label: "missing credential",
  });
  assert.deepEqual(providerBadge({ ...base, readiness: "missing_runtime", active_roles: ["main"] }), {
    tone: "warn",
    label: "missing runtime",
  });
  assert.deepEqual(providerBadge({ ...base, readiness: "platform_unavailable" }), {
    tone: "neutral",
    label: "platform unavailable",
  });
});

test("no state other than a passed probe can produce the qualified label", () => {
  for (const readiness of ["ready", "configured_unverified", "missing_credential", "missing_runtime", "not_implemented", "platform_unavailable", "weird"]) {
    for (const probe of ["not_run", "failed", "not_applicable", undefined]) {
      const badge = providerBadge({ ...base, readiness, probe_status: probe });
      assert.notEqual(badge.label, "qualified", `${readiness}/${probe}`);
      assert.notEqual(badge.label, "ready", "the word ready is never shown");
      assert.ok(badge.tone !== "live", `${readiness}/${probe} is not green`);
    }
  }
});

test("main.js renders provider badges through the module", () => {
  const main = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
  assert.match(main, /providerBadge\(provider\)/);
  assert.doesNotMatch(main, /function providerQualificationKind/);
});
