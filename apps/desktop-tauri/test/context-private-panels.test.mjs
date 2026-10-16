// T18b-T23b page modules: private inbox and weekly draft panels.
import assert from "node:assert/strict";
import test from "node:test";

import { createPrivatePanel, recentWindow } from "../src/context/private-panel.js";
import { createWeeklyPanel, currentMonday } from "../src/context/weekly-panel.js";
import { FakeDocument } from "./helpers/fake-dom.mjs";

const RECORD = {
  id: "0f8fad5b-d9cb-469f-a165-70867728950e",
  eventId: "pv_1",
  revision: 2,
  withdrawn: false,
  createdAt: "2026-10-08T01:02:03+00:00",
  revisionChangedAt: "2026-10-08T01:02:04+00:00",
  text: "synthetic private note",
  tags: ["question"],
  projectId: null,
  repositoryId: null,
};

function privateElements(doc) {
  const node = (tag) => doc.createElement(tag);
  const tags = ["decision", "question"].map((t) => {
    const box = node("input");
    box.dataset.tag = t;
    box.checked = false;
    return box;
  });
  return {
    text: Object.assign(node("textarea"), { value: "" }),
    tags,
    save: node("button"),
    refresh: node("button"),
    status: node("p"),
    list: node("ul"),
    empty: node("p"),
    omitted: Object.assign(node("p"), { hidden: true }),
    unavailable: Object.assign(node("p"), { hidden: true }),
  };
}

function scripted(responses) {
  const calls = [];
  const invoke = async (command, args) => {
    calls.push({ command, args });
    const reply = responses[command];
    if (reply instanceof Error) throw reply.payload;
    return typeof reply === "function" ? reply(args) : reply;
  };
  return { calls, invoke };
}

function refusal(error, detail) {
  return Object.assign(new Error(error), { payload: { error, detail } });
}

const buttons = (root) => root.all().filter((n) => n.tagName === "BUTTON");
const byText = (root, text) => buttons(root).find((b) => b.textContent === text);
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("the private list renders, and edits use the revision that was shown", async () => {
  const doc = new FakeDocument();
  const el = privateElements(doc);
  const { calls, invoke } = scripted({
    context_private_list: { items: [RECORD], nextCursor: null, omittedRecords: false },
    context_private_edit: { key: "request:pr_1", phase: "reserved" },
    context_private_step: { kind: "current", key: "request:pr_1", record: { ...RECORD, revision: 3, text: "edited" } },
  });
  const panel = createPrivatePanel({ doc, invoke, el, lang: () => "en" });
  await panel.refresh();
  assert.equal(el.list.children.length, 1);
  assert.match(el.list.text, /synthetic private note/);
  assert.match(el.list.text, /r2/);
  byText(el.list, "Edit").click();
  const box = el.list.all().find((n) => n.tagName === "TEXTAREA");
  assert.equal(doc.activeElement, box, "focus moves to the edit box");
  box.value = "edited";
  box.dispatch("input");
  byText(el.list, "Save edit").click();
  await settle();
  const edit = calls.find((c) => c.command === "context_private_edit");
  assert.deepEqual(edit.args.input, { recordId: RECORD.id, seenRevision: 2, text: "edited", tags: ["question"] });
  assert.match(el.list.text, /r3/);
});

test("a withdrawal needs a second, explicit click", async () => {
  const doc = new FakeDocument();
  const el = privateElements(doc);
  const { calls, invoke } = scripted({
    context_private_list: { items: [RECORD], nextCursor: null, omittedRecords: false },
    context_private_withdraw: { key: "request:pr_2", phase: "reserved" },
    context_private_step: { kind: "current", key: "request:pr_2", record: { ...RECORD, revision: 3, withdrawn: true, text: null, tags: [] } },
  });
  const panel = createPrivatePanel({ doc, invoke, el, lang: () => "en" });
  await panel.refresh();
  byText(el.list, "Withdraw").click();
  assert.ok(!calls.some((c) => c.command === "context_private_withdraw"), "first click only asks");
  assert.ok(byText(el.list, "Confirm withdraw"));
  byText(el.list, "Cancel").click();
  assert.ok(!byText(el.list, "Confirm withdraw"));
  byText(el.list, "Withdraw").click();
  byText(el.list, "Confirm withdraw").click();
  await settle();
  const call = calls.find((c) => c.command === "context_private_withdraw");
  assert.deepEqual(call.args.input, { recordId: RECORD.id, seenRevision: 2, confirm: true });
  assert.equal(el.list.children.length, 0, "a withdrawn note leaves the list");
});

