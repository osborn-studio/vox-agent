# ADR: Integrate org-collab into the shared mainline

- Status: Accepted
- Date: 2026-07-17
- Owners: Product, Engine, Security, Web
- Decision window: Phase 4 Enterprise hardening

## Decision

Organization collaboration will ship from this repository and the same engine
core as Personal. It will not become a second product fork or a separate
repository. Organization behavior will be enabled per deployment/workspace and
bound to the authenticated principal; Personal remains the default mode.

The existing `engine/org_*.py`, permissioned retrieval, summaries, demo seed,
and `tests/org_collab/` are the implementation seed. The isolated
`create_org_collab_app()` surface remains a test/pilot harness until its routes
are converted to context-bound `/v1/org/*` routers and pass the security exit
criteria below. It must not be exposed as a production sidecar in its current
requester-ID-from-body form.

## Scope

The first supported scope is one privately deployed organization and one pilot
engineering team. It includes organization identity, configured workspaces,
classified work events, permission-aware search/status answers, cited
summaries, dashboards, and cross-user access audit. GitHub remains the first
system-of-record integration.

Out of scope for the first merge are generic chat, HR performance scoring,
cross-company SaaS multi-tenancy, unrestricted raw desktop capture, and a
Slack/Teams replacement.

## Compatibility

- Personal installs keep their current local data and behavior with org mode
  disabled; no organization record is required to start the engine.
- Shared `CaptureSession`, memory, task, device, MCP, and ASR contracts stay
  edition-neutral. Edition-specific behavior is additive and namespaced.
- Experimental `/org/*` tests migrate to `/v1/org/*`. Because the old app has
  not been a supported mainline deployment surface, it receives a test-only
  compatibility adapter for one release, not an indefinite public guarantee.
- Web, Mobile, macOS, Windows, CLI, and MCP use the same authenticated API;
  clients must not invent their own permission rules.

## Migration

1. **Quarantine the prototype.** Label `create_org_collab_app()` as an
   internal harness and keep it off production startup.
2. **Bind identity.** Add an AppContext-owned org service and `/v1/org/*`
   router. Derive organization, user, and role from the bearer credential;
   ignore requester/org IDs supplied by a client when authorizing access.
3. **Centralize schema.** Move `org.db`, work-event, and access-audit schema
   creation into the engine migration registry. Keep an idempotent importer
   for pilot databases and a dry-run/backup step.
4. **Roll out one pilot.** Seed or import one organization, explicitly register
   capture workspaces, and enable read-only dashboards before ingest/query
   writes.
5. **Promote storage only behind interfaces.** Add Postgres after the private
   pilot passes; do not fork policy or query semantics by database.

Rollback disables org mode and its routes without deleting Personal data or
the pilot database. Schema migrations must remain backward readable through
the rollback window.

## Security

- Authentication is mandatory for every organization route. Tenant and actor
  identity come from the verified principal, never a request body or query
  parameter.
- Every store query is organization-scoped before filtering or ranking. A
  denied query returns no information about the target's existence.
- Cross-user raw reads, denials, exports, role/retention changes, and device
  actions write actor-bearing, tamper-evident audit records.
- Capture is limited to explicitly configured workspaces with a visible
  pause/private control. Private spans, credentials, secrets, and non-work
  content are redacted or excluded before persistence.
- Summaries retain source IDs and visibility labels, and are tested against
  private-source leakage. Retention and deletion apply to raw events,
  summaries, indexes, exports, and audit data according to their class.
- Service credentials remain in the platform keyring or deployment secret
  manager; organization mode must not reintroduce plaintext `.env` secrets.

## Rejected alternatives

- **Separate org-collab repository:** duplicates contracts, migrations,
  security fixes, and release gates.
- **Mount the existing prototype immediately:** its body-supplied requester
  identity is not a production authentication boundary.
- **Make organization mandatory:** breaks the Personal local-first workflow
  and creates a needless migration for single-user installs.

## Exit criteria

- `/v1/org/*` is AppContext-owned and rejects missing, invalid, cross-org, and
  insufficient-role credentials in integration tests.
- An idempotent pilot-data migration has dry-run, backup, rollback, and schema
  version evidence.
- Adversarial permission/leak tests cover raw events, summaries, search,
  exports, retention, devices, and audit identities.
- Personal regression suites pass with org mode off; one private pilot passes
  the documented install, capture, query, retention, and rollback runbook.
- Product, Security, and Engine owners sign the pilot evidence before wider
  deployment or Postgres/multi-team expansion.
