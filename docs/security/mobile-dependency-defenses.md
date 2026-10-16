# Temporary mobile dependency defenses

2026-10-05 — applies to PRs 82–85, which share the same mobile dependency tree.

## Scope and remaining release gate

Two high-severity root advisories propagate through 16 installed dependency
entries; these are not four independent vulnerabilities. The four PRs share
braces 3.0.3 and node-forge 1.4.0. Upstream has no patched release in the registry
at verification time:

- https://github.com/advisories/GHSA-vfj7-8cjw-p6xm
- https://github.com/advisories/GHSA-86w9-cpqp-85rv

These are temporary local defenses, not upstream releases. `npm audit
--audit-level=high` remains unchanged and FAILS: npm inspects versions, not the
patched source. No advisory allowlist, renamed package, fake version, or forced
Expo/React Native downgrade is used. The moderate uuid advisory is unchanged.
Do not claim security CI is green or merge readiness. Removing the CI release
gate is outside this change.

## Implementation

`security/mobile-dependency-patches.json` pins exact upstream and patched SHA-256
hashes. Postinstall validates all inputs before patching; unknown versions or
content fail installation. Reapplication is idempotent. Duplicate nested lockfile
copies fail closed. `security:regression` checks patched hashes before execution,
so installing with `--ignore-scripts` cannot silently pass tests.

- braces: cap parser stack depth (both braces and parentheses), and recursive
  compile/expand/stringify depth, at 128. Reject excessively nested input with a
  deliberate SyntaxError before exhausting the process stack. This is a bounded
  input policy; callers should handle invalid input errors. It is not a general
  defense against every possible combinatorial expansion or attacker-crafted AST.
- node-forge: require DigestAlgorithm element count to match captured OID plus
  optional NULL. Reject extra elements with and without NULL parameters. Existing
  ASN.1 type and OID validation remains in place. This does not replace a general
  cryptographic audit.

Patches are applied only to the mobile install, not other projects or machines.
Do not ship/install dependencies with lifecycle scripts disabled. Once upstream
publishes fixes, replace the dependency pins, remove corresponding patch entries,
and retain the regression tests. No manual edits to node_modules are required.

## Verification

Both behaviors reproduced against pristine dependencies: deep pattern caused
stack RangeError; correctly RSA-signed synthetic malformed DigestInfo was accepted.
The latter tests structural validation, not a practical signature forgery.
Patched tests reject deep braces, parentheses, unclosed input and direct ASTs;
normal expansion, stringify, valid SHA-256 signatures with/without NULL, and wrong
digests retain expected behavior.

Clean `npm ci` applies defenses. Typecheck, 152 mobile tests, Metro asset parser
checks, and pinned regression tests passed. osstack Loop task `task_502dfcb0`
records fixtures/typecheck/tests and diff verification. No device release claimed.
Claude CLI Opus 5.5 targeted audit timed out at 55 seconds; independent audit is
NOT counted as passing. Release gate remains blocked pending upstream fixes and
completed review.

## Installer failure-path acceptance

2026-10-05: four disposable-fixture scenarios execute the actual installer:
check-only leaves pristine files untouched; application and reapplication succeed;
corrupting the final source file rejects the entire batch without partial edits;
unknown package versions and nested lockfile copies reject without edits. These
checks are included in security:regression, before the unchanged CI audit gate.
osstack task task_9ac6fc55 passed the installer and vulnerability regressions.
Only temporary synthetic fixture trees are removed by this test.