test("clear() drops every private word and late replies, and tells Rust", async () => {
  const doc = new FakeDocument();
  const el = privateElements(doc);
  let release;
  const { calls, invoke } = scripted({
    context_private_list: () => new Promise((resolve) => { release = resolve; }),
    context_private_clear_view: null,
  });
  const panel = createPrivatePanel({ doc, invoke, el, lang: () => "en" });
  el.text.value = "unsaved private words";
  el.tags[0].checked = true;
  const pending = panel.refresh();
  panel.clear();
  release({ items: [RECORD], nextCursor: null, omittedRecords: false });
  await pending;
  assert.equal(el.text.value, "");
  assert.equal(el.tags[0].checked, false);
  assert.equal(el.list.children.length, 0, "a reply from before the clear is dropped");
  assert.deepEqual(panel.picks(), []);
  assert.ok(calls.some((c) => c.command === "context_private_clear_view"));
});

test("without a key store the panel says why, and saving keeps the text", async () => {
  const doc = new FakeDocument();
  const el = privateElements(doc);
  const { invoke } = scripted({ context_private_create: refusal("journal_unavailable") });
  createPrivatePanel({ doc, invoke, el, lang: () => "zh" });
  el.text.value = "keep me";
  el.save.click();
  await settle();
  assert.equal(el.unavailable.hidden, false);
  assert.match(el.status.textContent, /密钥存储/);
  assert.equal(el.text.value, "keep me");
});

function weeklyElements(doc) {
  const node = (tag) => doc.createElement(tag);
  return {
    week: Object.assign(node("input"), { value: "" }),
    load: node("button"),
    coverage: node("p"),
    list: node("ul"),
    compose: node("button"),
    draft: Object.assign(node("textarea"), { value: "" }),
    copy: node("button"),
    status: node("p"),
  };
}

const LOADED = {
  coverage: { week: "2026-10-05", timezone: "Asia/Shanghai", pages: 1, sources: 2, included: 2, excluded: 0, omittedRecords: false, truncated: false, statement: "all_pages_authorized_backups_only" },
  sources: [
    { eventId: "team-2", date: "2026-10-06", text: "team note 2", deliveryStatus: "delivered", excluded: false, repositoryId: null },
    { eventId: "team-1", date: "2026-10-07", text: "team note 1", deliveryStatus: "pending", excluded: false, repositoryId: null },
  ],
};

test("the weekly panel states coverage, keeps exclusions and passes private picks", async () => {
  const doc = new FakeDocument();
  const el = weeklyElements(doc);
  const { calls, invoke } = scripted({
    context_weekly_load: LOADED,
    context_weekly_exclude: ({ eventId }) => ({
      coverage: { ...LOADED.coverage, included: 1, excluded: 1 },
      sources: LOADED.sources.map((s) => ({ ...s, excluded: s.eventId === eventId })),
    }),
    context_weekly_compose: { text: "Weekly draft · 2026-10-05", privatePicks: 1 },
  });
  createWeeklyPanel({ doc, invoke, el, lang: () => "en", privatePicks: () => [RECORD.id] });
  assert.match(el.week.value, /^\d{4}-\d{2}-\d{2}$/);
  el.week.value = "2026-10-05";
  el.load.click();
  await settle();
  assert.match(el.coverage.textContent, /not a complete record of the week/);
  assert.doesNotMatch(el.coverage.textContent, /complete week\b(?! of)/);
  assert.equal(el.list.children.length, 2);
  assert.equal(el.compose.disabled, false);
  const box = el.list.children[1].children[0];
  box.checked = false;
  box.dispatch("change");
  await settle();
  assert.deepEqual(calls.find((c) => c.command === "context_weekly_exclude").args, { eventId: "team-1", excluded: true });
  assert.match(el.coverage.textContent, /1 excluded/);
  el.compose.click();
  await settle();
  assert.deepEqual(calls.find((c) => c.command === "context_weekly_compose").args.input, { lang: "en", privateRecordIds: [RECORD.id] });
  assert.equal(el.draft.hidden, false);
  assert.equal(el.copy.disabled, false);
});

