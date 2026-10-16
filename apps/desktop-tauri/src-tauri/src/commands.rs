use std::time::{SystemTime, UNIX_EPOCH};

use tauri::{AppHandle, State};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut};

use crate::audio::{AudioInputDevice, NativeAudioResult, NativeCaptureStatus};
use crate::bridge;
use crate::contracts::{
    AIMateMemoryExportInput, AIMateMemoryExportResponse, AgentAnswer, AgentQueryInput,
    AgentSummary, AppRuleSet, AppSettingsInput, AppSettingsSnapshot, CapturePreview,
    DesktopContract, DictionaryMutationInput, DictionaryPayload, DigestRebuildInput,
    FastPathResponse, HealthResponse, HistoryExportInput, HistoryExportResponse, HistoryItem,
    HotkeyConfig, LegacyMigrationResponse, LlmConfigSnapshot, MigrationImportInput,
    MigrationImportResponse, NamedMode, NamedModeHotkeyFailure, NamedModeHotkeySyncReport,
    NamedModeInput, ObsidianExportInput, ObsidianExportResponse, ObsidianSyncInput,
    ObsidianSyncResponse, OutputPolicyPreview, PortableDictionaryImportResponse,
    PortableHistoryImportResponse, ProviderConnectivityResponse, ProviderFlags,
    ProviderQualificationResponse, ProviderSettingsResponse, RecentDictionaryResponse,
    RecentDismissResponse, RecentEnvelope, RecentModeResponse, RecentRetryResponse, SessionDigest,
    UndoOutputResponse,
};
use crate::engine_operations::{
    self, EngineApprovalResponse, EngineCommandInput, EngineCommandResponse, EngineModelPolicy,
    EngineModelPolicyPatch, EngineOperationsSnapshot, EngineStreamStatus,
};
use crate::engine_supervisor::{EngineProcessStatus, ManagedEngineHealth};
use crate::secrets::{read_secret, set_secret as keyring_set};
use crate::state::{
    normalize_hotkey_behavior, save_stored_settings, vox_agent_data_dir, AppState, StoredLlmRoutes,
    StoredSettings, StoredSttRoutes,
};
use crate::system_integration::AutostartStatus;
use crate::text_input::TextInjectionResult;
use vox_agent_input_output::{
    decide_output_policy, OutputPolicyRequest, OutputStrategy, RiskLevel,
};

fn parse_risk_level(value: &str) -> RiskLevel {
    match value.trim().to_ascii_lowercase().as_str() {
        "high" => RiskLevel::High,
        "medium" => RiskLevel::Medium,
        _ => RiskLevel::Low,
    }
}

fn provided_secret(value: Option<String>) -> Option<String> {
    value
        .map(|secret| secret.trim().to_string())
        .filter(|secret| !secret.is_empty())
}

fn store_provided_secret(name: &str, value: Option<String>) -> Result<(), String> {
    if let Some(secret) = provided_secret(value) {
        keyring_set(name, Some(secret))?;
    }
    Ok(())
}

