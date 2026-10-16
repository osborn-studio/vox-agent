// Chinese and English strings for the Context panel, composer and timeline
// (T39). Status texts live in status-copy.js. Only the language preference
// is stored, never content.
import { LANGUAGES } from "./status-copy.js";

const L = (en, zh) => Object.freeze({ en, zh });

export const STRINGS = Object.freeze({
  "scope.group": L("local inputs and delivery status · not the group's full history", "本机输入与投递状态 · 不是群的完整历史"),
  "scope.session": L("current session", "当前会话"),
  "panel.context": L("Context", "Context"),
  "panel.operations": L("Operations", "运行"),
  "composer.labelGroup": L("Context record", "Context 记录"),
  "composer.labelAgent": L("Command for {name}", "发给 {name} 的指令"),
  "composer.placeholderGroup": L("Record what you are working on for the team…", "记录你正在做的工作，同步给团队…"),
  "composer.placeholderAgent": L("Tell your agents what outcome you want…", "告诉 Agent 你想要的结果…"),
  "composer.bytes": L("{bytes} / {max} bytes", "{bytes} / {max} 字节"),
  "project.none": L("No project selected", "未指定项目"),
  "project.original": L("{repository} (original project)", "{repository}（原项目）"),
  "project.help": L(
    "Select a project before typing; clear the draft to switch. Input started before the projects load stays unattributed.",
    "先选择项目再输入；清空草稿后可切换。目录加载前开始的输入保持未指定项目。",
  ),
  "project.fixed": L("Attribution fixed for this input: {repository}", "本次输入归属已固定：{repository}"),
  "project.unspecified": L("unspecified", "未指定"),
  "project.omitted": L("{count} project entries were invalid and are not shown.", "{count} 个项目条目无效，未显示。"),
  "project.loadFailed": L("Projects could not be loaded. Existing drafts keep their attribution.", "无法加载项目目录。已有草稿保持原归属。"),
  "repository.matches": L(
    "Matching projects: {list}. Choose one in the project menu; attribution was not changed automatically.",
    "可核对项目：{list}。请在项目菜单中明确选择；归属未自动修改。",
  ),
  "repository.none": L("No matching authorized project; attribution unchanged.", "未找到可核对的授权项目；归属保持不变。"),
  "repository.failed": L("This folder cannot be inspected read-only. Select a project manually.", "无法只读核对此目录，请手动选择项目。"),
  "original.integrity": L("This original failed its integrity check. It is not shown or copied.", "原文未通过完整性校验，不会显示或复制。"),
  "original.verifyOnly": L("Local records are being verified after a restore. Try again after verification.", "恢复后正在核对本机记录，请核对完成后再试。"),
  "original.notFound": L("This original is no longer stored on this device.", "本机已不再保存这份原文。"),
  "original.failed": L("The original could not be read.", "无法读取原文。"),
  "original.view": L("View original", "查看原文"),
  "original.viewLabel": L("View the original text of record {id}", "查看记录 {id} 的原文"),
  "original.copy": L("Copy", "复制"),
  "original.hide": L("Hide", "隐藏"),
  "original.copied": L("Copied.", "已复制。"),
  "original.copyFailed": L("Copy failed.", "复制失败。"),
  "original.notice": L("Hidden again when you leave this window or collapse a pane.", "离开窗口或收起侧栏时会再次隐藏。"),
  "record.check": L("Check again", "再次核对"),
  "record.checkLabel": L("Check record {id} again without sending it", "只读核对记录 {id}，不会再次发送"),
  "record.announce": L("Record {id}: {label}", "记录 {id}：{label}"),
  "session.notSetUp": L("Not set up on this device", "本机尚未启用"),
  "session.signedIn": L("Signed in", "已登录"),
  "session.signedOut": L("Signed out", "未登录"),
  "quota.line": L("{count} of {max} records stored on this device", "本机已保存 {count} / {max} 条记录"),
  "quota.unavailable": L("Local records are unavailable.", "本机记录暂不可用。"),
  "legend.title": L("What the states mean", "状态说明"),
  "legend.local": L("Saved on this device", "已保存在本机"),
  "legend.localDetail": L("Encrypted locally. Not yet accepted by the server.", "已在本机加密保存，服务端尚未接受。"),
  "legend.accepted": L("Accepted by the server", "服务端已接受"),
  "legend.acceptedDetail": L("Queued for delivery. Not yet in the group.", "已排队等待投递，尚未进入群。"),
  "legend.delivered": L("Delivered", "已投递"),
  "legend.deliveredDetail": L("The server reported delivery with a message id.", "服务端报告已投递，并给出消息编号。"),
  "legend.verifying": L("Result being verified", "结果待核对"),
  "legend.verifyingDetail": L("The request may have arrived. It is read back, never sent twice automatically.", "请求可能已到达，只读回核对，不会自动重复发送。"),
  "flow.note": L(
    "Your inputs to the team and their delivery status. This is not the group's full message history.",
    "这里是你给团队的输入及其投递状态，不是群的完整消息历史。",
  ),
  "flow.empty": L("No records yet. What you record is saved on this device first, then sent.", "暂无记录。记录会先保存在本机，再发送。"),
  "flow.loadOlder": L("Load older records", "加载更早的记录"),
  "delivery.title": L("Delivery", "投递"),
  "delivery.destination": L("Destination", "记录目标"),
  "project.title": L("Project", "项目"),
  "project.label": L("Project for this input", "本次输入的项目"),
  "project.refresh": L("Refresh projects", "刷新目录"),
  "project.inspect": L("Inspect local repository", "核对本机仓库"),
  "diagnostics.title": L("Context diagnostics", "Context 诊断"),
  "diagnostics.refresh": L("Refresh diagnostics", "刷新诊断"),
  "diagnostics.version": L("App version", "应用版本"),
  "diagnostics.platform": L("Platform", "平台"),
  "diagnostics.setup": L("Context recording", "Context 记录"),
  "diagnostics.signIn": L("Sign-in", "登录"),
  "diagnostics.keyStore": L("Windows key store", "Windows 密钥存储"),
  "diagnostics.journal": L("Encrypted journal", "加密日志"),
  "diagnostics.records": L("Records on this device", "本机记录"),
  "diagnostics.ready": L("ready", "已就绪"),
  "diagnostics.notSetUp": L("not set up", "未启用"),
  "diagnostics.yes": L("signed in", "已登录"),
  "diagnostics.no": L("not signed in", "未登录"),
  "diagnostics.notAvailable": L("not available in this build", "当前版本不可用"),
  "diagnostics.normal": L("normal", "正常"),
  "diagnostics.verifyOnly": L("verifying after a restore", "恢复后核对中"),
  "diagnostics.unavailable": L("unavailable", "不可用"),
  "diagnostics.none": L("none", "无"),
  "diagnostics.error": L("Diagnostics could not be read.", "无法读取诊断信息。"),
  "language.title": L("Language", "语言"),
  "aria.sidebar": L("Show or hide the sidebar (Ctrl+Alt+B)", "显示或隐藏侧栏（Ctrl+Alt+B）"),
  "title.sidebar": L("Sidebar (Ctrl+Alt+B)", "侧栏（Ctrl+Alt+B）"),
  "aria.inspector": L("Show or hide the right panel (Ctrl+Alt+I)", "显示或隐藏右侧面板（Ctrl+Alt+I）"),
  "title.inspector": L("Right panel (Ctrl+Alt+I)", "右侧面板（Ctrl+Alt+I）"),
  "aria.rightPanel": L("Right panel", "右侧面板"),
  "aria.records": L("Context records", "Context 记录"),
  "aria.record": L("Record and sync", "记录并同步"),
  "title.record": L("Record and sync (Ctrl+Enter)", "记录并同步（Ctrl+Enter）"),
  "aria.inspect": L(
    "Inspect a local repository read-only; attribution stays unchanged",
    "只读核对本机仓库；归属不会改变",
  ),
  "account.title": L("Feishu account", "飞书账号"),
  "account.signIn": L("Sign in with Feishu", "用飞书登录"),
  "account.signOut": L("Sign out", "退出登录"),
  "account.signingIn": L(
    "Finish signing in in your browser. This waits up to one minute.",
    "请在浏览器中完成登录，最多等待一分钟。",
  ),
  "account.signedInAs": L("Signed in as {name}", "已登录：{name}"),
  "account.signedInNoName": L("Signed in", "已登录"),
  "account.signedOut": L("Not signed in", "未登录"),
  "account.notAvailable": L(
    "Sign-in is not available in this build: no Context server is configured.",
    "当前版本无法登录：未配置 Context 服务。",
  ),
  "account.signedOutDone": L("Signed out. The server session was ended.", "已退出登录，服务端会话已结束。"),
  "account.signedOutLocalOnly": L(
    "Signed out on this device. The server did not confirm ending the session; it expires on its own.",
    "已在本机退出登录。服务端未确认结束会话，会话将自行过期。",
  ),
  "account.signedOutStoreFailed": L(
    "Signed out, but the saved sign-in could not be removed from the credential store.",
    "已退出登录，但无法从凭据存储中删除已保存的登录信息。",
  ),
  "account.error.not-available": L(
    "Sign-in is not available in this build: no Context server is configured.",
    "当前版本无法登录：未配置 Context 服务。",
  ),
  "account.error.sign-in-in-progress": L("A sign-in is already in progress.", "已有一次登录正在进行。"),
  "account.error.already-signed-in": L("Already signed in. Sign out first to switch accounts.", "已登录。如需切换账号，请先退出。"),
  "account.error.cancelled": L("The sign-in was cancelled because you signed out.", "已退出登录，本次登录已取消。"),
  "account.error.not-signed-in": L("Sign in to see your conversations.", "登录后可查看会话。"),
  "account.error.sign-in-required": L(
    "The server no longer accepts this sign-in. You were signed out; sign in again.",
    "服务端已不再接受当前登录，已退出，请重新登录。",
  ),
  "account.error.account-disabled": L(
    "This account is disabled on the server. Ask an administrator.",
    "该账号已在服务端停用，请联系管理员。",
  ),
  "account.error.organization-not-configured": L(
    "Your Feishu organization is not set up on this server. Ask an administrator.",
    "你的飞书组织尚未在此服务端配置，请联系管理员。",
  ),
  "account.error.not-permitted": L(
    "This account may not list Feishu conversations.",
    "当前账号无权查看飞书会话。",
  ),
  "account.error.identity-changed": L(
    "The server reported a different account. You were signed out; sign in again.",
    "服务端返回了不同的账号，已退出，请重新登录。",
  ),
  "account.error.rate-limited": L("Too many attempts. Wait a minute and try again.", "尝试过于频繁，请稍候一分钟再试。"),
  "account.error.login-unavailable": L("Sign-in is unavailable on the server right now.", "服务端暂时无法登录。"),
  "account.error.conversations-unavailable": L(
    "Conversations are unavailable on the server right now.",
    "服务端暂时无法提供会话列表。",
  ),
  "account.error.denied": L("The sign-in was declined in the browser.", "已在浏览器中拒绝登录。"),
  "account.error.timeout": L("The browser did not finish signing in in time. Try again.", "浏览器未在规定时间内完成登录，请重试。"),
  "account.error.expired": L("This sign-in attempt expired. Start again.", "本次登录已过期，请重新开始。"),
  "account.error.network": L("The server could not be reached.", "无法连接服务端。"),
  "account.error.browser-unavailable": L("No browser could be opened for sign-in.", "无法打开浏览器进行登录。"),
  "account.error.store-unavailable": L(
    "The sign-in could not be saved in the credential store. You are not signed in.",
    "无法将登录信息保存到凭据存储，未登录。",
  ),
  "account.error.invalid-response": L("The server answered in an unexpected way.", "服务端返回了无法识别的结果。"),
  "conversations.title": L("Feishu conversations", "飞书会话"),
  "conversations.refresh": L("Refresh", "刷新"),
  "conversations.loading": L("Loading conversations…", "正在加载会话…"),
  "conversations.empty": L("No conversations are available to this account.", "当前账号没有可用的会话。"),
  "conversations.omitted": L("{count} entries were invalid and are not shown.", "{count} 个条目无效，未显示。"),
  "conversations.personal": L("Personal", "个人"),
  "conversations.group": L("Group", "群"),
  "conversations.canSend": L("can send text", "可发送文字"),
  "conversations.viewOnly": L("view only", "仅查看"),
  "private.title": L("Private notes", "私人记录"),
  "private.refresh": L("Load this week", "加载本周"),
  "private.clearNotice": L(
    "Unsaved private input and weekly drafts are cleared when you collapse this panel, sign out or the screen locks.",
    "收起此面板、退出登录或锁屏时，未保存的私人输入和周报草稿会清空。",
  ),
  "private.unavailable": L(
    "Private notes need this device's protected key store, which is not available yet.",
    "私人记录需要本机受保护的密钥存储，目前尚不可用。",
  ),
  "private.label": L("New private note", "新私人记录"),
  "private.tags": L("Tags", "标签"),
  "private.save": L("Save privately", "私人保存"),
  "private.empty": L("No private notes loaded.", "尚未加载私人记录。"),
  "private.omitted": L("Some notes are left out because you can no longer see them.", "部分记录你已无权查看，未显示。"),
  "tag.decision": L("decision", "决定"),
  "tag.progress": L("progress", "进展"),
  "tag.blocker": L("blocker", "阻碍"),
  "tag.question": L("question", "问题"),
  "tag.verification": L("verification", "核实"),
  "weekly.title": L("Weekly draft", "周报草稿"),
  "weekly.boundary": L("Weeks run Monday to Monday in Asia/Shanghai time, as on macOS.", "周界按 Asia/Shanghai 时间周一至下周一计算，与 macOS 一致。"),
  "weekly.week": L("Week starting (Monday)", "周起始日（星期一）"),
  "weekly.load": L("Load sources", "加载来源"),
  "weekly.compose": L("Write draft", "生成草稿"),
  "weekly.draft": L("Draft (you can edit it)", "草稿（可编辑）"),
  "weekly.copy": L("Check and copy", "核对并复制"),
});

