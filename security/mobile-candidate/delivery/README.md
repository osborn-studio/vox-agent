# Portable four-branch synchronization candidate

This directory is a local proposal, not an enabled release path. Five unchanged
private archives are pinned in inventory.json; versions and licensing are the
previously audited derivatives. Package and lock templates remove machine paths.
Normal npm ci including the explicit canonical postinstall now passes in an
owned target-layout fixture. The legacy installer remains unchanged. The new
installer rejects nine contract/provenance tampering vectors. Full npm test in
the portable fixture passes 152 tests and checks four installed Metro copies.
The optional fsevents script remains skipped by npm's existing allow-scripts
policy; native watcher qualification is not inferred.

Target branches at last read:

| PR | branch | remote head |
| --- | --- | --- |
| 82 | codex/sidebar-groups | e7eca2669b998e906e0e4b9c486ae3d969623ce4 |
| 83 | existing release-security branch | f92760758cf7caf9b7ec21ec62f04b847f5904a3 |
| 84 | existing model-updates branch | aba479abac660acc3561597ce3aa7540bb35c2b0 |
| 85 | existing team-backup branch | 48a9dd9024e5dc1d42b2578199ef4be48755d7ec |

Synchronization inventory:

- Reviewed derivative source/archive/integrity records and five artifact files.
- Certificate contract, maintenance/API boundary and current provenance module.
- Canonical and historical verifiers, builder, full safety runner and refusal tests.
- CI adapter and test; workflow proposal contains a literal external runner SHA.
- Template mappings replace mobile package and lock only after target qualification.

The Python adapter explicitly checks all stage identities/statuses and historical
break. It must run from an immutable reviewed checkout with exact trusted Node
and Python toolchain; NODE_OPTIONS/NODE_PATH are cleared. Its literal hash is
protected through workflow review, not claimed unmodifiable to privileged writers.
The underlying runner additionally pins stage scripts, helpers, provenance source,
source patches, artifacts and manifests. Test with
`python3 scripts/test_mobile_canonical_ci.py` from repository root.

## Concrete remaining integration work

The stale postinstall coupling is repaired through an explicit canonical-contract
installation guard with literal provenance pins. Prepare a new fixture with
`python3 scripts/prepare_mobile_delivery_fixture.py --output build/<new-name> --legacy-dir <reviewed-legacy-fixture-directory>`.
This copies tracked mobile/scripts/security files only, applies the reviewed
legacy oracle dispatch patch and verifies every resulting helper hash. It never
installs packages or accesses credentials. Run normal npm ci in its mobile app.

The current full safety runner qualifies the owned build fixture. CI preparation
must explicitly create that fixture, supply pinned upstream archives and the
bounded matcher oracle, and bind the resulting target manifests. The workflow
step is deliberately unwired until those prerequisites pass. Re-audit changed
gate/provenance bytes, then check branch-specific patch application without
overwriting feature work. No live service qualification is inferred.

No four-branch push, remote CI, deployment or merge is authorized by this proposal.
Current remote-run authorization and budget must be verified separately; do not
trigger workflows by pushing before eligibility. Real certificate inventory,
reissue/re-sign/revocation and production adoption remain separately scoped.

## Local lifecycle closure

The fixed committed-source preparer, normal install, 152 tests, typecheck, zero
audit and complete 15-stage adapter are now locally qualified. Sonnet 5.5 final
static review reports no concrete blocker for local candidate adoption. See
`docs/security/forge-integration-2026-10-07/portable-lifecycle/status.md` for exact
source/runtime identities, byte-equivalence proof and residual integration gates.
This does not authorize remote CI, branch synchronization or production use.