fn validate_provider_settings(input: &AppSettingsInput) -> Result<(), String> {
    if !matches!(input.active_profile.trim(), "home" | "office") {
        return Err("active profile is invalid".to_string());
    }
    if input.llm_main == input.llm_fallback {
        return Err("LLM main and fallback routes must be different".to_string());
    }
    if input.stt_main == input.stt_fallback {
        return Err("STT main and fallback routes must be different".to_string());
    }
    if !matches!(
        input.digest_enhancement_provider.trim(),
        "auto"
            | "openai-gpt"
            | "qwen-turbo"
            | "doubao"
            | "claude"
            | "openai-compatible-custom"
            | "ollama"
    ) {
        return Err("digest enhancement provider is invalid".to_string());
    }
    for (label, value) in [
        ("OpenAI model", input.openai_llm_model.as_str()),
        ("Qwen model", input.qwen_llm_model.as_str()),
        ("Doubao model", input.doubao_model.as_str()),
        ("Anthropic model", input.anthropic_model.as_str()),
        ("Ollama model", input.ollama_model.as_str()),
    ] {
        let value = value.trim();
        if value.is_empty()
            || value.chars().count() > 200
            || value.chars().any(|character| character.is_control())
        {
            return Err(format!("{label} is invalid"));
        }
    }
    let custom_model = input.custom_llm_model.trim();
    if custom_model.chars().count() > 200
        || custom_model.chars().any(|character| character.is_control())
    {
        return Err("custom LLM model is invalid".to_string());
    }
    let custom_url = input.custom_llm_base_url.trim().trim_end_matches('/');
    if custom_url.is_empty() != custom_model.is_empty() {
        return Err("custom LLM base URL and model must be configured together".to_string());
    }
    if !custom_url.is_empty() {
        if custom_url.chars().count() > 500 {
            return Err("custom LLM base URL is too long".to_string());
        }
        let parsed = reqwest::Url::parse(custom_url)
            .map_err(|_| "custom LLM base URL is invalid".to_string())?;
        let host = parsed.host_str().unwrap_or_default();
        let loopback = host.eq_ignore_ascii_case("localhost")
            || host
                .trim_start_matches('[')
                .trim_end_matches(']')
                .parse::<std::net::IpAddr>()
                .is_ok_and(|address| address.is_loopback());
        if !matches!(parsed.scheme(), "http" | "https")
            || parsed.host_str().is_none()
            || (parsed.scheme() == "http" && !loopback)
            || !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.query().is_some()
            || parsed.fragment().is_some()
        {
            return Err("custom LLM base URL is unsafe".to_string());
        }
    }
    for value in [
        &input.dashscope_key_us,
        &input.dashscope_key_cn,
        &input.openai_key,
        &input.siliconflow_key,
        &input.doubao_key,
        &input.anthropic_key,
        &input.custom_llm_key,
        &input.notion_token,
    ]
    .into_iter()
    .flatten()
    {
        if value.chars().count() > 20_000 || value.chars().any(|character| character.is_control()) {
            return Err("provider credential is invalid".to_string());
        }
    }
    if !matches!(
        input.notion_sync_direction.trim(),
        "" | "upload_only" | "download_only" | "bidirectional"
    ) {
        return Err("Notion sync direction is invalid".to_string());
    }
    for (label, value, maximum) in [
        (
            "Notion data source ID",
            input.notion_data_source_id.as_str(),
            200,
        ),
        (
            "Git sync repository URL",
            input.git_sync_repo_url.as_str(),
            2_000,
        ),
        (
            "Git sync local path",
            input.git_sync_local_path.as_str(),
            2_000,
        ),
        (
            "Obsidian vault path",
            input.obsidian_vault_dir.as_str(),
            2_000,
        ),
        (
            "AIMate memory path",
            input.ai_mate_memory_dir.as_str(),
            2_000,
        ),
    ] {
        if value.chars().count() > maximum || value.chars().any(|character| character.is_control())
        {
            return Err(format!("{label} is invalid"));
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn list_audio_input_devices(
    state: State<'_, AppState>,
) -> Result<Vec<AudioInputDevice>, String> {
    state.audio.list_input_devices()
}

#[tauri::command]
pub async fn get_native_capture_status(
    state: State<'_, AppState>,
) -> Result<NativeCaptureStatus, String> {
    state.audio.status()
}

/// Start a capture for `owner` (`hotkey` or `ui`, T26). Busy while another
/// session runs; the caller must not open a second recorder then.
#[tauri::command]
pub async fn start_native_capture(
    device_name: Option<String>,
    owner: crate::capture_session::CaptureOwner,
    state: State<'_, AppState>,
) -> Result<NativeCaptureStatus, String> {
    state.audio.start(owner, device_name)
}

/// Finish the caller's own session and return its audio.
#[tauri::command]
pub async fn stop_native_capture(
    session_id: u64,
    state: State<'_, AppState>,
) -> Result<NativeAudioResult, String> {
    state.audio.stop(session_id)
}

/// Cancel the caller's own session; the audio is discarded in Rust.
#[tauri::command]
pub async fn cancel_native_capture(
    session_id: u64,
    state: State<'_, AppState>,
) -> Result<(), String> {
    state.audio.cancel(session_id)
}

#[tauri::command]
pub async fn inject_text(text: String, method: String) -> Result<TextInjectionResult, String> {
    crate::text_input::inject_text(&text, &method)
}

#[tauri::command]
pub async fn copy_text(text: String) -> Result<crate::text_input::ClipboardCopyResult, String> {
    crate::text_input::copy_text(&text)
}

#[tauri::command]
pub async fn start_agent_engine(state: State<'_, AppState>) -> Result<EngineProcessStatus, String> {
    state.engine.start()
}

#[tauri::command]
pub async fn get_agent_engine_process(
    state: State<'_, AppState>,
) -> Result<EngineProcessStatus, String> {
    state.engine.status()
}

#[tauri::command]
pub async fn get_agent_engine_health(
    state: State<'_, AppState>,
) -> Result<ManagedEngineHealth, String> {
    state.engine.health().await
}

#[tauri::command]
pub async fn get_engine_operations(
    state: State<'_, AppState>,
) -> Result<EngineOperationsSnapshot, String> {
    let token = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .engine_api_token
        .clone();
    engine_operations::fetch_operations_snapshot(token.as_deref()).await
}

#[tauri::command]
pub async fn get_engine_model_policy(
    state: State<'_, AppState>,
) -> Result<EngineModelPolicy, String> {
    let token = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .engine_api_token
        .clone();
    engine_operations::fetch_model_policy(token.as_deref()).await
}

#[tauri::command]
pub async fn update_engine_model_policy(
    input: EngineModelPolicyPatch,
    state: State<'_, AppState>,
) -> Result<EngineModelPolicy, String> {
    let token = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .engine_api_token
        .clone();
    engine_operations::update_model_policy(&input, token.as_deref()).await
}

#[tauri::command]
pub async fn run_engine_command(
    input: EngineCommandInput,
    state: State<'_, AppState>,
) -> Result<EngineCommandResponse, String> {
    let token = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .engine_api_token
        .clone();
    engine_operations::send_command(&input, token.as_deref()).await
}

#[tauri::command]
pub async fn resolve_engine_permission(
    approval_id: String,
    approved: bool,
    remember_scope: bool,
    state: State<'_, AppState>,
) -> Result<EngineApprovalResponse, String> {
    let token = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .engine_api_token
        .clone();
    engine_operations::resolve_permission(&approval_id, approved, remember_scope, token.as_deref())
        .await
}

#[tauri::command]
pub async fn start_engine_event_stream(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<EngineStreamStatus, String> {
    let token = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .engine_api_token
        .clone();
    state.engine_events.start(app, token)
}

#[tauri::command]
pub async fn stop_engine_event_stream(
    state: State<'_, AppState>,
) -> Result<EngineStreamStatus, String> {
    state.engine_events.stop()
}

#[tauri::command]
pub async fn stop_agent_engine(state: State<'_, AppState>) -> Result<EngineProcessStatus, String> {
    state.engine.stop()
}

#[tauri::command]
pub async fn get_autostart_status(app: AppHandle) -> Result<AutostartStatus, String> {
    crate::system_integration::get_autostart(&app)
}

#[tauri::command]
pub async fn set_autostart_enabled(
    enabled: bool,
    app: AppHandle,
) -> Result<AutostartStatus, String> {
    crate::system_integration::set_autostart(&app, enabled)
}

#[tauri::command]
pub async fn send_desktop_notification(
    title: String,
    body: String,
    app: AppHandle,
) -> Result<(), String> {
    crate::system_integration::send_notification(&app, &title, &body)
}

#[tauri::command]
pub async fn open_system_path(path: String, app: AppHandle) -> Result<String, String> {
    crate::system_integration::open_path(&app, &path)
}

/// Credential-store reads that feed a settings snapshot, passed in explicitly
/// so a store failure is reported instead of rendered as "not configured".
struct SnapshotCredentials {
    notion_token: Result<Option<String>, crate::secrets::SecretError>,
    provider_keys_error: Option<crate::secrets::SecretError>,
}

impl SnapshotCredentials {
    fn read(provider_keys_error: Option<crate::secrets::SecretError>) -> Self {
        Self {
            notion_token: read_secret("notion_token"),
            provider_keys_error,
        }
    }
}

fn settings_snapshot(
    config: &crate::state::AppConfig,
    stored: &StoredSettings,
    hotkey: &crate::state::HotkeyState,
    provider: Option<ProviderSettingsResponse>,
    credentials: SnapshotCredentials,
) -> AppSettingsSnapshot {
    let fallback_llm_config = LlmConfigSnapshot {
        openai_model: stored.openai_llm_model.clone(),
        qwen_model: stored.qwen_llm_model.clone(),
        doubao_model: stored.doubao_model.clone(),
        anthropic_model: stored.anthropic_model.clone(),
        custom_base_url: stored.custom_llm_base_url.clone(),
        custom_model: stored.custom_llm_model.clone(),
        ollama_model: stored.ollama_model.clone(),
    };
    let (provider_flags, profiles, llm_config) = provider
        .map(|payload| {
            (
                ProviderFlags {
                    dashscope_us: payload.providers.dashscope_us.configured,
                    dashscope_cn: payload.providers.dashscope_cn.configured,
                    openai: payload.providers.openai.configured,
                    siliconflow: payload.providers.siliconflow.configured,
                    doubao: payload.providers.doubao.configured,
                    anthropic: payload.providers.anthropic.configured,
                    custom_llm: payload.providers.custom_llm.configured,
                },
                payload.profiles,
                payload.llm_config,
            )
        })
        .unwrap_or((ProviderFlags::default(), Vec::new(), fallback_llm_config));

    let notion_token = credentials.notion_token;
    let credential_store_error = config
        .credential_store_error
        .clone()
        .or_else(|| {
            credentials
                .provider_keys_error
                .map(|error| error.to_string())
        })
        .or_else(|| notion_token.as_ref().err().map(|error| error.to_string()));
    AppSettingsSnapshot {
        credential_store_error,
        backend_base_url: config.backend_base_url.clone(),
        backend_api_token_present: config
            .api_token
            .as_ref()
            .is_some_and(|value| !value.is_empty()),
        engine_api_token_present: config
            .engine_api_token
            .as_ref()
            .is_some_and(|value| !value.is_empty()),
        active_profile: stored.active_profile.clone(),
        digest_enhancement_enabled: stored.digest_enhancement_enabled,
        digest_enhancement_provider: stored.digest_enhancement_provider.clone(),
        obsidian_vault_dir: stored.obsidian_vault_dir.clone(),
        ai_mate_memory_dir: stored.ai_mate_memory_dir.clone(),
        notion_sync_enabled: stored.notion_sync_enabled,
        notion_data_source_id: stored.notion_data_source_id.clone(),
        notion_include_raw_text: stored.notion_include_raw_text,
        notion_sync_direction: stored.notion_sync_direction.clone(),
        notion_token_present: notion_token.as_ref().is_ok_and(Option::is_some),
        git_sync_enabled: stored.git_sync_enabled,
        git_sync_repo_url: stored.git_sync_repo_url.clone(),
        git_sync_local_path: stored.git_sync_local_path.clone(),
        onboarding_completed: stored.onboarding_completed,
        hotkey_accelerator: hotkey.accelerator.clone(),
        hotkey_behavior: stored.hotkey_behavior.clone(),
        hotkey_enabled: hotkey.enabled,
        app_rules: AppRuleSet {
            never_archive_apps: stored.never_archive_apps.clone(),
            fast_path_only_apps: stored.fast_path_only_apps.clone(),
            disable_direct_typing_apps: stored.disable_direct_typing_apps.clone(),
        },
        provider_flags,
        llm_config,
        profiles,
    }
}

/// `Err` means provider keys could not be read from the credential store, so
/// hydration was skipped; `Ok(None)` means the engine was not reachable.
async fn hydrate_provider_settings(
    config: &crate::state::AppConfig,
    stored: &StoredSettings,
) -> Result<Option<ProviderSettingsResponse>, crate::secrets::SecretError> {
    let routes = stored.llm_routes_for(&stored.active_profile);
    let stt_routes = stored.stt_routes_for(&stored.active_profile);
    // Fail closed: if any provider key cannot be read, skip hydration rather
    // than telling the engine the key is absent (which would clear it).
    let keys = match crate::secrets::collect_provider_keys(
        &read_secret,
        &[
            "dashscope_key_us",
            "dashscope_key_cn",
            "openai_key",
            "siliconflow_key",
            "doubao_key",
            "anthropic_key",
            "custom_llm_key",
        ],
    ) {
        Ok(keys) => keys,
        Err(error) => {
            eprintln!("provider settings hydration skipped: {error}");
            return Err(error);
        }
    };
    let mut body = serde_json::json!({
        "active_profile": stored.active_profile,
        "digest_enhancement_enabled": stored.digest_enhancement_enabled,
        "digest_enhancement_provider": stored.digest_enhancement_provider,
        "openai_llm_model": stored.openai_llm_model,
        "qwen_llm_model": stored.qwen_llm_model,
        "doubao_model": stored.doubao_model,
        "anthropic_model": stored.anthropic_model,
        "custom_llm_base_url": stored.custom_llm_base_url,
        "custom_llm_model": stored.custom_llm_model,
        "ollama_model": stored.ollama_model,
        "llm_main": routes.main,
        "llm_fallback": routes.fallback,
        "stt_main": stt_routes.main,
        "stt_fallback": stt_routes.fallback,
    });
    if let Some(object) = body.as_object_mut() {
        object.extend(keys);
    }
    Ok(bridge::post_json(config, "/v1/settings/providers", &body)
        .await
        .ok())
}

fn apply_hotkey_state(
    app: &AppHandle,
    hotkey: &mut crate::state::HotkeyState,
    accelerator: &str,
    enabled: bool,
) -> Result<(), String> {
    if accelerator.trim().eq_ignore_ascii_case("escape") {
        return Err("Escape is reserved for cancelling an active capture".to_string());
    }
    let parsed = accelerator
        .parse::<Shortcut>()
        .map_err(|err| format!("invalid accelerator: {err}"))?;

    let previous = hotkey.clone();
    let previous_shortcut = previous
        .registered
        .then(|| {
            previous
                .accelerator
                .parse::<Shortcut>()
                .map_err(|err| format!("invalid current accelerator: {err}"))
        })
        .transpose()?;
    let shortcuts = app.global_shortcut();
    let same_registered_shortcut =
        previous_shortcut == Some(parsed) && shortcuts.is_registered(parsed);

    if enabled && !same_registered_shortcut && shortcuts.is_registered(parsed) {
        return Err("accelerator is already assigned to another Vox Agent action".to_string());
    }

    if let Some(current) = previous_shortcut.filter(|shortcut| {
        shortcuts.is_registered(*shortcut) && (!enabled || !same_registered_shortcut)
    }) {
        shortcuts
            .unregister(current)
            .map_err(|err| err.to_string())?;
    }

    if enabled && !same_registered_shortcut {
        if let Err(error) = shortcuts.register(parsed) {
            if let Some(current) = previous_shortcut {
                let _ = shortcuts.register(current);
            }
            return Err(error.to_string());
        }
    }

    hotkey.accelerator = accelerator.trim().to_string();
    hotkey.enabled = enabled;
    hotkey.registered = enabled;
    Ok(())
}

fn parse_mode_shortcut(value: Option<&str>) -> Result<Option<(String, Shortcut)>, String> {
    let Some(value) = value.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    if value.eq_ignore_ascii_case("escape") {
        return Err("Escape is reserved for cancelling an active capture".to_string());
    }
    let shortcut = value
        .parse::<Shortcut>()
        .map_err(|error| format!("invalid mode accelerator: {error}"))?;
    Ok(Some((shortcut.to_string(), shortcut)))
}

fn shortcut_matches_mode(candidate: Shortcut, mode: &NamedMode) -> bool {
    parse_mode_shortcut(mode.hotkey_accelerator.as_deref())
        .ok()
        .flatten()
        .is_some_and(|(_, shortcut)| shortcut == candidate)
}

fn mode_shortcut_conflicts(
    candidate: Shortcut,
    main_hotkey: &crate::state::HotkeyState,
    modes: &[NamedMode],
    excluded_mode_id: Option<&str>,
) -> bool {
    let conflicts_with_main = main_hotkey.enabled
        && main_hotkey
            .accelerator
            .parse::<Shortcut>()
            .is_ok_and(|shortcut| shortcut == candidate);
    conflicts_with_main
        || modes.iter().any(|mode| {
            excluded_mode_id != Some(mode.id.as_str()) && shortcut_matches_mode(candidate, mode)
        })
}

fn ensure_main_hotkey_does_not_conflict_with_modes(
    accelerator: &str,
    enabled: bool,
    modes: &[NamedMode],
) -> Result<(), String> {
    if !enabled {
        return Ok(());
    }
    let shortcut = accelerator
        .parse::<Shortcut>()
        .map_err(|error| format!("invalid accelerator: {error}"))?;
    if modes
        .iter()
        .any(|mode| shortcut_matches_mode(shortcut, mode))
    {
        return Err("accelerator is already assigned to a named mode".to_string());
    }
    Ok(())
}

fn rollback_hotkey_state(
    app: &AppHandle,
    hotkey: &mut crate::state::HotkeyState,
    previous: &crate::state::HotkeyState,
    persistence_error: String,
) -> String {
    match apply_hotkey_state(app, hotkey, &previous.accelerator, previous.enabled) {
        Ok(()) => persistence_error,
        Err(rollback_error) => {
            format!("{persistence_error}; failed to restore previous hotkey: {rollback_error}")
        }
    }
}

#[tauri::command]
pub async fn set_capture_abort_hotkey(enabled: bool, app: AppHandle) -> Result<bool, String> {
    let shortcut = "Escape"
        .parse::<Shortcut>()
        .map_err(|error| format!("failed to parse the capture cancel shortcut: {error}"))?;
    let shortcuts = app.global_shortcut();
    let registered = shortcuts.is_registered(shortcut);
    if enabled && !registered {
        shortcuts
            .register(shortcut)
            .map_err(|error| format!("failed to register the capture cancel shortcut: {error}"))?;
    } else if !enabled && registered {
        shortcuts.unregister(shortcut).map_err(|error| {
            format!("failed to unregister the capture cancel shortcut: {error}")
        })?;
    }
    Ok(shortcuts.is_registered(shortcut))
}

#[tauri::command]
pub async fn get_backend_base_url(state: State<'_, AppState>) -> Result<String, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    Ok(config.backend_base_url)
}

#[tauri::command]
pub async fn get_hotkey_config(state: State<'_, AppState>) -> Result<HotkeyConfig, String> {
    let hotkey = state
        .hotkey
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    Ok(HotkeyConfig {
        accelerator: hotkey.accelerator,
        enabled: hotkey.enabled,
        registered: hotkey.registered,
    })
}

#[tauri::command]
pub async fn get_app_settings(state: State<'_, AppState>) -> Result<AppSettingsSnapshot, String> {
    {
        let mut config = state
            .config
            .lock()
            .map_err(|_| "state lock failed".to_string())?;
        config.refresh_tokens_from_store();
    }

    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let provider_result = hydrate_provider_settings(&config, &stored).await;
    let hotkey = state
        .hotkey
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();

    let (provider_payload, provider_keys_error) = match provider_result {
        Ok(payload) => (payload, None),
        Err(error) => (None, Some(error)),
    };
    Ok(settings_snapshot(
        &config,
        &stored,
        &hotkey,
        provider_payload,
        SnapshotCredentials::read(provider_keys_error),
    ))
}

#[tauri::command]
pub async fn save_app_settings(
    input: AppSettingsInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<AppSettingsSnapshot, String> {
    let hotkey_behavior = normalize_hotkey_behavior(&input.hotkey_behavior)?;
    validate_provider_settings(&input)?;
    let backend_base_url = input
        .backend_base_url
        .trim()
        .trim_end_matches('/')
        .to_string();
    store_provided_secret("backend_api_token", input.backend_api_token.clone())?;
    store_provided_secret("engine_admin_token", input.engine_api_token.clone())?;
    store_provided_secret("dashscope_key_us", input.dashscope_key_us.clone())?;
    store_provided_secret("dashscope_key_cn", input.dashscope_key_cn.clone())?;
    store_provided_secret("openai_key", input.openai_key.clone())?;
    store_provided_secret("siliconflow_key", input.siliconflow_key.clone())?;
    store_provided_secret("doubao_key", input.doubao_key.clone())?;
    store_provided_secret("anthropic_key", input.anthropic_key.clone())?;
    store_provided_secret("custom_llm_key", input.custom_llm_key.clone())?;
    store_provided_secret("notion_token", input.notion_token.clone())?;

    {
        let mut stored = state
            .stored_settings
            .lock()
            .map_err(|_| "state lock failed".to_string())?;
        ensure_main_hotkey_does_not_conflict_with_modes(
            &input.hotkey_accelerator,
            input.hotkey_enabled,
            &stored.named_modes,
        )?;
        let mut hotkey = state
            .hotkey
            .lock()
            .map_err(|_| "state lock failed".to_string())?;
        let previous_hotkey = hotkey.clone();
        apply_hotkey_state(
            &app,
            &mut hotkey,
            &input.hotkey_accelerator,
            input.hotkey_enabled,
        )?;

        let mut next = stored.clone();
        next.backend_base_url = backend_base_url.clone();
        next.active_profile = input.active_profile.trim().to_string();
        next.digest_enhancement_enabled = input.digest_enhancement_enabled;
        next.digest_enhancement_provider = input.digest_enhancement_provider.trim().to_string();
        next.openai_llm_model = input.openai_llm_model.trim().to_string();
        next.qwen_llm_model = input.qwen_llm_model.trim().to_string();
        next.doubao_model = input.doubao_model.trim().to_string();
        next.anthropic_model = input.anthropic_model.trim().to_string();
        next.custom_llm_base_url = input
            .custom_llm_base_url
            .trim()
            .trim_end_matches('/')
            .to_string();
        next.custom_llm_model = input.custom_llm_model.trim().to_string();
        next.ollama_model = input.ollama_model.trim().to_string();
        next.llm_routes.insert(
            input.active_profile.trim().to_string(),
            StoredLlmRoutes {
                main: input.llm_main.clone(),
                fallback: input.llm_fallback.clone(),
            },
        );
        next.stt_routes.insert(
            input.active_profile.trim().to_string(),
            StoredSttRoutes {
                main: input.stt_main.clone(),
                fallback: input.stt_fallback.clone(),
            },
        );
        next.obsidian_vault_dir = input.obsidian_vault_dir.trim().to_string();
        next.ai_mate_memory_dir = input.ai_mate_memory_dir.trim().to_string();
        next.notion_sync_enabled = input.notion_sync_enabled;
        next.notion_data_source_id = input.notion_data_source_id.trim().to_string();
        next.notion_include_raw_text = input.notion_include_raw_text;
        next.notion_sync_direction = match input.notion_sync_direction.trim() {
            "" => "bidirectional".to_string(),
            value => value.to_string(),
        };
        next.git_sync_enabled = input.git_sync_enabled;
        next.git_sync_repo_url = input.git_sync_repo_url.trim().to_string();
        next.git_sync_local_path = input.git_sync_local_path.trim().to_string();
        next.onboarding_completed = input.onboarding_completed;
        next.hotkey_accelerator = hotkey.accelerator.clone();
        next.hotkey_behavior = hotkey_behavior;
        next.hotkey_enabled = hotkey.enabled;
        next.never_archive_apps = input.app_rules.never_archive_apps.clone();
        next.fast_path_only_apps = input.app_rules.fast_path_only_apps.clone();
        next.disable_direct_typing_apps = input.app_rules.disable_direct_typing_apps.clone();
        if let Err(error) = save_stored_settings(&next) {
            return Err(rollback_hotkey_state(
                &app,
                &mut hotkey,
                &previous_hotkey,
                error,
            ));
        }
        *stored = next;
    }

    {
        let mut config = state
            .config
            .lock()
            .map_err(|_| "state lock failed".to_string())?;
        config.backend_base_url = backend_base_url;
        config.refresh_tokens_from_store();
    }

    let provider_result = {
        let config = state
            .config
            .lock()
            .map_err(|_| "state lock failed".to_string())?
            .clone();
        let stored = state
            .stored_settings
            .lock()
            .map_err(|_| "state lock failed".to_string())?
            .clone();
        hydrate_provider_settings(&config, &stored).await
    };

    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();

    let hotkey = state
        .hotkey
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();

    let (provider_payload, provider_keys_error) = match provider_result {
        Ok(payload) => (payload, None),
        Err(error) => (None, Some(error)),
    };
    Ok(settings_snapshot(
        &config,
        &stored,
        &hotkey,
        provider_payload,
        SnapshotCredentials::read(provider_keys_error),
    ))
}

#[tauri::command]
pub async fn test_provider_settings(
    state: State<'_, AppState>,
) -> Result<ProviderConnectivityResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::get_json(&config, "/v1/settings/providers/test")
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_provider_qualifications(
    state: State<'_, AppState>,
) -> Result<ProviderQualificationResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let response: ProviderQualificationResponse =
        bridge::get_json(&config, "/v1/settings/provider-qualifications")
            .await
            .map_err(|err| err.to_string())?;
    response.ensure_compatible()
}

#[tauri::command]
pub async fn preview_capture_status(
    duration_ms: u64,
    target_app: String,
    state: State<'_, AppState>,
) -> Result<CapturePreview, String> {
    let settings = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let routes = settings.stt_routes_for(&settings.active_profile);
    let seconds = (duration_ms.clamp(250, 3_600_000) as f64) / 1000.0;
    let bounded_app = target_app
        .trim()
        .chars()
        .filter(|character| !character.is_control())
        .take(120)
        .collect::<String>();
    let target = if bounded_app.is_empty() {
        "the active application"
    } else {
        &bounded_app
    };
    Ok(CapturePreview {
        provider: routes.main.as_id().to_string(),
        model: "not invoked during capture".to_string(),
        status_text: format!("Captured {seconds:.1}s for {target}."),
        completion_hint: format!(
            "Transcription starts after capture stops; fallback route is {}.",
            routes.fallback.as_id()
        ),
        capture_window_count: 0,
    })
}

#[tauri::command]
pub async fn preview_output_policy(
    target_app: String,
    risk_level: String,
    requested_mode: String,
    app_rules: AppRuleSet,
) -> Result<OutputPolicyPreview, String> {
    let decision = decide_output_policy(&OutputPolicyRequest {
        target_app,
        requested_mode,
        risk_level: parse_risk_level(&risk_level),
        never_archive_apps: app_rules.never_archive_apps,
        fast_path_only_apps: app_rules.fast_path_only_apps,
        disable_direct_typing_apps: app_rules.disable_direct_typing_apps,
    });
    Ok(OutputPolicyPreview {
        strategy: match decision.strategy {
            OutputStrategy::Paste => "paste".to_string(),
            OutputStrategy::DirectTyping => "direct_typing".to_string(),
            OutputStrategy::PreviewOnly => "preview_only".to_string(),
        },
        should_confirm: decision.should_confirm,
        should_archive: decision.should_archive,
        reason: decision.reason,
    })
}

#[tauri::command]
pub async fn get_session_digest(
    session_id: String,
    state: State<'_, AppState>,
) -> Result<SessionDigest, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let path = format!(
        "/v1/digests/session?session_id={}",
        urlencoding::encode(&session_id)
    );
    bridge::get_json(&config, &path)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_daily_digest(
    date: String,
    state: State<'_, AppState>,
) -> Result<SessionDigest, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let path = format!("/v1/digests/daily?date={}", urlencoding::encode(&date));
    bridge::get_json(&config, &path)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_project_digest(
    project_key: String,
    state: State<'_, AppState>,
) -> Result<SessionDigest, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let path = format!(
        "/v1/digests/project?project_key={}",
        urlencoding::encode(&project_key)
    );
    bridge::get_json(&config, &path)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn rebuild_digest(
    input: DigestRebuildInput,
    state: State<'_, AppState>,
) -> Result<SessionDigest, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_json(&config, "/v1/digests/rebuild", &input)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn export_digest_to_obsidian(
    input: ObsidianExportInput,
    state: State<'_, AppState>,
) -> Result<ObsidianExportResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_json(&config, "/v1/integrations/obsidian/export", &input)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn sync_obsidian_archive(
    input: ObsidianSyncInput,
    state: State<'_, AppState>,
) -> Result<ObsidianSyncResponse, String> {
    validate_obsidian_sync_input(&input)?;
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_json(&config, "/v1/integrations/obsidian/sync", &input)
        .await
        .map_err(|err| err.to_string())
}

fn validate_obsidian_sync_input(input: &ObsidianSyncInput) -> Result<(), String> {
    if input.vault_dir.trim().is_empty() {
        return Err("Obsidian vault path is required".to_string());
    }
    if input.vault_dir.chars().count() > 4_096 {
        return Err("Obsidian vault path exceeds the 4,096 character limit".to_string());
    }
    if !(1..=365).contains(&input.days) {
        return Err("Sync window must be from 1 through 365 days".to_string());
    }
    Ok(())
}

#[tauri::command]
pub async fn export_digest_to_ai_mate_memory(
    input: AIMateMemoryExportInput,
    state: State<'_, AppState>,
) -> Result<AIMateMemoryExportResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_json(&config, "/v1/integrations/ai-mate-memory/export", &input)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn set_hotkey_config(
    accelerator: String,
    behavior: String,
    enabled: bool,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<HotkeyConfig, String> {
    let behavior = normalize_hotkey_behavior(&behavior)?;
    let mut stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?;
    ensure_main_hotkey_does_not_conflict_with_modes(&accelerator, enabled, &stored.named_modes)?;
    let mut hotkey = state
        .hotkey
        .lock()
        .map_err(|_| "state lock failed".to_string())?;
    let previous_hotkey = hotkey.clone();
    apply_hotkey_state(&app, &mut hotkey, &accelerator, enabled)?;

    let mut next = stored.clone();
    next.hotkey_accelerator = hotkey.accelerator.clone();
    next.hotkey_behavior = behavior;
    next.hotkey_enabled = hotkey.enabled;
    if let Err(error) = save_stored_settings(&next) {
        return Err(rollback_hotkey_state(
            &app,
            &mut hotkey,
            &previous_hotkey,
            error,
        ));
    }
    *stored = next;

    Ok(HotkeyConfig {
        accelerator: hotkey.accelerator.clone(),
        enabled: hotkey.enabled,
        registered: hotkey.registered,
    })
}

#[tauri::command]
pub async fn set_backend_base_url(
    backend_base_url: String,
    state: State<'_, AppState>,
) -> Result<String, String> {
    let mut config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?;
    config.backend_base_url = backend_base_url.trim().trim_end_matches('/').to_string();
    Ok(config.backend_base_url.clone())
}

#[tauri::command]
pub async fn get_recent_utterance(state: State<'_, AppState>) -> Result<RecentEnvelope, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::get_json(&config, "/v1/recent")
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_backend_health(state: State<'_, AppState>) -> Result<HealthResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::get_json(&config, "/health")
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_desktop_contract(state: State<'_, AppState>) -> Result<DesktopContract, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let contract: DesktopContract = bridge::get_json(&config, "/v1/desktop/contract")
        .await
        .map_err(|err| err.to_string())?;
    contract.ensure_compatible()?;
    Ok(contract)
}

#[tauri::command]
pub async fn query_agent(
    mut input: AgentQueryInput,
    state: State<'_, AppState>,
) -> Result<AgentAnswer, String> {
    input.query = input.query.trim().to_string();
    if input.query.is_empty() {
        return Err("Agent query must not be empty".to_string());
    }
    if input.query.chars().count() > 8_000 {
        return Err("Agent query exceeds the 8,000 character limit".to_string());
    }
    input.limit = input.limit.map(|limit| limit.clamp(1, 50));

    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_json(&config, "/v1/agents/query", &input)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_named_modes(state: State<'_, AppState>) -> Result<Vec<NamedMode>, String> {
    let stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?;
    Ok(stored.named_modes.clone())
}

#[tauri::command]
pub async fn sync_named_mode_hotkeys(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<NamedModeHotkeySyncReport, String> {
    let modes = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .named_modes
        .clone();
    let main_hotkey = state
        .hotkey
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let shortcuts = app.global_shortcut();
    let mut configured = 0_usize;
    let mut registered = 0_usize;
    let mut claimed: Vec<Shortcut> = Vec::new();
    let mut failures = Vec::new();

    for mode in &modes {
        let parsed = match parse_mode_shortcut(mode.hotkey_accelerator.as_deref()) {
            Ok(Some((_, shortcut))) => shortcut,
            Ok(None) => continue,
            Err(message) => {
                configured += 1;
                failures.push(NamedModeHotkeyFailure {
                    mode_id: mode.id.clone(),
                    message,
                });
                continue;
            }
        };
        configured += 1;
        if mode_shortcut_conflicts(parsed, &main_hotkey, &modes, Some(&mode.id))
            || claimed.contains(&parsed)
        {
            failures.push(NamedModeHotkeyFailure {
                mode_id: mode.id.clone(),
                message: "accelerator conflicts with another Vox Agent action".to_string(),
            });
            continue;
        }
        claimed.push(parsed);
        if shortcuts.is_registered(parsed) {
            registered += 1;
            continue;
        }
        match shortcuts.register(parsed) {
            Ok(()) => registered += 1,
            Err(error) => failures.push(NamedModeHotkeyFailure {
                mode_id: mode.id.clone(),
                message: error.to_string(),
            }),
        }
    }

    Ok(NamedModeHotkeySyncReport {
        configured,
        registered,
        failures,
    })
}

#[tauri::command]
pub async fn create_named_mode(
    input: NamedModeInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<Vec<NamedMode>, String> {
    let input = input.normalized()?;
    let parsed_hotkey = parse_mode_shortcut(input.hotkey_accelerator.as_deref())?;
    let seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("system clock is before Unix epoch: {error}"))?
        .as_nanos();
    let mut stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?;
    if stored.named_modes.len() >= 100 {
        return Err("named mode limit of 100 has been reached".to_string());
    }
    if stored
        .named_modes
        .iter()
        .any(|mode| mode.name.eq_ignore_ascii_case(&input.name))
    {
        return Err("a named mode with this name already exists".to_string());
    }
    let main_hotkey = state
        .hotkey
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    if let Some((_, shortcut)) = parsed_hotkey.as_ref() {
        if mode_shortcut_conflicts(*shortcut, &main_hotkey, &stored.named_modes, None)
            || app.global_shortcut().is_registered(*shortcut)
        {
            return Err("mode hotkey is already assigned to another Vox Agent action".to_string());
        }
    }
    let mut identifier = format!("mode-{seed:x}");
    let mut suffix = 0_u32;
    while stored.named_modes.iter().any(|mode| mode.id == identifier) {
        suffix += 1;
        identifier = format!("mode-{seed:x}-{suffix}");
    }

    let registered_shortcut = if let Some((_, shortcut)) = parsed_hotkey.as_ref() {
        app.global_shortcut()
            .register(*shortcut)
            .map_err(|error| format!("failed to register mode hotkey: {error}"))?;
        Some(*shortcut)
    } else {
        None
    };
    let mut next = stored.clone();
    next.named_modes.push(NamedMode {
        id: identifier,
        name: input.name,
        target_app: input.target_app,
        target_risk_level: input.target_risk_level,
        recording_mode: input.recording_mode,
        hotkey_accelerator: parsed_hotkey.map(|(canonical, _)| canonical),
    });
    if let Err(error) = save_stored_settings(&next) {
        if let Some(shortcut) = registered_shortcut {
            let _ = app.global_shortcut().unregister(shortcut);
        }
        return Err(error);
    }
    *stored = next;
    Ok(stored.named_modes.clone())
}

#[tauri::command]
pub async fn delete_named_mode(
    id: String,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<Vec<NamedMode>, String> {
    let id = id.trim();
    if id.is_empty() || id.chars().count() > 120 {
        return Err("valid mode identifier is required".to_string());
    }

    let mut stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?;
    let removed = stored
        .named_modes
        .iter()
        .find(|mode| mode.id == id)
        .cloned()
        .ok_or_else(|| "named mode was not found".to_string())?;
    let mut next = stored.clone();
    next.named_modes.retain(|mode| mode.id != id);
    let shortcut_to_unregister = parse_mode_shortcut(removed.hotkey_accelerator.as_deref())?
        .map(|(_, shortcut)| shortcut)
        .filter(|shortcut| {
            !next
                .named_modes
                .iter()
                .any(|mode| shortcut_matches_mode(*shortcut, mode))
                && app.global_shortcut().is_registered(*shortcut)
        });
    if let Some(shortcut) = shortcut_to_unregister {
        app.global_shortcut()
            .unregister(shortcut)
            .map_err(|error| format!("failed to unregister mode hotkey: {error}"))?;
    }
    if let Err(error) = save_stored_settings(&next) {
        if let Some(shortcut) = shortcut_to_unregister {
            let _ = app.global_shortcut().register(shortcut);
        }
        return Err(error);
    }
    *stored = next;
    Ok(stored.named_modes.clone())
}

#[tauri::command]
pub async fn get_history(
    q: String,
    limit: i32,
    state: State<'_, AppState>,
) -> Result<Vec<HistoryItem>, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let path = format!(
        "/v1/history?q={}&limit={}",
        urlencoding::encode(&q),
        limit.max(1)
    );
    bridge::get_json(&config, &path)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn export_history(
    input: HistoryExportInput,
    state: State<'_, AppState>,
) -> Result<HistoryExportResponse, String> {
    let input = input.normalized()?;
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let path = if input.agent.is_empty() {
        format!("/v1/history?q={}&limit=500", urlencoding::encode(&input.q))
    } else {
        format!(
            "/v1/history/agent?agent={}&limit=500",
            urlencoding::encode(&input.agent)
        )
    };
    let mut items: Vec<HistoryItem> = bridge::get_json(&config, &path)
        .await
        .map_err(|err| err.to_string())?;
    if !input.session_id.is_empty() {
        items.retain(|item| item.session_id == input.session_id);
    }

    crate::history_export::write_history_export(
        &items,
        &input.format,
        &vox_agent_data_dir().join("exports"),
    )
}

async fn collect_portable_history(
    config: &crate::state::AppConfig,
) -> Result<Vec<HistoryItem>, String> {
    let mut history = Vec::new();
    loop {
        let remaining = 50_000usize.saturating_sub(history.len());
        if remaining == 0 {
            let overflow: Vec<HistoryItem> =
                bridge::get_json(config, "/v1/migration/history?offset=50000&limit=1")
                    .await
                    .map_err(|error| error.to_string())?;
            if !overflow.is_empty() {
                return Err("migration export exceeds the 50000 history item limit".to_string());
            }
            break;
        }
        let limit = remaining.min(500);
        let path = format!(
            "/v1/migration/history?offset={}&limit={limit}",
            history.len()
        );
        let page: Vec<HistoryItem> = bridge::get_json(config, &path)
            .await
            .map_err(|error| error.to_string())?;
        let page_len = page.len();
        history.extend(page);
        if page_len < limit {
            break;
        }
    }
    Ok(history)
}

async fn import_portable_history(
    config: &crate::state::AppConfig,
    history: &[HistoryItem],
) -> Result<PortableHistoryImportResponse, String> {
    let mut imported = 0;
    let mut skipped = 0;
    for chunk in history.chunks(200) {
        let response: PortableHistoryImportResponse = bridge::post_json(
            config,
            "/v1/migration/history/import",
            &serde_json::json!({"items": chunk}),
        )
        .await
        .map_err(|error| error.to_string())?;
        imported += response.imported;
        skipped += response.skipped;
    }
    Ok(PortableHistoryImportResponse { imported, skipped })
}

async fn build_migration_bundle(
    config: &crate::state::AppConfig,
    stored: &StoredSettings,
) -> Result<crate::migration_bundle::MigrationBundle, String> {
    let history = collect_portable_history(config).await?;
    let dictionary = bridge::get_json(config, "/v1/dictionary")
        .await
        .map_err(|error| error.to_string())?;
    crate::migration_bundle::MigrationBundle::new(stored, history, dictionary)
}

#[tauri::command]
pub async fn export_migration_bundle(
    state: State<'_, AppState>,
) -> Result<crate::migration_bundle::MigrationBundleResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();

    let bundle = build_migration_bundle(&config, &stored).await?;
    crate::migration_bundle::write_bundle(&bundle, &vox_agent_data_dir().join("migrations"))
}

#[tauri::command]
pub async fn sync_notion_history(
    state: State<'_, AppState>,
) -> Result<crate::notion_sync::NotionSyncResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    if !stored.notion_sync_enabled {
        return Err("Notion sync is disabled".to_string());
    }
    let token = read_secret("notion_token")
        .map_err(|error| format!("Notion token unavailable: {error}"))?
        .ok_or_else(|| "Notion token is missing; save it in Settings first".to_string())?;
    let history = collect_portable_history(&config).await?;
    let mut outcome = crate::notion_sync::sync(
        &token,
        &stored.notion_data_source_id,
        &stored.notion_sync_direction,
        stored.notion_include_raw_text,
        &history,
    )
    .await?;
    let imported = import_portable_history(&config, &outcome.remote_history).await?;
    outcome.response.downloaded = imported.imported;
    outcome.response.skipped += imported.skipped;
    Ok(outcome.response)
}

#[tauri::command]
pub async fn sync_git_history(
    state: State<'_, AppState>,
) -> Result<crate::git_sync::GitSyncResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let stored = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    if !stored.git_sync_enabled {
        return Err("Git sync is disabled".to_string());
    }
    let bundle = build_migration_bundle(&config, &stored).await?;
    let repo_url = stored.git_sync_repo_url.clone();
    let local_path = stored.git_sync_local_path.clone();
    let data_dir = vox_agent_data_dir();
    let transport = tokio::task::spawn_blocking(move || {
        crate::git_sync::sync_transport(&bundle, &repo_url, &local_path, &data_dir)
    })
    .await
    .map_err(|error| format!("Git sync worker failed: {error}"))??;

    let mut history_imported = 0;
    let mut history_skipped = 0;
    let mut corrections_imported = 0;
    let mut preserved_terms_imported = 0;
    let imported_snapshots = transport.remote_snapshots.len();
    for snapshot in &transport.remote_snapshots {
        let remote = crate::migration_bundle::read_bundle(snapshot)?;
        let history = import_portable_history(&config, &remote.history).await?;
        history_imported += history.imported;
        history_skipped += history.skipped;
        let dictionary: PortableDictionaryImportResponse = bridge::post_json(
            &config,
            "/v1/migration/dictionary/import",
            &remote.dictionary,
        )
        .await
        .map_err(|error| error.to_string())?;
        corrections_imported += dictionary.imported_corrections;
        preserved_terms_imported += dictionary.imported_preserved_terms;
    }

    Ok(crate::git_sync::GitSyncResponse {
        device_id: transport.device_id,
        repository: transport.repository.to_string_lossy().to_string(),
        published: transport.published,
        imported_snapshots,
        history_imported,
        history_skipped,
        corrections_imported,
        preserved_terms_imported,
    })
}

#[tauri::command]
pub async fn import_legacy_voxlog(
    state: State<'_, AppState>,
) -> Result<LegacyMigrationResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_json(
        &config,
        "/v1/migration/legacy/import",
        &serde_json::json!({}),
    )
    .await
    .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn import_migration_bundle(
    input: MigrationImportInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<MigrationImportResponse, String> {
    let path_value = input.path.trim();
    if path_value.is_empty()
        || path_value.chars().count() > 4_000
        || !path_value.ends_with(".voxagent.json")
    {
        return Err("select a valid .voxagent.json migration bundle".to_string());
    }
    let path = std::path::PathBuf::from(path_value);
    if !path.is_file() {
        return Err("migration bundle file was not found".to_string());
    }
    let bundle = crate::migration_bundle::read_bundle(&path)?;

    let current = state
        .stored_settings
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let next = bundle.settings.apply_to(&current);
    normalize_hotkey_behavior(&next.hotkey_behavior)?;
    ensure_main_hotkey_does_not_conflict_with_modes(
        &next.hotkey_accelerator,
        next.hotkey_enabled,
        &next.named_modes,
    )?;

    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let history_response = import_portable_history(&config, &bundle.history).await?;
    let dictionary_response: PortableDictionaryImportResponse = bridge::post_json(
        &config,
        "/v1/migration/dictionary/import",
        &bundle.dictionary,
    )
    .await
    .map_err(|error| error.to_string())?;

    {
        let mut stored = state
            .stored_settings
            .lock()
            .map_err(|_| "state lock failed".to_string())?;
        let mut hotkey = state
            .hotkey
            .lock()
            .map_err(|_| "state lock failed".to_string())?;
        let previous_hotkey = hotkey.clone();
        apply_hotkey_state(
            &app,
            &mut hotkey,
            &next.hotkey_accelerator,
            next.hotkey_enabled,
        )?;
        if let Err(error) = save_stored_settings(&next) {
            return Err(rollback_hotkey_state(
                &app,
                &mut hotkey,
                &previous_hotkey,
                error,
            ));
        }
        *stored = next;
    }

    Ok(MigrationImportResponse {
        path: path.to_string_lossy().to_string(),
        history_imported: history_response.imported,
        history_skipped: history_response.skipped,
        corrections_imported: dictionary_response.imported_corrections,
        corrections_skipped: dictionary_response.skipped_corrections,
        preserved_terms_imported: dictionary_response.imported_preserved_terms,
        settings_applied: true,
        secrets_imported: false,
        reentry_required: bundle.secret_policy.reentry_required,
    })
}

#[tauri::command]
pub async fn get_agent_history(
    agent: String,
    limit: i32,
    state: State<'_, AppState>,
) -> Result<Vec<HistoryItem>, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    let path = format!(
        "/v1/history/agent?agent={}&limit={}",
        urlencoding::encode(&agent),
        limit.max(1)
    );
    bridge::get_json(&config, &path)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_agents(state: State<'_, AppState>) -> Result<Vec<AgentSummary>, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::get_json(&config, "/v1/agents")
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn get_dictionary(state: State<'_, AppState>) -> Result<DictionaryPayload, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::get_json(&config, "/v1/dictionary")
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn mutate_dictionary(
    input: DictionaryMutationInput,
    state: State<'_, AppState>,
) -> Result<DictionaryPayload, String> {
    let input = input.normalized()?;
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_json(&config, "/v1/dictionary", &input)
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn dismiss_recent_utterance(
    utterance_id: String,
    state: State<'_, AppState>,
) -> Result<RecentDismissResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_form(
        &config,
        "/v1/recent/dismiss",
        &[("utterance_id", utterance_id)],
    )
    .await
    .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn undo_output(
    output_id: String,
    state: State<'_, AppState>,
) -> Result<UndoOutputResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_form(&config, "/v1/output/undo", &[("output_id", output_id)])
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn retry_recent_utterance(
    utterance_id: String,
    provider: String,
    state: State<'_, AppState>,
) -> Result<RecentRetryResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_form(
        &config,
        "/v1/recent/retry",
        &[("utterance_id", utterance_id), ("provider", provider)],
    )
    .await
    .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn set_recent_mode(
    utterance_id: String,
    mode: String,
    state: State<'_, AppState>,
) -> Result<RecentModeResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_form(
        &config,
        "/v1/recent/mode",
        &[("utterance_id", utterance_id), ("mode", mode)],
    )
    .await
    .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn add_recent_dictionary_term(
    utterance_id: String,
    wrong: String,
    right: String,
    state: State<'_, AppState>,
) -> Result<RecentDictionaryResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_form(
        &config,
        "/v1/recent/dictionary",
        &[
            ("utterance_id", utterance_id),
            ("wrong", wrong),
            ("right", right),
        ],
    )
    .await
    .map_err(|err| err.to_string())
}

#[tauri::command]
pub async fn upload_voice(
    audio_base64: String,
    mime_type: String,
    source: String,
    target_app: String,
    session_id: String,
    mode: String,
    state: State<'_, AppState>,
) -> Result<FastPathResponse, String> {
    let config = state
        .config
        .lock()
        .map_err(|_| "state lock failed".to_string())?
        .clone();
    bridge::post_voice_upload(
        &config,
        &audio_base64,
        &mime_type,
        &source,
        &target_app,
        &session_id,
        &mode,
    )
    .await
    .map_err(|err| err.to_string())
}

#[cfg(test)]
mod tests {
    use super::{
        mode_shortcut_conflicts, parse_mode_shortcut, provided_secret,
        validate_obsidian_sync_input, validate_provider_settings,
    };
    use crate::contracts::{
        AppSettingsInput, LlmRoute, NamedMode, ObsidianSyncInput, RecordMode, SttRoute,
        TargetRiskLevel,
    };
    use crate::state::HotkeyState;

    #[test]
    fn blank_secure_fields_mean_keep_existing_secret() {
        assert_eq!(provided_secret(None), None);
        assert_eq!(provided_secret(Some("   ".to_string())), None);
        assert_eq!(
            provided_secret(Some("  replacement-token  ".to_string())),
            Some("replacement-token".to_string())
        );
    }

    #[test]
    fn provider_settings_require_bounded_distinct_routes_and_safe_custom_urls() {
        let mut input = AppSettingsInput {
            active_profile: "home".to_string(),
            digest_enhancement_provider: "auto".to_string(),
            openai_llm_model: "gpt-5-mini".to_string(),
            qwen_llm_model: "qwen3.7-plus".to_string(),
            doubao_model: "doubao-model".to_string(),
            anthropic_model: "claude-sonnet-5".to_string(),
            ollama_model: "qwen2.5:7b".to_string(),
            llm_main: LlmRoute::OpenaiGpt,
            llm_fallback: LlmRoute::QwenTurbo,
            stt_main: SttRoute::SherpaSensevoice,
            stt_fallback: SttRoute::OpenaiWhisper,
            ..AppSettingsInput::default()
        };
        assert!(validate_provider_settings(&input).is_ok());

        input.llm_fallback = LlmRoute::OpenaiGpt;
        assert!(validate_provider_settings(&input).is_err());
        input.llm_fallback = LlmRoute::QwenTurbo;
        input.custom_llm_base_url = "https://user:secret@example.test/v1".to_string();
        input.custom_llm_model = "team-model".to_string();
        assert!(validate_provider_settings(&input).is_err());
        input.custom_llm_base_url = "http://llm.example.test/v1".to_string();
        assert!(validate_provider_settings(&input).is_err());
        input.custom_llm_base_url = "http://127.example.test/v1".to_string();
        assert!(validate_provider_settings(&input).is_err());
        input.custom_llm_base_url = String::new();
        input.custom_llm_model = String::new();
        input.stt_fallback = SttRoute::SherpaSensevoice;
        assert!(validate_provider_settings(&input).is_err());
    }

    #[test]
    fn obsidian_sync_requires_a_vault_and_bounded_window() {
        assert!(validate_obsidian_sync_input(&ObsidianSyncInput {
            vault_dir: "  ".to_string(),
            days: 7,
        })
        .is_err());
        assert!(validate_obsidian_sync_input(&ObsidianSyncInput {
            vault_dir: "C:\\Users\\me\\Vault".to_string(),
            days: 0,
        })
        .is_err());
        assert!(validate_obsidian_sync_input(&ObsidianSyncInput {
            vault_dir: "C:\\Users\\me\\Vault".to_string(),
            days: 365,
        })
        .is_ok());
    }

    #[test]
    fn mode_shortcuts_reject_main_and_peer_conflicts() {
        let main_hotkey = HotkeyState {
            accelerator: "Ctrl+Shift+Space".to_string(),
            enabled: true,
            registered: true,
        };
        let modes = vec![NamedMode {
            id: "mode-focus".to_string(),
            name: "Focus".to_string(),
            target_app: "Cursor".to_string(),
            target_risk_level: TargetRiskLevel::Medium,
            recording_mode: RecordMode::Private,
            hotkey_accelerator: Some("Ctrl+Alt+1".to_string()),
        }];

        let main = parse_mode_shortcut(Some("Ctrl+Shift+Space"))
            .unwrap()
            .unwrap()
            .1;
        let peer = parse_mode_shortcut(Some("Ctrl+Alt+1")).unwrap().unwrap().1;
        let unique = parse_mode_shortcut(Some("Ctrl+Alt+2")).unwrap().unwrap().1;

        assert!(mode_shortcut_conflicts(main, &main_hotkey, &modes, None));
        assert!(mode_shortcut_conflicts(peer, &main_hotkey, &modes, None));
        assert!(!mode_shortcut_conflicts(
            peer,
            &main_hotkey,
            &modes,
            Some("mode-focus")
        ));
        assert!(!mode_shortcut_conflicts(unique, &main_hotkey, &modes, None));
    }
}

#[cfg(test)]
mod credential_snapshot_tests {
    use super::*;
    use crate::secrets::SecretError;
    use crate::state::{AppConfig, HotkeyState};

    fn snapshot_with(config: AppConfig, credentials: SnapshotCredentials) -> AppSettingsSnapshot {
        let hotkey = HotkeyState {
            accelerator: "CommandOrControl+Shift+Space".to_string(),
            enabled: true,
            registered: false,
        };
        settings_snapshot(
            &config,
            &StoredSettings::default(),
            &hotkey,
            None,
            credentials,
        )
    }

    #[test]
    fn snapshot_has_no_credential_error_when_every_store_read_succeeds() {
        let snapshot = snapshot_with(
            AppConfig::default(),
            SnapshotCredentials {
                notion_token: Ok(Some("n".to_string())),
                provider_keys_error: None,
            },
        );
        assert_eq!(snapshot.credential_store_error, None);
        assert!(snapshot.notion_token_present);
    }

    #[test]
    fn provider_key_read_failure_is_reported_not_shown_as_unconfigured() {
        let snapshot = snapshot_with(
            AppConfig::default(),
            SnapshotCredentials {
                notion_token: Ok(None),
                provider_keys_error: Some(SecretError::Locked),
            },
        );
        assert_eq!(
            snapshot.credential_store_error.as_deref(),
            Some("credential store locked or access denied")
        );
    }

    #[test]
    fn notion_read_failure_is_reported() {
        let snapshot = snapshot_with(
            AppConfig::default(),
            SnapshotCredentials {
                notion_token: Err(SecretError::Backend),
                provider_keys_error: None,
            },
        );
        assert_eq!(
            snapshot.credential_store_error.as_deref(),
            Some("credential store backend failure")
        );
        assert!(!snapshot.notion_token_present);
    }

    #[test]
    fn control_token_refresh_error_takes_precedence() {
        let config = AppConfig {
            credential_store_error: Some("credential store locked or access denied".into()),
            ..AppConfig::default()
        };
        let snapshot = snapshot_with(
            config,
            SnapshotCredentials {
                notion_token: Err(SecretError::Backend),
                provider_keys_error: Some(SecretError::Backend),
            },
        );
        assert_eq!(
            snapshot.credential_store_error.as_deref(),
            Some("credential store locked or access denied")
        );
    }
}
