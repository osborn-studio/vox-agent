import assert from "node:assert/strict";
import test from "node:test";

import {
  COORDINATOR_TARGET,
  GROUP_TARGET,
  agentTarget,
  routeSubmission,
  targetKey,
} from "../src/context/targets.js";
import {
  MAX_TEXT_BYTES,
  createComposer,
  isSubmitShortcut,
  utf8Bytes,
  validateText,
} from "../src/context/composer.js";
import { catalogEntries, composerMessage, describe } from "../src/context/status-copy.js";
import { STRINGS, readLanguage, t } from "../src/context/i18n.js";
import {
  announcement,
  beginReveal,
  hideAllOriginals,
  hideOriginal,
  renderRecords,
  revealCurrent,
  showOriginal,
  showOriginalError,
  upsertRecord,
} from "../src/context/timeline.js";
import { createPaneController, paneForShortcut } from "../src/context/layout.js";
import { FakeDocument, memoryStorage } from "./helpers/fake-dom.mjs";

// ---------- T12 routing ----------

test("the default target is the group and the coordinator is an agent", () => {
  assert.equal(GROUP_TARGET.kind, "group");
  assert.equal(COORDINATOR_TARGET.kind, "agent");
  assert.notEqual(targetKey(GROUP_TARGET), targetKey(COORDINATOR_TARGET));
  assert.throws(() => agentTarget({ name: "no id" }));
});

test("each target reaches exactly one channel and never the other", () => {
  const group = routeSubmission(GROUP_TARGET, { text: "x", eventId: "ctx-1", destination: "team" });
  assert.equal(group.command, "context_submit_record");
  assert.deepEqual(group.args, { input: { eventId: "ctx-1", targetId: "team", text: "x" } });

  const agent = routeSubmission(agentTarget({ id: "reviewer" }), { text: "x", eventId: "ctx-1", destination: "team" });
  assert.equal(agent.command, "run_engine_command");
  assert.deepEqual(agent.args, { input: { text: "x", agentId: "reviewer", provider: "claude" } });
  assert.equal(JSON.stringify(agent).includes("ctx-1"), false, "agent route carries no Context fields");

  assert.equal(routeSubmission(COORDINATOR_TARGET, { text: "x" }).args.input.agentId, null);
  assert.deepEqual(routeSubmission({ kind: "other" }, { text: "x" }), { error: "unknown-target" });
  assert.deepEqual(routeSubmission(GROUP_TARGET, { text: "x", eventId: "e" }), { error: "no-destination" });
});

test("a project snapshot is sent as id and repository only", () => {
  const route = routeSubmission(GROUP_TARGET, {
    text: "x",
    eventId: "e",
    destination: "team",
    project: { id: "p1", repository: "studio/repo", path: "/Users/someone/repo" },
  });
  assert.deepEqual(route.args.input.project, { id: "p1", repository: "studio/repo" });
});

// ---------- T13 composer ----------

test("the limit is counted in UTF-8 bytes", () => {
  assert.equal(utf8Bytes("中"), 3);
  assert.deepEqual(validateText("中".repeat(2730)), { ok: true, bytes: 8190 });
  assert.equal(validateText("中".repeat(2731)).reason, "too-large");
  assert.equal(validateText("  \n").reason, "empty");
  assert.equal(MAX_TEXT_BYTES, 8192);
});

test("Enter during IME composition never submits", () => {
  assert.equal(isSubmitShortcut({ key: "Enter", ctrlKey: true }), true);
  assert.equal(isSubmitShortcut({ key: "Enter", ctrlKey: true, isComposing: true }), false);
  assert.equal(isSubmitShortcut({ key: "Enter", ctrlKey: true, keyCode: 229 }), false);
  assert.equal(isSubmitShortcut({ key: "Process", ctrlKey: true }), false);
  assert.equal(isSubmitShortcut({ key: "Enter" }), false, "plain Enter is a newline");
});

function spyInvoke(results) {
  const calls = [];
  const invoke = async (command, args) => {
    calls.push({ command, args });
    const next = results.shift();
    if (next instanceof Error || typeof next === "string") throw next;
    return next;
  };
  return { calls, invoke };
}

let counter = 0;
const ids = () => `ctx-${++counter}`;

