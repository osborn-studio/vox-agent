use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::contracts::{HistoryExportResponse, HistoryItem, RecordMode, TargetRiskLevel};

const MAX_EXPORT_ITEMS: usize = 500;

pub fn write_history_export(
    items: &[HistoryItem],
    format: &str,
    directory: &Path,
) -> Result<HistoryExportResponse, String> {
    if items.len() > MAX_EXPORT_ITEMS {
        return Err(format!(
            "history export exceeds the {MAX_EXPORT_ITEMS} item limit"
        ));
    }
    if !matches!(format, "markdown" | "json") {
        return Err("history export format must be markdown or json".to_string());
    }

    fs::create_dir_all(directory)
        .map_err(|error| format!("failed to create history export directory: {error}"))?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("system clock is before Unix epoch: {error}"))?
        .as_millis();
    let extension = if format == "markdown" { "md" } else { "json" };
    let filename = format!("vox-agent-history-{timestamp}.{extension}");
    let path = directory.join(filename);
    let content = if format == "markdown" {
        render_markdown(items)
    } else {
        let mut json = serde_json::to_string_pretty(items)
            .map_err(|error| format!("failed to serialize history export: {error}"))?;
        json.push('\n');
        json
    };

    fs::write(&path, content.as_bytes())
        .map_err(|error| format!("failed to write history export: {error}"))?;
    Ok(HistoryExportResponse {
        path: path.to_string_lossy().to_string(),
        directory: directory.to_string_lossy().to_string(),
        format: format.to_string(),
        item_count: items.len(),
        bytes_written: content.len(),
    })
}

fn render_markdown(items: &[HistoryItem]) -> String {
    let mut output = String::from("# Vox Agent History Export\n\n");
    output.push_str(&format!("Items: {}\n\n", items.len()));
    for item in items {
        output.push_str("## ");
        output.push_str(&escape_markdown(&item.created_at));
        output.push_str(" — ");
        output.push_str(&escape_markdown(&item.target_app));
        output.push_str("\n\n");
        output.push_str(&format!(
            "- Agent: {}\n- Session: {}\n- Recording mode: {}\n- Risk: {}\n- Provider: {} / {}\n- Utterance: {}\n- Output: {}\n\n",
            escape_markdown(&item.agent),
            escape_markdown(&item.session_id),
            record_mode_label(item.recording_mode),
            risk_label(item.target_risk_level),
            escape_markdown(&item.stt_provider),
            escape_markdown(&item.stt_model),
            escape_markdown(&item.utterance_id),
            escape_markdown(&item.output_id),
        ));
        output.push_str("### Final text\n\n");
        output.push_str(&markdown_quote(&item.display_text));
        output.push_str("\n### Raw transcript\n\n");
        output.push_str(&markdown_quote(&item.raw_text));
        output.push_str("\n---\n\n");
    }
    output
}

fn markdown_quote(value: &str) -> String {
    let escaped = escape_markdown(value);
    let text = if escaped.is_empty() {
        "(empty)".to_string()
    } else {
        escaped
    };
    let mut quoted = text
        .lines()
        .map(|line| format!("> {line}"))
        .collect::<Vec<_>>()
        .join("\n");
    quoted.push_str("\n\n");
    quoted
}

fn escape_markdown(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '\\' | '`' | '*' | '_' | '{' | '}' | '[' | ']' | '(' | ')' | '#' | '+' | '-' | '.'
            | '!' | '|' => {
                escaped.push('\\');
                escaped.push(character);
            }
            '\r' => {}
            _ => escaped.push(character),
        }
    }
    escaped
}

fn record_mode_label(mode: RecordMode) -> &'static str {
    match mode {
        RecordMode::Normal => "normal",
        RecordMode::Private => "private",
        RecordMode::Ephemeral => "ephemeral",
    }
}

fn risk_label(risk: TargetRiskLevel) -> &'static str {
    match risk {
        TargetRiskLevel::Low => "low",
        TargetRiskLevel::Medium => "medium",
        TargetRiskLevel::High => "high",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::OutputMode;

    fn sample_item() -> HistoryItem {
        HistoryItem {
            id: "history-1".to_string(),
            created_at: "2026-07-19T10:00:00Z".to_string(),
            utterance_id: "utterance-1".to_string(),
            output_id: "output-1".to_string(),
            session_id: "session-1".to_string(),
            raw_text: "<script>alert(1)</script> raw".to_string(),
            display_text: "Vox Agent **final**".to_string(),
            polished_text: "Vox Agent final".to_string(),
            stt_provider: "local".to_string(),
            stt_model: "base".to_string(),
            llm_provider: "none".to_string(),
            latency_ms: 42,
            target_app: "Cursor: Plan".to_string(),
            target_risk_level: TargetRiskLevel::Medium,
            role: "user".to_string(),
            recording_mode: RecordMode::Private,
            output_mode: OutputMode::Paste,
            confidence: 0.98,
            archive_status: "archived".to_string(),
            agent: "claude-code".to_string(),
        }
    }

    #[test]
    fn writes_windows_safe_markdown_without_raw_html() {
        let directory = tempfile::tempdir().expect("temp directory");
        let response = write_history_export(&[sample_item()], "markdown", directory.path())
            .expect("history export");
        let filename = Path::new(&response.path)
            .file_name()
            .and_then(|name| name.to_str())
            .expect("filename");

        assert!(filename.starts_with("vox-agent-history-"));
        assert!(filename.ends_with(".md"));
        assert!(!filename
            .chars()
            .any(|character| "<>:\"/\\|?*".contains(character)));
        let content = fs::read_to_string(&response.path).expect("read export");
        assert!(content.contains("&lt;script&gt;"));
        assert!(!content.contains("<script>"));
        assert_eq!(response.item_count, 1);
        assert_eq!(response.bytes_written, content.len());
    }

    #[test]
    fn writes_json_and_rejects_unbounded_exports() {
        let directory = tempfile::tempdir().expect("temp directory");
        let response = write_history_export(&[sample_item()], "json", directory.path())
            .expect("history export");
        let content = fs::read_to_string(response.path).expect("read export");
        let payload: Vec<HistoryItem> = serde_json::from_str(&content).expect("valid JSON");
        assert_eq!(payload.len(), 1);

        let oversized = vec![sample_item(); MAX_EXPORT_ITEMS + 1];
        assert!(write_history_export(&oversized, "json", directory.path()).is_err());
        assert!(write_history_export(&[], "html", directory.path()).is_err());
    }
}
