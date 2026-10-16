// Context record timeline (T14): local inputs and their delivery status.
// This is not the group's message history and says so.
import { t } from "./i18n.js";
import { describe } from "./status-copy.js";

const EVENT_ID = /^[A-Za-z0-9_-]{1,128}$/;

/** Insert or replace by eventId, newest first; malformed DTOs are ignored. */
export function upsertRecord(records, dto) {
  if (!dto || typeof dto.eventId !== "string" || !EVENT_ID.test(dto.eventId)) return records;
  return records.some((r) => r.eventId === dto.eventId)
    ? records.map((r) => (r.eventId === dto.eventId ? dto : r))
    : [dto, ...records];
}

/**
 * Text for the polite live region, or null when nothing new should be read
 * out: repeated polls with the same status are merged into silence.
 */
export function announcement(previous, dto, lang = "en") {
  const now = describe(dto, lang).label;
  if (previous && describe(previous, lang).label === now) return null;
  return t("record.announce", lang, { id: dto.eventId.slice(-6), label: now });
}

function createItem(doc) {
  const item = doc.createElement("article");
  item.setAttribute("role", "listitem");
  const edge = doc.createElement("span");
  edge.setAttribute("aria-hidden", "true");
  const body = doc.createElement("div");
  body.className = "context-record-body";
  const status = doc.createElement("p");
  status.className = "context-record-status";
  const glyph = doc.createElement("span");
  glyph.setAttribute("aria-hidden", "true");
  const label = doc.createElement("strong");
  status.append(glyph, label);
  const detail = doc.createElement("p");
  detail.className = "secondary-copy";
  const id = doc.createElement("small");
  id.className = "mono-copy";
  body.append(status, detail, id);
  item.append(edge, body);
  item.parts = { edge, body, glyph, label, detail, id, check: null, view: null, original: null };
  return item;
}

function updateItem(doc, item, dto, onCheck, onView, lang) {
  const copy = describe(dto, lang);
  const parts = item.parts;
  item.className = `context-record tone-${copy.tone}`;
  item.dataset.eventId = dto.eventId;
  parts.edge.className = `card-edge ${copy.tone}`;
  parts.glyph.className = `status-glyph ${copy.tone}`;
  parts.glyph.textContent = copy.glyph;
  parts.label.textContent = copy.label;
  parts.detail.textContent = copy.detail;
  parts.id.textContent = dto.eventId;
  const wantsCheck = Boolean(copy.canCheck && onCheck);
  if (wantsCheck && !parts.check) {
    const check = doc.createElement("button");
    check.className = "button quiet";
    check.type = "button";
    check.textContent = t("record.check", lang);
    check.setAttribute("aria-label", t("record.checkLabel", lang, { id: dto.eventId }));
    check.addEventListener("click", () => onCheck(dto.eventId, check));
    parts.body.append(check);
    parts.check = check;
  } else if (!wantsCheck && parts.check) {
    parts.check.remove();
    parts.check = null;
  }
  // A language change relabels existing buttons in place.
  if (parts.check) {
    parts.check.textContent = t("record.check", lang);
    parts.check.setAttribute("aria-label", t("record.checkLabel", lang, { id: dto.eventId }));
  }
  if (parts.view) {
    parts.view.textContent = t("original.view", lang);
    parts.view.setAttribute("aria-label", t("original.viewLabel", lang, { id: dto.eventId }));
  }
  // Quarantined records failed authentication: their text is never offered.
  const wantsView = Boolean(onView && dto.action !== "quarantine");
  if (wantsView && !parts.view) {
    const view = doc.createElement("button");
    view.className = "button quiet";
    view.type = "button";
    view.textContent = t("original.view", lang);
    view.setAttribute("aria-label", t("original.viewLabel", lang, { id: dto.eventId }));
    view.addEventListener("click", () => onView(dto.eventId, item));
    parts.body.append(view);
    parts.view = view;
  } else if (!wantsView && parts.view) {
    parts.view.remove();
    parts.view = null;
    hideOriginal(item);
  }
}

