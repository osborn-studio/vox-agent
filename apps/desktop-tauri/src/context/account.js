// Feishu account and conversation discovery for the Context panel (T24).
// Rust owns the session; this module only turns its DTOs and error codes
// into words. Server text is never shown.
import { t } from "./i18n.js";

/** Codes `context_sign_in`, `context_sign_out` and `context_conversations` return. */
export const ACCOUNT_ERRORS = Object.freeze([
  "not-available",
  "sign-in-in-progress",
  "already-signed-in",
  "cancelled",
  "not-signed-in",
  "sign-in-required",
  "account-disabled",
  "organization-not-configured",
  "not-permitted",
  "identity-changed",
  "rate-limited",
  "login-unavailable",
  "conversations-unavailable",
  "denied",
  "timeout",
  "expired",
  "network",
  "browser-unavailable",
  "store-unavailable",
  "invalid-response",
]);

/** Refusals after which Rust has already ended the account. */
export const ENDS_ACCOUNT = new Set(["sign-in-required", "identity-changed", "account-disabled"]);

export function accountError(code, lang) {
  const known = ACCOUNT_ERRORS.includes(code) ? code : "invalid-response";
  return t(`account.error.${known}`, lang);
}

/** Which controls to show and the one-line account state. */
export function accountView(summary, lang) {
  if (summary.signedIn) {
    return {
      signIn: false,
      signOut: true,
      line: summary.displayName
        ? t("account.signedInAs", lang, { name: summary.displayName })
        : t("account.signedInNoName", lang),
    };
  }
  if (!summary.signInAvailable) {
    return { signIn: false, signOut: false, line: t("account.notAvailable", lang) };
  }
  return { signIn: true, signOut: false, line: t("account.signedOut", lang) };
}

export function signOutMessage(result, lang) {
  if (!result.storeCleared) return t("account.signedOutStoreFailed", lang);
  return t(result.serverConfirmed ? "account.signedOutDone" : "account.signedOutLocalOnly", lang);
}

/** Conversations as list items: name, kind and capability as words. */
export function renderConversations(doc, list, conversations, lang) {
  list.replaceChildren(
    ...conversations.map((conversation) => {
      const item = doc.createElement("li");
      item.dataset.conversationId = conversation.id;
      const name = doc.createElement("strong");
      name.textContent = conversation.name;
      const detail = doc.createElement("small");
      detail.className = "inline-note";
      detail.textContent = [
        t(conversation.kind === "personal" ? "conversations.personal" : "conversations.group", lang),
        t(conversation.canSend ? "conversations.canSend" : "conversations.viewOnly", lang),
      ].join(" · ");
      item.append(name, detail);
      return item;
    }),
  );
}