test("saved is reported only after the journal commit returns, and the draft clears then", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const calls = [];
  const composer = createComposer({
    invoke: async (command, args) => {
      calls.push({ command, args });
      await gate;
      return { eventId: args.input.eventId, phase: "reserved", action: "may-post" };
    },
    makeEventId: ids,
  });
  composer.setDraft(GROUP_TARGET, "note");
  const pending = composer.submitContext(GROUP_TARGET, "note", { destination: "team" });
  assert.equal(composer.draft(GROUP_TARGET), "note", "nothing is cleared before the commit");
  release();
  const result = await pending;
  assert.equal(result.status, "saved-locally");
  assert.equal(result.cleared, true);
  assert.equal(composer.draft(GROUP_TARGET), "");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, "context_submit_record");
});

test("a late reply never clears a draft the user changed meanwhile", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const composer = createComposer({
    invoke: async (_c, args) => {
      await gate;
      return { eventId: args.input.eventId };
    },
    makeEventId: ids,
  });
  composer.setDraft(GROUP_TARGET, "first");
  const pending = composer.submitContext(GROUP_TARGET, "first", { destination: "team" });
  composer.setDraft(GROUP_TARGET, "first, edited");
  release();
  const result = await pending;
  assert.equal(result.cleared, false);
  assert.equal(composer.draft(GROUP_TARGET), "first, edited");
});

test("a failed save keeps the draft and a retry of the same text reuses its id", async () => {
  const { calls, invoke } = spyInvoke(["journal-unavailable", { eventId: "x" }]);
  const composer = createComposer({ invoke, makeEventId: ids });
  composer.setDraft(GROUP_TARGET, "note");
  const failed = await composer.submitContext(GROUP_TARGET, "note", { destination: "team" });
  assert.deepEqual([failed.status, failed.code], ["failed", "journal-unavailable"]);
  assert.equal(composer.draft(GROUP_TARGET), "note");
  await composer.submitContext(GROUP_TARGET, "note", { destination: "team" });
  assert.equal(calls[0].args.input.eventId, calls[1].args.input.eventId);
});

test("a conflict or an edited draft gets a fresh id", async () => {
  const { calls, invoke } = spyInvoke(["conflict", "busy", { eventId: "x" }]);
  const composer = createComposer({ invoke, makeEventId: ids });
  await composer.submitContext(GROUP_TARGET, "a", { destination: "team" });
  await composer.submitContext(GROUP_TARGET, "a", { destination: "team" });
  await composer.submitContext(GROUP_TARGET, "a, edited", { destination: "team" });
  const used = calls.map((c) => c.args.input.eventId);
  assert.notEqual(used[0], used[1], "after a conflict");
  assert.notEqual(used[1], used[2], "after editing");
});

test("the Context composer refuses agent targets and invalid text without any call", async () => {
  const { calls, invoke } = spyInvoke([]);
  const composer = createComposer({ invoke, makeEventId: ids });
  assert.equal((await composer.submitContext(COORDINATOR_TARGET, "x", { destination: "team" })).status, "unknown-target");
  assert.equal((await composer.submitContext(GROUP_TARGET, " ", { destination: "team" })).status, "empty");
  assert.equal((await composer.submitContext(GROUP_TARGET, "x".repeat(8193), { destination: "team" })).status, "too-large");
  assert.equal((await composer.submitContext(GROUP_TARGET, "x", {})).status, "no-destination");
  assert.equal(calls.length, 0);
});

test("unexpected error shapes become an internal code, never raw text", async () => {
  const { invoke } = spyInvoke([new Error("panicked at secret path /Users/x")]);
  const composer = createComposer({ invoke, makeEventId: ids });
  const result = await composer.submitContext(GROUP_TARGET, "x", { destination: "team" });
  assert.equal(result.code, "internal");
  assert.equal(composerMessage(result).includes("/Users"), false);
});

test("drafts are per target and can be cleared together", () => {
  const composer = createComposer({ invoke: async () => ({}), makeEventId: ids });
  composer.setDraft(GROUP_TARGET, "group text");
  composer.setDraft(COORDINATOR_TARGET, "agent text");
  assert.equal(composer.draft(GROUP_TARGET), "group text");
  assert.equal(composer.draft(COORDINATOR_TARGET), "agent text");
  composer.clearAll();
  assert.equal(composer.draft(GROUP_TARGET), "");
});