test("a partial read cannot be drafted, a refused copy says why, and clear() drops everything", async () => {
  const doc = new FakeDocument();
  const el = weeklyElements(doc);
  const partial = { ...LOADED, coverage: { ...LOADED.coverage, statement: "partial", truncated: true } };
  let loadReply = partial;
  const { calls, invoke } = scripted({
    context_weekly_load: () => loadReply,
    context_weekly_compose: { text: "draft", privatePicks: 0, version: 3 },
    context_weekly_edit: 4,
    context_weekly_copy: { copied: false, blocked: "team_changed" },
    context_weekly_clear: null,
  });
  const panel = createWeeklyPanel({ doc, invoke, el, lang: () => "en", privatePicks: () => [] });
  el.load.click();
  await settle();
  assert.equal(el.compose.disabled, true);
  assert.match(el.coverage.textContent, /partial/);
  loadReply = LOADED;
  el.load.click();
  await settle();
  el.compose.click();
  await settle();
  el.draft.value = "my own words";
  el.copy.click();
  await settle();
  await settle();
  assert.equal(calls.find((c) => c.command === "context_weekly_edit").args.text, "my own words");
  assert.deepEqual(calls.find((c) => c.command === "context_weekly_copy").args, { version: 4 }, "the copy names the saved version");
  assert.match(el.status.textContent, /changed since review/);
  panel.clear();
  assert.equal(el.draft.value, "");
  assert.equal(el.draft.hidden, true);
  assert.equal(el.list.children.length, 0);
  assert.equal(el.copy.disabled, true);
  assert.ok(calls.some((c) => c.command === "context_weekly_clear"));
});

test("week and window helpers follow Asia/Shanghai and the server's +00:00 form", () => {
  // Sunday 2026-10-11 20:00 UTC is Monday 04:00 in Shanghai.
  assert.equal(currentMonday(new Date("2026-10-11T20:00:00Z")), "2026-10-12");
  assert.equal(currentMonday(new Date("2026-10-11T15:59:00Z")), "2026-10-05");
  const window = recentWindow(new Date("2026-10-10T12:00:00.500Z"));
  assert.match(window.start, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$/);
  assert.match(window.end, /\+00:00$/);
  assert.equal(Date.parse(window.end) - Date.parse(window.start), 7 * 86_400_000);
});


test("clear() empties detached rows and an edit survives a language re-render", async () => {
  const doc = new FakeDocument();
  const el = privateElements(doc);
  let language = "en";
  const { invoke } = scripted({
    context_private_list: { items: [RECORD], nextCursor: null, omittedRecords: false },
    context_private_clear_view: null,
    context_private_create: refusal("network"),
  });
  const panel = createPrivatePanel({ doc, invoke, el, lang: () => language });
  await panel.refresh();
  byText(el.list, "Edit").click();
  const box = el.list.all().find((n) => n.tagName === "TEXTAREA");
  box.value = "half-written edit";
  box.dispatch("input");
  language = "zh";
  panel.render();
  const again = el.list.all().find((n) => n.tagName === "TEXTAREA");
  assert.equal(again.value, "half-written edit", "a re-render keeps the edit");
  assert.equal(box.value, "", "the replaced node was emptied");
  // A status line follows the language.
  el.text.value = "x";
  el.save.click();
  await settle();
  assert.match(el.status.textContent, /无法连接/);
  language = "en";
  panel.render();
  assert.match(el.status.textContent, /No connection/);
  // After clear, nothing that was on screen still holds the text.
  const kept = el.list.all();
  panel.clear();
  assert.ok(kept.every((n) => !String(n.textContent).includes("synthetic") && !String(n.value ?? "").includes("half-written")));
});

