mod audio;
mod bridge;
pub mod capture_session;
mod commands;
pub mod context_account;
pub mod context_acl;
pub mod context_commands;
pub mod context_conversation;
pub mod context_delivery;
pub mod context_journal;
pub mod context_keys;
pub mod context_login;
pub mod context_policy;
pub mod context_private;
pub mod context_private_commands;
pub mod context_private_service;
pub mod context_repository;
pub mod context_service;
pub mod context_session;
pub mod context_sources;
pub mod context_transport;
pub mod context_weekly_commands;
pub mod context_weekly_draft;
pub mod context_wire;
mod contracts;
pub mod engine_local;
mod engine_operations;
mod engine_supervisor;
mod git_sync;
mod history_export;
mod migration_bundle;
mod notion_sync;
pub mod secrets;
mod state;
mod system_integration;
mod text_input;
pub mod updater_manifest;
mod voice_hud;

use contracts::GlobalHotkeyEvent;
use state::AppState;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::{Builder as GlobalShortcutBuilder, ShortcutState};

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let open = MenuItem::with_id(app, "open", "Open Vox Agent", true, None::<&str>)?;
    let start_engine = MenuItem::with_id(
        app,
        "start-engine",
        "Start Agent Engine",
        true,
        None::<&str>,
    )?;
    let stop_engine =
        MenuItem::with_id(app, "stop-engine", "Stop Agent Engine", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Vox Agent", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[&open, &start_engine, &stop_engine, &separator, &quit],
    )?;
    let mut builder = TrayIconBuilder::with_id("vox-agent-tray")
        .tooltip("Vox Agent")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main_window(app),
            "start-engine" => {
                let result = app.state::<AppState>().engine.start();
                let (title, body) = match result {
                    Ok(status) => (
                        "Vox Agent engine started",
                        format!("Process {} is running.", status.pid.unwrap_or_default()),
                    ),
                    Err(error) => ("Vox Agent engine failed", error),
                };
                let _ = crate::system_integration::send_notification(app, title, &body);
            }
            "stop-engine" => {
                let result = app.state::<AppState>().engine.stop();
                let (title, body) = match result {
                    Ok(_) => (
                        "Vox Agent engine stopped",
                        "The local engine is no longer running.".to_string(),
                    ),
                    Err(error) => ("Vox Agent engine stop failed", error),
                };
                let _ = crate::system_integration::send_notification(app, title, &body);
            }
            "quit" => app.exit(0),
            _ => {}
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

/// The Context server comes only from the process environment at startup
/// (`VOX_AGENT_CONTEXT_ENDPOINT`, https or loopback http origin). Without it,
/// sign-in reports `not-available`. The production origin is fixed by T49p.
fn context_state() -> context_commands::ContextState {
    let state = context_commands::ContextState::default();
    if let Some(endpoint) = std::env::var("VOX_AGENT_CONTEXT_ENDPOINT")
        .ok()
        .and_then(|value| context_wire::ContextEndpoint::parse(&value).ok())
    {
        state.set_endpoint(endpoint);
    }
    state
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Must be the first plugin: a second launch focuses the running app
        // instead of starting a second process (journal lock, plan C-25).
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(AppState::default())
        .manage(context_state())
        .manage(context_private_commands::PrivateState::default())
        .manage(context_weekly_commands::WeeklyState::default())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_notification::init())
        // Native folder picker, used only from Rust (context_inspect_repository);
        // no dialog permission is granted to any WebView.
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(
            GlobalShortcutBuilder::new()
                .with_handler(|app, shortcut, event| {
                    let phase = match event.state() {
                        ShortcutState::Pressed => "pressed",
                        ShortcutState::Released => "released",
                    };
                    let payload = GlobalHotkeyEvent {
                        accelerator: shortcut.to_string(),
                        phase: phase.to_string(),
                    };
                    let _ = app.emit_to("main", "vox-agent://global-hotkey", payload.clone());
                    // Keep one compatibility release for an already-running legacy WebView.
                    let _ = app.emit_to("main", "voxlog2://global-hotkey", payload);
                })
                .build(),
        )
        .setup(setup_tray)
        .invoke_handler(tauri::generate_handler![
            commands::list_audio_input_devices,
            commands::get_native_capture_status,
            commands::start_native_capture,
            commands::stop_native_capture,
            commands::cancel_native_capture,
            commands::set_capture_abort_hotkey,
            commands::inject_text,
            commands::copy_text,
            commands::start_agent_engine,
            commands::get_agent_engine_process,
            commands::get_agent_engine_health,
            commands::get_engine_operations,
            commands::get_engine_model_policy,
            commands::update_engine_model_policy,
            commands::run_engine_command,
            commands::resolve_engine_permission,
            commands::start_engine_event_stream,
            commands::stop_engine_event_stream,
            commands::stop_agent_engine,
            commands::get_autostart_status,
            commands::set_autostart_enabled,
            commands::send_desktop_notification,
            commands::open_system_path,
            commands::get_backend_base_url,
            commands::set_backend_base_url,
            commands::get_hotkey_config,
            commands::set_hotkey_config,
            commands::get_app_settings,
            commands::save_app_settings,
            commands::test_provider_settings,
            commands::get_provider_qualifications,
            commands::preview_capture_status,
            commands::preview_output_policy,
            commands::get_session_digest,
            commands::get_daily_digest,
            commands::get_project_digest,
            commands::rebuild_digest,
            commands::export_digest_to_obsidian,
            commands::sync_obsidian_archive,
            commands::export_digest_to_ai_mate_memory,
            commands::get_backend_health,
            commands::get_desktop_contract,
            commands::query_agent,
            commands::get_named_modes,
            commands::sync_named_mode_hotkeys,
            commands::create_named_mode,
            commands::delete_named_mode,
            commands::get_history,
            commands::get_agent_history,
            commands::export_history,
            commands::export_migration_bundle,
            commands::import_migration_bundle,
            commands::import_legacy_voxlog,
            commands::sync_git_history,
            commands::sync_notion_history,
            commands::get_agents,
            commands::get_dictionary,
            commands::mutate_dictionary,
            commands::get_recent_utterance,
            commands::dismiss_recent_utterance,
            commands::retry_recent_utterance,
            commands::set_recent_mode,
            commands::add_recent_dictionary_term,
            commands::undo_output,
            commands::upload_voice,
            voice_hud::update_voice_hud,
            context_commands::context_submit_record,
            context_commands::context_record_status,
            context_commands::context_list_records,
            context_commands::context_quota,
            context_commands::context_session_summary,
            context_commands::context_check_record,
            context_commands::context_list_projects,
            context_commands::context_inspect_repository,
            context_commands::context_page_records,
            context_commands::context_read_original,
            context_commands::context_diagnostics,
            context_commands::context_sign_in,
            context_commands::context_sign_out,
            context_commands::context_conversations,
            context_private_commands::context_private_create,
            context_private_commands::context_private_step,
            context_private_commands::context_private_list,
            context_private_commands::context_private_get,
            context_private_commands::context_private_edit,
            context_private_commands::context_private_withdraw,
            context_private_commands::context_private_kept,
            context_private_commands::context_private_dismiss,
            context_private_commands::context_private_clear_view,
            context_weekly_commands::context_weekly_load,
            context_weekly_commands::context_weekly_exclude,
            context_weekly_commands::context_weekly_compose,
            context_weekly_commands::context_weekly_edit,
            context_weekly_commands::context_weekly_copy,
            context_weekly_commands::context_weekly_clear,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Vox Agent Desktop Tauri shell");
}
