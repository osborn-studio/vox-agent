// T40b (local part): layout checks at Windows display scales.
//
// At 125/150/200 % scaling a 1920×1080 or 1366×768 screen gives the
// WebView a CSS viewport of width / scale. Resize the browser to each
// viewport, then in the page:
//   const { layoutReport } = await import("/a11y/dpi-states.js");
//   layoutReport("1920@150%");
// The real Windows run (Narrator, high contrast themes, per-monitor DPI)
// stays with the named-device operator; see
// docs/verification/windows-t40b-accessibility-device.md.

export const VIEWPORTS = Object.freeze([
  { label: "1920@100%", width: 1920, height: 1080 },
  { label: "1920@125%", width: 1536, height: 864 },
  { label: "1920@150%", width: 1280, height: 720 },
  { label: "1920@200%", width: 960, height: 540 },
  { label: "1366@125%", width: 1093, height: 614 },
]);

// Each key control and the toggle that reveals it when a pane is collapsed.
const KEY_CONTROLS = [
  ["target-group", "toggle-sidebar"],
  ["toggle-sidebar", null],
  ["toggle-inspector", null],
  ["agent-query", null],
  ["record-context", null],
];

/** On screen, not covered or clipped at its centre, at least 24×24 px. */
function visibleProblems(id, node, width, height) {
  const problems = [];
  const rect = node.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return [`${id} has no size`];
  if (rect.left < -1 || rect.right > width + 1) problems.push(`${id} is cut off horizontally`);
  if (rect.top < -1 || rect.bottom > height + 1) problems.push(`${id} is outside the viewport vertically`);
  if (rect.width < 24 || rect.height < 24) {
    problems.push(`${id} target under 24 px (${Math.round(rect.width)}×${Math.round(rect.height)})`);
  }
  const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
  if (!hit || !(hit === node || node.contains(hit))) problems.push(`${id} is covered or clipped at its centre`);
  return problems;
}

/** Check the page at the current viewport, which must be `expected` (e.g. "1280×720"). */
export function layoutReport(label, expected) {
  const root = document.documentElement;
  const width = window.innerWidth;
  const height = window.innerHeight;
  const problems = [];
  if (expected && expected !== `${width}×${height}`) problems.push(`viewport is ${width}×${height}, expected ${expected}`);
  if (root.scrollWidth > width + 1) problems.push(`page scrolls sideways (${root.scrollWidth} > ${width})`);
  for (const [id, toggle] of KEY_CONTROLS) {
    const node = document.getElementById(id);
    if (!node) {
      problems.push(`${id} missing`);
      continue;
    }
    if (node.closest("[hidden], [inert]")) {
      // Collapsed (an inert pane) or hidden: its toggle must be visible to bring it back.
      if (!toggle) problems.push(`${id} is hidden and has no toggle`);
      else problems.push(...visibleProblems(toggle, document.getElementById(toggle), width, height));
      continue;
    }
    problems.push(...visibleProblems(id, node, width, height));
  }
  return { label, viewport: `${width}×${height}`, problems };
}