// ---------- T14 receipts ----------

const dto = (fields) => ({ eventId: "ctx-abc123", phase: "accepted", serverState: null, action: "poll", next: null, ...fields });

test("accepted is never shown as delivered", () => {
  const accepted = describe(dto({ serverState: "pending" }));
  assert.match(accepted.label, /not yet delivered/);
  assert.equal(accepted.tone, "running");
  const delivered = describe(dto({ serverState: "delivered", action: "final" }));
  assert.equal(delivered.label, "Delivered");
  assert.equal(delivered.tone, "done");
});

test("unknown and possibly-sent records are explained and offer a read-only check", () => {
  const unknown = describe(dto({ serverState: "unknown" }));
  assert.match(unknown.detail, /never sent twice/);
  assert.equal(unknown.canCheck, true);
  const verifying = describe(dto({ phase: "dispatching", action: "read-back-only" }));
  assert.match(verifying.detail, /will not be sent again/);
  assert.equal(verifying.canCheck, true);
  const local = describe(dto({ phase: "reserved", action: "may-post" }));
  assert.equal(local.canCheck, false);
  assert.match(local.label, /Saved on this device/);
});

test("statuses that need a person use the awaiting tone, and every status has a word and a glyph", () => {
  for (const next of ["user-sign-in", "user-check-delivery", "user-administrator-recovery", "poll-ask-check-delivery"]) {
    assert.equal(describe(dto({ next })).tone, "awaiting", next);
  }
  assert.equal(describe(dto({ serverState: "blocked" })).tone, "awaiting");
  for (const fields of [{ serverState: "rejected" }, { action: "quarantine" }, { next: "stop-conflict" }, { phase: "reserved" }]) {
    const copy = describe(dto(fields));
    assert.ok(copy.label && copy.glyph, JSON.stringify(fields));
  }
});

test("composer messages say the draft is kept on every failure", () => {
  for (const code of ["not-configured", "journal-unavailable", "quota-exceeded", "internal", "invalid-input"]) {
    const text = composerMessage({ status: "failed", code });
    assert.ok(text.length > 10, code);
  }
  assert.match(composerMessage({ status: "too-large", bytes: 9000 }), /9000 of 8192 bytes/);
  assert.match(composerMessage({ status: "saved-locally" }), /Saved on this device/);
});

test("the timeline upserts by id, newest first, and ignores malformed ids", () => {
  let records = upsertRecord([], dto({ eventId: "a" }));
  records = upsertRecord(records, dto({ eventId: "b" }));
  records = upsertRecord(records, dto({ eventId: "a", serverState: "delivered" }));
  assert.deepEqual(records.map((r) => r.eventId), ["b", "a"]);
  assert.equal(records[1].serverState, "delivered");
  assert.equal(upsertRecord(records, dto({ eventId: "../x" })), records);
});

test("repeated polls with the same status are not announced again", () => {
  const first = dto({ serverState: "pending" });
  assert.match(announcement(null, first), /not yet delivered/);
  assert.equal(announcement(first, dto({ serverState: "pending" })), null);
  assert.match(announcement(first, dto({ serverState: "delivered" })), /Delivered/);
});

test("rendered records use text only, show the check button only where allowed, and never send", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  const checked = [];
  renderRecords(doc, list, [dto({ eventId: "a", serverState: "unknown" }), dto({ eventId: "b", serverState: "delivered", action: "final" })], {
    onCheck: (eventId) => checked.push(eventId),
  });
  assert.equal(list.children.length, 2);
  const buttons = list.all().filter((n) => n.tagName === "BUTTON");
  assert.equal(buttons.length, 1);
  assert.match(buttons[0].getAttribute("aria-label"), /without sending/);
  buttons[0].click();
  assert.deepEqual(checked, ["a"]);
  assert.equal(list.children[0].getAttribute("role"), "listitem");
  assert.match(list.children[0].text, /Delivery result unknown/);
});

// ---------- T11 layout ----------