const STORAGE_KEY = "vox-agent.context.language";

export function readLanguage(storage) {
  const value = storage?.get(STORAGE_KEY, "en");
  return LANGUAGES.includes(value) ? value : "en";
}

export function writeLanguage(storage, lang) {
  if (LANGUAGES.includes(lang)) storage?.set(STORAGE_KEY, lang);
}

/** Look up `key` in `lang`, substituting `{name}` placeholders. */
export function t(key, lang = "en", vars = {}) {
  const entry = STRINGS[key];
  if (!entry) throw new Error(`missing string ${key}`);
  const text = entry[lang] ?? entry.en;
  return text.replace(/\{(\w+)\}/g, (_, name) => (name in vars ? String(vars[name]) : `{${name}}`));
}

/**
 * Apply `data-i18n` (text), `data-i18n-aria` (aria-label) and
 * `data-i18n-title` (title) keys to static elements under `root`.
 */
export function translateStatic(root, lang) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n, lang);
  }
  for (const node of root.querySelectorAll("[data-i18n-aria]")) {
    node.setAttribute("aria-label", t(node.dataset.i18nAria, lang));
  }
  for (const node of root.querySelectorAll("[data-i18n-title]")) {
    node.setAttribute("title", t(node.dataset.i18nTitle, lang));
  }
}
