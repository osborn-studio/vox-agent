# Private derivative ownership and supported API

Owner: osborn-studio/osvox maintainers. These five local artifacts are not
upstream releases or registry-published packages. Package names retain importer
compatibility; versions have the distinct `-vox.1` suffix and `voxDerivative`
metadata binds the owner, upstream archive and signature contract. Source patches
and SHA512/SHA256 inputs determine each artifact. MIT upstream notices remain.

Scope: Metro 0.84.4/0.84.5 and Expo Metro 57.0.3 watcher `some` callsites use
picomatch; Expo certificates 0.0.6 and CLI 57.0.27 use the explicitly breaking
canonical certificate contract. This does not establish whole micromatch API
compatibility, Watchman/native platform acceptance or a trusted issuer chain.

Certificate package supports its root export and package metadata only. Old
Forge-shaped `build/utils` and shipped upstream tests are removed, with original
input hashes retained in the patch manifest; importing private utility paths is
unsupported. Root declarations use native key/certificate objects and asynchronous
CSR/certificate generation. Callers must await generation; they cannot assume
Forge objects or synchronous results. CLI's actual configured and synthetic
online/cache consumers are exercised separately. Unsupported signature validation
returns `VOX_UNSUPPORTED_CERTIFICATE_SIGNATURE` with reissue/re-sign guidance;
it does not diagnose a precise encoding cause from a generic verification error.

For an upstream update, first review the callsite/API diff and advisories, then
rebuild from new explicitly reviewed source hashes, qualify semantic parity and
attack corpus, inspect public declarations and consumer awaits, clean-install,
prebuild and independently audit the final bytes. Never carry old hashes forward
or remove checks because paths changed. Release maintainers must assign a real
artifact delivery/update channel before adoption; current absolute fixture paths
are local evidence only. No fork publication, production adoption or indefinite
security-support promise is authorized by these tests.

Historical Forge mode remains explicit and fails on the omitted-NULL positive.
Canonical mode separately asserts rejection and verifies the historical report
contains that exact retained break plus successful canonical/malformed checks.
It never treats arbitrary exit 2 as success. The owner decision is in
canonical-contract.json. Rollback restores existing production locks/builds;
real re-signing is separate and cannot be automatically undone by code rollback.