function panes(doc) {
  const make = () => ({ panel: doc.createElement("aside"), toggle: doc.createElement("button") });
  return { sidebar: make(), inspector: make() };
}

test("collapsing a pane makes it inert and returns focus to its toggle", () => {
  const doc = new FakeDocument();
  const root = doc.createElement("main");
  const p = panes(doc);
  const inside = doc.createElement("button");
  p.inspector.panel.append(inside);
  const storage = memoryStorage();
  const controller = createPaneController({ root, panes: p, storage });
  assert.equal(p.inspector.toggle.getAttribute("aria-expanded"), "true");
  inside.focus();
  p.inspector.toggle.click();
  assert.equal(controller.isCollapsed("inspector"), true);
  assert.equal(p.inspector.panel.inert, true);
  assert.equal(p.inspector.panel.hasAttribute("inert"), true);
  assert.equal(p.inspector.toggle.getAttribute("aria-expanded"), "false");
  assert.equal(doc.activeElement, p.inspector.toggle);
  assert.equal(root.dataset.inspectorCollapsed, "true");
  assert.equal(storage.values.get("vox-agent.layout.inspector"), "collapsed");
  p.inspector.toggle.click();
  assert.equal(p.inspector.panel.inert, false);
});

test("the stored layout is restored and only layout is stored", () => {
  const doc = new FakeDocument();
  const storage = memoryStorage({ "vox-agent.layout.sidebar": "collapsed" });
  const p = panes(doc);
  const controller = createPaneController({ root: doc.createElement("main"), panes: p, storage });
  assert.equal(controller.isCollapsed("sidebar"), true);
  assert.equal(p.sidebar.panel.inert, true);
  for (const key of storage.values.keys()) assert.match(key, /^vox-agent\.layout\./);
});

test("pane shortcuts need Ctrl/Cmd+Alt and ignore AltGr", () => {
  assert.equal(paneForShortcut({ ctrlKey: true, altKey: true, code: "KeyI" }), "inspector");
  assert.equal(paneForShortcut({ metaKey: true, altKey: true, code: "KeyB" }), "sidebar");
  assert.equal(paneForShortcut({ ctrlKey: true, code: "KeyI" }), null);
  assert.equal(
    paneForShortcut({ ctrlKey: true, altKey: true, code: "KeyI", getModifierState: (k) => k === "AltGraph" }),
    null,
  );
});


test("a second save while one is committing is refused, so no duplicate record is created", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const calls = [];
  const composer = createComposer({
    invoke: async (_c, args) => {
      calls.push(args.input.eventId);
      await gate;
      return { eventId: args.input.eventId };
    },
    makeEventId: ids,
  });
  const first = composer.submitContext(GROUP_TARGET, "note", { destination: "team" });
  const second = await composer.submitContext(GROUP_TARGET, "note", { destination: "team" });
  assert.equal(second.status, "in-flight");
  release();
  assert.equal((await first).status, "saved-locally");
  assert.equal(calls.length, 1);
});

test("re-rendering updates cards in place and keeps a focused check button", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  const records = [dto({ eventId: "a", serverState: "unknown" }), dto({ eventId: "b", serverState: "pending" })];
  renderRecords(doc, list, records, { onCheck: () => {} });
  const [cardA, cardB] = list.children;
  const button = cardA.all().find((n) => n.tagName === "BUTTON");
  button.focus();
  button.disabled = true;
  renderRecords(doc, list, [dto({ eventId: "a", serverState: "unknown" }), dto({ eventId: "b", serverState: "delivered", action: "final" })], {
    onCheck: () => {},
  });
  assert.equal(list.children[0], cardA, "same card node");
  assert.equal(cardA.all().find((n) => n.tagName === "BUTTON"), button, "same button node");
  assert.equal(button.disabled, true);
  assert.equal(doc.activeElement, button);
  assert.equal(list.children[1], cardB);
  assert.match(cardB.text, /Delivered/);
  // A record that no longer offers a check loses the button; removed records disappear.
  renderRecords(doc, list, [dto({ eventId: "a", serverState: "delivered", action: "final" })], { onCheck: () => {} });
  assert.equal(list.children.length, 1);
  assert.equal(cardA.all().some((n) => n.tagName === "BUTTON"), false);
});