/**
 * Remove every revealed original (and its copy feedback) from the list and
 * invalidate every pending reveal. Cards and records stay. Returns how many
 * originals were hidden.
 */
export function hideAllOriginals(list) {
  let hidden = 0;
  for (const item of Array.from(list.children)) {
    if (item.parts?.original) hidden += 1;
    hideOriginal(item);
  }
  return hidden;
}

/**
 * Remove a revealed original from a card. Its text nodes are emptied before
 * removal so no detached node keeps plaintext, and any reveal still in flight
 * for this card is invalidated.
 */
export function hideOriginal(item) {
  if (!item.parts) return;
  const box = item.parts.original;
  if (box) {
    for (const node of box.children ?? []) node.textContent = "";
    box.replaceChildren?.();
    box.remove();
  }
  item.parts.original = null;
  item.parts.revealSeq = (item.parts.revealSeq ?? 0) + 1;
}

/** Start a reveal for a card; it is valid until the card is hidden or removed. */
export function beginReveal(item) {
  return { item, seq: item.parts?.revealSeq ?? 0 };
}

/** Whether a reveal started with beginReveal may still show its result. */
export function revealCurrent(list, token) {
  return (token.item.parts?.revealSeq ?? 0) === token.seq && list.contains(token.item);
}

/**
 * Reveal a verified original inside its card, with Copy and Hide. The text
 * is set with textContent only.
 */
export function showOriginal(doc, item, text, { onCopy, lang = "en" } = {}) {
  hideOriginal(item);
  const box = doc.createElement("div");
  box.className = "context-original";
  const body = doc.createElement("p");
  body.className = "context-original-text";
  body.textContent = text;
  const feedback = doc.createElement("span");
  feedback.className = "inline-note";
  feedback.setAttribute("role", "status");
  const copy = doc.createElement("button");
  copy.className = "button quiet";
  copy.type = "button";
  copy.textContent = t("original.copy", lang);
  // Copy never captures the shown text: the page re-reads the original.
  copy.addEventListener("click", async () => {
    const copied = await onCopy?.();
    if (item.parts.original === box) feedback.textContent = t(copied ? "original.copied" : "original.copyFailed", lang);
  });
  const hide = doc.createElement("button");
  hide.className = "text-button";
  hide.type = "button";
  hide.textContent = t("original.hide", lang);
  hide.addEventListener("click", () => {
    hideOriginal(item);
    item.parts.view?.focus();
  });
  const notice = doc.createElement("small");
  notice.className = "inline-note";
  notice.textContent = t("original.notice", lang);
  box.append(body, copy, hide, feedback, notice);
  item.parts.body.append(box);
  item.parts.original = box;
  return box;
}

/** Explain why no text is shown (e.g. a failed integrity check). */
export function showOriginalError(doc, item, message) {
  hideOriginal(item);
  const box = doc.createElement("p");
  box.className = "inline-note";
  box.setAttribute("role", "status");
  box.textContent = message;
  item.parts.body.append(box);
  item.parts.original = box;
}

/**
 * Keyed, incremental render: existing cards (and a focused or disabled
 * "Check again" button inside them) are updated in place, never rebuilt,
 * and only moved when their position actually changes.
 */
export function renderRecords(doc, list, records, { onCheck, onView, lang = "en" } = {}) {
  const existing = new Map(Array.from(list.children, (child) => [child.dataset.eventId, child]));
  const wanted = new Set(records.map((dto) => dto.eventId));
  for (const [eventId, child] of existing) {
    if (!wanted.has(eventId)) child.remove();
  }
  records.forEach((dto, index) => {
    const item = existing.get(dto.eventId) ?? createItem(doc);
    updateItem(doc, item, dto, onCheck, onView, lang);
    const current = list.children[index];
    if (current !== item) list.insertBefore(item, current ?? null);
  });
}
