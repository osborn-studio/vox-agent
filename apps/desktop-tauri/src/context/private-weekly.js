// Private record mutations (T18a) and weekly report source coverage (T21) as
// words. Rust owns the journal and the collection; this module never states
// more than they do: a weekly draft is the user's own accepted team backups
// for one Asia/Shanghai week, not a complete record of the week's work.
import { pick } from "./status-copy.js";

const L = (en, zh) => Object.freeze({ en, zh });

/** `Phase` of a private mutation (snake_case, as serde writes it). */
export const PRIVATE_PHASES = Object.freeze({
  reserved: L("Saved on this device, not sent yet", "已保存在本机，尚未发送"),
  dispatching: L("Sending…", "发送中…"),
  unknown: L(
    "Result not confirmed yet · checking again sends the same change, never a second one",
    "结果尚未确认 · 再次核对只会发送同一修改，不会重复",
  ),
  applied: L("Saved", "已保存"),
  conflict: L(
    "Changed elsewhere first · your text is kept; review before applying it again",
    "已在别处先被修改 · 你的文字已保留，请核对后再决定",
  ),
  rejected: L("Refused by the server · your text is kept", "服务器拒绝 · 你的文字已保留"),
  blocked: L("Sign in again to continue · your text is kept", "请重新登录后继续 · 你的文字已保留"),
});

/** `CoverageStatement` codes. */
export const COVERAGE = Object.freeze({
  all_pages_authorized_backups_only: L(
    "All pages read · only your accepted team backups you can still see · not a complete record of the week",
    "已读完全部分页 · 仅含你当前有权查看的已接受团队备份 · 不是本周完整工作记录",
  ),
  all_pages_with_omissions: L(
    "All pages read, but some records were left out by the server · not a complete record of the week",
    "已读完全部分页，但服务器略去了部分记录 · 不是本周完整工作记录",
  ),
  partial: L("Not all pages are loaded yet · this draft is partial", "尚未加载全部分页 · 当前草稿不完整"),
});

/** `SourceError` codes. */
export const SOURCE_ERRORS = Object.freeze({
  not_monday: L("Choose the Monday that starts the week.", "请选择一周开始的星期一。"),
  sign_in_required: L("Sign in again to load sources.", "请重新登录后加载来源。"),
  not_permitted: L("This account may not read these sources.", "此账号无权读取这些来源。"),
  invalid_week: L("That week cannot be read.", "无法读取该周。"),
  invalid_cursor: L("The listing changed; loading again from the first page.", "列表已变化，正在从第一页重新加载。"),
  rate_limited: L("Too many requests. Wait a moment and try again.", "请求过于频繁，请稍后再试。"),
  unavailable: L("Sources are unavailable right now.", "暂时无法读取来源。"),
  invalid_response: L("A page did not match the requested week and was not shown.", "某一页与所选周不符，未显示。"),
  network: L("No connection to the server.", "无法连接服务器。"),
});

export const WEEK_BOUNDARY = L(
  "Weeks run Monday to Monday in Asia/Shanghai time, as on macOS.",
  "周界按 Asia/Shanghai 时间周一至下周一计算，与 macOS 一致。",
);

export const RECHECK = Object.freeze({
  unchanged: L("Sources unchanged since review.", "来源自核对后未变化。"),
  changed: L("Sources changed since review · review again before copying.", "来源自核对后已变化 · 复制前请重新核对。"),
  cannot_confirm: L("Could not confirm the sources · load all pages again before copying.", "无法确认来源 · 复制前请重新加载全部分页。"),
});

/** `CopyBlocked` codes: why a weekly draft cannot be copied yet. */
export const COPY_BLOCKED = Object.freeze({
  team_changed: L("Team sources changed since review · review the draft again.", "团队来源自核对后已变化 · 请重新核对草稿。"),
  team_cannot_confirm: L(
    "Could not confirm the team sources · load all pages again.",
    "无法确认团队来源 · 请重新加载全部分页。",
  ),
  private_changed: L(
    "A private note you picked changed or was withdrawn · pick again.",
    "你选择的私人记录已修改或撤回 · 请重新选择。",
  ),
  private_not_checked: L("Checking your private notes before copying…", "复制前正在核对你的私人记录…"),
});

/** `DraftError` codes. */
export const DRAFT_ERRORS = Object.freeze({
  invalid_private_pick: L(
    "Only current private notes from this week can be added, each once.",
    "只能加入本周的当前私人记录，且每条只加一次。",
  ),
  incomplete_sources: L(
    "Load every page of the week before writing a draft.",
    "请先加载本周全部分页，再生成草稿。",
  ),
  too_many_picks: L("Too many private notes for one draft.", "单份草稿选择的私人记录过多。"),
  too_long: L("The draft is too long.", "草稿过长。"),
});

