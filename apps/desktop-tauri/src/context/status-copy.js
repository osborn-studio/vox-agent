// What a Context record's state means to the user (T14, bilingual since T39).
// One source for the timeline, the right panel and the live region. Tones
// follow DESIGN.md: running (blue), awaiting (orange: a human must act),
// failed (red), done (green), idle (gray). Every status has a word and a
// glyph, never color only. Server "accepted" is never shown as "delivered".

export const LANGUAGES = Object.freeze(["en", "zh"]);

const L = (en, zh) => Object.freeze({ en, zh });
const record = (label, detail, tone, glyph, canCheck = false) => ({ label, detail, tone, glyph, canCheck });

export function pick(text, lang) {
  return text[lang] ?? text.en;
}

const SIGN_IN = record(
  L("Sign in to continue", "请重新登录以继续"),
  L(
    "The server did not accept the session. The record is kept on this device and continues after sign-in.",
    "服务端未接受当前登录。记录保留在本机，登录后继续。",
  ),
  "awaiting",
  "!",
);

const CHECK_GROUP = (polling) =>
  record(
    L("Check the group", "请到群内核对"),
    polling
      ? L(
          "The server does not report this record yet. Checking continues slowly; look in the group before writing it again.",
          "服务端暂未查到这条记录，会继续低频核对；重写前请先到群内确认。",
        )
      : L(
          "The server does not report this record yet. The request may still have arrived: look in the group before writing it again.",
          "服务端暂未查到这条记录，但请求可能已经到达；重写前请先到群内确认。",
        ),
    "awaiting",
    "!",
    true,
  );

const ADMIN = record(
  L("Blocked: administrator needed", "已阻止：需要管理员处理"),
  L(
    "The server could not deliver with the current access. An administrator can restore access and requeue it.",
    "以当前权限无法投递。管理员恢复权限后可重新排队。",
  ),
  "awaiting",
  "!",
  true,
);

const CONFLICT = record(
  L("Conflict: not sent again", "冲突：不会再次发送"),
  L(
    "This record id already holds different text on the server. Both versions are kept; nothing is overwritten.",
    "服务端该记录编号已对应不同内容。两份都保留，不会覆盖。",
  ),
  "failed",
  "×",
);

const NEXT = {
  "user-sign-in": SIGN_IN,
  "poll-ask-sign-in": SIGN_IN,
  "user-check-delivery": CHECK_GROUP(false),
  "poll-ask-check-delivery": CHECK_GROUP(true),
  "user-administrator-recovery": ADMIN,
  "poll-ask-administrator-recovery": ADMIN,
  "stop-conflict": CONFLICT,
  "stop-permission-denied": record(
    L("Permission denied", "没有权限"),
    L("Your account cannot record to this destination. The text is kept on this device.", "当前账号不能记录到这个目标。文字保留在本机。"),
    "failed",
    "×",
  ),
  "stop-project-invalid": record(
    L("Project no longer valid", "项目已不可用"),
    L(
      "The selected project is not available to this account. Choose another project and record again.",
      "所选项目对当前账号不可用。请选择其他项目后重新记录。",
    ),
    "failed",
    "×",
  ),
  "stop-invalid-input": record(
    L("Not accepted", "未被接受"),
    L("The server rejected the input format. The text is kept on this device.", "服务端拒绝了输入格式。文字保留在本机。"),
    "failed",
    "×",
  ),
  "stop-too-large": record(
    L("Too long", "内容过长"),
    L("The rendered message is over the server limit. The text is kept on this device.", "生成的消息超过服务端上限。文字保留在本机。"),
    "failed",
    "×",
  ),
  "stop-server-address-invalid": record(
    L("Server address problem", "服务地址异常"),
    L(
      "The server redirected the request, which is never followed. Ask an administrator to check the configuration.",
      "服务端返回了重定向，应用不会跟随。请管理员检查配置。",
    ),
    "failed",
    "×",
  ),
};

const STATE = {
  delivered: record(L("Delivered", "已投递"), L("The server reported delivery with a message id.", "服务端报告已投递，并给出消息编号。"), "done", "✓"),
  rejected: record(
    L("Rejected: not delivered", "已拒绝：未投递"),
    L("The server finished this record without delivering it.", "服务端已结束处理，没有投递。"),
    "failed",
    "×",
  ),
  conflict: CONFLICT,
  pending: record(
    L("Accepted by the server, not yet delivered", "服务端已接受，尚未投递"),
    L("The server has queued it. Delivery is checked automatically.", "服务端已排队，投递结果会自动核对。"),
    "running",
    "●",
  ),
  sending: record(L("Delivering", "正在投递"), L("The server is delivering it now.", "服务端正在投递。"), "running", "●"),
  unknown: record(
    L("Delivery result unknown", "投递结果未知"),
    L(
      "The server does not know whether the message arrived. It retries for about 50 minutes; after that only an administrator can recover it. It is never sent twice automatically.",
      "服务端不确定消息是否到达，会重试约 50 分钟；之后只能由管理员恢复。应用不会自动重复发送。",
    ),
    "running",
    "?",
    true,
  ),
  blocked: ADMIN,
};