test("a new record is inserted first without moving existing cards out of order", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a" })], {});
  const cardA = list.children[0];
  renderRecords(doc, list, [dto({ eventId: "new" }), dto({ eventId: "a" })], {});
  assert.deepEqual(list.children.map((c) => c.dataset.eventId), ["new", "a"]);
  assert.equal(list.children[1], cardA);
});


// ---------- T15 project attribution ----------

const P1 = { id: "p1", repository: "studio/repo" };
const P2 = { id: "p2", repository: "studio/other" };

test("the project is frozen when the draft starts and cannot be changed until it is cleared", () => {
  const composer = createComposer({ invoke: async () => ({}), makeEventId: ids });
  assert.equal(composer.selectProject(GROUP_TARGET, P1), true);
  composer.setDraft(GROUP_TARGET, "w");
  assert.equal(composer.isFrozen(GROUP_TARGET), true);
  assert.equal(composer.selectProject(GROUP_TARGET, P2), false);
  assert.deepEqual(composer.project(GROUP_TARGET), P1);
  composer.setDraft(GROUP_TARGET, "");
  assert.equal(composer.isFrozen(GROUP_TARGET), false);
  assert.equal(composer.selectProject(GROUP_TARGET, P2), true);
});

test("starting a draft without a project freezes 'no project' too", () => {
  const composer = createComposer({ invoke: async () => ({}), makeEventId: ids });
  composer.setDraft(GROUP_TARGET, "typed before the catalog loaded");
  assert.equal(composer.selectProject(GROUP_TARGET, P1), false);
  assert.equal(composer.project(GROUP_TARGET), null);
});

test("the submission carries the frozen snapshot, and a save that clears the draft unfreezes it", async () => {
  const { calls, invoke } = spyInvoke([{ eventId: "x" }]);
  const composer = createComposer({ invoke, makeEventId: ids });
  composer.selectProject(GROUP_TARGET, { ...P1, name: "Studio", path: "C:/secret" });
  composer.setDraft(GROUP_TARGET, "note");
  await composer.submitContext(GROUP_TARGET, "note", { destination: "team" });
  assert.deepEqual(calls[0].args.input.project, P1, "id and repository only");
  assert.equal(composer.isFrozen(GROUP_TARGET), false);
});

test("a submit without prior typing (e.g. voice) freezes the current selection at submit time", async () => {
  const { calls, invoke } = spyInvoke(["journal-unavailable"]);
  const composer = createComposer({ invoke, makeEventId: ids });
  composer.selectProject(GROUP_TARGET, P1);
  await composer.submitContext(GROUP_TARGET, "from voice", { destination: "team" });
  assert.deepEqual(calls[0].args.input.project, P1);
  assert.equal(composer.isFrozen(GROUP_TARGET), true, "kept frozen while the failed draft remains");
});

test("invalid projects are refused and attribution is per target and cleared together", () => {
  const composer = createComposer({ invoke: async () => ({}), makeEventId: ids });
  assert.equal(composer.selectProject(GROUP_TARGET, { id: "", repository: "x/y" }), false);
  assert.equal(composer.selectProject(GROUP_TARGET, P1), true);
  assert.equal(composer.project(COORDINATOR_TARGET), null);
  composer.clearAll();
  assert.equal(composer.project(GROUP_TARGET), null);
});


// ---------- T17 retained originals ----------

test("every card except a quarantined one offers View original", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  const viewed = [];
  renderRecords(doc, list, [dto({ eventId: "a" }), dto({ eventId: "q", action: "quarantine" })], {
    onView: (eventId) => viewed.push(eventId),
  });
  const buttons = (card) => card.all().filter((n) => n.tagName === "BUTTON").map((b) => b.textContent);
  assert.deepEqual(buttons(list.children[0]), ["View original"]);
  assert.deepEqual(buttons(list.children[1]), []);
  list.children[0].all().find((n) => n.textContent === "View original").click();
  assert.deepEqual(viewed, ["a"]);
});

