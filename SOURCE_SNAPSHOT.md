# Source snapshot provenance and boundaries

This independent public source repository is derived from the maintained Vox
Agent source tree on 2026-10-16:

- Source repository: `osborn-studio/osvox` (remains private).
- Source main commit: `0a48e756dd689b49c118ac1082939e414fc83b72`.
- Source tree: `8ebe93a8741871c2f108433ed315b4b130efcc73`.
- Public target: `osborn-studio/vox-agent`, with a new initial root commit.

A source commit identifier is provenance text, not imported Git history. This
repository has no old parent commits, tags, PR refs, release assets or private
backup object store. The original repository's development and releases are
separate and have not been renamed, transferred or rewritten.

## Included

Application/library source, tests, dependency lockfiles, build tools, source
contracts, MIT and third-party notices. Build-required original/patched npm
source archives and their licenses are preserved. A small set of deterministic
source-contract fixtures is retained for existing tests (API/label tables,
provider contract and revision-pinned upstream model-file hashes); these do
not include private runtime/audit receipts or the model files themselves.

## Omitted or adapted

- Internal audit/qualification logs and receipts, operational CI evidence,
  private development plans and handoffs, backups and historic Git metadata.
- All six source-tree audio/array resources, including both current generated
  test files and optional bundled sound cues. The mathematical fixture
  generator remains, and existing sound code supports synthesized fallback.
- Application installers/binaries and model weights; no original release assets
  or the separate native-library supplement are copied here.
- Original CI definitions are moved byte-for-byte to `.github/source-workflows/`
  with `.yml.txt` extensions. Five source-inspection test/verification entrypoints
  read those inert copies; their assertions are preserved. There are no active
  workflow files, and repository Actions is disabled before the initial push.
- README files and three release/Windows architecture notes are adapted to this
  source-only context instead of carrying private deployment/qualification claims.

`SOURCE_MANIFEST.json` records the exact exported file bytes (excluding itself).
Some retained historical technical docs may refer to upstream/private plans or
old versions; those references are not new qualification claims or accessible
private history in this repository.

## Verification scope

The actual export and nested dependency source members were checked for the
former test WAV/NPY, their shared PCM payload and complete Base64 representations;
none matched. Common credential patterns did not match. This is a bounded byte
check of published content, not a guarantee about arbitrary transformed audio.
No audio was played/transcribed and no model, GUI, microphone, provider or CI
job was started for this publication.

The affected source-contract/metadata checks and workflow-reference checks were
run locally. They do not establish a full cross-platform application build,
external-service E2E result or a device/release pass. Private workflow/protection
rules have not been weakened or copied as live public automation.

Future public updates must be reviewed source patches/snapshots on this clean
root. Do not merge private ancestry, copy run evidence/secrets, enable runner
access or add public installation packages without their own applicable review.
