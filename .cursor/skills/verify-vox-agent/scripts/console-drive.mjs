#!/usr/bin/env node
// console-drive.mjs — drive the Vox Agent web console (apps/web-console) in headless Chrome over CDP.
// Verification scaffolding: needs Node >=22 (global WebSocket/fetch) and Chrome/Chromium (CHROME_BIN overrides).
// Usage: node console-drive.mjs <url> <out-prefix> '<steps-json>' [--timeout-ms N]
// Steps (run in order; each wait/step has the shared timeout):
//   {"click":"Sessions"}                      click the first <button> whose trimmed text equals (or starts with) the string
//   {"fill":{"placeholder":"Pairing code","value":"ABCD1234"}}   set a React-controlled input by placeholder
//   {"submit":"Search memory (e.g. Japan launch)"}               requestSubmit() the form containing that placeholder's input
//   {"waitText":"Japan launch"}               wait until innerText or textContent contains the text (section titles are CSS-uppercased in innerText)
//   {"waitNoText":"Loading"}                  wait until the text is gone
//   {"shot":"label"}                          intermediate PNG + innerText: <out-prefix>.<label>.png/.text.txt
// Always writes <out-prefix>.png/.dom.html/.text.txt (final state) and <out-prefix>.network.json:
// every request the page made to the engine (url, method, status) — use it to tell live data from
// the console's silent mock fallback. Exit 1 if any step fails or times out.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, out, stepsJson, ...rest] = process.argv.slice(2);
if (!url || !out || !stepsJson) { console.error("usage: console-drive.mjs <url> <out-prefix> '<steps-json>' [--timeout-ms N]"); process.exit(2); }
const ti = rest.indexOf("--timeout-ms");
const timeoutMs = Number(ti >= 0 ? rest[ti + 1] : 15000);
const steps = JSON.parse(stepsJson);
const chromeBin = process.env.CHROME_BIN ?? ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find(existsSync);
if (!chromeBin) { console.error("no Chrome/Chromium found; set CHROME_BIN"); process.exit(2); }

const profile = mkdtempSync(join(tmpdir(), "vox-verify-chrome-"));
const chrome = spawn(chromeBin, ["--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
  `--user-data-dir=${profile}`, "--remote-debugging-port=0", "--window-size=1400,1600", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const network = [];
let code = 1;
try {
  let port; const portDeadline = Date.now() + 15000;
  while (!port && Date.now() < portDeadline) {
    try { port = readFileSync(join(profile, "DevToolsActivePort"), "utf8").split("\n")[0]; } catch { await sleep(100); }
  }
  if (!port) throw new Error("Chrome did not expose a DevTools port");
  const page = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0; const pending = new Map(); const reqs = new Map();
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
    if (d.method === "Network.requestWillBeSent" && d.params.request.url.includes(":18900/")) {
      reqs.set(d.params.requestId, { method: d.params.request.method, url: d.params.request.url, status: null, error: null });
    } else if (d.method === "Network.responseReceived" && reqs.has(d.params.requestId)) {
      const r = reqs.get(d.params.requestId);
      if (d.params.type !== "Preflight") r.status = d.params.response.status;
    } else if (d.method === "Network.loadingFailed" && reqs.has(d.params.requestId)) {
      reqs.get(d.params.requestId).error = d.params.errorText;
    }
  };
  const send = (method, params = {}) => new Promise((r) => { const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
  const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
  const until = async (expr, what) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) { if (await evaluate(expr)) return; await sleep(200); }
    throw new Error(`timeout waiting for ${what}`);
  };
  const J = JSON.stringify;
  await send("Page.enable"); await send("Network.enable");
  await send("Page.navigate", { url });
  await until("document.readyState === 'complete' && !!document.querySelector('.topbar .tabs')", "console topbar");
  const snap = async (prefix) => {
    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${prefix}.png`, Buffer.from(shot.result.data, "base64"));
    writeFileSync(`${prefix}.text.txt`, await evaluate("document.body.innerText"));
  };
  let failed = null;
  for (const [i, step] of steps.entries()) {
    try {
      if (step.click !== undefined) {
        await until(`[...document.querySelectorAll('button')].some(b => !b.disabled && (b.innerText.trim() === ${J(step.click)} || b.innerText.trim().startsWith(${J(step.click)})))`, `enabled button ${J(step.click)}`);
        await evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => !b.disabled && (b.innerText.trim() === ${J(step.click)} || b.innerText.trim().startsWith(${J(step.click)}))); b.click(); return true; })()`);
      } else if (step.fill !== undefined) {
        const { placeholder, value } = step.fill;
        await until(`!!document.querySelector('input[placeholder=' + ${J(J(placeholder))} + ']')`, `input ${placeholder}`);
        await evaluate(`(() => { const el = document.querySelector('input[placeholder=' + ${J(J(placeholder))} + ']');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, ${J(value)});
          el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
      } else if (step.submit !== undefined) {
        await until(`!!document.querySelector('input[placeholder=' + ${J(J(step.submit))} + ']')?.form`, `form for ${step.submit}`);
        await evaluate(`document.querySelector('input[placeholder=' + ${J(J(step.submit))} + ']').form.requestSubmit() ?? true`);
      } else if (step.waitText !== undefined) {
        await until(`document.body.innerText.includes(${J(step.waitText)}) || document.body.textContent.includes(${J(step.waitText)})`, `text ${J(step.waitText)}`);
      } else if (step.waitNoText !== undefined) {
        await until(`!document.body.innerText.includes(${J(step.waitNoText)}) && !document.body.textContent.includes(${J(step.waitNoText)})`, `text gone ${J(step.waitNoText)}`);
      } else if (step.shot !== undefined) {
        await snap(`${out}.${step.shot}`);
      } else throw new Error(`unknown step ${J(step)}`);
      console.log(`step ${i + 1} ok ${J(step)}`);
    } catch (e) { failed = `step ${i + 1} ${J(step)}: ${e.message}`; break; }
  }
  await sleep(300);
  await snap(out);
  writeFileSync(`${out}.dom.html`, await evaluate("document.documentElement.outerHTML"));
  for (const r of reqs.values()) network.push(r);
  writeFileSync(`${out}.network.json`, JSON.stringify(network, null, 2));
  const offline = network.filter((r) => r.error || (r.status !== null && r.status >= 400));
  console.log(`engine requests: ${network.length} (${offline.length} failed/non-2xx${offline.length ? " — console may be showing MOCK fallback data" : ""})`);
  console.log(`evidence: ${out}.png ${out}.text.txt ${out}.dom.html ${out}.network.json`);
  if (failed) console.error(`FAILED ${failed}`);
  code = failed ? 1 : 0;
  ws.close();
} catch (e) {
  console.error(`console-drive: ${e instanceof Error ? e.message : String(e)}`);
} finally {
  chrome.kill("SIGTERM");
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
process.exit(code);
