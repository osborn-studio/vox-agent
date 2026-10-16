use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::contracts::{DictionaryPayload, HistoryItem, LlmRoute, NamedMode, SttRoute};
use crate::state::{StoredLlmRoutes, StoredSettings, StoredSttRoutes};

pub const MIGRATION_CONTRACT_VERSION: &str = "2026-07-19.1";
pub const MAX_BUNDLE_BYTES: usize = 64 * 1024 * 1024;
const MAX_HISTORY_ITEMS: usize = 50_000;
const MAX_DICTIONARY_ITEMS: usize = 10_000;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationBundle {
    pub contract_version: String,
    pub product: String,
    pub exported_at_unix_ms: u128,
    pub source_platform: String,
    pub secret_policy: MigrationSecretPolicy,
    pub settings: PortableSettings,
    pub history: Vec<HistoryItem>,
    pub dictionary: PortableDictionary,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationSecretPolicy {
    pub included: bool,
    pub reentry_required: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PortableDictionary {
    pub version: Option<i32>,
    pub corrections: HashMap<String, String>,
    pub preserve: Vec<String>,
    pub format_rules: Option<PortableDictionaryFormatRules>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PortableDictionaryFormatRules {
    pub cn_en_space: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PortableSettings {
    pub active_profile: String,
    pub digest_enhancement_enabled: bool,
    pub digest_enhancement_provider: String,
    pub openai_llm_model: String,
    pub qwen_llm_model: String,
    pub doubao_model: String,
    pub anthropic_model: String,
    pub custom_llm_base_url: Option<String>,
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
    pub hotkey_accelerator: String,
    pub hotkey_behavior: String,
    pub hotkey_enabled: bool,
    pub never_archive_apps: Vec<String>,
    pub fast_path_only_apps: Vec<String>,
    pub disable_direct_typing_apps: Vec<String>,
    pub named_modes: Vec<NamedMode>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MigrationBundleResponse {
    pub path: String,
    pub directory: String,
    pub history_count: usize,
    pub correction_count: usize,
    pub preserved_term_count: usize,
    pub bytes_written: usize,
    pub secrets_included: bool,
}

impl MigrationBundle {
    pub fn new(
        settings: &StoredSettings,
        history: Vec<HistoryItem>,
        dictionary: DictionaryPayload,
    ) -> Result<Self, String> {
        let exported_at_unix_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| format!("system clock is before Unix epoch: {error}"))?
            .as_millis();
        let bundle = Self {
            contract_version: MIGRATION_CONTRACT_VERSION.to_string(),
            product: "Vox Agent".to_string(),
            exported_at_unix_ms,
            source_platform: std::env::consts::OS.to_string(),
            secret_policy: MigrationSecretPolicy {
                included: false,
                reentry_required: vec![
                    "backend_api_token".to_string(),
                    "engine_admin_token".to_string(),
                    "dashscope_key_us".to_string(),
                    "dashscope_key_cn".to_string(),
                    "openai_key".to_string(),
                    "siliconflow_key".to_string(),
                    "doubao_key".to_string(),
                    "anthropic_key".to_string(),
                    "custom_llm_key".to_string(),
                    "custom_llm_endpoint".to_string(),
                    "notion_token".to_string(),
                    "git_credential".to_string(),
                ],
            },
            settings: PortableSettings::from(settings),
            history,
            dictionary: PortableDictionary::from(dictionary),
        };
        bundle.validate()?;
        Ok(bundle)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.contract_version != MIGRATION_CONTRACT_VERSION {
            return Err(format!(
                "migration contract mismatch: expected {MIGRATION_CONTRACT_VERSION}, got {}",
                self.contract_version
            ));
        }
        if self.product != "Vox Agent" {
            return Err("migration bundle is not a Vox Agent bundle".to_string());
        }
        if self.secret_policy.included {
            return Err("migration bundles cannot contain secrets".to_string());
        }
        if self.source_platform.is_empty() || self.source_platform.chars().count() > 40 {
            return Err("migration bundle source platform is invalid".to_string());
        }
        if self.history.len() > MAX_HISTORY_ITEMS {
            return Err(format!(
                "migration bundle exceeds the {MAX_HISTORY_ITEMS} history item limit"
            ));
        }
        if self.dictionary.corrections.len() > MAX_DICTIONARY_ITEMS
            || self.dictionary.preserve.len() > MAX_DICTIONARY_ITEMS
        {
            return Err(format!(
                "migration bundle exceeds the {MAX_DICTIONARY_ITEMS} dictionary item limit"
            ));
        }
        let mut ids = HashSet::new();
        for item in &self.history {
            if item.id.trim().is_empty()
                || item.id.chars().count() > 200
                || !ids.insert(item.id.as_str())
                || item.created_at.chars().count() > 80
                || item.raw_text.chars().count() > 100_000
                || item.display_text.chars().count() > 100_000
                || item.polished_text.chars().count() > 100_000
                || matches!(item.recording_mode, crate::contracts::RecordMode::Ephemeral)
            {
                return Err("migration bundle contains invalid history".to_string());
            }
        }
        if self.dictionary.corrections.iter().any(|(wrong, right)| {
            wrong.trim().is_empty()
                || wrong.chars().count() > 200
                || right.trim().is_empty()
                || right.chars().count() > 500
        }) || self
            .dictionary
            .preserve
            .iter()
            .any(|term| term.trim().is_empty() || term.chars().count() > 500)
        {
            return Err("migration bundle contains invalid dictionary data".to_string());
        }
        self.settings.validate()
    }
}

impl From<DictionaryPayload> for PortableDictionary {
    fn from(dictionary: DictionaryPayload) -> Self {
        Self {
            version: dictionary.version,
            corrections: dictionary.corrections,
            preserve: dictionary.preserve,
            format_rules: dictionary
                .format_rules
                .map(|rules| PortableDictionaryFormatRules {
                    cn_en_space: rules.cn_en_space,
                }),
        }
    }
}

impl From<&StoredSettings> for PortableSettings {
    fn from(settings: &StoredSettings) -> Self {
        Self {
            active_profile: settings.active_profile.clone(),
            digest_enhancement_enabled: settings.digest_enhancement_enabled,
            digest_enhancement_provider: settings.digest_enhancement_provider.clone(),
            openai_llm_model: settings.openai_llm_model.clone(),
            qwen_llm_model: settings.qwen_llm_model.clone(),
            doubao_model: settings.doubao_model.clone(),
            anthropic_model: settings.anthropic_model.clone(),
            custom_llm_base_url: portable_url(&settings.custom_llm_base_url),
            custom_llm_model: settings.custom_llm_model.clone(),
            ollama_model: settings.ollama_model.clone(),
            llm_routes: settings.llm_routes.clone(),
            stt_routes: settings.stt_routes.clone(),
            obsidian_vault_dir: settings.obsidian_vault_dir.clone(),
            ai_mate_memory_dir: settings.ai_mate_memory_dir.clone(),
            notion_sync_enabled: settings.notion_sync_enabled,
            notion_data_source_id: settings.notion_data_source_id.clone(),
            notion_include_raw_text: settings.notion_include_raw_text,
            notion_sync_direction: settings.notion_sync_direction.clone(),
            git_sync_enabled: settings.git_sync_enabled,
            git_sync_repo_url: portable_git_url(&settings.git_sync_repo_url).unwrap_or_default(),
            git_sync_local_path: settings.git_sync_local_path.clone(),
            hotkey_accelerator: settings.hotkey_accelerator.clone(),
            hotkey_behavior: settings.hotkey_behavior.clone(),
            hotkey_enabled: settings.hotkey_enabled,
            never_archive_apps: settings.never_archive_apps.clone(),
            fast_path_only_apps: settings.fast_path_only_apps.clone(),
            disable_direct_typing_apps: settings.disable_direct_typing_apps.clone(),
            named_modes: settings.named_modes.clone(),
        }
    }
}

impl PortableSettings {
    pub fn apply_to(&self, current: &StoredSettings) -> StoredSettings {
        let mut next = current.clone();
        next.active_profile = self.active_profile.clone();
        next.digest_enhancement_enabled = self.digest_enhancement_enabled;
        next.digest_enhancement_provider = self.digest_enhancement_provider.clone();
        next.openai_llm_model = self.openai_llm_model.clone();
        next.qwen_llm_model = self.qwen_llm_model.clone();
        next.doubao_model = self.doubao_model.clone();
        next.anthropic_model = self.anthropic_model.clone();
        next.custom_llm_base_url = self.custom_llm_base_url.clone().unwrap_or_default();
        next.custom_llm_model = if self.custom_llm_base_url.is_some() {
            self.custom_llm_model.clone()
        } else {
            String::new()
        };
        next.ollama_model = self.ollama_model.clone();
        next.llm_routes = self.llm_routes.clone();
        next.stt_routes = self.stt_routes.clone();
        next.obsidian_vault_dir = self.obsidian_vault_dir.clone();
        next.ai_mate_memory_dir = self.ai_mate_memory_dir.clone();
        next.notion_sync_enabled = self.notion_sync_enabled;
        next.notion_data_source_id = self.notion_data_source_id.clone();
        next.notion_include_raw_text = self.notion_include_raw_text;
        next.notion_sync_direction = self.notion_sync_direction.clone();
        next.git_sync_enabled = self.git_sync_enabled;
        next.git_sync_repo_url = self.git_sync_repo_url.clone();
        next.git_sync_local_path = self.git_sync_local_path.clone();
        next.hotkey_accelerator = self.hotkey_accelerator.clone();
        next.hotkey_behavior = self.hotkey_behavior.clone();
        next.hotkey_enabled = self.hotkey_enabled;
        next.never_archive_apps = self.never_archive_apps.clone();
        next.fast_path_only_apps = self.fast_path_only_apps.clone();
        next.disable_direct_typing_apps = self.disable_direct_typing_apps.clone();
        next.named_modes = self.named_modes.clone();
        next
    }

    fn validate(&self) -> Result<(), String> {
        if !matches!(self.active_profile.as_str(), "home" | "office")
            || !matches!(
                self.digest_enhancement_provider.as_str(),
                "auto"
                    | "none"
                    | "openai-gpt"
                    | "qwen-turbo"
                    | "doubao"
                    | "claude"
                    | "openai-compatible-custom"
                    | "ollama"
            )
            || !matches!(
                self.notion_sync_direction.as_str(),
                "upload_only" | "download_only" | "bidirectional"
            )
            || !matches!(self.hotkey_behavior.as_str(), "hold" | "toggle")
            || self.named_modes.len() > 100
            || self.llm_routes.len() > 20
            || self.stt_routes.len() > 20
        {
            return Err("migration bundle contains invalid settings".to_string());
        }
        if self
            .custom_llm_base_url
            .as_deref()
            .is_some_and(|value| portable_url(value).as_deref() != Some(value))
            || portable_git_url(&self.git_sync_repo_url).as_deref()
                != Some(self.git_sync_repo_url.as_str())
        {
            return Err("migration bundle contains unsafe remote URLs".to_string());
        }
        for value in [
            self.obsidian_vault_dir.as_str(),
            self.ai_mate_memory_dir.as_str(),
            self.git_sync_local_path.as_str(),
            self.notion_data_source_id.as_str(),
            self.hotkey_accelerator.as_str(),
        ] {
            if value.chars().count() > 4_000 || value.chars().any(char::is_control) {
                return Err("migration bundle contains invalid settings text".to_string());
            }
        }
        validate_route_maps(&self.llm_routes, &self.stt_routes)
    }
}

fn validate_route_maps(
    llm: &BTreeMap<String, StoredLlmRoutes>,
    stt: &BTreeMap<String, StoredSttRoutes>,
) -> Result<(), String> {
    if llm.iter().any(|(profile, routes)| {
        profile.trim().is_empty()
            || profile.chars().count() > 120
            || routes.main == routes.fallback
            || !matches!(
                routes.main,
                LlmRoute::OpenaiGpt
                    | LlmRoute::QwenTurbo
                    | LlmRoute::Doubao
                    | LlmRoute::Claude
                    | LlmRoute::OpenaiCompatibleCustom
                    | LlmRoute::Ollama
            )
    }) || stt.iter().any(|(profile, routes)| {
        profile.trim().is_empty()
            || profile.chars().count() > 120
            || routes.main == routes.fallback
            || !matches!(
                routes.main,
                SttRoute::QwenUs
                    | SttRoute::QwenCn
                    | SttRoute::OpenaiWhisper
                    | SttRoute::SiliconflowWhisper
                    | SttRoute::WhispercppLocal
                    | SttRoute::QwenLocal
                    | SttRoute::SherpaSensevoice
            )
    }) {
        return Err("migration bundle contains invalid provider routes".to_string());
    }
    Ok(())
}

fn portable_url(value: &str) -> Option<String> {
    let trimmed = value.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return None;
    }
    let parsed = reqwest::Url::parse(trimmed).ok()?;
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return None;
    }
    Some(trimmed.to_string())
}

pub(crate) fn portable_git_url(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Some(String::new());
    }
    if let Ok(parsed) = reqwest::Url::parse(trimmed) {
        if matches!(parsed.scheme(), "https" | "ssh")
            && parsed.password().is_none()
            && (parsed.scheme() == "ssh" || parsed.username().is_empty())
            && parsed.query().is_none()
            && parsed.fragment().is_none()
        {
            return Some(trimmed.to_string());
        }
        return None;
    }
    let scp_style = trimmed
        .strip_prefix("git@")
        .and_then(|value| value.split_once(':'))
        .is_some_and(|(host, path)| {
            !host.is_empty()
                && host
                    .chars()
                    .all(|character| character.is_ascii_alphanumeric() || ".-".contains(character))
                && !path.is_empty()
                && path.chars().count() <= 2_000
                && path.chars().all(|character| {
                    character.is_ascii_alphanumeric() || "/._~-".contains(character)
                })
        });
    scp_style.then(|| trimmed.to_string())
}

pub fn write_bundle(
    bundle: &MigrationBundle,
    directory: &Path,
) -> Result<MigrationBundleResponse, String> {
    bundle.validate()?;
    fs::create_dir_all(directory)
        .map_err(|error| format!("failed to create migration directory: {error}"))?;
    let mut content = serde_json::to_vec_pretty(bundle)
        .map_err(|error| format!("failed to serialize migration bundle: {error}"))?;
    content.push(b'\n');
    if content.len() > MAX_BUNDLE_BYTES {
        return Err(format!(
            "migration bundle exceeds the {MAX_BUNDLE_BYTES} byte limit"
        ));
    }
    let filename = format!(
        "vox-agent-migration-{}.voxagent.json",
        bundle.exported_at_unix_ms
    );
    let path = directory.join(filename);
    let mut temporary = tempfile::NamedTempFile::new_in(directory)
        .map_err(|error| format!("failed to create migration bundle: {error}"))?;
    temporary
        .write_all(&content)
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|error| format!("failed to write migration bundle: {error}"))?;
    temporary
        .persist(&path)
        .map_err(|error| format!("failed to persist migration bundle: {}", error.error))?;
    Ok(MigrationBundleResponse {
        path: path.to_string_lossy().to_string(),
        directory: directory.to_string_lossy().to_string(),
        history_count: bundle.history.len(),
        correction_count: bundle.dictionary.corrections.len(),
        preserved_term_count: bundle.dictionary.preserve.len(),
        bytes_written: content.len(),
        secrets_included: false,
    })
}