test("a copy never follows a refused edit or a clear, and runs one at a time", async () => {
  const doc = new FakeDocument();
  const el = weeklyElements(doc);
  let editReply = null;
  let releaseEdit;
  const { calls, invoke } = scripted({
    context_weekly_load: LOADED,
    context_weekly_compose: { text: "draft", privatePicks: 0 },
    context_weekly_edit: () => (editReply ? Promise.reject(editReply) : new Promise((r) => { releaseEdit = r; })),
    context_weekly_copy: { copied: true, blocked: null },
    context_weekly_clear: null,
  });
  const panel = createWeeklyPanel({ doc, invoke, el, lang: () => "en", privatePicks: () => [] });
  el.load.click();
  await settle();
  el.compose.click();
  await settle();
  // One at a time: a second click while the first copy is running does nothing.
  el.copy.click();
  el.copy.click();
  await settle();
  assert.equal(calls.filter((c) => c.command === "context_weekly_edit").length, 1);
  // Cleared while the edit is being saved: no copy follows.
  panel.clear();
  releaseEdit(7);
  await settle();
  assert.ok(!calls.some((c) => c.command === "context_weekly_copy"));
  // A refused edit stops the copy and says why.
  el.load.click();
  await settle();
  el.compose.click();
  await settle();
  editReply = { error: "draft", detail: "too_long" };
  el.copy.click();
  await settle();
  await settle();
  assert.ok(!calls.some((c) => c.command === "context_weekly_copy"));
  assert.match(el.status.textContent, /too long/);
});


test("scrub() empties nodes through the standard DOM API, and a draft replaced during the edit save is not copied", async () => {
  const { scrub } = await import("../src/context/private-panel.js");
  // A standard-shaped element: querySelectorAll, no fake-only helpers.
  const leaf = { textContent: "secret", value: "draft secret" };
  const root = { textContent: "outer", querySelectorAll: (sel) => (sel === "*" ? [leaf] : []) };
  scrub(root);
  assert.equal(leaf.textContent, "");
  assert.equal(leaf.value, "");
  assert.equal(root.textContent, "");

  const doc = new FakeDocument();
  const el = weeklyElements(doc);
  let releaseEdit;
  let drafts = 0;
  const { calls, invoke } = scripted({
    context_weekly_load: LOADED,
    context_weekly_compose: () => ({ text: `draft ${++drafts}`, privatePicks: 0 }),
    context_weekly_edit: () => new Promise((r) => { releaseEdit = r; }),
    context_weekly_copy: { copied: true, blocked: null },
  });
  createWeeklyPanel({ doc, invoke, el, lang: () => "en", privatePicks: () => [] });
  el.load.click();
  await settle();
  el.compose.click();
  await settle();
  el.copy.click();
  await settle();
  el.compose.click(); // draft 2 replaces draft 1 while its edit is saving
  await settle();
  releaseEdit(null);
  await settle();
  assert.ok(!calls.some((c) => c.command === "context_weekly_copy"), "the earlier click does not copy the new draft");
});

test("after saving an edit, focus lands on the updated row's Edit button", async () => {
  const doc = new FakeDocument();
  const el = privateElements(doc);
  const { invoke } = scripted({
    context_private_list: { items: [RECORD], nextCursor: null, omittedRecords: false },
    context_private_edit: { key: "request:pr_1", phase: "reserved" },
    context_private_step: { kind: "current", key: "request:pr_1", record: { ...RECORD, revision: 3 } },
  });
  const panel = createPrivatePanel({ doc, invoke, el, lang: () => "en" });
  await panel.refresh();
  byText(el.list, "Edit").click();
  byText(el.list, "Save edit").click();
  await settle();
  const editButton = byText(el.list, "Edit");
  assert.equal(doc.activeElement, editButton);
  assert.ok(el.list.contains(editButton), "focus is on a node in the current list");
});


