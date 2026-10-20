use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub const DESKTOP_CONTRACT_VERSION: &str = "2026-07-18.1";
pub const DESKTOP_PRODUCT_NAME: &str = "Vox Agent";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DesktopContract {
    pub api_version: String,
    pub product: String,
    pub capabilities: Vec<String>,
}

impl DesktopContract {
    pub fn ensure_compatible(&self) -> Result<(), String> {
        if self.api_version != DESKTOP_CONTRACT_VERSION {
            return Err(format!(
                "incompatible desktop API: expected {}, received {}",
                DESKTOP_CONTRACT_VERSION, self.api_version
            ));
        }
        if self.product != DESKTOP_PRODUCT_NAME {
            return Err(format!("unexpected backend product: {}", self.product));
        }
        if !self.capabilities.iter().any(|item| item == "agent.query") {
            return Err("backend is missing required capability: agent.query".to_string());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentQueryInput {
    pub query: String,
    pub node_ids: Option<Vec<String>>,
    pub limit: Option<i32>,
    pub answerer: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentCitation {
    pub id: String,
    pub title: Option<String>,
    #[serde(rename = "type")]
    pub kind: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentAnswer {
    pub query: String,
    pub answer: String,
    pub answerer: String,
    pub citations: Vec<AgentCitation>,
    pub context_node_ids: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordMode {
    Normal,
    Private,
    Ephemeral,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetRiskLevel {
    Low,
    Medium,
    High,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputMode {
    Paste,
    DirectTyping,
    PreviewOnly,
    None,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FastPathStatus {
    Ok,
    NeedsReview,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FastPathResponse {
    pub id: String,
    pub status: FastPathStatus,
    pub raw_text: String,
    pub display_text: String,
    // Additive, optional (PR-D). `polish_status` stays a String rather than an
    // enum so an unknown future value reaches the WebView as text instead of
    // failing deserialization. Clients key on it, never on text equality.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polished_text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polish_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polish_error: Option<String>,
    pub stt_provider: String,
    pub stt_model: String,
    pub target_app: String,
    pub target_risk_level: TargetRiskLevel,
    pub should_autopaste: bool,
    pub needs_review: bool,
    pub confidence: f32,
    pub dictionary_applied: Vec<DictionaryReplacement>,
    pub latency_ms: i32,
    pub session_id: String,
    pub utterance_id: String,
    pub output_id: String,
    pub output_mode: OutputMode,
    pub archive_status: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DictionaryReplacement {
    pub from: String,
    pub to: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecentUtterance {
    pub id: String,
    pub utterance_id: String,
    pub session_id: String,
    pub output_id: String,
    pub status: FastPathStatus,
    pub raw_text: String,
    pub display_text: String,
    pub target_app: String,
    pub target_risk_level: TargetRiskLevel,
    pub recording_mode: RecordMode,
    pub output_mode: OutputMode,
    pub archive_status: String,
    pub confidence: f32,
    pub stt_provider: String,
    pub stt_model: String,
    pub dictionary_applied: Vec<DictionaryReplacement>,
    pub expires_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecentEnvelope {
    pub recent: Option<RecentUtterance>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UndoOutputResponse {
    pub output_id: String,
    pub utterance_id: String,
    pub accepted: bool,
    pub archive_recalled: bool,
    pub ui_undo_required: bool,
    pub mode: RecordMode,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecentModeResponse {
    pub utterance_id: String,
    pub mode: RecordMode,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecentDismissResponse {
    pub utterance_id: String,
    pub dismissed: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DictionaryPayload {
    pub version: Option<i32>,
    pub corrections: HashMap<String, String>,
    pub preserve: Vec<String>,
    pub format_rules: Option<DictionaryFormatRules>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DictionaryMutationInput {
    pub action: String,
    pub wrong: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub right: Option<String>,
}

impl DictionaryMutationInput {
    pub fn normalized(mut self) -> Result<Self, String> {
        self.action = self.action.trim().to_ascii_lowercase();
        self.wrong = self.wrong.trim().to_string();
        self.right = self
            .right
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty());

        if !matches!(self.action.as_str(), "add" | "delete") {
            return Err("dictionary action must be add or delete".to_string());
        }
        if self.wrong.is_empty() {
            return Err("recognized text is required".to_string());
        }
        if self.wrong.chars().count() > 200 {
            return Err("recognized text exceeds 200 characters".to_string());
        }
        if self.action == "add" {
            let right = self
                .right
                .as_deref()
                .ok_or_else(|| "preferred text is required".to_string())?;
            if right.chars().count() > 500 {
                return Err("preferred text exceeds 500 characters".to_string());
            }
        } else {
            self.right = None;
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DictionaryFormatRules {
    pub cn_en_space: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecentDictionaryResponse {
    pub utterance_id: String,
    pub wrong: String,
    pub right: String,
    pub dictionary: DictionaryPayload,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecentRetryResponse {
    pub id: String,
    pub utterance_id: String,
    pub session_id: String,
    pub output_id: String,
    pub status: FastPathStatus,
    pub raw_text: String,
    pub display_text: String,
    pub target_app: String,
    pub target_risk_level: TargetRiskLevel,
    pub recording_mode: RecordMode,
    pub output_mode: OutputMode,
    pub archive_status: String,
    pub confidence: f32,
    pub stt_provider: String,
    pub stt_model: String,
    pub dictionary_applied: Vec<DictionaryReplacement>,
    pub expires_at: i64,
    pub retried_from: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HotkeyConfig {
    pub accelerator: String,
    pub enabled: bool,
    pub registered: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlobalHotkeyEvent {
    pub accelerator: String,
    pub phase: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthResponse {
    pub status: String,
    pub service: Option<String>,
    pub version: Option<String>,
    pub profile: Option<String>,
    pub python_backend_base_url: Option<String>,
    pub vox_agent_engine_base_url: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryItem {
    pub id: String,
    pub created_at: String,
    pub utterance_id: String,
    pub output_id: String,
    pub session_id: String,
    pub raw_text: String,
    pub display_text: String,
    pub polished_text: String,
    pub stt_provider: String,
    pub stt_model: String,
    pub llm_provider: String,
    pub latency_ms: i32,
    pub target_app: String,
    pub target_risk_level: TargetRiskLevel,
    pub role: String,
    pub recording_mode: RecordMode,
    pub output_mode: OutputMode,
    pub confidence: f32,
    pub archive_status: String,
    pub agent: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct NamedMode {
    pub id: String,
    pub name: String,
    pub target_app: String,
    pub target_risk_level: TargetRiskLevel,
    pub recording_mode: RecordMode,
    #[serde(default)]
    pub hotkey_accelerator: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct NamedModeInput {
    pub name: String,
    pub target_app: String,
    pub target_risk_level: TargetRiskLevel,
    pub recording_mode: RecordMode,
    #[serde(default)]
    pub hotkey_accelerator: Option<String>,
}

impl NamedModeInput {
    pub fn normalized(mut self) -> Result<Self, String> {
        self.name = self.name.trim().to_string();
        self.target_app = self.target_app.trim().to_string();
        self.hotkey_accelerator = self
            .hotkey_accelerator
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty());

        if self.name.is_empty() {
            return Err("mode name is required".to_string());
        }
        if self.name.chars().count() > 80 {
            return Err("mode name exceeds 80 characters".to_string());
        }
        if self.target_app.is_empty() {
            return Err("target application is required".to_string());
        }
        if self.target_app.chars().count() > 200 {
            return Err("target application exceeds 200 characters".to_string());
        }
        if self
            .hotkey_accelerator
            .as_ref()
            .is_some_and(|value| value.chars().count() > 120)
        {
            return Err("mode hotkey exceeds 120 characters".to_string());
        }
        if self
            .hotkey_accelerator
            .as_deref()
            .is_some_and(|value| value.eq_ignore_ascii_case("escape"))
        {
            return Err("Escape is reserved for cancelling an active capture".to_string());
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct NamedModeHotkeyFailure {
    pub mode_id: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct NamedModeHotkeySyncReport {
    pub configured: usize,
    pub registered: usize,
    pub failures: Vec<NamedModeHotkeyFailure>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct HistoryExportInput {
    pub q: String,
    pub agent: String,
    pub session_id: String,
    pub format: String,
}

impl HistoryExportInput {
    pub fn normalized(mut self) -> Result<Self, String> {
        self.q = self.q.trim().to_string();
        self.agent = self.agent.trim().to_string();
        self.session_id = self.session_id.trim().to_string();
        self.format = self.format.trim().to_ascii_lowercase();

        if self.q.chars().count() > 500 {
            return Err("history query exceeds 500 characters".to_string());
        }
        if self.agent.chars().count() > 200 {
            return Err("history agent exceeds 200 characters".to_string());
        }
        if self.session_id.chars().count() > 200 {
            return Err("history session exceeds 200 characters".to_string());
        }
        if !matches!(self.format.as_str(), "markdown" | "json") {
            return Err("history export format must be markdown or json".to_string());
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HistoryExportResponse {
    pub path: String,
    pub directory: String,
    pub format: String,
    pub item_count: usize,
    pub bytes_written: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSummary {
    pub agent: String,
    pub count: i32,
    pub last_active: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ProviderFlags {
    pub dashscope_us: bool,
    pub dashscope_cn: bool,
    pub openai: bool,
    pub siliconflow: bool,
    pub doubao: bool,
    pub anthropic: bool,
    pub custom_llm: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum LlmRoute {
    #[default]
    OpenaiGpt,
    QwenTurbo,
    Doubao,
    Claude,
    OpenaiCompatibleCustom,
    Ollama,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum SttRoute {
    #[default]
    QwenUs,
    QwenCn,
    OpenaiWhisper,
    SiliconflowWhisper,
    WhispercppLocal,
    QwenLocal,
    SherpaSensevoice,
}

impl SttRoute {
    pub fn as_id(&self) -> &'static str {
        match self {
            Self::QwenUs => "qwen-us",
            Self::QwenCn => "qwen-cn",
            Self::OpenaiWhisper => "openai-whisper",
            Self::SiliconflowWhisper => "siliconflow-whisper",
            Self::WhispercppLocal => "whispercpp-local",
            Self::QwenLocal => "qwen-local",
            Self::SherpaSensevoice => "sherpa-sensevoice",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderProfile {
    pub name: String,
    pub stt_main: SttRoute,
    pub stt_fallback: SttRoute,
    pub llm_main: LlmRoute,
    pub llm_fallback: LlmRoute,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmConfigSnapshot {
    pub openai_model: String,
    pub qwen_model: String,
    pub doubao_model: String,
    pub anthropic_model: String,
    pub custom_base_url: String,
    pub custom_model: String,
    pub ollama_model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppSettingsSnapshot {
    pub backend_base_url: String,
    pub backend_api_token_present: bool,
    pub engine_api_token_present: bool,
    pub active_profile: String,
    pub digest_enhancement_enabled: bool,
    pub digest_enhancement_provider: String,
    pub obsidian_vault_dir: String,
    pub ai_mate_memory_dir: String,
    pub notion_sync_enabled: bool,
    pub notion_data_source_id: String,
    pub notion_include_raw_text: bool,
    pub notion_sync_direction: String,
    pub notion_token_present: bool,
    pub git_sync_enabled: bool,
    pub git_sync_repo_url: String,
    pub git_sync_local_path: String,
    pub onboarding_completed: bool,
    pub hotkey_accelerator: String,
    pub hotkey_behavior: String,
    pub hotkey_enabled: bool,
    pub app_rules: AppRuleSet,
    pub provider_flags: ProviderFlags,
    pub llm_config: LlmConfigSnapshot,
    pub profiles: Vec<ProviderProfile>,
    /// Present only when the OS credential store could not be read; token
    /// presence flags are then the last known state, not "not configured".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub credential_store_error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct AppSettingsInput {
    pub backend_base_url: String,
    pub backend_api_token: Option<String>,
    pub engine_api_token: Option<String>,
    pub active_profile: String,
    pub digest_enhancement_enabled: bool,
    pub digest_enhancement_provider: String,
    pub obsidian_vault_dir: String,
    pub ai_mate_memory_dir: String,
    pub notion_sync_enabled: bool,
    pub notion_data_source_id: String,
    pub notion_include_raw_text: bool,
    pub notion_sync_direction: String,
    pub notion_token: Option<String>,
    pub git_sync_enabled: bool,
    pub git_sync_repo_url: String,
    pub git_sync_local_path: String,
    pub onboarding_completed: bool,
    pub hotkey_accelerator: String,
    pub hotkey_behavior: String,
    pub hotkey_enabled: bool,
    pub app_rules: AppRuleSet,
    pub dashscope_key_us: Option<String>,
    pub dashscope_key_cn: Option<String>,
    pub openai_key: Option<String>,
    pub siliconflow_key: Option<String>,
    pub doubao_key: Option<String>,
    pub anthropic_key: Option<String>,
    pub custom_llm_key: Option<String>,
    pub openai_llm_model: String,
    pub qwen_llm_model: String,
    pub doubao_model: String,
    pub anthropic_model: String,
    pub custom_llm_base_url: String,
    pub custom_llm_model: String,
    pub ollama_model: String,
    pub llm_main: LlmRoute,
    pub llm_fallback: LlmRoute,
    pub stt_main: SttRoute,
    pub stt_fallback: SttRoute,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AppRuleSet {
    pub never_archive_apps: Vec<String>,
    pub fast_path_only_apps: Vec<String>,
    pub disable_direct_typing_apps: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderSettingsResponse {
    pub providers: ProviderSettingsProviders,
    pub active_profile: String,
    pub llm_config: LlmConfigSnapshot,
    pub profiles: Vec<ProviderProfile>,
    pub backend_auth_required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderConnectivityCheck {
    pub key: String,
    pub label: String,
    pub status: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderConnectivityResponse {
    pub ready: bool,
    pub active_profile: String,
    pub recommended_stt_provider: String,
    pub backend_url_reachable: bool,
    pub configured_provider_count: i32,
    pub checks: Vec<ProviderConnectivityCheck>,
}

pub const PROVIDER_QUALIFICATION_CONTRACT_VERSION: &str = "2026-07-19.2";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderPlatform {
    Windows,
    Macos,
    Linux,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderQualificationKind {
    Stt,
    Llm,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderTransport {
    Cloud,
    Local,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderSupport {
    Supported,
    InstallRequired,
    Deferred,
    PlatformUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderReadiness {
    Ready,
    ConfiguredUnverified,
    MissingCredential,
    MissingRuntime,
    NotImplemented,
    PlatformUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderProbeStatus {
    NotRun,
    Passed,
    Failed,
    NotApplicable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderActiveRole {
    SttMain,
    SttFallback,
    LlmMain,
    LlmFallback,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderQualificationEntry {
    pub id: String,
    pub label: String,
    pub kind: ProviderQualificationKind,
    pub transport: ProviderTransport,
    pub capabilities: Vec<String>,
    pub support: ProviderSupport,
    pub readiness: ProviderReadiness,
    pub configured: bool,
    pub runtime_available: Option<bool>,
    pub package_available: Option<bool>,
    pub assets_available: Option<bool>,
    pub probe_status: ProviderProbeStatus,
    pub active_roles: Vec<ProviderActiveRole>,
    pub action: String,
    pub install_command: Option<String>,
    pub asset_command: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderNetworkEnvironment {
    pub httpx_trust_env: bool,
    pub proxy_environment_configured: bool,
    pub no_proxy_configured: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderQualificationSummary {
    pub qualified_count: usize,
    pub configured_count: usize,
    pub action_required_count: usize,
    pub active_routes_configured: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderQualificationResponse {
    pub contract_version: String,
    pub platform: ProviderPlatform,
    pub active_profile: String,
    pub network_environment: ProviderNetworkEnvironment,
    pub summary: ProviderQualificationSummary,
    pub providers: Vec<ProviderQualificationEntry>,
}

impl ProviderQualificationResponse {
    pub fn ensure_compatible(self) -> Result<Self, String> {
        if self.contract_version != PROVIDER_QUALIFICATION_CONTRACT_VERSION {
            return Err(format!(
                "provider qualification contract mismatch: expected {}, got {}",
                PROVIDER_QUALIFICATION_CONTRACT_VERSION, self.contract_version
            ));
        }
        if self.active_profile.is_empty() || self.active_profile.chars().count() > 120 {
            return Err("provider qualification active profile is invalid".to_string());
        }
        if self.providers.len() > 100 {
            return Err("provider qualification exceeds the 100 provider limit".to_string());
        }
        if !self.network_environment.httpx_trust_env {
            return Err("provider qualification network policy is invalid".to_string());
        }

        let mut ids = std::collections::HashSet::new();
        for provider in &self.providers {
            if provider.id.is_empty()
                || provider.id.chars().count() > 120
                || provider.label.is_empty()
                || provider.label.chars().count() > 160
                || provider.capabilities.len() > 20
                || provider
                    .capabilities
                    .iter()
                    .any(|capability| capability.is_empty() || capability.chars().count() > 80)
                || provider.active_roles.len() > 4
                || provider.action.is_empty()
                || provider.action.chars().count() > 500
                || provider
                    .install_command
                    .as_ref()
                    .is_some_and(|value| value.is_empty() || value.chars().count() > 500)
                || provider
                    .asset_command
                    .as_ref()
                    .is_some_and(|value| value.is_empty() || value.chars().count() > 500)
                || !ids.insert(provider.id.as_str())
            {
                return Err("provider qualification contains invalid provider metadata".to_string());
            }
            match provider.transport {
                ProviderTransport::Local if provider.runtime_available.is_none() => {
                    return Err(
                        "local provider qualification must report runtime availability".to_string(),
                    );
                }
                ProviderTransport::Cloud if provider.runtime_available.is_some() => {
                    return Err(
                        "cloud provider qualification cannot report a local runtime".to_string()
                    );
                }
                _ => {}
            }
            match provider.transport {
                ProviderTransport::Local if provider.package_available.is_none() => {
                    return Err(
                        "local provider qualification must report package availability".to_string(),
                    );
                }
                ProviderTransport::Cloud
                    if provider.package_available.is_some()
                        || provider.assets_available.is_some()
                        || provider.install_command.is_some()
                        || provider.asset_command.is_some() =>
                {
                    return Err(
                        "cloud provider qualification cannot expose local installation metadata"
                            .to_string(),
                    );
                }
                _ => {}
            }
            if provider.transport == ProviderTransport::Local {
                let expected_runtime = provider.package_available == Some(true)
                    && provider.assets_available != Some(false);
                if provider.runtime_available != Some(expected_runtime)
                    || provider.configured != expected_runtime
                    || (provider.readiness == ProviderReadiness::Ready) != expected_runtime
                    || provider.install_command.is_none()
                {
                    return Err(
                        "local provider qualification readiness is inconsistent".to_string()
                    );
                }
            }
        }

        let configured_count = self.providers.iter().filter(|item| item.configured).count();
        let qualified_count = self
            .providers
            .iter()
            .filter(|item| item.probe_status == ProviderProbeStatus::Passed)
            .count();
        let active: Vec<_> = self
            .providers
            .iter()
            .filter(|item| !item.active_roles.is_empty())
            .collect();
        let action_required_count = active
            .iter()
            .filter(|item| item.probe_status != ProviderProbeStatus::Passed)
            .count();
        let active_routes_configured =
            !active.is_empty() && active.iter().all(|item| item.configured);
        if self.summary.configured_count != configured_count
            || self.summary.qualified_count != qualified_count
            || self.summary.action_required_count != action_required_count
            || self.summary.active_routes_configured != active_routes_configured
        {
            return Err("provider qualification summary is inconsistent".to_string());
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderSettingsProviders {
    pub dashscope_us: ProviderConfigured,
    pub dashscope_cn: ProviderConfigured,
    pub openai: ProviderConfigured,
    pub siliconflow: ProviderConfigured,
    pub doubao: ProviderConfigured,
    pub anthropic: ProviderConfigured,
    pub custom_llm: ProviderConfigured,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderConfigured {
    pub configured: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapturePreview {
    pub provider: String,
    pub model: String,
    pub status_text: String,
    pub completion_hint: String,
    pub capture_window_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OutputPolicyPreview {
    pub strategy: String,
    pub should_confirm: bool,
    pub should_archive: bool,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionDigest {
    pub id: String,
    pub digest_type: String,
    pub session_id: String,
    pub digest_date: String,
    pub project_key: String,
    pub source_event_id: String,
    pub created_at: String,
    pub updated_at: String,
    pub summary: String,
    pub intent: String,
    pub suggested_tags: Vec<String>,
    pub mentioned_entities: Vec<String>,
    pub enhanced: bool,
    pub enhancer_provider: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DigestRebuildInput {
    pub scope: String,
    pub session_id: Option<String>,
    pub date: Option<String>,
    pub project_key: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObsidianExportInput {
    pub scope: String,
    pub vault_dir: String,
    pub session_id: Option<String>,
    pub date: Option<String>,
    pub project_key: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObsidianExportResponse {
    pub ok: bool,
    pub vault_path: String,
    pub note_path: String,
    pub bytes_written: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObsidianSyncInput {
    pub vault_dir: String,
    pub days: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObsidianSyncResponse {
    pub exported: usize,
    pub dates: Vec<String>,
    pub output_dir: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct MigrationImportInput {
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortableHistoryImportResponse {
    pub imported: usize,
    pub skipped: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortableDictionaryImportResponse {
    pub imported_corrections: usize,
    pub skipped_corrections: usize,
    pub imported_preserved_terms: usize,
    pub dictionary: DictionaryPayload,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MigrationImportResponse {
    pub path: String,
    pub history_imported: usize,
    pub history_skipped: usize,
    pub corrections_imported: usize,
    pub corrections_skipped: usize,
    pub preserved_terms_imported: usize,
    pub settings_applied: bool,
    pub secrets_imported: bool,
    pub reentry_required: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LegacyMigrationSource {
    pub path: String,
    pub kind: String,
    pub records_found: usize,
    pub imported: usize,
    pub skipped: usize,
    pub invalid: usize,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LegacyMigrationResponse {
    pub sources: Vec<LegacyMigrationSource>,
    pub records_found: usize,
    pub imported: usize,
    pub skipped: usize,
    pub invalid: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AIMateMemoryExportInput {
    pub scope: String,
    pub base_dir: String,
    pub session_id: Option<String>,
    pub date: Option<String>,
    pub project_key: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AIMateMemoryExportResponse {
    pub ok: bool,
    pub base_path: String,
    pub record_path: String,
    pub bytes_written: i32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fast_path_response_accepts_optional_polish_fields() {
        const WITHOUT: &str = r#"{"id":"u1","status":"ok","raw_text":"嗯 测试","display_text":"嗯 测试","stt_provider":"qwen-local","stt_model":"Qwen/Qwen3-ASR-0.6B","target_app":"cockpit","target_risk_level":"low","should_autopaste":true,"needs_review":false,"confidence":0.9,"dictionary_applied":[],"latency_ms":120,"session_id":"s1","utterance_id":"u1","output_id":"o1","output_mode":"paste","archive_status":"queued","created_at":"2026-10-20T00:00:00Z"}"#;
        let without: FastPathResponse = serde_json::from_str(WITHOUT).unwrap();
        assert_eq!(without.polished_text, None);
        assert_eq!(without.polish_status, None);
        assert_eq!(without.polish_error, None);
        let serialized = serde_json::to_value(&without).unwrap();
        assert!(serialized.get("polish_status").is_none());
        assert!(serialized.get("polish_error").is_none());
        assert!(serialized.get("polished_text").is_none());

        let with = WITHOUT.replacen(
            "\"created_at\"",
            "\"polished_text\":\"测试\",\"polish_status\":\"failed\",\"polish_error\":\"Qwen Bailian: HTTP 429\",\"created_at\"",
            1,
        );
        let with: FastPathResponse = serde_json::from_str(&with).unwrap();
        assert_eq!(with.polished_text.as_deref(), Some("测试"));
        assert_eq!(with.polish_status.as_deref(), Some("failed"));
        assert_eq!(with.polish_error.as_deref(), Some("Qwen Bailian: HTTP 429"));
        let serialized = serde_json::to_value(&with).unwrap();
        assert_eq!(serialized["polish_status"], "failed");
    }

    #[test]
    fn desktop_contract_requires_matching_version_product_and_agent_query() {
        let compatible = DesktopContract {
            api_version: DESKTOP_CONTRACT_VERSION.to_string(),
            product: DESKTOP_PRODUCT_NAME.to_string(),
            capabilities: vec!["agent.query".to_string(), "voice.capture".to_string()],
        };
        assert!(compatible.ensure_compatible().is_ok());

        let stale = DesktopContract {
            api_version: "2026-01-01".to_string(),
            ..compatible.clone()
        };
        assert!(stale.ensure_compatible().is_err());

        let recorder_only = DesktopContract {
            capabilities: vec!["voice.capture".to_string()],
            ..compatible
        };
        assert!(recorder_only.ensure_compatible().is_err());
    }

    #[test]
    fn agent_answer_deserializes_shared_camel_case_contract() {
        let answer: AgentAnswer = serde_json::from_value(serde_json::json!({
            "query": "What changed?",
            "answer": "The Agent timeline shipped.",
            "answerer": "vox-agent",
            "citations": [{"id": "node-1", "title": "Release", "type": "note"}],
            "contextNodeIds": ["node-1"]
        }))
        .expect("shared Agent response should deserialize");

        assert_eq!(answer.context_node_ids, vec!["node-1"]);
        assert_eq!(answer.citations[0].kind.as_deref(), Some("note"));
    }

    #[test]
    fn dictionary_mutations_are_trimmed_and_bounded() {
        let add = DictionaryMutationInput {
            action: "add".to_string(),
            wrong: "  vox log  ".to_string(),
            right: Some("  Vox Agent  ".to_string()),
        }
        .normalized()
        .expect("valid correction");
        assert_eq!(add.action, "add");
        assert_eq!(add.wrong, "vox log");
        assert_eq!(add.right.as_deref(), Some("Vox Agent"));

        let delete = DictionaryMutationInput {
            action: "delete".to_string(),
            wrong: "obsolete".to_string(),
            right: Some("ignored".to_string()),
        }
        .normalized()
        .expect("valid deletion");
        assert_eq!(delete.right, None);
        let delete_json = serde_json::to_value(&delete).expect("serialize deletion");
        assert_eq!(delete_json.get("right"), None);

        for invalid in [
            DictionaryMutationInput {
                action: "replace".to_string(),
                wrong: "old".to_string(),
                right: Some("new".to_string()),
            },
            DictionaryMutationInput {
                action: "add".to_string(),
                wrong: "".to_string(),
                right: Some("new".to_string()),
            },
            DictionaryMutationInput {
                action: "add".to_string(),
                wrong: "old".to_string(),
                right: Some("".to_string()),
            },
            DictionaryMutationInput {
                action: "add".to_string(),
                wrong: "x".repeat(201),
                right: Some("new".to_string()),
            },
            DictionaryMutationInput {
                action: "add".to_string(),
                wrong: "old".to_string(),
                right: Some("x".repeat(501)),
            },
        ] {
            assert!(invalid.normalized().is_err());
        }

        assert!(
            serde_json::from_value::<DictionaryMutationInput>(serde_json::json!({
                "action": "add",
                "wrong": "old",
                "right": "new",
                "actor": "untrusted"
            }))
            .is_err()
        );
    }

    #[test]
    fn named_mode_inputs_are_normalized_and_bounded() {
        let input = NamedModeInput {
            name: "  Focus writing  ".to_string(),
            target_app: "  Cursor  ".to_string(),
            target_risk_level: TargetRiskLevel::Medium,
            recording_mode: RecordMode::Private,
            hotkey_accelerator: Some("  CommandOrControl+Alt+1  ".to_string()),
        }
        .normalized()
        .expect("valid named mode");

        assert_eq!(input.name, "Focus writing");
        assert_eq!(input.target_app, "Cursor");
        assert_eq!(input.target_risk_level, TargetRiskLevel::Medium);
        assert_eq!(input.recording_mode, RecordMode::Private);
        assert_eq!(
            input.hotkey_accelerator.as_deref(),
            Some("CommandOrControl+Alt+1")
        );

        let mut invalid = input.clone();
        invalid.name = " ".to_string();
        assert!(invalid.normalized().is_err());

        let mut oversized = input;
        oversized.target_app = "x".repeat(201);
        assert!(oversized.normalized().is_err());

        let invalid_hotkey = NamedModeInput {
            name: "Private capture".to_string(),
            target_app: "Notes".to_string(),
            target_risk_level: TargetRiskLevel::Low,
            recording_mode: RecordMode::Private,
            hotkey_accelerator: Some("x".repeat(121)),
        };
        assert!(invalid_hotkey.normalized().is_err());
    }

    #[test]
    fn history_export_inputs_reject_unsafe_or_unbounded_filters() {
        let input = HistoryExportInput {
            q: " agent plan ".to_string(),
            agent: " claude-code ".to_string(),
            session_id: " session-1 ".to_string(),
            format: " MARKDOWN ".to_string(),
        }
        .normalized()
        .expect("valid history export");

        assert_eq!(input.q, "agent plan");
        assert_eq!(input.agent, "claude-code");
        assert_eq!(input.session_id, "session-1");
        assert_eq!(input.format, "markdown");

        let mut invalid = input.clone();
        invalid.format = "html".to_string();
        assert!(invalid.normalized().is_err());

        let mut oversized = input;
        oversized.session_id = "x".repeat(201);
        assert!(oversized.normalized().is_err());
    }

    #[test]
    fn provider_qualification_requires_the_versioned_secret_free_shape() {
        let response: ProviderQualificationResponse = serde_json::from_value(serde_json::json!({
            "contract_version": "2026-07-19.2",
            "platform": "windows",
            "active_profile": "home",
            "network_environment": {
                "httpx_trust_env": true,
                "proxy_environment_configured": false,
                "no_proxy_configured": true
            },
            "summary": {
                "qualified_count": 0,
                "configured_count": 1,
                "action_required_count": 1,
                "active_routes_configured": true
            },
            "providers": [{
                "id": "whispercpp-local",
                "label": "whisper.cpp Local",
                "kind": "stt",
                "transport": "local",
                "capabilities": ["batch", "offline"],
                "support": "install_required",
                "readiness": "ready",
                "configured": true,
                "runtime_available": true,
                "package_available": true,
                "assets_available": null,
                "probe_status": "not_run",
                "active_roles": ["stt_main"],
                "action": "Run a real audio smoke before release.",
                "install_command": "py -m pip install \".[local]\"",
                "asset_command": null
            }]
        }))
        .expect("valid qualification response");

        assert_eq!(response.platform, ProviderPlatform::Windows);
        assert_eq!(response.providers[0].kind, ProviderQualificationKind::Stt);
        response.ensure_compatible().expect("compatible contract");

        let mismatch: ProviderQualificationResponse = serde_json::from_value(serde_json::json!({
            "contract_version": "legacy",
            "platform": "windows",
            "active_profile": "home",
            "network_environment": {
                "httpx_trust_env": true,
                "proxy_environment_configured": false,
                "no_proxy_configured": false
            },
            "summary": {
                "qualified_count": 0,
                "configured_count": 0,
                "action_required_count": 0,
                "active_routes_configured": false
            },
            "providers": []
        }))
        .expect("shape still deserializes");
        assert!(mismatch.ensure_compatible().is_err());
    }

    #[test]
    fn provider_settings_decode_shared_windows_llm_routes() {
        let response: ProviderSettingsResponse = serde_json::from_value(serde_json::json!({
            "providers": {
                "dashscope_us": {"configured": true},
                "dashscope_cn": {"configured": false},
                "openai": {"configured": true},
                "siliconflow": {"configured": false},
                "doubao": {"configured": true},
                "anthropic": {"configured": true},
                "custom_llm": {"configured": true}
            },
            "active_profile": "home",
            "digest_enhancement_enabled": true,
            "digest_enhancement_provider": "auto",
            "llm_config": {
                "openai_model": "gpt-5-mini",
                "qwen_model": "qwen3.7-plus",
                "doubao_model": "doubao-model",
                "anthropic_model": "claude-sonnet-5",
                "custom_base_url": "https://llm.example.test/v1",
                "custom_model": "team-model",
                "ollama_model": "qwen2.5:7b"
            },
            "profiles": [{
                "name": "home",
                "stt_main": "qwen-us",
                "stt_fallback": "openai-whisper",
                "llm_main": "doubao",
                "llm_fallback": "claude"
            }],
            "backend_auth_required": false
        }))
        .expect("valid provider settings");

        assert_eq!(response.profiles[0].llm_main, LlmRoute::Doubao);
        assert_eq!(response.profiles[0].llm_fallback, LlmRoute::Claude);
        assert_eq!(response.profiles[0].stt_main, SttRoute::QwenUs);
        assert_eq!(response.profiles[0].stt_fallback, SttRoute::OpenaiWhisper);
        assert!(response.providers.custom_llm.configured);
        assert_eq!(response.llm_config.custom_model, "team-model");
    }
}