pub fn write_bundle_at_path(bundle: &MigrationBundle, path: &Path) -> Result<usize, String> {
    bundle.validate()?;
    if !path
        .file_name()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.ends_with(".voxagent.json"))
    {
        return Err("migration snapshot path must end with .voxagent.json".to_string());
    }
    let directory = path
        .parent()
        .ok_or_else(|| "migration snapshot has no parent directory".to_string())?;
    fs::create_dir_all(directory)
        .map_err(|error| format!("failed to create migration snapshot directory: {error}"))?;
    let mut content = serde_json::to_vec_pretty(bundle)
        .map_err(|error| format!("failed to serialize migration snapshot: {error}"))?;
    content.push(b'\n');
    if content.len() > MAX_BUNDLE_BYTES {
        return Err(format!(
            "migration snapshot exceeds the {MAX_BUNDLE_BYTES} byte limit"
        ));
    }
    let mut temporary = tempfile::NamedTempFile::new_in(directory)
        .map_err(|error| format!("failed to create migration snapshot: {error}"))?;
    temporary
        .write_all(&content)
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|error| format!("failed to write migration snapshot: {error}"))?;
    temporary
        .persist(path)
        .map_err(|error| format!("failed to persist migration snapshot: {}", error.error))?;
    Ok(content.len())
}

