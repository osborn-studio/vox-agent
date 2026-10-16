// Feishu conversation history and text outbox copy (T25). Rust owns paging,
// receipts and the outbox; this module turns its codes into words. The
// coverage line is mandatory: a group timeline without a shared-read policy
// shows only what this user sent, and must say so.
import { pick } from "./status-copy.js";

const L = (en, zh) => Object.freeze({ en, zh });

/** `HistoryMode::coverage()` codes. */
export const COVERAGE = Object.freeze({
  personal_chat: L("Your Feishu CLI chat", "你的飞书 CLI 会话"),
  own_submissions_only: L(
    "Only what you sent from Vox Agent · not the group's history",
    "仅显示你从 Vox Agent 发送的内容 · 不是群历史",
  ),
  shared_since_policy: L(
    "Group messages since sharing was enabled · earlier messages are not shown",
    "群消息（自开启共享起）· 更早的消息不显示",
  ),
});

/** `HistoryError` codes (snake_case, as serde serializes them). */
export const HISTORY_ERRORS = Object.freeze({
  sign_in_required: L("Sign in again to read this conversation.", "请重新登录后查看此会话。"),
  not_permitted: L("You may not read this conversation.", "你无权查看此会话。"),
  restart: L("Access changed; loading again from the newest message.", "权限已变化，正在从最新消息重新加载。"),
  read_limit: L("Too many pages in one go. Refresh to start again.", "单次翻页过多，请刷新后重新开始。"),
  rate_limited: L("Too many requests. Wait a moment and try again.", "请求过于频繁，请稍后再试。"),
  unavailable: L("History is unavailable right now.", "暂时无法读取历史。"),
  conversation_changed: L("The server answered for another conversation; nothing was shown.", "服务器返回了其他会话的内容，已忽略。"),
  invalid_response: L("The history page was invalid and was not shown.", "历史页面无效，未显示。"),
  network: L("No connection to the server.", "无法连接服务器。"),
});

/** `EntryState` (outbox) plus `SendStatus` for accepted/final receipts. */
export const OUTBOX = Object.freeze({
  queued: L("Waiting to send", "等待发送"),
  posting: L("Sending…", "发送中…"),
  not_confirmed: L(
    "Not confirmed yet · retrying sends this same message, never a second copy",
    "尚未确认 · 重试只会发送这同一条，不会重复",
  ),
  "accepted.pending": L("Queued on the server", "服务器已排队"),
  "accepted.sending": L("Being delivered to Feishu", "正在投递到飞书"),
  "accepted.unknown": L("Checking delivery…", "正在核对投递结果…"),
  "final.sent": L("Sent to Feishu", "已发送到飞书"),
  "final.failed": L("Not delivered", "未投递"),
  "final.membership_lost": L("Not delivered: you are no longer in this group", "未投递：你已不在此群"),
  "final.unknown": L(
    "Delivery unknown · check Feishu before writing it again",
    "投递结果未知 · 请先在飞书确认，再决定是否重写",
  ),
  conflict: L(
    "This message ID already holds different content; it was not sent again",
    "此消息 ID 已对应其他内容，未再次发送",
  ),
  rejected: L("Refused by the server (too long or invalid)", "服务器拒绝（过长或无效）"),
  blocked: L("Sign-in or permission lost · retry after signing in", "登录或权限失效 · 重新登录后可重试"),
});

export function coverageLine(code, lang) {
  // Unknown coverage never falls back to a wider claim.
  return pick(COVERAGE[code] ?? COVERAGE.own_submissions_only, lang);
}

export function historyError(code, lang) {
  return pick(HISTORY_ERRORS[code] ?? HISTORY_ERRORS.invalid_response, lang);
}

/** One outbox entry as words: `{ state, status? }`. */
export function outboxLine(entry, lang) {
  const key = entry.state === "accepted" || entry.state === "final" ? `${entry.state}.${entry.status}` : entry.state;
  return pick(OUTBOX[key] ?? OUTBOX.not_confirmed, lang);
}

/** Retry is offered only where it resends the same eventId safely. */
export function canRetry(entry) {
  return entry.state === "not_confirmed" || entry.state === "blocked";
}
