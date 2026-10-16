// Collapsible side panes (T11). A collapsed pane is `inert`: none of its
// controls stay in the tab order or the accessibility tree, and focus inside
// it returns to the pane's toggle. Toggles live in the always-visible toolbar.
// Only the open/collapsed layout preference is stored, never content.

export const PANES = Object.freeze(["sidebar", "inspector"]);
const STORAGE_PREFIX = "vox-agent.layout.";

/** Ctrl/⌘ + Alt + B (sidebar) or I (inspector, DESIGN ⌘⌥I). AltGr is ignored. */
export function paneForShortcut(event) {
  if (!(event.ctrlKey || event.metaKey) || !event.altKey || event.shiftKey) return null;
  if (event.getModifierState?.("AltGraph")) return null;
  if (event.code === "KeyI") return "inspector";
  if (event.code === "KeyB") return "sidebar";
  return null;
}

export function createPaneController({ root, panes, storage, onChange }) {
  const collapsed = new Map();

  function apply(name) {
    const { panel, toggle } = panes[name];
    const isCollapsed = collapsed.get(name);
    if (isCollapsed && panel.contains?.(root.ownerDocument?.activeElement)) {
      toggle.focus();
    }
    panel.inert = isCollapsed;
    panel.toggleAttribute?.("inert", isCollapsed);
    toggle.setAttribute("aria-expanded", String(!isCollapsed));
    root.dataset[`${name}Collapsed`] = String(isCollapsed);
  }

  function set(name, isCollapsed) {
    if (!PANES.includes(name)) return;
    collapsed.set(name, Boolean(isCollapsed));
    storage?.set(`${STORAGE_PREFIX}${name}`, isCollapsed ? "collapsed" : "open");
    apply(name);
    onChange?.(name, Boolean(isCollapsed));
  }

  for (const name of PANES) {
    collapsed.set(name, storage?.get(`${STORAGE_PREFIX}${name}`, "open") === "collapsed");
    apply(name);
    panes[name].toggle.addEventListener("click", () => set(name, !collapsed.get(name)));
  }

  return {
    isCollapsed: (name) => collapsed.get(name),
    toggle: (name) => set(name, !collapsed.get(name)),
    set,
  };
}
