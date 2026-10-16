# Feishu team service — Phase 2 offline candidate

Phase 2 supplies an identity dependency and configuration only. No conversation
list, send, read, worker, UI or voice routes are installed. Offline implementation
is owner-authorized; production rollout remains blocked on the live tenant,
OAuth/native callback and team HTTPS acceptance gates in the
[design](../../docs/plans/2026-09-27-feishu-conversations-sidebar.md).
No callback registration or deployment is performed by this change.

## Configuration

Use the existing `python -m engine.backup_runtime --config /private/path/service.json`
entry point on the controlled service host. The configuration is strict JSON:
unknown fields and type coercion (including booleans as integers) are rejected.
Example placeholders below are not credentials or an approved deployment:

```json
{
  "data_dir": "/srv/vox-feishu/private",
  "app_id": "cli_example",
  "app_secret_ref": "feishu-service-app",
  "bootstrap_secret_ref": "feishu-service-bootstrap",
  "enabled": false,
  "conversations_enabled": false,
  "targets": [],
  "feishu_login": {
    "public_base_url": "https://vox.example.invalid",
    "tenant_orgs": {"tenant-example": "default-org"},
    "backup_target_ids": []
  },
  "conversation_policy": {
    "pinned_chat_ids": [],
    "denied_chat_ids": [],
    "shared_read_chat_ids": []
  }
}
```

`enabled` controls existing input backup ingestion/delivery. Independently,
`conversations_enabled` defaults to false and gates the conversation dependency.
Enabling it requires exactly one configured tenant and Feishu login; one app is
specified by `app_id`. It does not start a conversation worker or enable any
Phase 3–6 API. Conversation-only mode does not load the app secret at startup;
existing OAuth loads it only during exchange. Backup-only configurations retain
their existing authentication, targets and worker lifecycle.

The new dependency `engine.backup_app.require_feishu_principal` must be attached
to every future conversation route. It uses actual session authentication, then
rechecks `auth_method == "feishu"`, current active user/org, persisted identity
app and tenant→org mapping, and organization existence under identity-lock → login-lock ordering. It returns the freshly read role and records authenticated eligibility denials with actor IDs and a fixed reason only, without request bodies or headers. Bootstrap,
API bearer credentials, token-prefix imitations and caller-supplied actor/tenant
headers cannot grant conversation access. Authentication failures return 401;
an authenticated but ineligible principal returns 403; authenticated requests
when the switch is off return 503. Existing backup routes retain their guard.
Session authorization is a request-time snapshot: logout or expiry after that
check does not cancel an already in-flight request. Later send/read operations
must still recheck their resource authorization; this guard is not a transaction
covering remote Feishu operations.

## Shared-read policy and change control

Chat IDs must match `oc_` followed by ASCII letters/digits (maximum 256 characters).
Pinned, denied and shared lists reject duplicates. Denied IDs cannot also be
pinned/shared; clear that conflict explicitly. Pinning is sorting metadata and
never confers read authorization. A shared entry has this required shape:

```json
{
  "chat_id": "oc_example",
  "created_by": "admin-stable-id",
  "effective_at": "2026-09-27T00:00:00Z",
  "version": 1,
  "audit_ref": "approved-policy-record-id",
  "scope": "current_members",
  "revoked_at": null
}
```

`created_by` identifies the establishing administrator; `audit_ref` points to the
controlled approval record documenting that administrator's authority, exact
group/content scope and effective time. Both must contain printable, non-empty text without surrounding whitespace or control characters.
Version is a positive integer. Times are ISO-8601 strings with explicit timezone;
revocation cannot precede effectiveness. Future effective times are permitted as
metadata and must not be treated as immediately effective grants.

This is independent Vox authorization to share the designated group's content
with current members, not a claim of parity with Feishu history visibility.
Config validation does not verify administrator authority or create an audit
record. Operators must keep the approval and versioned config history in a
restricted durable store; never include message bodies or credentials there.
No Phase 2 code reads group content or evaluates policy for message disclosure.
A future reader must enforce effective/revoked times, version and fresh membership
for every returned object; a bare chat ID or cursor is not authorization.

To revoke: record the actor/reason in the external audit record, increment the
version and set `revoked_at`, or remove the grant and optionally deny the chat.
Retain prior versions for audit. Validate a replacement configuration, then
restart gracefully: configuration is loaded at startup, with no hot reload or
promise of immediate revocation in an already running process. For urgent
suspension, stop the service first. Disabling conversations or changing policy
must not delete, rewrite or reroute backup jobs. Preserve the identity and outbox
databases on rollback; do not delete them to revoke sessions. Keep each deployed
binary revision paired with its validated, versioned configuration. Rolling back
to a pre-Phase-2 runtime requires restoring the matching earlier configuration:
that runtime rejects the new `conversations_enabled` and `conversation_policy`
keys because its schema forbids unknown fields. Preserve the previous config
alongside the new one, and validate the pair before any rollout.

## Deployment boundary (requires separate acceptance)

- Run one process/worker under a dedicated service account on a controlled host.
  SQLite and the in-memory OAuth attempts require single-process ownership.
  Do not use multiple Uvicorn workers, replicas, or development reload.
- Use persistent local storage owned by the service account, directory mode 0700;
  runtime rejects a public/wrong-owner data directory. Preserve identity, session,
  audit and backup databases together through restart and protected backups.
- Store app/bootstrap secrets in the supported OS credential vault on the server;
  config contains references only. Provisioning and access are operator tasks.
  Employee Macs must never receive the app secret or tenant access token.
- Terminate HTTPS at a controlled same-host reverse proxy; keep the engine on its
  default `127.0.0.1:8766` loopback bind. Restrict local access on shared hosts.
  Use the exact configured public Host (including port if configured) upstream.
  Unknown Hosts are rejected; forwarded Host does not substitute for Host.
- Keep runtime `proxy_headers=False`, one worker and access logging off, as set
  by its CLI. Do not override this with Uvicorn auto-trust settings or wildcard
  trusted proxies. Application rate limits use the socket peer, not Forwarded or
  X-Forwarded-For. Behind this proxy, all clients share the loopback application
  bucket (including the five-outstanding-attempt limit); it is a conservative
  backstop and a potential team login bottleneck, not per-user fairness.
- The proxy must enforce per-client-IP login/request limits using its verified
  connection peer; strip client-supplied Forwarded/X-Forwarded-* headers. If a
  load balancer precedes it, accept source-IP metadata only from an explicit
  trusted allowlist with direct ingress blocked. Do not key limits from arbitrary
  caller headers. Validate this chain before rollout.
- Disable/redact proxy access logs for OAuth URLs/query strings and authorization,
  cookie and response headers; never log tokens, codes, session proofs or bodies.
  Do not expose the bootstrap credential to employee clients.

Before team rollout, obtain separately approved real-tenant evidence for the
Phase 0 capabilities, real OAuth/native callback, controlled HTTPS/Host routing,
proxy rate limiting and employee credential isolation. Fake transport/provider
results do not satisfy those gates. Any callback change, credential provisioning
or production launch remains a separate operator action.
