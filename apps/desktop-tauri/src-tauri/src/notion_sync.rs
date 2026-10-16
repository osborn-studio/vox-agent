use std::collections::{HashMap, HashSet};
use std::time::Duration;

use reqwest::{Client, Method, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::contracts::{HistoryItem, OutputMode, RecordMode, TargetRiskLevel};

const NOTION_API_BASE: &str = "https://api.notion.com/v1";
const NOTION_API_VERSION: &str = "2026-03-11";
const MAX_REMOTE_PAGES: usize = 10_000;
const MAX_UPLOADS_PER_RUN: usize = 200;
const RICH_TEXT_LIMIT: usize = 1_900;

const ID_PROPERTY: &str = "Vox Agent ID";
const CREATED_AT_PROPERTY: &str = "Created At";
const FINAL_TEXT_PROPERTY: &str = "Final Text";
const RAW_TEXT_PROPERTY: &str = "Raw Text";
const AGENT_PROPERTY: &str = "Agent";
const STT_PROVIDER_PROPERTY: &str = "STT Provider";
const TARGET_APP_PROPERTY: &str = "Target App";
const RECORDING_MODE_PROPERTY: &str = "Recording Mode";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NotionSyncResponse {
    pub uploaded: usize,
    pub downloaded: usize,
    pub skipped: usize,
    pub pending_uploads: usize,
    pub remote_pages: usize,
}

pub struct NotionSyncOutcome {
    pub response: NotionSyncResponse,
    pub remote_history: Vec<HistoryItem>,
}

struct NotionClient {
    http: Client,
    token: String,
    base_url: String,
}

impl NotionClient {
    fn new(token: &str) -> Result<Self, String> {
        let token = token.trim();
        if token.is_empty() || token.chars().count() > 500 || token.chars().any(char::is_control) {
            return Err("Notion token is invalid".to_string());
        }
        Ok(Self {
            http: Client::builder()
                .timeout(Duration::from_secs(45))
                .build()
                .map_err(|error| format!("failed to initialize Notion client: {error}"))?,
            token: token.to_string(),
            base_url: NOTION_API_BASE.to_string(),
        })
    }

    async fn request_json(
        &self,
        method: Method,
        path: &str,
        body: Option<&Value>,
    ) -> Result<Value, String> {
        for attempt in 0..3 {
            let mut request = self
                .http
                .request(method.clone(), format!("{}{}", self.base_url, path))
                .bearer_auth(&self.token)
                .header("Notion-Version", NOTION_API_VERSION)
                .header("Content-Type", "application/json");
            if let Some(body) = body {
                request = request.json(body);
            }
            let response = request
                .send()
                .await
                .map_err(|error| format!("Notion request failed: {error}"))?;
            let status = response.status();
            if status == StatusCode::TOO_MANY_REQUESTS && attempt < 2 {
                let delay = retry_after_seconds(response.headers().get("retry-after"));
                tokio::time::sleep(Duration::from_secs(delay)).await;
                continue;
            }
            let bytes = response
                .bytes()
                .await
                .map_err(|error| format!("failed to read Notion response: {error}"))?;
            if !status.is_success() {
                let detail = serde_json::from_slice::<Value>(&bytes)
                    .ok()
                    .and_then(|value| {
                        value
                            .get("message")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .unwrap_or_else(|| String::from_utf8_lossy(&bytes).chars().take(500).collect());
                return Err(format!("Notion returned {status}: {detail}"));
            }
            return serde_json::from_slice(&bytes)
                .map_err(|error| format!("Notion returned invalid JSON: {error}"));
        }
        Err("Notion remained rate limited after retries".to_string())
    }

    async fn ensure_schema(&self, data_source_id: &str) -> Result<String, String> {
        let data_source = self
            .request_json(
                Method::GET,
                &format!("/data_sources/{data_source_id}"),
                None,
            )
            .await?;
        let properties = data_source
            .get("properties")
            .and_then(Value::as_object)
            .ok_or_else(|| "Notion data source has no property schema".to_string())?;
        let title_property = properties
            .iter()
            .find_map(|(name, value)| {
                (value.get("type").and_then(Value::as_str) == Some("title")).then(|| name.clone())
            })
            .ok_or_else(|| "Notion data source has no title property".to_string())?;

        let required = required_schema();
        for (name, configuration) in &required {
            let Some(existing) = properties.get(name) else {
                continue;
            };
            let expected = configuration
                .as_object()
                .and_then(|value| value.keys().next())
                .map(String::as_str)
                .unwrap_or_default();
            if existing.get("type").and_then(Value::as_str) != Some(expected) {
                return Err(format!(
                    "Notion property {name} must use the {expected} type"
                ));
            }
        }
        let missing: Map<String, Value> = required
            .into_iter()
            .filter(|(name, _)| !properties.contains_key(name))
            .collect();
        if !missing.is_empty() {
            self.request_json(
                Method::PATCH,
                &format!("/data_sources/{data_source_id}"),
                Some(&json!({ "properties": missing })),
            )
            .await?;
        }
        Ok(title_property)
    }

    async fn query_pages(&self, data_source_id: &str) -> Result<Vec<Value>, String> {
        let mut pages = Vec::new();
        let mut cursor: Option<String> = None;
        loop {
            let mut body = json!({ "page_size": 100, "result_type": "page" });
            if let Some(cursor) = cursor.as_ref() {
                body["start_cursor"] = Value::String(cursor.clone());
            }
            let response = self
                .request_json(
                    Method::POST,
                    &format!("/data_sources/{data_source_id}/query"),
                    Some(&body),
                )
                .await?;
            let batch = response
                .get("results")
                .and_then(Value::as_array)
                .ok_or_else(|| "Notion query response has no results".to_string())?;
            pages.extend(
                batch
                    .iter()
                    .filter(|page| {
                        page.get("object").and_then(Value::as_str) == Some("page")
                            && page.get("in_trash").and_then(Value::as_bool) != Some(true)
                    })
                    .cloned(),
            );
            if pages.len() > MAX_REMOTE_PAGES {
                return Err(format!(
                    "Notion sync exceeds the {MAX_REMOTE_PAGES} page safety limit"
                ));
            }
            if response.get("has_more").and_then(Value::as_bool) != Some(true) {
                break;
            }
            cursor = response
                .get("next_cursor")
                .and_then(Value::as_str)
                .map(str::to_string);
            if cursor.is_none() {
                return Err("Notion pagination stopped without a next cursor".to_string());
            }
        }
        Ok(pages)
    }

    async fn create_page(
        &self,
        data_source_id: &str,
        title_property: &str,
        item: &HistoryItem,
        include_raw_text: bool,
    ) -> Result<(), String> {
        let body = page_body(data_source_id, title_property, item, include_raw_text);
        self.request_json(Method::POST, "/pages", Some(&body))
            .await?;
        Ok(())
    }
}

pub async fn sync(
    token: &str,
    data_source_id: &str,
    direction: &str,
    include_raw_text: bool,
    local_history: &[HistoryItem],
) -> Result<NotionSyncOutcome, String> {
    validate_data_source_id(data_source_id)?;
    if !matches!(direction, "upload_only" | "download_only" | "bidirectional") {
        return Err("Notion sync direction is invalid".to_string());
    }
    let client = NotionClient::new(token)?;
    let title_property = client.ensure_schema(data_source_id).await?;
    let pages = client.query_pages(data_source_id).await?;
    let remote_history: Vec<HistoryItem> = pages.iter().filter_map(history_from_page).collect();
    let remote_ids: HashSet<&str> = remote_history.iter().map(|item| item.id.as_str()).collect();
    let upload_enabled = matches!(direction, "upload_only" | "bidirectional");
    let download_enabled = matches!(direction, "download_only" | "bidirectional");
    let missing: Vec<&HistoryItem> = if upload_enabled {
        local_history
            .iter()
            .filter(|item| {
                !matches!(item.recording_mode, RecordMode::Ephemeral)
                    && !remote_ids.contains(item.id.as_str())
            })
            .collect()
    } else {
        Vec::new()
    };
    let upload_count = missing.len().min(MAX_UPLOADS_PER_RUN);
    for (index, item) in missing.iter().take(upload_count).enumerate() {
        client
            .create_page(data_source_id, &title_property, item, include_raw_text)
            .await?;
        if index + 1 < upload_count {
            tokio::time::sleep(Duration::from_millis(350)).await;
        }
    }
    let skipped = if upload_enabled {
        local_history.len().saturating_sub(missing.len())
    } else {
        0
    };
    let downloadable = if download_enabled {
        remote_history
    } else {
        Vec::new()
    };
    Ok(NotionSyncOutcome {
        response: NotionSyncResponse {
            uploaded: upload_count,
            downloaded: downloadable.len(),
            skipped,
            pending_uploads: missing.len().saturating_sub(upload_count),
            remote_pages: pages.len(),
        },
        remote_history: downloadable,
    })
}

fn validate_data_source_id(value: &str) -> Result<(), String> {
    let compact: String = value
        .trim()
        .chars()
        .filter(|character| *character != '-')
        .collect();
    if compact.len() != 32
        || !compact
            .chars()
            .all(|character| character.is_ascii_hexdigit())
    {
        return Err("Notion data source ID must be a UUID".to_string());
    }
    Ok(())
}

fn required_schema() -> HashMap<String, Value> {
    HashMap::from([
        (ID_PROPERTY.to_string(), json!({ "rich_text": {} })),
        (CREATED_AT_PROPERTY.to_string(), json!({ "date": {} })),
        (FINAL_TEXT_PROPERTY.to_string(), json!({ "rich_text": {} })),
        (RAW_TEXT_PROPERTY.to_string(), json!({ "rich_text": {} })),
        (AGENT_PROPERTY.to_string(), json!({ "rich_text": {} })),
        (
            STT_PROVIDER_PROPERTY.to_string(),
            json!({ "rich_text": {} }),
        ),
        (TARGET_APP_PROPERTY.to_string(), json!({ "rich_text": {} })),
        (RECORDING_MODE_PROPERTY.to_string(), json!({ "select": {} })),
    ])
}

fn retry_after_seconds(value: Option<&reqwest::header::HeaderValue>) -> u64 {
    value
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<u64>().ok())
        .map(|value| value.clamp(1, 60))
        .unwrap_or(1)
}

fn rich_text(value: &str) -> Value {
    let value: String = value.chars().take(RICH_TEXT_LIMIT).collect();
    json!({ "rich_text": [{ "type": "text", "text": { "content": value } }] })
}

fn enum_value<T: Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_default()
}

fn page_body(
    data_source_id: &str,
    title_property: &str,
    item: &HistoryItem,
    include_raw_text: bool,
) -> Value {
    let final_text = if item.polished_text.trim().is_empty() {
        item.display_text.as_str()
    } else {
        item.polished_text.as_str()
    };
    let title: String = final_text.trim().chars().take(80).collect();
    let title = if title.is_empty() {
        "Vox Agent message".to_string()
    } else {
        title
    };
    let mut properties = Map::from_iter([
        (
            title_property.to_string(),
            json!({ "title": [{ "type": "text", "text": { "content": title } }] }),
        ),
        (ID_PROPERTY.to_string(), rich_text(&item.id)),
        (
            CREATED_AT_PROPERTY.to_string(),
            json!({ "date": { "start": item.created_at } }),
        ),
        (FINAL_TEXT_PROPERTY.to_string(), rich_text(final_text)),
        (AGENT_PROPERTY.to_string(), rich_text(&item.agent)),
        (
            STT_PROVIDER_PROPERTY.to_string(),
            rich_text(&item.stt_provider),
        ),
        (TARGET_APP_PROPERTY.to_string(), rich_text(&item.target_app)),
        (
            RECORDING_MODE_PROPERTY.to_string(),
            json!({ "select": { "name": enum_value(&item.recording_mode) } }),
        ),
    ]);
    if include_raw_text {
        properties.insert(RAW_TEXT_PROPERTY.to_string(), rich_text(&item.raw_text));
    }
    json!({
        "parent": { "type": "data_source_id", "data_source_id": data_source_id },
        "properties": properties,
    })
}

fn plain_text(properties: &Map<String, Value>, name: &str) -> String {
    properties
        .get(name)
        .and_then(|property| property.get("rich_text"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("plain_text").and_then(Value::as_str))
                .collect::<String>()
        })
        .unwrap_or_default()
}

