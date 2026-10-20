import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const html = read("../src/index.html");
const main = read("../src/main.js");
const ui = read("../src/context/ui.js");
const composer = read("../src/context/composer.js");
const css = read("../src/styles.css");
import { STRINGS } from "../src/context/i18n.js";

const at = (needle) => {
  const index = html.indexOf(needle);
  assert.ok(index >= 0, `missing ${needle}`);
  return index;
};

function section(id) {
  const start = at(`id="${id}"`);
  const open = html.lastIndexOf("<", start);
  const tag = html.slice(open + 1, html.indexOf(" ", open));
  let depth = 0;
  const pattern = new RegExp(`<${tag}[\\s>]|</${tag}>`, "g");
  pattern.lastIndex = open;
  for (let match; (match = pattern.exec(html)); ) {
    depth += match[0].startsWith("</") ? -1 : 1;
    if (depth === 0) return html.slice(open, match.index);
  }
  throw new Error(`unclosed ${id}`);
}

test("tab order is navigation, timeline, composer, then the right panel", () => {
  const order = [
    at('id="target-group"'),
    at('id="context-records"'),
    at('id="agent-query"'),
    at('id="record-context"'),
    at('id="context-destination"'),
  ];
  assert.deepEqual([...order].sort((a, b) => a - b), order);
});

test("pane toggles live in the toolbar and control real panes", () => {
  for (const [toggle, pane] of [
    ["toggle-sidebar", "sidebar-pane"],
    ["toggle-inspector", "inspector-pane"],
  ]) {
    const button = html.slice(at(`id="${toggle}"`), html.indexOf(">", at(`id="${toggle}"`)));
    assert.match(button, new RegExp(`aria-controls="${pane}"`));
    assert.match(button, /aria-expanded="true"/);
    assert.ok(section("sidebar-pane") && section("inspector-pane"));
    assert.ok(!section(pane).includes(`id="${toggle}"`), "a toggle must stay reachable when its pane is collapsed");
  }
  assert.match(css, /data-inspector-collapsed="true"\]\s*>\s*\.inspector/);
});

test("the default conversation is the group, and agents are a collapsible list", () => {
  assert.match(html, /id="target-group"[^>]*aria-pressed="true"/);
  assert.match(html, /id="toggle-agents"[^>]*aria-expanded="true"[^>]*aria-controls="agent-targets"/);
  assert.match(section("agent-targets"), /id="target-coordinator"/);
  assert.match(main, /row\.dataset\.agentId = item\.id/);
  assert.ok(!/agentId: null, provider/.test(main), "agent sends use the selected agent");
});

