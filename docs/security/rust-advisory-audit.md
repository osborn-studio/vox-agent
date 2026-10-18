# Rust advisory audit policy

The Vox Agent Rust dependency gate is reproducible and fail closed. CI audits
every declared `Cargo.lock` with the exact `cargo-audit` release artifact and
RustSec advisory database revision in `security/rust-audit-policy.json`.

## What CI enforces

- The cargo-audit version is exact, and the Linux release archive is verified
  against its published SHA-256 digest before execution.
- The RustSec repository origin and full 40-character commit must match policy.
- The audit runs with advisory fetching and live yanked-crate lookup disabled,
  so its security result cannot drift away from the reviewed database snapshot.
- The advisory database pin may be at most 14 days old. CI deliberately fails
  after that window, forcing a reviewed database refresh rather than silently
  scanning against an obsolete snapshot.
- Every listed lockfile must exist inside the repository.
- A vulnerability fails the job unless its advisory has an active, accountable
  exception. There are no exceptions at the initial baseline.

The gate script is `scripts/rust_audit_gate.py`. Run the same check locally by
downloading the pinned cargo-audit artifact, checking out the pinned RustSec
commit, and then running:

```bash
python scripts/rust_audit_gate.py \
  --database /path/to/pinned/advisory-db \
  --repo-root .
```

## Updating the database or audit tool

Use a dedicated pull request. Update the matching pinned environment values in
`.github/workflows/verify.yml` together with the policy. Record the new official release version, artifact
URL and SHA-256 digest, or the new RustSec commit and its commit date. Run policy
unit tests and the live audit before requesting review. A version or database
change must never be mixed into an unrelated feature PR.

The database pin should be refreshed weekly and must be refreshed before its
14-day age gate expires. Review any newly reported advisory as product code,
including target-specific Windows dependencies present in the canonical Tauri
lockfile.

## Exception process

Fix or upgrade the dependency first. An exception is allowed only when no safe
upgrade exists and reachability has been assessed. Add one object to
`exceptions` with all of the following:

- `id`: the exact `RUSTSEC-YYYY-NNNN` advisory ID;
- `reason`: at least 20 characters explaining exposure and why remediation is
  not currently possible;
- `owner`: the team or named role responsible for remediation;
- `trackingUrl`: an HTTPS issue containing the remediation plan and evidence;
- `expiresOn`: an ISO date no more than 30 days from the current audit date.

Expired, duplicate, malformed, ownerless, untracked, or longer-than-30-day
exceptions fail before cargo-audit runs. Renewal requires a new risk review and
updated evidence; do not extend dates mechanically.