fn date_value(properties: &Map<String, Value>, name: &str) -> String {
    properties
        .get(name)
        .and_then(|property| property.get("date"))
        .and_then(|date| date.get("start"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn select_value(properties: &Map<String, Value>, name: &str) -> String {
    properties
        .get(name)
        .and_then(|property| property.get("select"))
        .and_then(|select| select.get("name"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn history_from_page(page: &Value) -> Option<HistoryItem> {
    let properties = page.get("properties")?.as_object()?;
    let id = plain_text(properties, ID_PROPERTY);
    let created_at = date_value(properties, CREATED_AT_PROPERTY);
    let final_text = plain_text(properties, FINAL_TEXT_PROPERTY);
    if id.trim().is_empty() || created_at.trim().is_empty() || final_text.trim().is_empty() {
        return None;
    }
    let raw_text = plain_text(properties, RAW_TEXT_PROPERTY);
    let recording_mode = serde_json::from_value(Value::String(select_value(
        properties,
        RECORDING_MODE_PROPERTY,
    )))
    .unwrap_or(RecordMode::Normal);
    if matches!(recording_mode, RecordMode::Ephemeral) {
        return None;
    }
    Some(HistoryItem {
        id,
        created_at,
        utterance_id: String::new(),
        output_id: String::new(),
        session_id: String::new(),
        raw_text: if raw_text.is_empty() {
            final_text.clone()
        } else {
            raw_text
        },
        display_text: final_text.clone(),
        polished_text: final_text,
        stt_provider: plain_text(properties, STT_PROVIDER_PROPERTY),
        stt_model: String::new(),
        llm_provider: String::new(),
        latency_ms: 0,
        target_app: plain_text(properties, TARGET_APP_PROPERTY),
        target_risk_level: TargetRiskLevel::Low,
        role: "me".to_string(),
        recording_mode,
        output_mode: OutputMode::Paste,
        confidence: 0.0,
        archive_status: "synced".to_string(),
        agent: plain_text(properties, AGENT_PROPERTY),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn history() -> HistoryItem {
        HistoryItem {
            id: "event-1".to_string(),
            created_at: "2026-07-19T10:00:00Z".to_string(),
            utterance_id: "utterance-1".to_string(),
            output_id: "output-1".to_string(),
            session_id: "session-1".to_string(),
            raw_text: "raw transcript".to_string(),
            display_text: "final transcript".to_string(),
            polished_text: "final transcript".to_string(),
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

    fn response_property(value: Value) -> Value {
        if let Some(items) = value.get("rich_text").and_then(Value::as_array) {
            return json!({
                "rich_text": items.iter().map(|item| json!({
                    "plain_text": item["text"]["content"],
                })).collect::<Vec<_>>()
            });
        }
        value
    }

    #[test]
    fn notion_payload_uses_vox_agent_schema_and_data_source_parent() {
        let body = page_body("0123456789abcdef0123456789abcdef", "Name", &history(), true);
        assert_eq!(body["parent"]["type"], "data_source_id");
        assert_eq!(
            body["properties"][ID_PROPERTY]["rich_text"][0]["text"]["content"],
            "event-1"
        );
        assert_eq!(
            body["properties"][RAW_TEXT_PROPERTY]["rich_text"][0]["text"]["content"],
            "raw transcript"
        );
        assert!(body.to_string().contains("Vox Agent ID"));
        assert!(!body.to_string().contains("VoxLog ID"));
    }

    #[test]
    fn notion_page_round_trips_the_portable_history_identity() {
        let source = history();
        let body = page_body("0123456789abcdef0123456789abcdef", "Name", &source, true);
        let request_properties = body["properties"].as_object().unwrap();
        let properties = request_properties
            .iter()
            .map(|(name, value)| (name.clone(), response_property(value.clone())))
            .collect::<Map<_, _>>();
        let page = json!({ "properties": properties });

        let restored = history_from_page(&page).unwrap();
        assert_eq!(restored.id, source.id);
        assert_eq!(restored.created_at, source.created_at);
        assert_eq!(restored.raw_text, source.raw_text);
        assert_eq!(restored.display_text, source.display_text);
        assert_eq!(restored.agent, source.agent);
        assert_eq!(restored.stt_provider, source.stt_provider);
        assert_eq!(restored.target_app, source.target_app);
    }

    #[test]
    fn notion_ids_and_retry_delays_are_bounded() {
        assert!(validate_data_source_id("01234567-89ab-cdef-0123-456789abcdef").is_ok());
        assert!(validate_data_source_id("database-name").is_err());
        assert_eq!(retry_after_seconds(None), 1);
        let large = reqwest::header::HeaderValue::from_static("999");
        assert_eq!(retry_after_seconds(Some(&large)), 60);
    }
}
