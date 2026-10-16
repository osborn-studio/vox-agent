# Provider qualification is evidence, not credential presence

## Status

Accepted

## Date

2026-07-19

## Context

The Windows settings workspace inherited a compatibility endpoint named
`/v1/settings/providers/test`. That endpoint only counted configured API keys,
always reported the backend as reachable, and labeled the result ready. It did
not distinguish an implemented adapter from a deferred one, a stored credential
from a successful provider request, or an installed local runtime from a model
that can actually run on Windows.

That ambiguity is unsafe for release qualification. It also recreates a VoxLog
settings form instead of giving Vox Agent an operational control plane.

## Decision

Add one additive, versioned read model at
`GET /v1/settings/provider-qualifications`. The compatibility runtime owns the
provider inventory because it owns voice routing and its credentials. The
TypeScript gateway validates the response, the Rust bridge deserializes the same
shape, and the Tauri cockpit renders it without an HTML sink.

Each provider exposes separate axes:

- `support`: `supported`, `install_required`, `deferred`, or
  `platform_unavailable`;
- `readiness`: `ready`, `configured_unverified`, `missing_credential`,
  `missing_runtime`, `not_implemented`, or `platform_unavailable`;
- `probe_status`: `not_run`, `passed`, `failed`, or `not_applicable`;
- `configured`, `runtime_available`, `package_available`, and
  `assets_available`, which never include credential values;
- `active_roles`, so main and fallback routes can be audited against the active
  profile;
- a bounded human action explaining the next safe step.

Contract version `2026-07-19.2` is additive to the existing desktop contract.
Cloud credentials are only `configured_unverified` until an explicit live probe
records evidence. Static readiness checks never contact a provider or incur
usage. Local providers become runtime-ready only when their package and declared
model assets are discoverable; runtime readiness alone does not increment the
qualified count. The response also exposes secret-free HTTP proxy-environment
presence because HTTPX honors those variables by default, but never returns a
proxy URL or environment value.

The existing connection check remains temporarily compatible but its UI wording
changes from a live connection claim to configuration readiness.

## Alternatives considered

### Treat a stored key as a successful connection

Rejected because a revoked, region-mismatched, or malformed key would appear
release-ready without any provider evidence.

### Probe every configured provider on application startup

Rejected because startup would transmit credentials, create network traffic,
possibly incur cost, and turn transient outages into launch failures.

### Put qualification logic only in the Tauri frontend

Rejected because it would duplicate routing rules, could expose secret handling
to the WebView, and would let desktop and gateway clients disagree.

### Port every macOS provider before exposing status

Rejected because users and release operators need an honest inventory now. A
deferred provider is visible as deferred; it is never silently treated as ready.

## Consequences

- Provider status becomes audit evidence rather than optimistic UI copy.
- Shared Zod and Rust types are the public contract and must evolve additively.
- Live credentialed probes remain a separate explicit action and evidence gate.
- Windows-only qualification still requires a Windows host; the matrix reports
  implementation and runtime state without pretending hardware validation ran.
- No response, log, test fixture, or Web storage field may contain credential
  material.