test("a revealed original survives re-renders, copies with feedback, and hides back to its button", async () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a" })], { onView: () => {} });
  const card = list.children[0];
  const copied = [];
  showOriginal(doc, card, "<b>synthetic</b> 中文", { onCopy: async (...args) => (copied.push(args), true) });
  renderRecords(doc, list, [dto({ eventId: "a", serverState: "delivered", action: "final" })], { onView: () => {} });
  const textNode = card.all().find((n) => n.className === "context-original-text");
  assert.equal(textNode.textContent, "<b>synthetic</b> 中文", "text only, never parsed");
  await card.all().find((n) => n.textContent === "Copy").listeners.get("click")[0]();
  assert.deepEqual(copied, [[]], "Copy never passes the shown text; the page re-reads it");
  assert.match(card.text, /Copied\./);
  card.all().find((n) => n.textContent === "Hide").click();
  assert.equal(card.all().some((n) => n.className === "context-original"), false);
  assert.equal(doc.activeElement?.textContent, "View original");
});

test("an integrity failure shows a message and no text", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a" })], { onView: () => {} });
  const card = list.children[0];
  showOriginalError(doc, card, "This original failed its integrity check. It is not shown or copied.");
  assert.match(card.text, /integrity check/);
  assert.equal(card.all().some((n) => n.className === "context-original-text"), false);
  hideOriginal(card);
  assert.doesNotMatch(card.text, /integrity check/);
});

test("a record that becomes quarantined loses its revealed original", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a" })], { onView: () => {} });
  showOriginal(doc, list.children[0], "secret", {});
  renderRecords(doc, list, [dto({ eventId: "a", action: "quarantine" })], { onView: () => {} });
  assert.doesNotMatch(list.children[0].text, /secret/);
});


test("an unfrozen selection is dropped when the catalog no longer lists it; a frozen one is kept", () => {
  const composer = createComposer({ invoke: async () => ({}), makeEventId: ids });
  composer.selectProject(GROUP_TARGET, P1);
  composer.revalidateProject(GROUP_TARGET, ["p2"]);
  assert.equal(composer.project(GROUP_TARGET), null, "revoked before typing: not attached");
  composer.selectProject(GROUP_TARGET, P2);
  composer.setDraft(GROUP_TARGET, "typing");
  composer.revalidateProject(GROUP_TARGET, []);
  assert.deepEqual(composer.project(GROUP_TARGET), P2, "a frozen draft keeps its original project");
});

test("a late save never clears a draft that was cleared and retyped meanwhile", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const composer = createComposer({
    invoke: async (_c, args) => {
      await gate;
      return { eventId: args.input.eventId };
    },
    makeEventId: ids,
  });
  composer.setDraft(GROUP_TARGET, "same");
  const pending = composer.submitContext(GROUP_TARGET, "same", { destination: "team" });
  composer.setDraft(GROUP_TARGET, "");
  composer.setDraft(GROUP_TARGET, "same");
  release();
  const result = await pending;
  assert.equal(result.cleared, false);
  assert.equal(composer.draft(GROUP_TARGET), "same");
});

test("a late save after clearAll never clears the new session's draft", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const composer = createComposer({
    invoke: async (_c, args) => {
      await gate;
      return { eventId: args.input.eventId };
    },
    makeEventId: ids,
  });
  composer.setDraft(GROUP_TARGET, "same");
  const pending = composer.submitContext(GROUP_TARGET, "same", { destination: "team" });
  composer.clearAll();
  composer.setDraft(GROUP_TARGET, "same");
  release();
  assert.equal((await pending).cleared, false);
  assert.equal(composer.draft(GROUP_TARGET), "same");
});


// ---------- T23a plaintext lifecycle ----------

test("hiding all originals clears every revealed text and its copy feedback but keeps the cards", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a" }), dto({ eventId: "b" })], { onView: () => {} });
  showOriginal(doc, list.children[0], "secret one", {});
  showOriginal(doc, list.children[1], "secret two", {});
  assert.equal(hideAllOriginals(list), 2);
  assert.doesNotMatch(list.text, /secret/);
  assert.equal(list.children.length, 2, "records stay; only revealed plaintext goes");
  assert.equal(hideAllOriginals(list), 0);
});