test("the Context flow says it is not the group's full history and announces politely", () => {
  const flow = section("context-flow");
  assert.match(flow, /not the group's full message history/);
  assert.match(flow, /role="list"/);
  assert.match(flow, /id="context-live"[^>]*aria-live="polite"/);
  assert.match(html, /id="composer-status"[^>]*role="status"/);
});

test("approval cards are never inside the view that hides with the target", () => {
  const agentFlow = section("agent-flow");
  for (const id of ["engine-permission-list", "policy-preview-card"]) {
    assert.ok(html.includes(`id="${id}"`), id);
    assert.ok(!agentFlow.includes(`id="${id}"`), `${id} must stay visible`);
  }
  assert.ok(!section("context-flow").includes("engine-permission-list"));
});

test("drafts never reach WebView storage", () => {
  for (const source of [ui, composer]) {
    assert.ok(!/localStorage|sessionStorage|indexedDB/.test(source));
  }
  assert.ok(!/storage\?*\.set\(/.test(composer));
});

test("the composer submit shortcut is IME-safe and owned by one handler", () => {
  assert.match(ui, /isSubmitShortcut\(event\)/);
  assert.ok(!/agentQuery\.addEventListener\("keydown"/.test(main), "no second, IME-unaware Enter handler");
  assert.match(main, /import \{ initContextUi \} from "\.\/context\/ui\.js";/);
});

test("context UI renders text only", () => {
  for (const file of ["ui.js", "timeline.js", "composer.js", "status-copy.js", "layout.js", "targets.js"]) {
    assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML/.test(read(`../src/context/${file}`)), file);
  }
});

test("agent commands refuse the group target and clear their draft through the composer", () => {
  assert.match(main, /async function askAgent\(\) \{\n  \/\/ Agent commands only ever go to an agent target[^\n]*\n  if \(contextUi\.currentTarget\(\)\.kind !== "agent" \|\| agentCommandInFlight\)/);
  assert.match(main, /contextUi\.finishAgentCommand\(submittedTarget, submittedText\)/);
  assert.match(main, /agentCommandInFlight\) \{\n    return;/);
  assert.ok(!/elements\.agentQuery\.value = "";/.test(main), "agent replies never clear the field blindly");
});

test("record events are hints only: the status is always re-read from Rust", () => {
  assert.match(ui, /listen\(RECORD_EVENT, \(event\) => refreshRecord\(event\?\.payload\?\.eventId\)\)/);
  assert.match(ui, /invoke\("context_record_status", \{ eventId \}\)/);
  assert.match(ui, /if \(submitting\) return;/);
});

test("narrow windows shrink pane tokens instead of hard-coding columns or hiding a pane", () => {
  const media = css.slice(css.indexOf("@media (max-width: 1180px)"));
  assert.ok(!/grid-template-columns: \d+px minmax/.test(media));
  assert.ok(!/\.inspector \{\s*display: none;/.test(media));
});

test("a running agent uses the running badge, not the human-action warning", () => {
  assert.match(main, /"Agent Running", "running"/);
  assert.match(css, /\.badge\.running \{/);
});

test("the project menu sits in the Context panel and the repository check is read-only by design", () => {
  const panel = section("context-panel");
  assert.match(panel, /id="context-project"/);
  assert.match(panel, /id="context-inspect-repository"[^>]*attribution stays unchanged/);
  assert.match(ui, /invoke\("context_inspect_repository"\)/);
  assert.ok(!/context_inspect_repository", \{/.test(ui), "the WebView never passes a path");
  assert.match(STRINGS["repository.matches"].en, /attribution was not changed automatically/);
  assert.match(STRINGS["project.original"].en, /\(original project\)/);
  assert.match(ui, /t\("project\.original"/);
});

test("replies made before clear() are dropped, and copying re-verifies the original", () => {
  assert.match(ui, /viewEpoch \+= 1;/);
  for (const fn of ["refreshRecord", "checkRecord", "viewOriginal", "loadPage", "refreshProjects"]) {
    const body = ui.slice(ui.indexOf(`function ${fn}`), ui.indexOf("\n  }\n", ui.indexOf(`function ${fn}`)));
    assert.match(body, /viewEpoch/, fn);
  }
  assert.match(ui, /const fresh = await invoke\("context_read_original"/);
  assert.match(ui, /Only a frozen draft can keep a project/);
});

test("revealed originals are cleared on collapse, blur, hidden window, target switch and clear()", () => {
  assert.match(ui, /onChange: \((?:_name|name), collapsed\) => \{\s*if \(collapsed\) hideRevealed\(\);/);
  assert.match(ui, /addEventListener\("blur", hideRevealed\)/);
  assert.match(ui, /visibilitychange/);
  const select = ui.slice(ui.indexOf("function selectTarget"), ui.indexOf("function updateBytes"));
  assert.match(select, /hideRevealed\(\)/);
  const clearBody = ui.slice(ui.indexOf("function resetView"), ui.indexOf("\n  }\n", ui.indexOf("function resetView")));
  assert.match(clearBody, /hideRevealed\(\)/);
  assert.match(ui, /clear: resetView/);
});

test("T23b: private notes and the weekly draft clear on inspector collapse, hidden window and account end, not on blur", () => {
  assert.match(ui, /if \(collapsed && name === "inspector"\) clearPrivateSide\(\);/);
  const hidden = ui.slice(ui.indexOf('addEventListener?.("visibilitychange"'), ui.indexOf("/** Diagnostics labels"));
  assert.match(hidden, /clearPrivateSide\(\)/);
  const clearBody = ui.slice(ui.indexOf("function resetView"), ui.indexOf("\n  }\n", ui.indexOf("function resetView")));
  assert.match(clearBody, /clearPrivateSide\(\)/);
  assert.doesNotMatch(ui, /addEventListener\("blur", clearPrivateSide\)/);
  const clear = ui.slice(ui.indexOf("function clearPrivateSide"), ui.indexOf("\n  }\n", ui.indexOf("function clearPrivateSide")));
  assert.match(clear, /privatePanel\?\.clear\(\)/);
  assert.match(clear, /weeklyPanel\?\.clear\(\)/);
});

test("blur and collapse never clear team drafts or delete records", () => {
  const hide = ui.slice(ui.indexOf("function hideRevealed"), ui.indexOf("\n  }\n", ui.indexOf("function hideRevealed")));
  assert.doesNotMatch(hide, /composer\.|records = |invoke\(/);
});

test("every data-i18n key in the page exists, and Context code never logs", () => {
  const keys = [...html.matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(keys.length >= 20);
  for (const key of keys) assert.ok(STRINGS[key], key);
  for (const file of ["ui.js", "timeline.js", "composer.js", "status-copy.js", "layout.js", "targets.js", "i18n.js"]) {
    assert.ok(!/console\./.test(read(`../src/context/${file}`)), file);
  }
  assert.ok(!/["'`][A-Z][a-z]+ [a-z]+[^"'`]*[.…]["'`]/.test(ui.replace(/\/\/.*$/gm, "")), "ui.js has no untranslated sentences");
});

test("translated aria-labels and titles use keys that exist", () => {
  const keys = [...html.matchAll(/data-i18n-(?:aria|title)="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(keys.length >= 9);
  for (const key of keys) assert.ok(STRINGS[key], key);
  for (const id of ["toggle-sidebar", "toggle-inspector", "context-records", "record-context", "context-inspect-repository"]) {
    assert.match(html, new RegExp(`id="${id}"[^>]*data-i18n-aria=`), id);
  }
});

test("a language switch hides originals and redraws cached state without reloading", () => {
  const body = ui.slice(ui.indexOf("function setLanguage"), ui.indexOf("\n  }\n", ui.indexOf("function setLanguage")));
  assert.match(body, /hideRevealed\(\)/);
  assert.match(body, /renderComposerStatus\(\)/);
  assert.doesNotMatch(body, /invoke\(|refreshSummary|refreshDiagnostics|refreshProjects|loadPage/);
  const summaryBody = ui.slice(ui.indexOf("function renderSummary"), ui.indexOf("function renderQuota"));
  assert.match(summaryBody, /const chosen = el\.destination\.value/);
  assert.match(ui, /if \(cursor !== null \|\| nextCursor === null\) nextCursor/);
});

test("late original reads and copies are bound to the current reveal", () => {
  const view = ui.slice(ui.indexOf("async function viewOriginal"), ui.indexOf("async function loadPage"));
  assert.match(view, /beginReveal\(item\)/);
  assert.match(view, /revealCurrent\(el\.records, reveal\)/);
  const copy = ui.slice(ui.indexOf("function copier"), ui.indexOf("async function viewOriginal"));
  assert.match(copy, /item\.parts\.original === shown\.box && el\.records\.contains\(item\)/);
});

test("Context settings show diagnostics and a language choice", () => {
  const settings = section("context-diagnostics");
  assert.ok(settings !== undefined);
  assert.match(html, /id="context-language"[\s\S]*value="zh">中文/);
  assert.match(ui, /invoke\("context_diagnostics"\)/);
});

test("diagnostics map every Rust key_store label to fixed copy (PR-A)", () => {
  const block = ui.slice(ui.indexOf("const KEY_STORE_COPY"), ui.indexOf("function renderDiagnostics"));
  for (const label of ["dpapi-user", "unsafe-acl", "unavailable", "not-available-in-this-build"]) {
    assert.ok(block.includes(`"${label}"`) || block.includes(`${label}:`), label);
  }
  assert.match(block, /"unsafe-acl": "diagnostics\.unsafeAcl"/);
  assert.match(block, /"dpapi-user": "diagnostics\.ready"/);
  assert.match(ui, /KEY_STORE_COPY\[d\.keyStore\] \?\? "diagnostics\.notAvailable"/);
  const i18n = read("../src/context/i18n.js");
  assert.match(i18n, /"diagnostics\.unsafeAcl": L\(\s*"Local encrypted directory permissions are unsafe; delivery disabled",\s*"本机加密目录权限不安全，已停用投递",/);
});

test("a renewal sign-out from Rust refreshes the account view with a known code (PR-A)", () => {
  assert.match(ui, /export const ACCOUNT_EVENT = "vox-agent:\/\/context-account"/);
  const block = ui.slice(ui.indexOf("listen(ACCOUNT_EVENT"), ui.indexOf("doc.defaultView?.addEventListener(\"blur\""));
  assert.match(block, /accountError\(typeof code === "string" \? code : "sign-in-required", l\)/);
  assert.match(block, /clearPrivateSide\(\)/);
  assert.match(block, /refreshSummary\(\)/);
});

test("diagnostics render the renewal status as fixed copy with the server code only (PR-A)", () => {
  const fn = ui.slice(ui.indexOf("function renewalCopy"), ui.indexOf("function renderDiagnostics"));
  for (const label of ["none", "scheduled", "renewed", "not-offered", "renewal-unavailable", "signed-out"]) {
    assert.ok(fn.includes(label), label);
  }
  assert.match(ui, /\["diagnostics\.renewal", renewalCopy\(d\.renewal\)\]/);
  const i18n = read("../src/context/i18n.js");
  for (const key of ["renewal", "renewalNone", "renewalScheduled", "renewalRenewed", "renewalNotOffered", "renewalUnavailable", "renewalSignedOut"]) {
    assert.match(i18n, new RegExp(`"diagnostics\\.${key}": L\\(`), key);
  }
});

test("Windows high contrast keeps status edges, badges and focus visible (T40b)", () => {
  const block = css.slice(css.indexOf("@media (forced-colors: active)"));
  assert.ok(block.length > 0, "forced-colors block");
  for (const needle of [".card-edge", "forced-color-adjust: none", ".badge", "Highlight", "GrayText"]) {
    assert.ok(block.includes(needle), needle);
  }
  assert.doesNotMatch(block, /#[0-9a-f]{3,8}\b|rgb\(/i, "system colours only");
});

/** WCAG relative luminance contrast of two #rrggbb colours. */
function contrast(a, b) {
  const lum = (hex) => {
    const [r, g, bl] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) =>
      c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
    );
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

function tokens(block) {
  return Object.fromEntries([...block.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6});/gi)].map((m) => [m[1], m[2].toLowerCase()]));
}

test("text tokens meet 4.5:1 on every surface in both appearances (DESIGN 165, T40b)", () => {
  const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {"))));
  const darkStart = css.indexOf("@media (prefers-color-scheme: dark)");
  const dark = { ...light, ...tokens(css.slice(darkStart, css.indexOf("}\n}", darkStart))) };
  for (const [name, set] of [["light", light], ["dark", dark]]) {
    for (const text of ["label", "secondary-label", "tertiary-label"]) {
      for (const surface of ["app-background", "surface", "surface-raised"]) {
        const ratio = contrast(set[text], set[surface]);
        assert.ok(ratio >= 4.5, `${name} ${text} on ${surface}: ${ratio.toFixed(2)}`);
      }
    }
    assert.ok(contrast(set["on-accent"], set.accent) >= 4.5, `${name} on-accent`);
    assert.ok(contrast(set["on-success"], set.success) >= 4.5, `${name} on-success`);
  }
  assert.match(css.slice(css.indexOf(".button.success {")), /^\.button\.success \{[^}]*background: var\(--success\);/, "Approve keeps its success fill (DESIGN)");
  for (const selector of [".button {", ".send-button {", ".button.success {", ".button.danger {"]) {
    const rule = css.slice(css.indexOf(selector), css.indexOf("}", css.indexOf(selector)));
    assert.doesNotMatch(rule, /^\s*color: (white|var\(--(danger|success|warning)\));/m, selector);
  }
});