const QUARANTINE = record(
  L("Kept aside: integrity check failed", "已隔离：完整性校验失败"),
  L("The stored copy did not pass its integrity check. Nothing is sent or changed automatically.", "本机副本未通过完整性校验，不会自动发送或修改。"),
  "failed",
  "×",
);
const LOCAL = record(
  L("Saved on this device, not sent yet", "已保存在本机，尚未发送"),
  L("It is sent as soon as the connection allows.", "连接可用后即发送。"),
  "idle",
  "○",
);
const VERIFYING = record(
  L("Result being verified", "结果待核对"),
  L(
    "The request may already have reached the server, so it will not be sent again. Its result is being read back.",
    "请求可能已到达服务端，因此不会再次发送，正在读回结果。",
  ),
  "running",
  "?",
  true,
);
const PHASE = {
  reserved: LOCAL,
  dispatching: VERIFYING,
  restored: VERIFYING,
  accepted: STATE.pending,
};
// Labels whose meaning comes from another field: an automatic poll, resend
// or a final stop is described by the server state; a locally unknown
// delivery state by the dispatch phase.
const DEFERRED_NEXT = Object.freeze(["poll", "resend", "stop-final"]);
const DEFERRED_SERVER_STATE = Object.freeze(["local-unknown"]);

const UNKNOWN_STATUS = record(L("Status unavailable", "状态暂不可用"), L("No status has been reported yet.", "尚未收到状态。"), "idle", "○");

function entry(dto) {
  if (dto.action === "quarantine") return QUARANTINE;
  if (dto.next && NEXT[dto.next]) return NEXT[dto.next];
  if (dto.serverState && STATE[dto.serverState]) return STATE[dto.serverState];
  if (dto.phase && PHASE[dto.phase]) return PHASE[dto.phase];
  return UNKNOWN_STATUS;
}

export function describe(dto, lang = "en") {
  const found = entry(dto);
  return { ...found, label: pick(found.label, lang), detail: pick(found.detail, lang) };
}

const COMPOSER = {
  saving: L("Saving on this device…", "正在保存到本机…"),
  "in-flight": L("Already saving this record…", "正在保存这条记录…"),
  "saved-locally": L("Saved on this device. Cloud delivery is shown on the record.", "已保存在本机。云端投递状态显示在记录上。"),
  empty: L("Write something to record.", "请先输入要记录的内容。"),
  "no-destination": L("No destination is available for this account.", "当前账号没有可用的记录目标。"),
  "unknown-target": L("Choose a conversation first.", "请先选择会话。"),
  "not-configured": L(
    "Context recording is not set up on this device yet. It needs sign-in and the Windows key store.",
    "本机尚未启用 Context 记录，需要登录并启用 Windows 密钥存储。",
  ),
  "not-signed-in": L("Sign in to record.", "请先登录。"),
  unverified: L("Reconnecting. Your record is kept and sent after sign-in is confirmed.", "正在重新连接。记录已保留，确认登录后发送。"),
  "journal-unavailable": L("Encrypted local storage is unavailable. Your draft is kept.", "本机加密存储不可用，草稿已保留。"),
  "quota-exceeded": L("The local storage limit is reached. Your draft is kept.", "已达到本机存储上限，草稿已保留。"),
  conflict: L(
    "This record id already holds different text. Your draft is kept as a new record.",
    "该记录编号已对应不同内容，草稿将作为新记录保留。",
  ),
  tombstoned: L(
    "This record id was given up earlier and cannot be reused. Your draft is kept as a new record.",
    "该记录编号此前已放弃，不能复用，草稿将作为新记录保留。",
  ),
  "verify-only": L("Local records are being verified after a restore. Your draft is kept.", "恢复后正在核对本机记录，草稿已保留。"),
  busy: L("This record is being updated. Try again in a moment.", "这条记录正在更新，请稍后再试。"),
  "invalid-input": L("The text or project is not valid. Your draft is kept.", "文字或项目无效，草稿已保留。"),
  "not-found": L("This record is no longer stored on this device.", "本机已不再保存这条记录。"),
  "catalog-unavailable": L("Projects could not be loaded. Your draft is kept.", "无法加载项目目录，草稿已保留。"),
  "repository-unavailable": L("This folder cannot be read. Select a project manually.", "无法读取此目录，请手动选择项目。"),
  "not-a-repository": L("This folder is not a Git repository. Select a project manually.", "此目录不是 Git 仓库，请手动选择项目。"),
  "integrity-failed": L(
    "A stored record failed its integrity check. It is kept aside and not sent.",
    "本机记录未通过完整性校验，已隔离且不会发送。",
  ),
  internal: L("Something went wrong. Your draft is kept.", "出现问题，草稿已保留。"),
};

export function composerMessage(result, { maxBytes = 8192, lang = "en" } = {}) {
  if (result.status === "too-large") {
    return lang === "zh"
      ? `内容过长：${result.bytes} / ${maxBytes} 字节，草稿已保留。`
      : `Too long: ${result.bytes} of ${maxBytes} bytes. Your draft is kept.`;
  }
  const key = result.status === "failed" ? result.code : result.status;
  return pick(COMPOSER[key] ?? COMPOSER.internal, lang);
}

/** Which labels have copy, for the Rust-to-catalog completeness test (T39b). */
export function catalogCoverage() {
  return {
    next: Object.keys(NEXT),
    deferredNext: [...DEFERRED_NEXT],
    serverState: Object.keys(STATE),
    deferredServerState: [...DEFERRED_SERVER_STATE],
    phase: Object.keys(PHASE),
    composer: Object.keys(COMPOSER),
  };
}

/** Every catalog entry, for completeness tests. */
export function catalogEntries() {
  const texts = [];
  for (const item of [...Object.values(NEXT), ...Object.values(STATE), QUARANTINE, LOCAL, VERIFYING, UNKNOWN_STATUS]) {
    texts.push(item.label, item.detail);
  }
  texts.push(...Object.values(COMPOSER));
  return texts;
}