test("typing during a copy stops that copy, and the draft is read-only while it runs", async () => {
  const doc = new FakeDocument();
  const el = weeklyElements(doc);
  let releaseEdit;
  const { calls, invoke } = scripted({
    context_weekly_load: LOADED,
    context_weekly_compose: { text: "draft A", privatePicks: 0 },
    context_weekly_edit: () => new Promise((r) => { releaseEdit = r; }),
    context_weekly_copy: { copied: true, blocked: null },
  });
  createWeeklyPanel({ doc, invoke, el, lang: () => "en", privatePicks: () => [] });
  el.load.click();
  await settle();
  el.compose.click();
  await settle();
  el.copy.click();
  await settle();
  assert.equal(el.draft.readOnly, true);
  el.draft.value = "draft B";
  el.draft.dispatch("input");
  releaseEdit(null);
  await settle();
  assert.ok(!calls.some((c) => c.command === "context_weekly_copy"));
  assert.equal(el.draft.readOnly, false);
});


test("compose and load are disabled while a copy runs", async () => {
  const doc = new FakeDocument();
  const el = weeklyElements(doc);
  let releaseEdit;
  const { invoke } = scripted({
    context_weekly_load: LOADED,
    context_weekly_compose: { text: "draft A", privatePicks: 0, version: 1 },
    context_weekly_edit: () => new Promise((r) => { releaseEdit = r; }),
    context_weekly_copy: { copied: true, blocked: null },
  });
  createWeeklyPanel({ doc, invoke, el, lang: () => "en", privatePicks: () => [] });
  el.load.click();
  await settle();
  el.compose.click();
  await settle();
  el.copy.click();
  await settle();
  assert.equal(el.compose.disabled, true);
  assert.equal(el.load.disabled, true);
  releaseEdit(2);
  await settle();
  await settle();
  assert.equal(el.compose.disabled, false);
  assert.equal(el.load.disabled, false);
});


test("edits name the draft version they were written on, and no copy starts while a compose is pending", async () => {
  const doc = new FakeDocument();
  const el = weeklyElements(doc);
  let releaseCompose;
  let composeCount = 0;
  const { calls, invoke } = scripted({
    context_weekly_load: LOADED,
    context_weekly_compose: () => {
      composeCount += 1;
      if (composeCount === 1) return { text: "draft A", privatePicks: 1, version: 5 };
      return new Promise((r) => { releaseCompose = r; });
    },
    context_weekly_edit: 6,
    context_weekly_copy: { copied: true, blocked: null },
  });
  createWeeklyPanel({ doc, invoke, el, lang: () => "en", privatePicks: () => [] });
  el.load.click();
  await settle();
  el.compose.click();
  await settle();
  el.compose.click(); // re-compose B is pending
  await settle();
  assert.equal(el.copy.disabled, true);
  el.copy.click();
  await settle();
  assert.ok(!calls.some((c) => c.command === "context_weekly_edit"), "no edit of A while B is being composed");
  releaseCompose({ text: "draft B", privatePicks: 0, version: 7 });
  await settle();
  el.copy.click();
  await settle();
  await settle();
  assert.deepEqual(calls.find((c) => c.command === "context_weekly_edit").args, { text: "draft B", version: 7 });
  assert.deepEqual(calls.find((c) => c.command === "context_weekly_copy").args, { version: 6 });
});


test("an open edit is saved against the revision it was opened on, even after a refresh", async () => {
  const doc = new FakeDocument();
  const el = privateElements(doc);
  let listed = RECORD;
  const { calls, invoke } = scripted({
    context_private_list: () => ({ items: [listed], nextCursor: null, omittedRecords: false }),
    context_private_edit: refusal("stale_revision"),
  });
  const panel = createPrivatePanel({ doc, invoke, el, lang: () => "en" });
  await panel.refresh();
  byText(el.list, "Edit").click();
  const box = el.list.all().find((n) => n.tagName === "TEXTAREA");
  box.value = "based on r2";
  box.dispatch("input");
  listed = { ...RECORD, revision: 3, text: "changed elsewhere" };
  await panel.refresh();
  byText(el.list, "Save edit").click();
  await settle();
  const edit = calls.find((c) => c.command === "context_private_edit");
  assert.equal(edit.args.input.seenRevision, 2, "never the newer revision the user did not edit");
  assert.equal(edit.args.input.text, "based on r2");
  assert.match(el.status.textContent, /changed since you opened it/);
  const again = el.list.all().find((n) => n.tagName === "TEXTAREA");
  assert.equal(again.value, "based on r2", "the edit text is kept");
});
