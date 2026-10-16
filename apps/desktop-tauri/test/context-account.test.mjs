// T24: Feishu account panel and conversation discovery (pure modules plus
// source checks of the page wiring).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  ACCOUNT_ERRORS,
  ENDS_ACCOUNT,
  accountError,
  accountView,
  renderConversations,
  signOutMessage,
} from "../src/context/account.js";
import { STRINGS } from "../src/context/i18n.js";
import { FakeDocument } from "./helpers/fake-dom.mjs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const html = read("../src/index.html");
const ui = read("../src/context/ui.js");
const rust = read("../src-tauri/src/context_account.rs");

test("every Rust account error code has a message in both languages", () => {
  const body = rust.slice(rust.indexOf("pub enum AccountError"), rust.indexOf("impl From<LoginError>"));
  const variants = [...body.matchAll(/^\s{4}([A-Z][A-Za-z]+),$/gm)].map((m) => m[1]);
  assert.ok(variants.length >= 18);
  const kebab = variants.map((v) => v.replace(/[A-Z]/g, (c, i) => (i ? "-" : "") + c.toLowerCase()));
  assert.deepEqual([...kebab].sort(), [...ACCOUNT_ERRORS].sort());
  for (const code of kebab) {
    for (const lang of ["en", "zh"]) {
      const text = accountError(code, lang);
      assert.ok(text && !text.includes("{"), `${code} ${lang}`);
    }
  }
  assert.equal(accountError("server-said-something", "en"), accountError("invalid-response", "en"));
});

test("errors that end the account are exactly the server-side refusals", () => {
  assert.deepEqual([...ENDS_ACCOUNT].sort(), ["account-disabled", "identity-changed", "sign-in-required"]);
});

test("the account view never shows sign-in when it cannot work", () => {
  assert.deepEqual(accountView({ signInAvailable: false, signedIn: false }, "en"), {
    signIn: false,
    signOut: false,
    line: STRINGS["account.notAvailable"].en,
  });
  assert.deepEqual(accountView({ signInAvailable: true, signedIn: false }, "en"), {
    signIn: true,
    signOut: false,
    line: STRINGS["account.signedOut"].en,
  });
  const signed = accountView({ signInAvailable: true, signedIn: true, displayName: "Alice" }, "zh");
  assert.equal(signed.signIn, false);
  assert.equal(signed.signOut, true);
  assert.match(signed.line, /Alice/);
  assert.equal(accountView({ signInAvailable: true, signedIn: true, displayName: null }, "en").line, STRINGS["account.signedInNoName"].en);
});

test("sign-out reports honestly what was and was not ended", () => {
  assert.equal(signOutMessage({ serverConfirmed: true, storeCleared: true }, "en"), STRINGS["account.signedOutDone"].en);
  assert.match(signOutMessage({ serverConfirmed: false, storeCleared: true }, "en"), /server/i);
  assert.match(signOutMessage({ serverConfirmed: true, storeCleared: false }, "en"), /credential/i);
});

test("conversations render as text with kind and capability words, never markup", () => {
  const doc = new FakeDocument();
  const list = doc.createElement("ul");
  renderConversations(doc, list, [
    { id: "fc_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", kind: "personal", name: "Feishu CLI", canSend: true },
    { id: "fc_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", kind: "group", name: "<b>Team</b>", canSend: false },
  ], "en");
  assert.equal(list.children.length, 2);
  const [dm, group] = list.children;
  assert.equal(dm.tagName, "LI");
  assert.match(dm.text, /Feishu CLI/);
  assert.match(dm.text, new RegExp(STRINGS["conversations.personal"].en));
  assert.match(dm.text, new RegExp(STRINGS["conversations.canSend"].en));
  assert.match(group.text, /<b>Team<\/b>/, "names are plain text");
  assert.match(group.text, new RegExp(STRINGS["conversations.viewOnly"].en));
  assert.equal(group.dataset.conversationId, "fc_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB");
  renderConversations(doc, list, [], "zh");
  assert.equal(list.children.length, 0);
});

test("the panel has account and conversation sections with translated labels", () => {
  for (const id of [
    "context-sign-in",
    "context-sign-out",
    "context-account-line",
    "context-account-note",
    "context-conversations",
    "context-refresh-conversations",
    "context-conversations-note",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
  assert.match(html, /id="context-account-note"[^>]*role="status"/);
  assert.match(html, /id="context-conversations-note"[^>]*role="status"/);
  assert.match(html, /id="context-sign-in"[^>]*data-i18n="account.signIn"/);
});

test("the page passes nothing to sign-in and ends the view on refusals", () => {
  assert.match(ui, /invoke\("context_sign_in"\)/);
  assert.match(ui, /invoke\("context_sign_out"\)/);
  assert.match(ui, /invoke\("context_conversations"\)/);
  const signOut = ui.slice(ui.indexOf("async function signOut"), ui.indexOf("\n  }\n", ui.indexOf("async function signOut")));
  assert.ok(signOut.indexOf("resetView()") >= 0, "sign-out clears the view");
  assert.ok(
    signOut.indexOf("resetView()") < signOut.indexOf('invoke("context_sign_out")'),
    "the view is cleared before Rust (and the server logout) is awaited",
  );
  const summary = ui.slice(ui.indexOf("async function refreshSummary"), ui.indexOf("function renderSummary"));
  assert.match(summary, /ticket !== summaryTicket \|\| epoch !== viewEpoch/);
  const conversations = ui.slice(
    ui.indexOf("async function refreshConversations"),
    ui.indexOf("\n  }\n", ui.indexOf("async function refreshConversations")),
  );
  assert.match(conversations, /ENDS_ACCOUNT\.has\(/);
  assert.match(conversations, /viewEpoch/);
});
