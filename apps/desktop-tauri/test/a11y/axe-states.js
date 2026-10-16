// T40a: run axe-core over the Context UI states in a real browser.
//
// Usage: serve apps/desktop-tauri/src with axe.min.js (axe-core 4.10.x) next
// to index.html, open index.html, load axe.min.js, then in the page:
//   const { runAxeStates } = await import("/a11y/axe-states.js");
//   await runAxeStates();
// The page is driven through its own controls (no state is faked).

const RULES = { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } };

const settle = () => new Promise((resolve) => setTimeout(resolve, 120));

async function scan(label) {
  const result = await window.axe.run(document, RULES);
  return {
    state: label,
    // Contrast axe could not decide (e.g. content outside the viewport);
    // run again at a taller viewport until this is empty.
    undecidedContrast: result.incomplete.filter((v) => v.id === "color-contrast").flatMap((v) => v.nodes.map((n) => n.target.join(" "))),
    violations: result.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.length,
      targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")),
    })),
  };
}

function setLanguage(value) {
  const select = document.getElementById("context-language");
  select.value = value;
  select.dispatchEvent(new Event("change"));
}

function showView(name) {
  document.querySelector(`[data-view-target="${name}"]`)?.click();
}

/** Open or collapse a pane through its own toggle, based on aria-expanded. */
function setPane(id, open) {
  const toggle = document.getElementById(id);
  if ((toggle.getAttribute("aria-expanded") === "true") !== open) toggle.click();
}

function visible(selector) {
  const node = document.querySelector(selector);
  return Boolean(node) && !node.hidden && node.closest("[hidden]") === null;
}

/** Every scanned state must actually be reached, or the run fails. */
function expectState(label, checks) {
  const failed = Object.entries(checks).filter(([, ok]) => !ok).map(([name]) => name);
  if (failed.length) throw new Error(`state "${label}" not reached: ${failed.join(", ")}`);
}

const expanded = (id) => document.getElementById(id).getAttribute("aria-expanded") === "true";

export async function runAxeStates() {
  if (!window.axe) throw new Error("load axe.min.js first");
  const reports = [];
  const step = async (label, checks) => {
    await settle();
    expectState(label, checks());
    reports.push(await scan(label));
  };
  setLanguage("en");
  setPane("toggle-sidebar", true);
  setPane("toggle-inspector", true);
  showView("command");
  document.getElementById("target-group").click();
  await step("group · en", () => ({
    lang: document.documentElement.lang === "en",
    command: visible('[data-view="command"]'),
    contextFlow: visible("#context-flow"),
    contextPanel: visible("#context-panel"),
  }));
  setLanguage("zh");
  await step("group · zh", () => ({
    lang: document.documentElement.lang === "zh-CN",
    translated: document.getElementById("toggle-sidebar").getAttribute("aria-label").includes("侧栏"),
  }));
  setLanguage("en");
  document.getElementById("target-coordinator").click();
  await step("agent · en", () => ({
    lang: document.documentElement.lang === "en",
    agentFlow: visible("#agent-flow"),
    operations: visible("#operations-panel"),
    contextHidden: !visible("#context-flow"),
  }));
  document.getElementById("target-group").click();
  setPane("toggle-sidebar", false);
  setPane("toggle-inspector", false);
  await step("group · panes collapsed", () => ({
    sidebar: !expanded("toggle-sidebar"),
    inspector: !expanded("toggle-inspector"),
    contextFlow: visible("#context-flow"),
  }));
  setPane("toggle-sidebar", true);
  setPane("toggle-inspector", true);
  showView("settings");
  await step("settings · en", () => ({
    settings: visible('[data-view="settings"]'),
    language: visible("#context-language"),
    sidebar: expanded("toggle-sidebar"),
  }));
  showView("command");
  await settle();
  const serious = reports.flatMap((r) =>
    r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => ({ state: r.state, ...v })),
  );
  return { axe: window.axe.version, reports, serious };
}
