// T25: conversation history coverage and outbox copy match the Rust codes.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  COVERAGE,
  HISTORY_ERRORS,
  OUTBOX,
  canRetry,
  coverageLine,
  historyError,
  outboxLine,
} from "../src/context/conversation.js";

const rust = readFileSync(new URL("../src-tauri/src/context_conversation.rs", import.meta.url), "utf8");
const snake = (name) => name.replace(/[A-Z]/g, (c, i) => (i ? "_" : "") + c.toLowerCase());

function variants(enumName) {
  const start = rust.indexOf(`pub enum ${enumName} {`);
  assert.ok(start >= 0, enumName);
  const body = rust.slice(start, rust.indexOf("\n}", start));
  return [...body.matchAll(/^\s{4}([A-Z][A-Za-z]+)[,(]/gm)].map((m) => snake(m[1]));
}

test("every history error and coverage code has text in both languages", () => {
  assert.deepEqual(Object.keys(HISTORY_ERRORS).sort(), variants("HistoryError").sort());
  const coverage = [...rust.matchAll(/=> "([a-z_]+)",\n/g)].map((m) => m[1]).filter((c) => c in COVERAGE);
  assert.deepEqual(coverage.sort(), Object.keys(COVERAGE).sort());
  for (const table of [COVERAGE, HISTORY_ERRORS, OUTBOX]) {
    for (const [key, text] of Object.entries(table)) {
      assert.ok(text.en && text.zh && text.en !== text.zh, key);
    }
  }
});

test("every outbox state and receipt status has a line", () => {
  const states = variants("EntryState");
  const statuses = variants("SendStatus");
  for (const state of states) {
    if (state === "accepted" || state === "final") {
      for (const status of statuses) {
        if (state === "accepted" && ["sent", "failed", "membership_lost"].includes(status)) continue;
        if (state === "final" && ["pending", "sending"].includes(status)) continue;
        assert.ok(`${state}.${status}` in OUTBOX, `${state}.${status}`);
      }
    } else {
      assert.ok(state in OUTBOX, state);
    }
  }
});

test("a group timeline never claims the group's full history", () => {
  assert.match(coverageLine("own_submissions_only", "en"), /not the group's history/);
  assert.match(coverageLine("own_submissions_only", "zh"), /不是群历史/);
  assert.match(coverageLine("shared_since_policy", "en"), /earlier messages are not shown/);
  // Unknown coverage falls back to the narrowest claim.
  assert.equal(coverageLine("full_group", "en"), coverageLine("own_submissions_only", "en"));
  assert.equal(historyError("SOMETHING", "zh"), historyError("invalid_response", "zh"));
});

test("unknown delivery is stated, and only safe states offer a retry", () => {
  assert.match(outboxLine({ state: "final", status: "unknown" }, "en"), /check Feishu/);
  assert.match(outboxLine({ state: "not_confirmed" }, "zh"), /不会重复/);
  assert.equal(outboxLine({ state: "weird" }, "en"), outboxLine({ state: "not_confirmed" }, "en"));
  assert.deepEqual(
    ["queued", "posting", "not_confirmed", "accepted", "final", "conflict", "rejected", "blocked"].filter((s) =>
      canRetry({ state: s }),
    ),
    ["not_confirmed", "blocked"],
  );
});