test("a hidden original leaves no plaintext in detached nodes and a late copy shows no feedback", async () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a" })], { onView: () => {} });
  const card = list.children[0];
  let release;
  const box = showOriginal(doc, card, "secret", { onCopy: () => new Promise((resolve) => (release = resolve)) });
  const nodes = box.all();
  const pending = nodes.find((n) => n.textContent === "Copy").listeners.get("click")[0]();
  hideAllOriginals(list);
  release(true);
  await pending;
  assert.ok(nodes.every((n) => !/secret|Copied/.test(n.textContent)), "detached box is emptied");
  assert.doesNotMatch(card.text, /secret|Copied/);
});

test("a reveal in flight is invalidated by any hide, a manual Hide, or removal of its card", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a" }), dto({ eventId: "b" })], { onView: () => {} });
  const [a, b] = list.children;
  let token = beginReveal(a);
  assert.equal(revealCurrent(list, token), true);
  hideAllOriginals(list); // a lifecycle trigger with nothing revealed yet
  assert.equal(revealCurrent(list, token), false);
  showOriginal(doc, b, "shown", {});
  token = beginReveal(b);
  b.all().find((n) => n.textContent === "Hide").click(); // manual Hide
  assert.equal(revealCurrent(list, token), false);
  token = beginReveal(a);
  renderRecords(doc, list, [dto({ eventId: "b" })], { onView: () => {} });
  assert.equal(revealCurrent(list, token), false, "card removed from the list");
  token = beginReveal(b);
  renderRecords(doc, list, [dto({ eventId: "b", serverState: "delivered", action: "final" })], { onView: () => {} });
  assert.equal(revealCurrent(list, token), true, "a re-render alone keeps the reveal");
});

test("pane changes are reported so the page can clear revealed plaintext", () => {
  const doc = new FakeDocument();
  const changes = [];
  const p = panes(doc);
  createPaneController({ root: doc.createElement("main"), panes: p, storage: memoryStorage(), onChange: (name, collapsed) => changes.push([name, collapsed]) });
  p.sidebar.toggle.click();
  p.sidebar.toggle.click();
  assert.deepEqual(changes, [["sidebar", true], ["sidebar", false]]);
});


// ---------- T39 Chinese and English ----------

test("every status, composer and UI string exists in Chinese and English", () => {
  const entries = [...catalogEntries(), ...Object.values(STRINGS)];
  assert.ok(entries.length > 100);
  for (const entry of entries) {
    assert.ok(entry.en && entry.en.trim(), JSON.stringify(entry));
    assert.ok(entry.zh && entry.zh.trim(), JSON.stringify(entry));
    const vars = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    assert.deepEqual(vars(entry.zh), vars(entry.en), `placeholders differ: ${entry.en}`);
  }
});

test("statuses and composer messages render in Chinese", () => {
  assert.equal(describe(dto({ serverState: "pending" }), "zh").label, "服务端已接受，尚未投递");
  assert.equal(describe(dto({ serverState: "delivered", action: "final" }), "zh").label, "已投递");
  assert.match(composerMessage({ status: "too-large", bytes: 9000 }, { lang: "zh" }), /9000 \/ 8192 字节/);
  assert.equal(t("composer.labelAgent", "zh", { name: "Reviewer" }), "发给 Reviewer 的指令");
  assert.throws(() => t("no.such.key"));
});

test("the language preference defaults to English and accepts only known values", () => {
  assert.equal(readLanguage(memoryStorage()), "en");
  assert.equal(readLanguage(memoryStorage({ "vox-agent.context.language": "zh" })), "zh");
  assert.equal(readLanguage(memoryStorage({ "vox-agent.context.language": "fr" })), "en");
});

test("rendered records and controls follow the language", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("div");
  renderRecords(doc, list, [dto({ eventId: "a", serverState: "unknown" })], { onCheck: () => {}, onView: () => {}, lang: "zh" });
  assert.match(list.text, /投递结果未知/);
  const labels = list.all().filter((n) => n.tagName === "BUTTON").map((b) => b.textContent);
  assert.deepEqual(labels, ["再次核对", "查看原文"]);
  renderRecords(doc, list, [dto({ eventId: "a", serverState: "unknown" })], { onCheck: () => {}, onView: () => {}, lang: "en" });
  assert.deepEqual(list.all().filter((n) => n.tagName === "BUTTON").map((b) => b.textContent), ["Check again", "View original"]);
});
