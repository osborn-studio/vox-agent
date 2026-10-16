// Weekly report draft in the right panel (T21, T22, T23b). Rust holds the
// sources and the draft and decides what may be copied; this module lists the
// sources, keeps the user's exclusions, shows the draft for editing and
// reports a copy that was refused. The coverage line is always shown; it never
// claims a complete week. clear() drops the sources and the draft.
import { coverageLine, COPY_BLOCKED, PANEL, sourceError, weeklyError } from "./private-weekly.js";
import { scrub } from "./private-panel.js";
import { pick } from "./status-copy.js";

/** The Monday that starts the current Asia/Shanghai week (fixed UTC+8). */
export function currentMonday(now = new Date()) {
  const shanghai = new Date(now.getTime() + 8 * 3_600_000);
  const day = shanghai.getUTCDay();
  shanghai.setUTCDate(shanghai.getUTCDate() - ((day + 6) % 7));
  return shanghai.toISOString().slice(0, 10);
}

export function createWeeklyPanel({ doc, invoke, el, lang, privatePicks }) {
  let epoch = 0;
  let loaded = null;
  let drafted = false;
  let copying = false;
  let composing = false;
  // Advanced whenever the sources or the draft change (load, exclude,
  // compose, clear): a copy whose draft was replaced while it waited stops.
  let version = 0;
  // The backend version of the draft on screen (from compose and edit
  // replies); a copy names it, so Rust copies exactly that draft or nothing.
  let draftVersion = null;
  let focusAfter = null;
  // The status line as a function of the language, so a switch re-translates.
  let status = null;
  const say = (fn) => {
    status = fn;
    el.status.textContent = fn ? fn(lang()) : "";
  };
  const sayError = (error) => {
    if (error?.error === "sources") say((l) => sourceError(error.detail, l));
    else say((l) => weeklyError(error, l));
  };

  function setDraft(text) {
    version += 1;
    el.draft.value = text;
    drafted = text !== "";
    el.draft.hidden = !drafted;
    if (el.draftField) el.draftField.hidden = !drafted;
    el.copy.disabled = !drafted || copying || composing;
  }

  async function load() {
    const mine = epoch;
    try {
      const result = await invoke("context_weekly_load", { monday: el.week.value });
      if (mine !== epoch) return;
      loaded = result;
      setDraft("");
      render();
      say(null);
    } catch (error) {
      if (mine === epoch) sayError(error);
    }
  }

  async function exclude(eventId, excluded) {
    const mine = epoch;
    try {
      const result = await invoke("context_weekly_exclude", { eventId, excluded });
      if (mine !== epoch) return;
      loaded = result;
      setDraft("");
      focusAfter = eventId;
      render();
    } catch (error) {
      if (mine === epoch) sayError(error);
    }
  }

  async function compose() {
    const mine = epoch;
    composing = true;
    el.copy.disabled = true;
    try {
      const draft = await invoke("context_weekly_compose", {
        input: { lang: lang(), privateRecordIds: privatePicks() },
      });
      if (mine !== epoch) return;
      draftVersion = draft.version;
      composing = false;
      setDraft(draft.text);
      say(null);
    } catch (error) {
      if (mine === epoch) sayError(error);
    } finally {
      if (mine === epoch) {
        composing = false;
        el.copy.disabled = !drafted || copying;
      }
    }
  }

  /** Save the edited text; its backend version, or null if refused/cleared. */
  async function saveEdit() {
    if (!drafted || draftVersion === null) return null;
    const mine = epoch;
    const drafting = version;
    try {
      const saved = await invoke("context_weekly_edit", { text: el.draft.value, version: draftVersion });
      if (mine !== epoch || drafting !== version) return null;
      draftVersion = saved;
      return saved;
    } catch (error) {
      if (mine === epoch) sayError(error);
      return null;
    }
  }

  /** One copy at a time; never after a refused edit or a clear. */
  async function copy() {
    if (copying || composing) return;
    const mine = epoch;
    const drafting = version;
    copying = true;
    el.copy.disabled = true;
    // The text being copied cannot change while the copy runs.
    el.draft.readOnly = true;
    el.compose.disabled = true;
    el.load.disabled = true;
    try {
      const saved = await saveEdit();
      if (saved === null || mine !== epoch || drafting !== version) return;
      const result = await invoke("context_weekly_copy", { version: saved });
      if (mine !== epoch) return;
      const blocked = result.blocked;
      say((l) => (result.copied ? pick(PANEL.copied, l) : pick(COPY_BLOCKED[blocked] ?? COPY_BLOCKED.team_cannot_confirm, l)));
    } catch (error) {
      if (mine === epoch) sayError(error);
    } finally {
      if (mine === epoch) {
        copying = false;
        el.copy.disabled = !drafted;
        el.draft.readOnly = false;
        el.load.disabled = false;
        render();
      }
    }
  }

  // The handler captures only the eventId, never the source text.
  function row(source) {
    const l = lang();
    const eventId = source.eventId;
    const item = doc.createElement("li");
    item.className = "weekly-source";
    item.dataset.eventId = eventId;
    const box = doc.createElement("input");
    box.type = "checkbox";
    box.checked = !source.excluded;
    box.setAttribute("aria-label", pick(PANEL.includeSource, l));
    box.addEventListener("change", () => exclude(eventId, !box.checked));
    const meta = doc.createElement("small");
    meta.className = "inline-note mono-copy";
    meta.textContent = `${source.date} · ${pick(PANEL.delivery[source.deliveryStatus] ?? PANEL.delivery.unknown, l)}`;
    const body = doc.createElement("p");
    body.className = "weekly-text";
    body.textContent = source.text;
    item.append(box, meta, body);
    return item;
  }

  function render() {
    for (const old of [...el.list.children]) scrub(old);
    if (!loaded) {
      el.coverage.textContent = "";
      el.list.replaceChildren();
      el.compose.disabled = true;
      if (status) el.status.textContent = status(lang());
      return;
    }
    const c = loaded.coverage;
    el.coverage.textContent = `${coverageLine(c.statement, lang())} · ${pick(PANEL.counts, lang())
      .replace("{included}", c.included).replace("{excluded}", c.excluded).replace("{pages}", c.pages)}`;
    el.list.replaceChildren(...loaded.sources.map(row));
    el.compose.disabled = c.statement !== "all_pages_authorized_backups_only" && c.statement !== "all_pages_with_omissions";
    if (focusAfter) {
      const id = focusAfter;
      focusAfter = null;
      const item = [...el.list.children].find((n) => n.dataset.eventId === id);
      item?.children?.[0]?.focus?.();
    }
    if (status) el.status.textContent = status(lang());
  }

  /** Drop the sources and the draft from the page and from Rust. */
  function clear() {
    epoch += 1;
    copying = false;
    composing = false;
    draftVersion = null;
    el.draft.readOnly = false;
    el.load.disabled = false;
    focusAfter = null;
    loaded = null;
    setDraft("");
    render();
    say(null);
    invoke("context_weekly_clear").catch(() => {});
  }

  el.week.value = currentMonday();
  el.load.addEventListener("click", load);
  el.compose.addEventListener("click", compose);
  el.copy.addEventListener("click", copy);
  el.draft.addEventListener("change", saveEdit);
  // Typing changes the draft: a copy started before it does not copy it.
  el.draft.addEventListener("input", () => {
    version += 1;
  });
  render();
  setDraft("");

  return { clear, render, load };
}
