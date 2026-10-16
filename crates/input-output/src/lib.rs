use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RiskLevel {
    Low,
    Medium,
    High,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum OutputStrategy {
    Paste,
    DirectTyping,
    PreviewOnly,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OutputPolicyRequest {
    pub target_app: String,
    pub requested_mode: String,
    pub risk_level: RiskLevel,
    pub never_archive_apps: Vec<String>,
    pub fast_path_only_apps: Vec<String>,
    pub disable_direct_typing_apps: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OutputPolicyDecision {
    pub strategy: OutputStrategy,
    pub should_confirm: bool,
    pub should_archive: bool,
    pub reason: String,
}

pub fn decide_output_policy(input: &OutputPolicyRequest) -> OutputPolicyDecision {
    let app = input.target_app.to_ascii_lowercase();
    let listed = |items: &[String]| {
        items
            .iter()
            .any(|item| !item.trim().is_empty() && app.contains(&item.trim().to_ascii_lowercase()))
    };
    let is_sensitive = app.contains("terminal")
        || app.contains("iterm")
        || app.contains("warp")
        || app.contains("password")
        || app.contains("wallet")
        || app.contains("bank");
    let never_archive = listed(&input.never_archive_apps);
    let fast_path_only = listed(&input.fast_path_only_apps);
    let disable_direct_typing = listed(&input.disable_direct_typing_apps);

    if fast_path_only {
        return OutputPolicyDecision {
            strategy: OutputStrategy::Paste,
            should_confirm: false,
            should_archive: !never_archive,
            reason: "App rule enforces fast-path only output.".to_string(),
        };
    }

    if matches!(input.risk_level, RiskLevel::High) || is_sensitive {
        return OutputPolicyDecision {
            strategy: OutputStrategy::PreviewOnly,
            should_confirm: true,
            should_archive: !app.contains("password") && !never_archive,
            reason: "High-risk target detected; require preview before output.".to_string(),
        };
    }

    if matches!(input.risk_level, RiskLevel::Medium) {
        return OutputPolicyDecision {
            strategy: OutputStrategy::Paste,
            should_confirm: true,
            should_archive: !never_archive,
            reason: "Medium-risk target prefers confirm-before-paste.".to_string(),
        };
    }

    let strategy =
        if input.requested_mode.eq_ignore_ascii_case("direct_typing") && !disable_direct_typing {
            OutputStrategy::DirectTyping
        } else {
            OutputStrategy::Paste
        };

    OutputPolicyDecision {
        strategy,
        should_confirm: false,
        should_archive: !never_archive,
        reason: if disable_direct_typing {
            "App rule disables direct typing; fallback to paste.".to_string()
        } else if never_archive {
            "App rule keeps this target out of long-term archive.".to_string()
        } else {
            "Low-risk target allows fast-path output.".to_string()
        },
    }
}