pub fn read_bundle(path: &Path) -> Result<MigrationBundle, String> {
    let metadata = fs::metadata(path)
        .map_err(|error| format!("failed to inspect migration bundle: {error}"))?;
    if metadata.len() > MAX_BUNDLE_BYTES as u64 {
        return Err(format!(
            "migration bundle exceeds the {MAX_BUNDLE_BYTES} byte limit"
        ));
    }
    let content =
        fs::read(path).map_err(|error| format!("failed to read migration bundle: {error}"))?;
    let bundle: MigrationBundle = serde_json::from_slice(&content)
        .map_err(|error| format!("invalid migration bundle: {error}"))?;
    bundle.validate()?;
    Ok(bundle)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::{
        DictionaryPayload, HistoryItem, OutputMode, RecordMode, TargetRiskLevel,
    };
    use crate::state::StoredSettings;

    fn sample_history() -> HistoryItem {
        HistoryItem {
            id: "event-1".to_string(),
            created_at: "2026-07-19T10:00:00Z".to_string(),
            utterance_id: "utterance-1".to_string(),
            output_id: "output-1".to_string(),
            session_id: "session-1".to_string(),
            raw_text: "raw".to_string(),
            display_text: "final".to_string(),
            polished_text: "final".to_string(),
            stt_provider: "sherpa-sensevoice".to_string(),
            stt_model: "sensevoice".to_string(),
            llm_provider: "openai-gpt".to_string(),
            latency_ms: 42,
            target_app: "Cursor".to_string(),
            target_risk_level: TargetRiskLevel::Low,
            role: "me".to_string(),
            recording_mode: RecordMode::Normal,
            output_mode: OutputMode::Paste,
            confidence: 0.98,
            archive_status: "archived".to_string(),
            agent: "vox-agent".to_string(),
        }
    }

    #[test]
    fn bundle_is_versioned_for_vox_agent_and_contains_no_secret_values() {
        let mut settings = StoredSettings {
            active_profile: "office".to_string(),
            notion_sync_enabled: true,
            notion_data_source_id: "notion-source".to_string(),
            git_sync_repo_url: "https://github.com/acme/vox-agent-sync.git".to_string(),
            ..StoredSettings::default()
        };
        settings.custom_llm_base_url = "https://user:password@example.com/v1".to_string();
        let bundle = MigrationBundle::new(
            &settings,
            vec![sample_history()],
            DictionaryPayload {
                version: Some(1),
                corrections: std::collections::HashMap::from([(
                    "voxagent".to_string(),
                    "Vox Agent".to_string(),
                )]),
                preserve: vec!["Vox Agent".to_string()],
                format_rules: None,
            },
        )
        .expect("migration bundle");

        let json = serde_json::to_string(&bundle).expect("serialize bundle");
        assert_eq!(bundle.product, "Vox Agent");
        assert_eq!(bundle.contract_version, MIGRATION_CONTRACT_VERSION);
        assert!(!bundle.secret_policy.included);
        assert!(bundle
            .secret_policy
            .reentry_required
            .contains(&"notion_token".to_string()));
        assert!(!json.contains("user:password"));
        assert!(!json.contains("https://user:password@example.com/v1"));
        assert_eq!(bundle.settings.custom_llm_base_url, None);
    }

    #[test]
    fn bundle_round_trip_preserves_provider_choices_without_overwriting_local_control_plane() {
        let source = StoredSettings {
            active_profile: "office".to_string(),
            obsidian_vault_dir: "D:\\Knowledge".to_string(),
            notion_sync_direction: "upload_only".to_string(),
            git_sync_local_path: "D:\\VoxAgentSync".to_string(),
            ..StoredSettings::default()
        };
        let bundle = MigrationBundle::new(
            &source,
            vec![sample_history()],
            DictionaryPayload::default(),
        )
        .expect("bundle");
        let directory = tempfile::tempdir().expect("tempdir");
        let response = write_bundle(&bundle, directory.path()).expect("write bundle");
        let loaded = read_bundle(std::path::Path::new(&response.path)).expect("read bundle");

        let current = StoredSettings {
            backend_base_url: "http://127.0.0.1:7999".to_string(),
            onboarding_completed: false,
            ..StoredSettings::default()
        };
        let imported = loaded.settings.apply_to(&current);

        assert_eq!(imported.active_profile, "office");
        assert_eq!(imported.obsidian_vault_dir, "D:\\Knowledge");
        assert_eq!(imported.backend_base_url, "http://127.0.0.1:7999");
        assert!(!imported.onboarding_completed);
        assert!(response.path.ends_with(".voxagent.json"));
        assert_eq!(loaded.history.len(), 1);
    }

    #[test]
    fn reader_rejects_wrong_product_and_oversized_payloads() {
        let settings = StoredSettings::default();
        let mut bundle = MigrationBundle::new(
            &settings,
            vec![sample_history()],
            DictionaryPayload::default(),
        )
        .expect("bundle");
        bundle.product = "VoxLog".to_string();
        assert!(bundle.validate().is_err());

        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("oversized.voxagent.json");
        let file = std::fs::File::create(&path).expect("oversized fixture");
        file.set_len((MAX_BUNDLE_BYTES + 1) as u64)
            .expect("sparse oversized fixture");
        assert!(read_bundle(&path).is_err());

        let valid = MigrationBundle::new(
            &settings,
            vec![sample_history()],
            DictionaryPayload::default(),
        )
        .expect("valid bundle");
        let mut value = serde_json::to_value(valid).expect("bundle JSON");
        value["history"][0]["api_token"] = serde_json::json!("must-not-be-ignored");
        std::fs::write(
            &path,
            serde_json::to_vec(&value).expect("malicious fixture"),
        )
        .expect("write malicious fixture");
        assert!(read_bundle(&path).is_err());
    }

    #[test]
    fn stable_snapshot_path_is_replaced_atomically() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("devices/device-1/snapshot.voxagent.json");
        let bundle = MigrationBundle::new(
            &StoredSettings::default(),
            vec![sample_history()],
            DictionaryPayload::default(),
        )
        .unwrap();

        let bytes = write_bundle_at_path(&bundle, &path).unwrap();
        let restored = read_bundle(&path).unwrap();

        assert!(bytes > 0);
        assert_eq!(restored.history.len(), 1);
        assert_eq!(restored.product, "Vox Agent");
    }

    #[test]
    fn portable_git_urls_reject_credentials_and_option_like_scp_paths() {
        assert_eq!(
            portable_git_url("git@github.com:acme/vox-agent-sync.git").as_deref(),
            Some("git@github.com:acme/vox-agent-sync.git")
        );
        assert!(portable_git_url("https://token@github.com/acme/sync.git").is_none());
        assert!(portable_git_url("git@github.com:-oProxyCommand=bad").is_none());
        assert!(portable_git_url("https://github.com/acme/sync.git?token=bad").is_none());
    }
}
