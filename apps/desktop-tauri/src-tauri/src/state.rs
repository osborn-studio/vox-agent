use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

use crate::contracts::{LlmRoute, NamedMode, SttRoute};

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub backend_base_url: String,
    pub api_token: Option<String>,
    pub engine_api_token: Option<String>,
    /// Error class from the last credential-store refresh, if the store could
    /// not be read. Distinguishes "store unavailable" from "not configured".
    pub credential_store_error: Option<String>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            backend_base_url: "http://127.0.0.1:7901".to_string(),
            api_token: None,
            engine_api_token: None,
            credential_store_error: None,
        }
    }
}

impl AppConfig {
    /// Reload both control tokens from the OS credential store. A store
    /// failure keeps the last known token and records the error class.
    pub fn refresh_tokens_from_store(&mut self) {
        self.refresh_tokens_with(&crate::secrets::read_secret);
    }

    pub(crate) fn refresh_tokens_with(
        &mut self,
        read: &dyn Fn(&str) -> Result<Option<String>, crate::secrets::SecretError>,
    ) {
        let api = crate::secrets::refresh_slot(read, &mut self.api_token, "backend_api_token");
        let engine =
            crate::secrets::refresh_slot(read, &mut self.engine_api_token, "engine_admin_token");
        self.credential_store_error = api.or(engine).map(|error| error.to_string());
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct StoredSettings {
    pub backend_base_url: String,
    pub active_profile: String,
    pub digest_enhancement_enabled: bool,
    pub digest_enhancement_provider: String,
    pub openai_llm_model: String,
    pub qwen_llm_model: String,
    pub doubao_model: String,
    pub anthropic_model: String,
    pub custom_llm_base_url: String,
    pub custom_llm_model: String,
    pub ollama_model: String,
    pub llm_routes: BTreeMap<String, StoredLlmRoutes>,
    pub stt_routes: BTreeMap<String, StoredSttRoutes>,
    pub obsidian_vault_dir: String,
    pub ai_mate_memory_dir: String,
    pub notion_sync_enabled: bool,
    pub notion_data_source_id: String,
    pub notion_include_raw_text: bool,
    pub notion_sync_direction: String,
    pub git_sync_enabled: bool,
    pub git_sync_repo_url: String,
    pub git_sync_local_path: String,
    pub onboarding_completed: bool,
    pub hotkey_accelerator: String,
    pub hotkey_behavior: String,
    pub hotkey_enabled: bool,
    pub never_archive_apps: Vec<String>,
    pub fast_path_only_apps: Vec<String>,
    pub disable_direct_typing_apps: Vec<String>,
    pub named_modes: Vec<NamedMode>,
}

impl Default for StoredSettings {
    fn default() -> Self {
        Self {
            backend_base_url: "http://127.0.0.1:7901".to_string(),
            active_profile: "home".to_string(),
            digest_enhancement_enabled: true,
            digest_enhancement_provider: "auto".to_string(),
            openai_llm_model: "gpt-5-mini".to_string(),
            qwen_llm_model: "qwen3.7-plus".to_string(),
            doubao_model: "doubao-seed-2-0-lite-260215".to_string(),
            anthropic_model: "claude-sonnet-5".to_string(),
            custom_llm_base_url: String::new(),
            custom_llm_model: String::new(),
            ollama_model: "qwen2.5:7b".to_string(),
            llm_routes: BTreeMap::from([
                (
                    "home".to_string(),
                    StoredLlmRoutes {
                        main: LlmRoute::OpenaiGpt,
                        fallback: LlmRoute::QwenTurbo,
                    },
                ),
                (
                    "office".to_string(),
                    StoredLlmRoutes {
                        main: LlmRoute::QwenTurbo,
                        fallback: LlmRoute::Ollama,
                    },
                ),
            ]),
            stt_routes: BTreeMap::from([
                (
                    "home".to_string(),
                    StoredSttRoutes {
                        main: SttRoute::QwenUs,
                        fallback: SttRoute::OpenaiWhisper,
                    },
                ),
                (
                    "office".to_string(),
                    StoredSttRoutes {
                        main: SttRoute::QwenCn,
                        fallback: SttRoute::SiliconflowWhisper,
                    },
                ),
            ]),
            obsidian_vault_dir: String::new(),
            ai_mate_memory_dir: String::new(),
            notion_sync_enabled: false,
            notion_data_source_id: String::new(),
            notion_include_raw_text: false,
            notion_sync_direction: "bidirectional".to_string(),
            git_sync_enabled: false,
            git_sync_repo_url: String::new(),
            git_sync_local_path: String::new(),
            onboarding_completed: false,
            hotkey_accelerator: "CommandOrControl+Shift+Space".to_string(),
            hotkey_behavior: "hold".to_string(),
            hotkey_enabled: false,
            never_archive_apps: vec!["1password".to_string(), "keychain access".to_string()],
            fast_path_only_apps: vec!["cursor".to_string(), "claude".to_string()],
            disable_direct_typing_apps: vec![
                "terminal".to_string(),
                "iterm".to_string(),
                "warp".to_string(),
            ],
            named_modes: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredLlmRoutes {
    pub main: LlmRoute,
    pub fallback: LlmRoute,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredSttRoutes {
    pub main: SttRoute,
    pub fallback: SttRoute,
}

impl StoredSettings {
    pub fn llm_routes_for(&self, profile: &str) -> StoredLlmRoutes {
        self.llm_routes
            .get(profile)
            .cloned()
            .or_else(|| self.llm_routes.get("home").cloned())
            .unwrap_or(StoredLlmRoutes {
                main: LlmRoute::OpenaiGpt,
                fallback: LlmRoute::QwenTurbo,
            })
    }

    pub fn stt_routes_for(&self, profile: &str) -> StoredSttRoutes {
        self.stt_routes
            .get(profile)
            .cloned()
            .or_else(|| self.stt_routes.get("home").cloned())
            .unwrap_or(StoredSttRoutes {
                main: SttRoute::QwenUs,
                fallback: SttRoute::OpenaiWhisper,
            })
    }
}

pub fn normalize_hotkey_behavior(value: &str) -> Result<String, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "" | "hold" => Ok("hold".to_string()),
        "toggle" => Ok("toggle".to_string()),
        _ => Err("hotkey behavior must be hold or toggle".to_string()),
    }
}

#[derive(Debug, Clone)]
pub struct HotkeyState {
    pub accelerator: String,
    pub enabled: bool,
    pub registered: bool,
}

impl Default for HotkeyState {
    fn default() -> Self {
        Self {
            accelerator: "CommandOrControl+Shift+Space".to_string(),
            enabled: false,
            registered: false,
        }
    }
}

pub struct AppState {
    pub audio: crate::audio::AudioCaptureState,
    pub config: Mutex<AppConfig>,
    pub engine: crate::engine_supervisor::EngineSupervisor,
    pub engine_events: crate::engine_operations::EngineEventStream,
    pub hotkey: Mutex<HotkeyState>,
    pub stored_settings: Mutex<StoredSettings>,
}

impl Default for AppState {
    fn default() -> Self {
        if let Err(error) = crate::secrets::migrate_default_legacy_env_files() {
            eprintln!("Vox Agent legacy secret migration skipped: {error}");
        }
        let stored_settings = load_stored_settings().unwrap_or_default();
        Self {
            audio: crate::audio::AudioCaptureState::default(),
            config: Mutex::new({
                let mut config = AppConfig {
                    backend_base_url: stored_settings.backend_base_url.clone(),
                    ..AppConfig::default()
                };
                config.refresh_tokens_from_store();
                config
            }),
            engine: crate::engine_supervisor::EngineSupervisor::default(),
            engine_events: crate::engine_operations::EngineEventStream::default(),
            hotkey: Mutex::new(HotkeyState {
                accelerator: stored_settings.hotkey_accelerator.clone(),
                enabled: stored_settings.hotkey_enabled,
                registered: false,
            }),
            stored_settings: Mutex::new(stored_settings),
        }
    }
}

pub fn settings_file_path() -> PathBuf {
    vox_agent_data_dir().join("desktop-settings.json")
}

pub fn vox_agent_data_dir() -> PathBuf {
    resolve_vox_agent_data_dir(
        std::env::var_os("VOXAGENT_DATA_DIR"),
        std::env::var_os("LOCALAPPDATA"),
        std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")),
        cfg!(windows),
    )
}

fn resolve_vox_agent_data_dir(
    override_dir: Option<OsString>,
    local_app_data: Option<OsString>,
    home: Option<OsString>,
    windows: bool,
) -> PathBuf {
    if let Some(path) = override_dir.filter(|value| !value.is_empty()) {
        return PathBuf::from(path);
    }
    if windows {
        if let Some(path) = local_app_data.filter(|value| !value.is_empty()) {
            return PathBuf::from(path).join("Vox Agent");
        }
    }
    home.filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".voxagent")
}

fn legacy_settings_file_paths() -> Vec<PathBuf> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let current = settings_file_path();
    [
        home.join(".voxagent").join("desktop-settings.json"),
        home.join(".voxlog2").join("desktop-settings.json"),
    ]
    .into_iter()
    .filter(|path| path != &current)
    .collect()
}

pub fn load_stored_settings() -> Result<StoredSettings, String> {
    load_stored_settings_from_candidates(&settings_file_path(), &legacy_settings_file_paths())
}

#[cfg(test)]
fn load_stored_settings_from(
    current_path: &std::path::Path,
    legacy_path: &std::path::Path,
) -> Result<StoredSettings, String> {
    load_stored_settings_from_candidates(current_path, &[legacy_path.to_path_buf()])
}

fn load_stored_settings_from_candidates(
    current_path: &std::path::Path,
    legacy_paths: &[PathBuf],
) -> Result<StoredSettings, String> {
    if current_path.exists() {
        return load_settings_at(current_path);
    }
    if let Some(legacy_path) = legacy_paths.iter().find(|path| path.exists()) {
        let settings = load_settings_at(legacy_path)?;
        save_settings_at(current_path, &settings)?;
        return Ok(settings);
    }
    Ok(StoredSettings::default())
}

pub fn save_stored_settings(settings: &StoredSettings) -> Result<(), String> {
    save_settings_at(&settings_file_path(), settings)
}

fn load_settings_at(path: &std::path::Path) -> Result<StoredSettings, String> {
    let text = fs::read_to_string(path).map_err(|err| err.to_string())?;
    let mut settings: StoredSettings =
        serde_json::from_str(&text).map_err(|err| err.to_string())?;
    settings.digest_enhancement_provider = match settings.digest_enhancement_provider.as_str() {
        "openai" => "openai-gpt".to_string(),
        "qwen" => "qwen-turbo".to_string(),
        "custom" => "openai-compatible-custom".to_string(),
        _ => settings.digest_enhancement_provider,
    };
    Ok(settings)
}

fn save_settings_at(path: &std::path::Path, settings: &StoredSettings) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("settings path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    let text = serde_json::to_string_pretty(settings).map_err(|err| err.to_string())?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)
        .map_err(|error| format!("failed to create settings file: {error}"))?;
    temporary
        .write_all(text.as_bytes())
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|error| format!("failed to write settings file: {error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("failed to secure settings file: {error}"))?;
    }
    temporary
        .persist(path)
        .map_err(|error| format!("failed to replace {}: {}", path.display(), error.error))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_data_directory_prefers_override_then_local_app_data() {
        assert_eq!(
            resolve_vox_agent_data_dir(
                Some(std::ffi::OsString::from("D:\\VoxAgentData")),
                Some(std::ffi::OsString::from("C:\\Users\\me\\AppData\\Local")),
                Some(std::ffi::OsString::from("C:\\Users\\me")),
                true,
            ),
            PathBuf::from("D:\\VoxAgentData")
        );
        assert_eq!(
            resolve_vox_agent_data_dir(
                None,
                Some(std::ffi::OsString::from("C:\\Users\\me\\AppData\\Local")),
                Some(std::ffi::OsString::from("C:\\Users\\me")),
                true,
            ),
            PathBuf::from("C:\\Users\\me\\AppData\\Local").join("Vox Agent")
        );
    }

    #[test]
    fn windows_settings_migration_checks_vox_agent_home_before_voxlog2() {
        let root = tempfile::tempdir().expect("tempdir");
        let current = root
            .path()
            .join("AppData/Local/Vox Agent/desktop-settings.json");
        let prior_vox_agent = root.path().join(".voxagent/desktop-settings.json");
        let legacy_voxlog = root.path().join(".voxlog2/desktop-settings.json");
        let prior_settings = StoredSettings {
            active_profile: "office".to_string(),
            ..StoredSettings::default()
        };
        let legacy_settings = StoredSettings {
            active_profile: "home".to_string(),
            ..StoredSettings::default()
        };
        save_settings_at(&prior_vox_agent, &prior_settings).expect("prior Vox Agent settings");
        save_settings_at(&legacy_voxlog, &legacy_settings).expect("legacy VoxLog settings");

        let migrated = load_stored_settings_from_candidates(
            &current,
            &[prior_vox_agent.clone(), legacy_voxlog.clone()],
        )
        .expect("migrate settings");

        assert_eq!(migrated.active_profile, "office");
        assert!(current.exists());
        assert!(prior_vox_agent.exists(), "migration must be copy-only");
        assert!(
            legacy_voxlog.exists(),
            "legacy source must remain recoverable"
        );
    }

    #[test]
    fn legacy_settings_are_copied_to_the_vox_agent_location() {
        let root = tempfile::tempdir().expect("tempdir");
        let legacy = root.path().join(".voxlog2/desktop-settings.json");
        let current = root.path().join(".voxagent/desktop-settings.json");
        let settings = StoredSettings {
            active_profile: "office".to_string(),
            digest_enhancement_provider: "openai".to_string(),
            ..StoredSettings::default()
        };
        save_settings_at(&legacy, &settings).expect("legacy settings");

        let migrated = load_stored_settings_from(&current, &legacy).expect("migrate settings");

        assert_eq!(migrated.active_profile, "office");
        assert_eq!(migrated.digest_enhancement_provider, "openai-gpt");
        assert!(legacy.exists(), "migration must remain recoverable");
        assert!(current.exists());
    }

    #[test]
    fn hotkey_behavior_accepts_only_hold_or_toggle() {
        assert_eq!(normalize_hotkey_behavior(" HOLD ").unwrap(), "hold");
        assert_eq!(normalize_hotkey_behavior("").unwrap(), "hold");
        assert_eq!(normalize_hotkey_behavior("toggle").unwrap(), "toggle");
        assert!(normalize_hotkey_behavior("repeat").is_err());
    }

    #[test]
    fn named_modes_survive_atomic_settings_replacement() {
        use crate::contracts::{NamedMode, RecordMode, TargetRiskLevel};

        let root = tempfile::tempdir().expect("tempdir");
        let path = root.path().join(".voxagent/desktop-settings.json");
        let mut settings = StoredSettings::default();
        save_settings_at(&path, &settings).expect("initial settings");
        settings.named_modes.push(NamedMode {
            id: "mode-focus".to_string(),
            name: "Focus writing".to_string(),
            target_app: "Cursor".to_string(),
            target_risk_level: TargetRiskLevel::Medium,
            recording_mode: RecordMode::Private,
            hotkey_accelerator: Some("Ctrl+Alt+1".to_string()),
        });
        save_settings_at(&path, &settings).expect("replace settings");

        let loaded = load_settings_at(&path).expect("load settings");
        assert_eq!(loaded.named_modes, settings.named_modes);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }

    #[test]
    fn legacy_named_modes_without_hotkeys_remain_loadable() {
        let mode: NamedMode = serde_json::from_value(serde_json::json!({
            "id": "mode-legacy",
            "name": "Legacy focus",
            "target_app": "Cursor",
            "target_risk_level": "low",
            "recording_mode": "normal"
        }))
        .expect("legacy mode");

        assert_eq!(mode.hotkey_accelerator, None);
    }

    #[test]
    fn settings_without_stt_routes_keep_existing_values_and_receive_defaults() {
        let root = tempfile::tempdir().expect("tempdir");
        let path = root.path().join("desktop-settings.json");
        fs::write(
            &path,
            r#"{
                "active_profile": "office",
                "obsidian_vault_dir": "C:\\Users\\me\\Vault",
                "openai_llm_model": "gpt-5-mini"
            }"#,
        )
        .expect("legacy settings fixture");

        let loaded = load_settings_at(&path).expect("load settings without STT routes");

        assert_eq!(loaded.active_profile, "office");
        assert_eq!(loaded.obsidian_vault_dir, "C:\\Users\\me\\Vault");
        let routes = loaded.stt_routes_for("office");
        assert_eq!(routes.main, SttRoute::QwenCn);
        assert_eq!(routes.fallback, SttRoute::SiliconflowWhisper);
    }
}

#[cfg(test)]
mod credential_refresh_tests {
    use super::AppConfig;
    use crate::secrets::SecretError;

    #[test]
    fn locked_store_keeps_tokens_and_records_the_error_class() {
        let mut config = AppConfig {
            api_token: Some("known-api".into()),
            engine_api_token: Some("known-engine".into()),
            ..AppConfig::default()
        };
        config.refresh_tokens_with(&|_| Err(SecretError::Locked));
        assert_eq!(config.api_token.as_deref(), Some("known-api"));
        assert_eq!(config.engine_api_token.as_deref(), Some("known-engine"));
        assert_eq!(
            config.credential_store_error.as_deref(),
            Some("credential store locked or access denied")
        );
    }

    #[test]
    fn healthy_store_clears_a_previous_error() {
        let mut config = AppConfig {
            credential_store_error: Some("old".into()),
            ..AppConfig::default()
        };
        config.refresh_tokens_with(&|name| {
            Ok((name == "backend_api_token").then(|| "t".to_string()))
        });
        assert_eq!(config.api_token.as_deref(), Some("t"));
        assert_eq!(config.engine_api_token, None);
        assert_eq!(config.credential_store_error, None);
    }
}
