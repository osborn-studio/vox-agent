// App commands require an explicit capability grant per window (Tauri ACL).
// Without this manifest every window, including the voice HUD, could call
// every command. Keep this list identical to `generate_handler!` in
// src/lib.rs; test/capability-scope.test.mjs enforces that.
const APP_COMMANDS: &[&str] = &[
    "list_audio_input_devices",
    "get_native_capture_status",
    "start_native_capture",
    "stop_native_capture",
    "cancel_native_capture",
    "set_capture_abort_hotkey",
    "inject_text",
    "copy_text",
    "start_agent_engine",
    "get_agent_engine_process",
    "get_agent_engine_health",
    "get_engine_operations",
    "get_engine_model_policy",
    "update_engine_model_policy",
    "run_engine_command",
    "resolve_engine_permission",
    "start_engine_event_stream",
    "stop_engine_event_stream",
    "stop_agent_engine",
    "get_autostart_status",
    "set_autostart_enabled",
    "send_desktop_notification",
    "open_system_path",
    "get_backend_base_url",
    "set_backend_base_url",
    "get_hotkey_config",
    "set_hotkey_config",
    "get_app_settings",
    "save_app_settings",
    "test_provider_settings",
    "get_provider_qualifications",
    "preview_capture_status",
    "preview_output_policy",
    "get_session_digest",
    "get_daily_digest",
    "get_project_digest",
    "rebuild_digest",
    "export_digest_to_obsidian",
    "sync_obsidian_archive",
    "export_digest_to_ai_mate_memory",
    "get_backend_health",
    "get_desktop_contract",
    "query_agent",
    "get_named_modes",
    "sync_named_mode_hotkeys",
    "create_named_mode",
    "delete_named_mode",
    "get_history",
    "get_agent_history",
    "export_history",
    "export_migration_bundle",
    "import_migration_bundle",
    "import_legacy_voxlog",
    "sync_git_history",
    "sync_notion_history",
    "get_agents",
    "get_dictionary",
    "mutate_dictionary",
    "get_recent_utterance",
    "dismiss_recent_utterance",
    "retry_recent_utterance",
    "set_recent_mode",
    "add_recent_dictionary_term",
    "undo_output",
    "upload_voice",
    "update_voice_hud",
    "context_submit_record",
    "context_record_status",
    "context_list_records",
    "context_quota",
    "context_session_summary",
    "context_check_record",
    "context_list_projects",
    "context_inspect_repository",
    "context_page_records",
    "context_read_original",
    "context_diagnostics",
    "context_sign_in",
    "context_sign_out",
    "context_conversations",
    "context_private_create",
    "context_private_step",
    "context_private_list",
    "context_private_get",
    "context_private_edit",
    "context_private_withdraw",
    "context_private_kept",
    "context_private_dismiss",
    "context_private_clear_view",
    "context_weekly_load",
    "context_weekly_exclude",
    "context_weekly_compose",
    "context_weekly_edit",
    "context_weekly_copy",
    "context_weekly_clear",
];

fn main() {
    // tauri-build embeds resources for the application, but integration test
    // executables also need Common Controls v6 before their test harness starts.
    // Without it MockRuntime links TaskDialogIndirect from comctl32 v5 and the
    // Windows loader exits with STATUS_ENTRYPOINT_NOT_FOUND (Tauri #13419).
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows")
        && std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc")
    {
        let manifest = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap())
            .join("tests/windows-test.manifest");
        println!("cargo:rerun-if-changed={}", manifest.display());
        println!("cargo:rustc-link-arg-tests=/MANIFEST:EMBED");
        println!(
            "cargo:rustc-link-arg-tests=/MANIFESTINPUT:{}",
            manifest.display()
        );
    }

    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(APP_COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
