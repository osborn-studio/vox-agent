# Feishu session renewal: permissions and diagnostics

The team service issues a 1-hour access token and a 90-day refresh token per
device. The macOS app renews through `POST /v1/auth/feishu/renew` from its
60-second maintenance loop once the access token is within five minutes of
expiry. Every renewal re-checks that the member is still active in the tenant
by calling Feishu `GET /open-apis/contact/v3/users/{open_id}` with the custom
app's tenant token (`engine/feishu_client.py`, `member_active`).

If that check cannot run, the server answers `503 MEMBERSHIP_CHECK_UNAVAILABLE`.
The app keeps the credential and retries, but the access token is not extended,
so sign-in silently stops working about an hour after the last successful
renewal while the settings page still says the account is linked. The account
card now also shows the session expiry and the last renewal outcome, which is
how to tell "linked" from "session valid".

## Feishu app requirements for the membership check

Configure these in the Feishu developer console for the custom app named by
`app_id`, then **publish a new app version**; permission and visibility edits
do not take effect in the released app until a version is published and
approved by the tenant administrator.

1. **Contact scope: user base read.** Grant the contact scope that allows
   reading a single user's base profile (`contact:user.base:readonly`,
   "获取用户基本信息"). The server rejects a response whose `status` block is
   missing any of `is_activated`, `is_frozen`, `is_resigned`, `is_exited`,
   `is_unjoin`; if the server log shows `upstream=PERMISSION_DENIED` although
   the base scope is granted, also grant the employee-status scope
   (`contact:user.employee:readonly`, "获取用户雇佣信息") and re-check the
   current "获取单个用户信息" permission table in the Feishu documentation.
2. **Contact visibility range includes the user.** The app's 通讯录权限范围
   (data visibility range) must include every member who signs in. A member
   outside the range comes back without a usable profile, which the server
   classifies as `IDENTITY_MISMATCH` or `PERMISSION_DENIED`, never as active.
3. **Publish a new app version** after changing scopes or the visibility
   range, and wait for the tenant administrator's approval.

## One-tenant requirement

Renewal uses the custom app's tenant access token, never a store-app token.
`feishu_login.tenant_orgs` in `runtime.json` must therefore map **exactly one**
tenant key to an organization. With zero or several tenants every renewal is
refused with `503 MEMBERSHIP_CHECK_UNAVAILABLE` and the server log shows
`upstream=TENANT_CONFIG`.

## Reading the server log

`engine.feishu_session_renewal` writes one line per renewal attempt:

```
Feishu session renewal: outcome=<code> user=<12 hex> device=<8 chars> upstream=<class>
```

- `user` is a prefix of the SHA-256 of the internal Vox user id; `device` is a
  prefix of the device record id. Neither an open_id, a token nor a Feishu
  response body is logged.
- `outcome` is `RENEWED`, `MEMBERSHIP_CHECK_UNAVAILABLE`, `RENEWAL_RATE_LIMIT`,
  `RENEWAL_IN_PROGRESS`, `SESSION_EXPIRED` or `ACCOUNT_DISABLED`. Successful
  renewals log at INFO, every refusal at WARNING.
- `upstream` is `none` unless the membership check failed. Then it is the
  Feishu client failure class (`PERMISSION_DENIED`, `IDENTITY_MISMATCH`,
  `RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`, `UPSTREAM_PROTOCOL_ERROR`), the
  Python exception type for transport failures, or `TENANT_CONFIG`.

## What the macOS app shows

The account card renders, as plain read-only text, the session expiry time and
`上次续期 <time>：<code>` where the code is one of the app's fixed outcome codes
(`RENEWED`, `MEMBERSHIP_CHECK_UNAVAILABLE`, `RATE_LIMITED`, `IN_PROGRESS`,
`SESSION_EXPIRED`, `ACCOUNT_DISABLED`, `SERVER_UNAVAILABLE`, `SERVER_ERROR`,
`UNREACHABLE`, `INVALID_RESPONSE`, `IDENTITY_MISMATCH`, `STORAGE_FAILED`).
The status line below it is a fixed localized sentence per outcome; raw server
text and tokens never reach the UI. `MEMBERSHIP_CHECK_UNAVAILABLE` in the app
means: check the three app requirements above on the server side.
