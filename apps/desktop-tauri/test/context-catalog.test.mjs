// T39b: every status and error the Rust side can send has Chinese and
// English copy, and no server or exception text reaches the page.
// The labels are read from the Rust sources, so a new label without copy
// fails here.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ACCOUNT_ERRORS, accountError } from "../src/context/account.js";
import { LANGUAGES, catalogCoverage, composerMessage, describe } from "../src/context/status-copy.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const commands = read("../src-tauri/src/context_commands.rs");
const account = read("../src-tauri/src/context_account.rs");

/**
 * `Pattern => "label",` arms inside the named Rust function. Strict: every
 * arm must be a plain label or a nested `match`, so a label written any other
 * way fails here instead of being skipped.
 */
function labels(fn, source = commands) {
  const start = source.indexOf(`fn ${fn}(`);
  assert.ok(start >= 0, fn);
  const body = source.slice(start, source.indexOf("\n}\n", start));
  const found = [];
  for (const line of body.split("\n")) {
    if (!line.includes("=>")) continue;
    const plain = line.match(/=> "([a-z-]+)",\s*$/);
    if (plain) found.push(plain[1]);
    else if (!/=> match \w+ \{\s*$/.test(line)) throw new Error(`unparsed arm in ${fn}: ${line.trim()}`);
  }
  return found;
}

/**
 * Variants of a fieldless Rust enum, as serde kebab-case strings. Strict:
 * only doc comments and `Variant,` lines; data-carrying variants (server
 * text in IPC errors) and serde renames fail.
 */
function variants(source, name) {
  const start = source.indexOf(`pub enum ${name} {`);
  assert.ok(start >= 0, name);
  const prefix = source.slice(Math.max(0, start - 200), start);
  assert.match(prefix, /#\[serde\(rename_all = "kebab-case"\)\]\s*$/, `${name} must serialize kebab-case`);
  const body = source.slice(source.indexOf("{", start) + 1, source.indexOf("\n}\n", start));
  const found = [];
  for (const line of body.split("\n")) {
    const text = line.trim();
    if (!text || text.startsWith("//")) continue;
    const variant = text.match(/^([A-Z][A-Za-z]+),$/);
    if (!variant) throw new Error(`unparsed line in ${name}: ${text}`);
    found.push(variant[1].replace(/[A-Z]/g, (c, i) => (i ? "-" : "") + c.toLowerCase()));
  }
  return found;
}

test("the Rust parsers fail loudly instead of skipping what they cannot read", () => {
  const mutated = commands.replace('=> "poll-ask-sign-in",', '=> { "missing-copy" }');
  assert.notEqual(mutated, commands);
  assert.throws(() => labels("next_label", mutated), /unparsed arm/);
  const withData = commands.replace("    NotConfigured,\n", "    NotConfigured,\n    ServerSaid(String),\n");
  assert.throws(() => variants(withData, "CommandError"), /unparsed line/);
  const renamed = commands.replace("    NotConfigured,\n", '    #[serde(rename = "x")]\n    NotConfigured,\n');
  assert.throws(() => variants(renamed, "CommandError"), /unparsed line/);
});

const coverage = catalogCoverage();

test("every next-step label is described or explicitly deferred to the server state", () => {
  const sent = labels("next_label");
  assert.ok(sent.length >= 15);
  for (const label of sent) {
    assert.ok(coverage.next.includes(label) || coverage.deferredNext.includes(label), `no copy for next=${label}`);
  }
  for (const label of coverage.deferredNext) assert.ok(sent.includes(label), `stale deferral ${label}`);
});

test("every server state and dispatch phase has copy", () => {
  for (const label of labels("state_label")) {
    assert.ok(coverage.serverState.includes(label) || coverage.deferredServerState.includes(label), `serverState=${label}`);
  }
  for (const label of labels("phase_label")) {
    assert.ok(coverage.phase.includes(label), `phase=${label}`);
  }
});

test("every IPC error code has composer copy in both languages", () => {
  const codes = variants(commands, "CommandError");
  assert.ok(codes.length >= 16);
  for (const code of codes) {
    assert.ok(coverage.composer.includes(code), `no composer copy for ${code}`);
    for (const lang of LANGUAGES) {
      const text = composerMessage({ status: "failed", code }, { lang });
      assert.ok(text && !text.includes("{"), `${code} ${lang}`);
    }
  }
  const accountCodes = variants(account, "AccountError");
  assert.deepEqual([...accountCodes].sort(), [...ACCOUNT_ERRORS].sort());
  for (const code of accountCodes) for (const lang of LANGUAGES) assert.ok(accountError(code, lang));
});

test("described statuses differ between languages and never fall back silently", () => {
  for (const dto of [
    { phase: "accepted" },
    { phase: "reserved" },
    { next: "poll-ask-administrator-recovery", serverState: "unknown" },
    { serverState: "local-unknown", phase: "dispatching" },
  ]) {
    const en = describe(dto, "en");
    const zh = describe(dto, "zh");
    assert.notEqual(en.label, "Status unavailable", JSON.stringify(dto));
    assert.notEqual(en.label, zh.label, JSON.stringify(dto));
  }
});

test("unknown codes fall back to a generic message, never to the code or a server body", () => {
  const body = '{"detail":"Traceback: secret"}';
  for (const lang of LANGUAGES) {
    const text = composerMessage({ status: "failed", code: body }, { lang });
    assert.ok(!text.includes("Traceback") && !text.includes("detail"));
    assert.ok(!accountError(body, lang).includes("Traceback"));
  }
});

test("page code never writes an exception or response body into the page", () => {
  for (const file of ["ui.js", "account.js", "timeline.js", "status-copy.js"]) {
    const source = read(`../src/context/${file}`);
    assert.ok(!/\.message\b/.test(source), `${file} reads error.message`);
    for (const line of source.split("\n")) {
      if (/textContent\s*=/.test(line)) {
        const code = line.replace(/"[^"]*"/g, '""');
        assert.ok(!/\berror\b/.test(code), `${file}: ${line.trim()}`);
      }
    }
  }
});
