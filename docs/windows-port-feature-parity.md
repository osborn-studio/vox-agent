# Windows source feature boundaries

The sole Windows-capable desktop source is `apps/desktop-tauri`; native macOS
source remains under `macos/`. The source covers chat/agent routing, settings,
history, voice controls and platform adapters under shared contract tests.

Source availability does not establish feature parity on devices. ASR, audio,
credential migration, hotkeys, installation, updating, accessibility and signing
require platform verification. No private PASS report is reproduced here; new
results must identify the public commit and device actually tested.
