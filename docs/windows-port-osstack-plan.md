# Windows source architecture

The canonical Windows-capable shell is `apps/desktop-tauri`. Its Rust owner and
React client use a typed bridge to the Python engine. Shared contracts remain
in `packages/contracts`; native audio, hotkeys and credential storage stay behind
platform adapters.

This source architecture note is not a deployment or device qualification.
No private runner, credentials, model weights or signed installer is included.
Preserve permission checks and run the retained Rust/TypeScript checks before
changing these boundaries. Do not import private Git ancestry into this public
repository's clean history.
