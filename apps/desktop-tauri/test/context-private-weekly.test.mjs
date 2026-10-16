// T18a / T21: private mutation phases and weekly coverage copy match Rust.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  COPY_BLOCKED,
  COVERAGE,
  DRAFT_ERRORS,
  STALE_REVISION,
  PRIVATE_PHASES,
  RECHECK,
  SOURCE_ERRORS,
  WEEK_BOUNDARY,
  canCopy,
  coverageLine,
  privatePhase,
  sourceError,
} from "../src/context/private-weekly.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const privateRs = read("../src-tauri/src/context_private.rs");
const sourcesRs = read("../src-tauri/src/context_sources.rs");
const snake = (name) => name.replace(/[A-Z]/g, (c, i) => (i ? "_" : "") + c.toLowerCase());

function variants(source, enumName) {
  const start = source.indexOf(`pub enum ${enumName} {`);
  assert.ok(start >= 0, enumName);
  const body = source.slice(start, source.indexOf("\n}", start));
  return [...body.matchAll(/^\s{4}([A-Z][A-Za-z]+)[,( {]/gm)].map((m) => snake(m[1]));
}

test("every Rust phase, coverage statement and error has text in both languages", () => {
  assert.deepEqual(Object.keys(PRIVATE_PHASES).sort(), variants(privateRs, "Phase").sort());
  assert.deepEqual(Object.keys(COVERAGE).sort(), variants(sourcesRs, "CoverageStatement").sort());
  assert.deepEqual(Object.keys(SOURCE_ERRORS).sort(), variants(sourcesRs, "SourceError").sort());
  assert.deepEqual(Object.keys(RECHECK).sort(), variants(sourcesRs, "Recheck").sort());
  for (const table of [PRIVATE_PHASES, COVERAGE, SOURCE_ERRORS, RECHECK, { WEEK_BOUNDARY }]) {
    for (const [key, text] of Object.entries(table)) {
      assert.ok(text.en && text.zh && text.en !== text.zh, key);
    }
  }
});

test("no coverage line claims a complete week", () => {
  for (const text of Object.values(COVERAGE)) {
    assert.doesNotMatch(text.en, /complete week|full week|all (your )?work/i);
    assert.doesNotMatch(text.zh, /完整一周|全部工作/);
  }
  assert.match(coverageLine("all_pages_authorized_backups_only", "en"), /not a complete record/);
  assert.match(coverageLine("all_pages_authorized_backups_only", "zh"), /不是本周完整工作记录/);
  assert.equal(coverageLine("complete_week", "en"), coverageLine("partial", "en"));
  assert.match(WEEK_BOUNDARY.zh, /Asia\/Shanghai/);
});

test("phases that keep the user's text say so, and copy needs an unchanged recheck", () => {
  for (const phase of ["conflict", "rejected", "blocked"]) {
    assert.match(privatePhase(phase, "en"), /text is kept/);
    assert.match(privatePhase(phase, "zh"), /文字已保留/);
  }
  assert.match(privatePhase("unknown", "en"), /never a second/);
  assert.equal(privatePhase("whatever", "en"), privatePhase("unknown", "en"));
  assert.equal(sourceError("SOMETHING", "zh"), sourceError("invalid_response", "zh"));
  assert.deepEqual(["unchanged", "changed", "cannot_confirm"].filter(canCopy), ["unchanged"]);
});

const draftRs = read("../src-tauri/src/context_weekly_draft.rs");

test("every copy-gate reason and draft error has text in both languages", () => {
  assert.deepEqual(Object.keys(COPY_BLOCKED).sort(), variants(draftRs, "CopyBlocked").sort());
  assert.deepEqual(Object.keys(DRAFT_ERRORS).sort(), variants(draftRs, "DraftError").sort());
  for (const table of [COPY_BLOCKED, DRAFT_ERRORS, { STALE_REVISION }]) {
    for (const [key, text] of Object.entries(table)) {
      assert.ok(text.en && text.zh && text.en !== text.zh, key);
    }
  }
});

import { PRIVATE_ERRORS, WEEKLY_ERRORS, PANEL } from "../src/context/private-weekly.js";
const serviceRs = read("../src-tauri/src/context_private_service.rs");
const weeklyRs = read("../src-tauri/src/context_weekly_commands.rs");

test("every private service and weekly command error has text in both languages", () => {
  assert.deepEqual(Object.keys(PRIVATE_ERRORS).sort(), variants(serviceRs, "PrivateServiceError").sort());
  assert.deepEqual(Object.keys(WEEKLY_ERRORS).sort(), variants(weeklyRs, "WeeklyError").sort());
  for (const table of [PRIVATE_ERRORS, WEEKLY_ERRORS, PANEL.tags, PANEL.delivery]) {
    for (const [key, text] of Object.entries(table)) {
      assert.ok(text.en && text.zh && text.en !== text.zh, key);
    }
  }
  const sourcesRs = read("../src-tauri/src/context_sources.rs");
  assert.deepEqual(Object.keys(PANEL.delivery).sort(), variants(sourcesRs, "DeliveryStatus").sort());
});