export const STALE_REVISION = L(
  "This note changed since you opened it · showing the current version first.",
  "此记录在你打开后已变化 · 先显示当前版本。",
);

export function privatePhase(phase, lang) {
  return pick(PRIVATE_PHASES[phase] ?? PRIVATE_PHASES.unknown, lang);
}

export function coverageLine(statement, lang) {
  // Anything unrecognized is shown as partial, never as more.
  return pick(COVERAGE[statement] ?? COVERAGE.partial, lang);
}

export function sourceError(code, lang) {
  return pick(SOURCE_ERRORS[code] ?? SOURCE_ERRORS.invalid_response, lang);
}

/** Copy is offered only after an unchanged recheck of a complete read. */
export function canCopy(recheck) {
  return recheck === "unchanged";
}

/** `PrivateServiceError` codes (snake_case, as serde writes them). */
export const PRIVATE_ERRORS = Object.freeze({
  not_signed_in: L("Sign in to use private notes.", "登录后才能使用私人记录。"),
  journal_unavailable: L(
    "Private notes need this device's protected key store, which is not available yet.",
    "私人记录需要本机受保护的密钥存储，目前尚不可用。",
  ),
  stale_identity: L("The account changed; nothing from the earlier account is shown.", "账号已变化，之前账号的内容不会显示。"),
  view_cleared: L("The panel was cleared; load again to see your notes.", "面板已清空，请重新加载查看记录。"),
  busy: L("This note is already being saved.", "这条记录正在保存。"),
  not_found: L("Load your notes again first.", "请先重新加载你的记录。"),
  stale_revision: STALE_REVISION,
  not_allowed: L("That is not possible for this note now.", "这条记录现在不能这样操作。"),
  invalid_input: L("Check the text (1–8192 characters) and tags.", "请检查文字（1–8192 字）和标签。"),
  journal: L("This device could not save the note. Your text is kept.", "本机未能保存这条记录，文字已保留。"),
  refused: L("The server refused this request.", "服务器拒绝了这个请求。"),
  invalid_response: L("The server's reply was invalid and was not shown.", "服务器回复无效，未显示。"),
  network: L("No connection to the server.", "无法连接服务器。"),
});

/** `WeeklyError` codes. */
export const WEEKLY_ERRORS = Object.freeze({
  not_signed_in: L("Sign in to load your weekly sources.", "登录后才能加载周报来源。"),
  invalid_week: L("Choose a Monday from 1992 onward.", "请选择 1992 年以后的星期一。"),
  not_loaded: L("Load the week's sources first.", "请先加载本周来源。"),
  stale: L("The panel was cleared or the account changed; load again.", "面板已清空或账号已变化，请重新加载。"),
  sources: L("The sources could not be read.", "无法读取来源。"),
  draft: L("The draft could not be written.", "无法生成草稿。"),
  private: L("Your private notes could not be checked.", "无法核对你的私人记录。"),
  clipboard: L("Could not copy to the clipboard.", "无法复制到剪贴板。"),
});

export function privateError(error, lang) {
  return pick(PRIVATE_ERRORS[error?.error] ?? PRIVATE_ERRORS.invalid_response, lang);
}

export function weeklyError(error, lang) {
  if (error?.error === "draft" && DRAFT_ERRORS[error.detail]) return pick(DRAFT_ERRORS[error.detail], lang);
  if (error?.error === "private") return privateError(error.detail, lang);
  return pick(WEEKLY_ERRORS[error?.error] ?? WEEKLY_ERRORS.sources, lang);
}

/** Labels the two panels render at run time. */
export const PANEL = Object.freeze({
  saved: L("Saved", "已保存"),
  edit: L("Edit", "编辑"),
  editLabel: L("Edit this private note", "编辑这条私人记录"),
  saveEdit: L("Save edit", "保存修改"),
  cancel: L("Cancel", "取消"),
  withdraw: L("Withdraw", "撤回"),
  confirmWithdraw: L("Confirm withdraw", "确认撤回"),
  include: L("Use in weekly draft", "用于周报草稿"),
  includeSource: L("Include this source in the draft", "将此来源加入草稿"),
  copied: L("Copied. Sources were checked just now.", "已复制，来源刚刚核对过。"),
  counts: L("{included} included · {excluded} excluded · {pages} pages", "已含 {included} · 已排除 {excluded} · {pages} 页"),
  tags: Object.freeze({
    decision: L("decision", "决定"),
    progress: L("progress", "进展"),
    blocker: L("blocker", "阻碍"),
    question: L("question", "问题"),
    verification: L("verification", "核实"),
  }),
  delivery: Object.freeze({
    pending: L("queued", "排队中"),
    sending: L("sending", "发送中"),
    unknown: L("result unknown", "结果未知"),
    delivered: L("delivered", "已投递"),
    blocked: L("blocked", "被阻止"),
    rejected: L("rejected", "被拒绝"),
  }),
});
