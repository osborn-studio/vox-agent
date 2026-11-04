# Windows source feature boundaries

The sole Windows-capable desktop source is `apps/desktop-tauri`; native macOS
source remains under `macos/`. The source covers chat/agent routing, settings,
history, voice controls and platform adapters under shared contract tests.

Source availability does not establish feature parity on devices. ASR, audio,
credential migration, hotkeys, installation, updating, accessibility and signing
require platform verification. No private PASS report is reproduced here; new
results must identify the public commit and device actually tested.

## Parity inventory against macOS 4.2.10 (updated 2026-11-04)

Source comparison only; no row below has named-device evidence yet. "Source"
means the Tauri shell has the code path; "CI" means a GitHub-hosted Windows
build or test exercises it.

| Area | macOS reference | Windows (Tauri) status |
|---|---|---|
| Installer | `release-dmg.yml` (ad-hoc signed DMG) | `release-windows.yml`: unsigned NSIS + MSI artifact with the frozen engine and voice backend (CI) |
| Voice backend for ASR/polish | Swift ASR and LLM clients in-process | Bundled `voxagent-backend.exe` on loopback, launch-token auth, kill-on-close job (PR-B, #26; source + CI build) |
| Composer voice turn → polished draft | `ChatInputBar.polishVoiceTurn()` | Mic capture polishes via `/v1/voice?polish=1` and fills the composer (PR-E, #23; source) |
| Global hotkey, HUD, text injection | `HotkeyManager`, `FloatingBar`, `TextInjectionEngine` | Present (source) |
| Inline permission card, operations inspector | `PermissionApprovalCard`, `OperationsInspector` | Present (source) |
| Feishu Context: login, timeline, voice draft, private inbox, weekly review | `UI/Login`, `FeishuTimeline`, `ContextPrivateInboxView`, `ContextWeeklyReviewView` | Present (source); DPAPI journal (PR-A) |
| Modes, vocabulary, history, Notion/Obsidian/Git sync, engine settings, LLM routes | `UI/Settings/*` | Present (source) |
| Translate controls | `ComposerTranslateControls`, `Translation/*` | **Missing**: needs a free-text translate route or shell-side LLM call |
| Polish model badge | `ChatInputBar` control row | **Missing** |
| Two-row composer, counting/finalized voice states | `ChatInputBar`, `VoiceInputStateMachine` | **Partial**: single row; idle/starting/running/finishing only |
| Local ASR (SenseVoice/sherpa, Qwen3-ASR-0.6B) and model download | `ModelManager`, `QwenModelCatalog` | **Missing in the installer**: runtimes not frozen into the backend (PR-C); local routes fail closed |
| Cloud ASR providers | ~18 providers in `ASR/Providers` | **Partial**: Qwen US/CN, OpenAI, SiliconFlow |
| Mission control, stats dashboard | `MissionControlView`, `AgentStatsDashboard` | **Missing** |
| Status strip and sidebar status grammar | `AgentStatusStrip`, `ChatSidebar` | **Partial**: mostly static |
| zh/en UI language | `Localization`, `LanguageSwitcher` | **Partial**: only the Context section is translated |
| Setup wizard | `SetupWizardView` | **Partial**: inline onboarding checklist |
| Hotkey recorder, input backup, chat submission settings | `HotkeyRecorderView`, `InputBackupSettingsView`, `ChatSubmissionSettingsView` | **Partial/Missing**: typed accelerator field; others missing |
| Update check | `AppUpdater`, `UpdateChecker` | **Missing in UI**: `updater_manifest.rs` is library-only |
| Sound cues, system volume ducking | `SoundFeedback`, `SystemVolumeManager` | **Missing** |
| Code signing | ad-hoc, not notarized | Unsigned; SmartScreen steps documented in the README |
| Release publication | `publish-release.yml` draft release | Same draft release carries `setup.exe`, MSI and checksums (#25); no release published yet |

Device evidence still required on a named Windows PC: installed launch and
quit (sidecars end with the app), WASAPI capture, hotkey and HUD, SendInput into
common targets, cloud ASR and polish with real keys, Context delivery, and
upgrade/uninstall.
