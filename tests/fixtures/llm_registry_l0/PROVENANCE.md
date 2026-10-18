# Provenance

These files are verbatim copies from the Osborn-PA repository
(`packages/llm-registry/`), branch `codex/llm-provider-learnings`, commit
`af7dfe66d3fad1341331925f3029fa79ef5b3965` (2026-10-18). They are the L0 LLM
registry v0 that Vox Agent reconciles against; Vox's tests read them so the
suite stays deterministic. Do not edit them here. Refresh them by re-copying
from PA and updating this note.

| File | PA path |
| --- | --- |
| providers.json | packages/llm-registry/data/providers.json |
| models.json | packages/llm-registry/data/models.json |
| urls.json | packages/llm-registry/fixtures/urls.json |
| baseline-v1.json | packages/llm-registry/compat/baseline-v1.json |
| providers.schema.json | packages/llm-registry/schema/providers.schema.json |
| models.schema.json | packages/llm-registry/schema/models.schema.json |

The PA Cloud read-only endpoint (`/api/llm-registry/v1/*.json`) is a later PA
PR; until then the scripts default to these copies.
