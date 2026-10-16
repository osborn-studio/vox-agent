// Private inbox in the right panel (T18b-T20, T23b). Rust owns the journal,
// identity, revisions and the record cache; this module renders what the
// commands return and never chooses an owner, a revision it did not show, or
// a state. Withdrawal needs a second, explicit click on the same row.
// clear() drops every private word from the page (collapse, hide/lock,
// sign-out): visible and detached nodes are emptied and no handler keeps a
// record; saved mutations stay in Rust.
import { privatePhase, privateError, PANEL } from "./private-weekly.js";
import { pick } from "./status-copy.js";

const TAGS = Object.freeze(["decision", "progress", "blocker", "question", "verification"]);
const MAX_STEPS = 6;

function utc(date) {
  return date.toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

/** The last seven days, as the server's +00:00 window. */
export function recentWindow(now = new Date()) {
  const end = new Date(Math.ceil(now.getTime() / 1000) * 1000 + 1000);
  const start = new Date(end.getTime() - 7 * 86_400_000);
  return { start: utc(start), end: utc(end) };
}

/** Empty every text-bearing node under `root` (detached copies included). */
export function scrub(root) {
  // Standard DOM traversal: the root and every descendant, collected before
  // any of them is emptied.
  const nodes = [root, ...(root.querySelectorAll ? root.querySelectorAll("*") : [])];
  for (const node of nodes) {
    if ("value" in node) node.value = "";
  }
  for (const node of nodes.reverse()) node.textContent = "";
}

export function createPrivatePanel({ doc, invoke, el, lang }) {
  // Bumped by clear(): replies to requests made before it are dropped.
  let epoch = 0;
  let records = [];
  const picks = new Set();
  let confirming = null;
  let editing = null;
  let editText = "";
  // The revision and tags the edit was opened on: a save is written against
  // exactly them, even if a refresh brought a newer revision meanwhile.
  let editBase = null;
  let focusAfter = null;
  // The status line as a function of the language, so a switch re-translates.
  let status = null;
  const say = (fn) => {
    status = fn;
    el.status.textContent = fn ? fn(lang()) : "";
  };
  const sayError = (error) => {
    if (error?.error === "journal_unavailable") el.unavailable.hidden = false;
    say((l) => privateError(error, l));
  };
  const find = (id) => records.find((r) => r.id === id);

  function selectedTags() {
    return el.tags.filter((box) => box.checked).map((box) => box.dataset.tag);
  }

  async function drive(key, mine, focusRole = null) {
    for (let i = 0; i < MAX_STEPS; i += 1) {
      const result = await invoke("context_private_step", { input: { key, retry: false } });
      if (mine !== epoch) return;
      if (result.kind === "current") {
        const record = result.record;
        if (focusRole && !record.withdrawn) focusAfter = { id: record.id, role: focusRole };
        upsert(record);
        if (focusRole && record.withdrawn) el.refresh.focus?.();
        say((l) => pick(PANEL.saved, l));
        return;
      }
      const phase = result.phase;
      say((l) => privatePhase(phase, l));
      if (!["reserved", "unknown", "applied"].includes(phase)) return;
    }
  }

  function upsert(record) {
    records = records.filter((r) => r.id !== record.id);
    if (!record.withdrawn) records.unshift(record);
    else picks.delete(record.id);
    render();
  }

  async function save() {
    const text = el.text.value;
    if (!text.trim()) return;
    const mine = epoch;
    try {
      const phase = await invoke("context_private_create", {
        input: { text, tags: selectedTags(), projectId: null, repositoryId: null },
      });
      if (mine !== epoch) return;
      el.text.value = "";
      for (const box of el.tags) box.checked = false;
      say((l) => privatePhase(phase.phase, l));
      await drive(phase.key, mine);
    } catch (error) {
      if (mine === epoch) sayError(error);
    }
  }

  async function refresh() {
    const mine = epoch;
    try {
      const page = await invoke("context_private_list", {
        input: { ...recentWindow(), limit: 50, cursor: null, tag: null, projectId: null, text: null },
      });
      if (mine !== epoch) return;
      records = page.items;
      for (const id of [...picks]) if (!find(id)) picks.delete(id);
      el.omitted.hidden = !page.omittedRecords;
      render();
      say(null);
    } catch (error) {
      if (mine === epoch) sayError(error);
    }
  }

  async function saveEdit(id) {
    if (editing !== id || !editBase) return;
    const base = editBase;
    const mine = epoch;
    try {
      const phase = await invoke("context_private_edit", {
        input: { recordId: id, seenRevision: base.revision, text: editText, tags: base.tags },
      });
      if (mine !== epoch) return;
      editing = null;
      editText = "";
      editBase = null;
      focusAfter = { id, role: "edit-open" };
      render();
      await drive(phase.key, mine, "edit-open");
    } catch (error) {
      if (mine === epoch) sayError(error);
    }
  }

  async function withdraw(id) {
    const record = find(id);
    if (!record) return;
    const mine = epoch;
    try {
      const phase = await invoke("context_private_withdraw", {
        input: { recordId: record.id, seenRevision: record.revision, confirm: true },
      });
      if (mine !== epoch) return;
      confirming = null;
      await drive(phase.key, mine, "withdraw");
    } catch (error) {
      if (mine === epoch) sayError(error);
    }
  }

  function button(label, role, onClick, className = "text-button") {
    const node = doc.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    node.dataset.role = role;
    node.addEventListener("click", onClick);
    return node;
  }

  // Handlers below capture only the record id; the record itself is looked
  // up when they run, so a cleared panel leaves nothing reachable.
  function row(record) {
    const l = lang();
    const id = record.id;
    const item = doc.createElement("li");
    item.className = "private-note";
    item.dataset.recordId = id;
    const meta = doc.createElement("small");
    meta.className = "inline-note mono-copy";
    const tags = record.tags.map((t) => pick(PANEL.tags[t], l)).join(" · ");
    meta.textContent = `${record.createdAt.slice(0, 10)} · r${record.revision}${tags ? ` · ${tags}` : ""}`;
    item.append(meta);
    if (editing === id) {
      const box = doc.createElement("textarea");
      box.className = "private-edit";
      box.dataset.role = "edit";
      box.value = editText;
      box.setAttribute("aria-label", pick(PANEL.editLabel, l));
      box.addEventListener("input", () => {
        editText = box.value;
      });
      item.append(
        box,
        button(pick(PANEL.saveEdit, l), "save-edit", () => saveEdit(id), "button quiet"),
        button(pick(PANEL.cancel, l), "cancel", () => {
          editing = null;
          editText = "";
          editBase = null;
          focusAfter = { id, role: "edit-open" };
          render();
        }),
      );
      return item;
    }
    const body = doc.createElement("p");
    body.className = "private-text";
    body.textContent = record.text ?? "";
    item.append(body);
    const include = doc.createElement("input");
    include.type = "checkbox";
    include.dataset.role = "include";
    include.checked = picks.has(id);
    include.setAttribute("aria-label", pick(PANEL.include, l));
    include.addEventListener("change", () => {
      if (include.checked && find(id)) picks.add(id);
      else picks.delete(id);
    });
    const includeLabel = doc.createElement("label");
    includeLabel.className = "inline-note";
    const includeText = doc.createElement("span");
    includeText.textContent = pick(PANEL.include, l);
    includeLabel.append(include, includeText);
    item.append(
      includeLabel,
      button(pick(PANEL.edit, l), "edit-open", () => {
        const current = find(id);
        if (!current) return;
        editing = id;
        editText = current.text ?? "";
        editBase = { revision: current.revision, tags: [...current.tags] };
        focusAfter = { id, role: "edit" };
        render();
      }),
    );
    if (confirming === id) {
      item.append(
        button(pick(PANEL.confirmWithdraw, l), "confirm-withdraw", () => withdraw(id), "button danger"),
        button(pick(PANEL.cancel, l), "cancel", () => {
          confirming = null;
          focusAfter = { id, role: "withdraw" };
          render();
        }),
      );
    } else {
      item.append(
        button(pick(PANEL.withdraw, l), "withdraw", () => {
          confirming = id;
          focusAfter = { id, role: "confirm-withdraw" };
          render();
        }),
      );
    }
    return item;
  }

  function render() {
    for (const old of [...el.list.children]) scrub(old);
    el.list.replaceChildren(...records.map(row));
    el.empty.hidden = records.length > 0;
    if (focusAfter) {
      const { id, role } = focusAfter;
      focusAfter = null;
      const item = el.list.children.find?.((n) => n.dataset.recordId === id)
        ?? [...el.list.children].find((n) => n.dataset.recordId === id);
      const target = item?.all
        ? item.all().find((n) => n.dataset?.role === role)
        : item?.querySelector?.(`[data-role="${role}"]`);
      (target ?? item)?.focus?.();
    }
    if (status) el.status.textContent = status(lang());
  }

  /** Drop every private word from the page; saved mutations stay in Rust. */
  function clear() {
    epoch += 1;
    for (const old of [...el.list.children]) scrub(old);
    records = [];
    picks.clear();
    confirming = null;
    editing = null;
    editText = "";
    editBase = null;
    focusAfter = null;
    el.text.value = "";
    for (const box of el.tags) box.checked = false;
    el.list.replaceChildren();
    el.empty.hidden = false;
    el.omitted.hidden = true;
    say(null);
    invoke("context_private_clear_view").catch(() => {});
  }

  el.save.addEventListener("click", save);
  el.refresh.addEventListener("click", refresh);

  return {
    clear,
    refresh,
    render,
    /** Private record ids the user ticked for the weekly draft. */
    picks: () => [...picks],
    get records() {
      return records;
    },
  };
}

export { TAGS };
