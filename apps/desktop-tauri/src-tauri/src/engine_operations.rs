use futures_util::StreamExt;
use reqwest::{Client, RequestBuilder};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;
use tauri::async_runtime::JoinHandle;
use tauri::{AppHandle, Emitter};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::{header::AUTHORIZATION, HeaderValue, Request};
use tokio_tungstenite::tungstenite::Message;

const ENGINE_HTTP_BASE: &str = "http://127.0.0.1:18900";
pub const ENGINE_WS_URL: &str = "ws://127.0.0.1:18900/ws";
pub const ENGINE_EVENT_NAME: &str = "vox-agent://engine-event";
pub const ENGINE_STREAM_NAME: &str = "vox-agent://engine-stream";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EngineAgent {
    pub id: String,
    pub name: String,
    pub emoji: String,
    pub provider: String,
    #[serde(alias = "parent_id")]
    pub parent_id: Option<String>,
    #[serde(alias = "external_system")]
    pub external_system: Option<String>,
    pub skills: Vec<String>,
    #[serde(alias = "max_concurrent")]
    pub max_concurrent: i32,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EngineTask {
    pub id: String,
    #[serde(alias = "agent_id")]
    pub agent_id: String,
    pub title: String,
    pub status: String,
    pub priority: i32,
    #[serde(alias = "created_at")]
    pub created_at: String,
    #[serde(alias = "started_at")]
    pub started_at: Option<String>,
    #[serde(alias = "completed_at")]
    pub completed_at: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EnginePermissionRequest {
    #[serde(alias = "approval_id")]
    pub approval_id: String,
    #[serde(alias = "task_id")]
    pub task_id: String,
    #[serde(alias = "agent_id")]
    pub agent_id: String,
    pub tool: String,
    pub scope: HashMap<String, String>,
    #[serde(alias = "requested_at")]
    pub requested_at: String,
    #[serde(alias = "expires_at")]
    pub expires_at: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EngineOperationsSnapshot {
    pub agents: Vec<EngineAgent>,
    pub tasks: Vec<EngineTask>,
    pub pending_approvals: Vec<EnginePermissionRequest>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineModelPolicy {
    pub answerer: String,
    pub asr_adapter: String,
    pub answerer_override: Option<String>,
    pub asr_adapter_override: Option<String>,
    pub available_answerers: Vec<String>,
    pub available_asr_adapters: Vec<String>,
    pub llm_configured: bool,
    pub llm_model: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineModelPolicyPatch {
    pub answerer: Option<String>,
    pub asr_adapter: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineCommandInput {
    pub text: String,
    pub agent_id: Option<String>,
    pub provider: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EngineCommandResponse {
    pub action: String,
    pub message: String,
    #[serde(alias = "task_id")]
    pub task_id: Option<String>,
    pub data: Value,
}

#[derive(Debug, Serialize)]
struct EngineMessageRequest<'a> {
    text: &'a str,
    agent_id: Option<&'a str>,
    provider: &'a str,
}

#[derive(Debug, Serialize)]
struct EngineApprovalRequest<'a> {
    approval_id: &'a str,
    approved: bool,
    remember_scope: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EngineApprovalResponse {
    pub ok: bool,
    pub decision: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineEventEnvelope {
    #[serde(rename = "type")]
    pub event_type: String,
    pub payload: Value,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineStreamStatus {
    pub state: String,
    pub attempt: u32,
    pub message: String,
}

pub fn engine_http_url(path: &str) -> String {
    format!("{ENGINE_HTTP_BASE}/{}", path.trim_start_matches('/'))
}

/// The request target exactly as reqwest sends it (encoded path and query),
/// which is what the engine signature check compares against.
fn request_target(path: &str) -> String {
    match reqwest::Url::parse(&engine_http_url(path)) {
        // An empty query ("/health?") is signed as the engine reconstructs
        // it: without the "?".
        Ok(url) => match url.query().filter(|query| !query.is_empty()) {
            Some(query) => format!("{}?{query}", url.path()),
            None => url.path().to_string(),
        },
        Err(_) => format!("/{}", path.trim_start_matches('/')),
    }
}

/// Engine requests never use a proxy and never follow redirects (T29).
fn engine_client() -> Client {
    crate::engine_local::loopback_client(Duration::from_secs(12))
        .expect("fixed engine client configuration must be valid")
}

/// How a request to the local engine is authenticated (T29).
///
/// An engine this shell launched gets only a one-time signature for this
/// method and target, and only after it proved it holds the launch secret;
/// otherwise nothing is sent. It never receives the reusable admin bearer:
/// it runs the personal edition, which does not use one. An engine started
/// by someone else (development) keeps the configured bearer.
#[derive(Debug, PartialEq, Eq)]
enum EngineAuth {
    Signed(String),
    Bearer(Option<String>),
}

const ENGINE_UNVERIFIED: &str =
    "The Vox Agent engine has not proved its identity yet; nothing was sent.";

fn engine_auth(
    managed: bool,
    signature: Option<String>,
    token: Option<&str>,
) -> Result<EngineAuth, String> {
    if managed {
        return signature
            .map(EngineAuth::Signed)
            .ok_or_else(|| ENGINE_UNVERIFIED.to_string());
    }
    Ok(EngineAuth::Bearer(
        token
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string),
    ))
}

fn authenticate(
    request: RequestBuilder,
    token: Option<&str>,
    method: &str,
    path: &str,
) -> Result<RequestBuilder, String> {
    let signature = crate::engine_local::signed_header(method, &request_target(path));
    Ok(
        match engine_auth(crate::engine_local::is_managed(), signature, token)? {
            EngineAuth::Signed(value) => request.header(crate::engine_local::AUTH_HEADER, value),
            EngineAuth::Bearer(Some(value)) => request.bearer_auth(value),
            EngineAuth::Bearer(None) => request,
        },
    )
}

async fn get_json<T: DeserializeOwned>(path: &str, token: Option<&str>) -> Result<T, String> {
    authenticate(
        engine_client().get(engine_http_url(path)),
        token,
        "GET",
        path,
    )?
    .send()
    .await
    .map_err(|error| error.to_string())?
    .error_for_status()
    .map_err(|error| error.to_string())?
    .json::<T>()
    .await
    .map_err(|error| error.to_string())
}

async fn post_json<T: DeserializeOwned, B: Serialize>(
    path: &str,
    body: &B,
    token: Option<&str>,
) -> Result<T, String> {
    authenticate(
        engine_client().post(engine_http_url(path)).json(body),
        token,
        "POST",
        path,
    )?
    .send()
    .await
    .map_err(|error| error.to_string())?
    .error_for_status()
    .map_err(|error| error.to_string())?
    .json::<T>()
    .await
    .map_err(|error| error.to_string())
}

async fn patch_json<T: DeserializeOwned, B: Serialize>(
    path: &str,
    body: &B,
    token: Option<&str>,
) -> Result<T, String> {
    authenticate(
        engine_client().patch(engine_http_url(path)).json(body),
        token,
        "PATCH",
        path,
    )?
    .send()
    .await
    .map_err(|error| error.to_string())?
    .error_for_status()
    .map_err(|error| error.to_string())?
    .json::<T>()
    .await
    .map_err(|error| error.to_string())
}

pub async fn fetch_operations_snapshot(
    token: Option<&str>,
) -> Result<EngineOperationsSnapshot, String> {
    let (agents, tasks, pending_approvals) = tokio::try_join!(
        get_json::<Vec<EngineAgent>>("/v1/agents", token),
        get_json::<Vec<EngineTask>>("/v1/tasks", token),
        get_json::<Vec<EnginePermissionRequest>>("/v1/permissions/pending", token),
    )?;
    Ok(EngineOperationsSnapshot {
        agents,
        tasks,
        pending_approvals,
    })
}

pub async fn fetch_model_policy(token: Option<&str>) -> Result<EngineModelPolicy, String> {
    get_json("/v1/admin/model-policy", token).await
}

pub async fn update_model_policy(
    patch: &EngineModelPolicyPatch,
    token: Option<&str>,
) -> Result<EngineModelPolicy, String> {
    for value in [&patch.answerer, &patch.asr_adapter].into_iter().flatten() {
        if value.trim().is_empty() || value.chars().count() > 120 {
            return Err("model policy values must contain 1 through 120 characters".to_string());
        }
    }
    patch_json("/v1/admin/model-policy", patch, token).await
}

pub async fn send_command(
    input: &EngineCommandInput,
    token: Option<&str>,
) -> Result<EngineCommandResponse, String> {
    let text = input.text.trim();
    if text.is_empty() {
        return Err("Agent command must not be empty".to_string());
    }
    if text.chars().count() > 8_000 {
        return Err("Agent command exceeds the 8,000 character limit".to_string());
    }
    let provider = input.provider.as_deref().unwrap_or("claude").trim();
    let body = EngineMessageRequest {
        text,
        agent_id: input
            .agent_id
            .as_deref()
            .filter(|value| !value.trim().is_empty()),
        provider: if provider.is_empty() {
            "claude"
        } else {
            provider
        },
    };
    post_json("/v1/message", &body, token).await
}

pub async fn resolve_permission(
    approval_id: &str,
    approved: bool,
    remember_scope: bool,
    token: Option<&str>,
) -> Result<EngineApprovalResponse, String> {
    let approval_id = approval_id.trim();
    if approval_id.is_empty() {
        return Err("approval id must not be empty".to_string());
    }
    post_json(
        "/v1/permissions/approve",
        &EngineApprovalRequest {
            approval_id,
            approved,
            remember_scope,
        },
        token,
    )
    .await
}

pub fn build_websocket_request(token: Option<&str>) -> Result<Request<()>, String> {
    let auth = engine_auth(
        crate::engine_local::is_managed(),
        crate::engine_local::signed_header("GET", "/ws"),
        token,
    )?;
    build_websocket_request_with(auth)
}

fn build_websocket_request_with(auth: EngineAuth) -> Result<Request<()>, String> {
    let mut request = ENGINE_WS_URL
        .into_client_request()
        .map_err(|_| "invalid fixed engine WebSocket URL".to_string())?;
    match auth {
        EngineAuth::Signed(signature) => {
            let value = HeaderValue::from_str(&signature).map_err(|_| {
                "engine launch signature contains invalid header characters".to_string()
            })?;
            request
                .headers_mut()
                .insert(crate::engine_local::AUTH_HEADER, value);
        }
        EngineAuth::Bearer(Some(token)) => {
            let value = HeaderValue::from_str(&format!("Bearer {token}"))
                .map_err(|_| "engine credential contains invalid header characters".to_string())?;
            request.headers_mut().insert(AUTHORIZATION, value);
        }
        EngineAuth::Bearer(None) => {}
    }
    Ok(request)
}

pub struct EngineEventStream {
    task: Mutex<Option<JoinHandle<()>>>,
}

impl Default for EngineEventStream {
    fn default() -> Self {
        Self {
            task: Mutex::new(None),
        }
    }
}

impl EngineEventStream {
    pub fn start(
        &self,
        app: AppHandle,
        token: Option<String>,
    ) -> Result<EngineStreamStatus, String> {
        let mut task = self
            .task
            .lock()
            .map_err(|_| "engine event stream lock failed".to_string())?;
        if let Some(previous) = task.take() {
            previous.abort();
        }
        *task = Some(tauri::async_runtime::spawn(run_event_stream(app, token)));
        Ok(EngineStreamStatus {
            state: "connecting".to_string(),
            attempt: 1,
            message: "Connecting to the local Agent Engine".to_string(),
        })
    }

    pub fn stop(&self) -> Result<EngineStreamStatus, String> {
        let mut task = self
            .task
            .lock()
            .map_err(|_| "engine event stream lock failed".to_string())?;
        if let Some(previous) = task.take() {
            previous.abort();
        }
        Ok(EngineStreamStatus {
            state: "stopped".to_string(),
            attempt: 0,
            message: "Agent event stream stopped".to_string(),
        })
    }
}

impl Drop for EngineEventStream {
    fn drop(&mut self) {
        if let Ok(task) = self.task.get_mut() {
            if let Some(handle) = task.take() {
                handle.abort();
            }
        }
    }
}

async fn run_event_stream(app: AppHandle, token: Option<String>) {
    let mut attempt = 0_u32;
    loop {
        attempt = attempt.saturating_add(1);
        emit_stream_status(
            &app,
            EngineStreamStatus {
                state: "connecting".to_string(),
                attempt,
                message: "Connecting to the local Agent Engine".to_string(),
            },
        );
        let request = match build_websocket_request(token.as_deref()) {
            Ok(request) => request,
            Err(error) => {
                emit_stream_status(
                    &app,
                    EngineStreamStatus {
                        state: "blocked".to_string(),
                        attempt,
                        message: error,
                    },
                );
                return;
            }
        };
        match tokio_tungstenite::connect_async(request).await {
            Ok((mut socket, _response)) => {
                attempt = 0;
                emit_stream_status(
                    &app,
                    EngineStreamStatus {
                        state: "connected".to_string(),
                        attempt,
                        message: "Authenticated live Agent events".to_string(),
                    },
                );
                while let Some(message) = socket.next().await {
                    match message {
                        Ok(Message::Text(text)) => {
                            if let Ok(event) = serde_json::from_str::<EngineEventEnvelope>(&text) {
                                let _ = app.emit_to("main", ENGINE_EVENT_NAME, event);
                            }
                        }
                        Ok(Message::Close(_)) => break,
                        Ok(_) => {}
                        Err(_) => break,
                    }
                }
            }
            Err(error) => {
                emit_stream_status(
                    &app,
                    EngineStreamStatus {
                        state: "reconnecting".to_string(),
                        attempt,
                        message: format!("Agent event stream unavailable: {error}"),
                    },
                );
            }
        }
        let delay = 1_u64 << attempt.min(4);
        tokio::time::sleep(Duration::from_secs(delay)).await;
    }
}

fn emit_stream_status(app: &AppHandle, status: EngineStreamStatus) {
    let _ = app.emit_to("main", ENGINE_STREAM_NAME, status);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_urls_are_fixed_to_loopback() {
        assert_eq!(
            engine_http_url("/v1/tasks"),
            "http://127.0.0.1:18900/v1/tasks"
        );
        assert_eq!(ENGINE_WS_URL, "ws://127.0.0.1:18900/ws");
    }

    #[test]
    fn the_signed_target_is_the_encoded_path_and_query_that_is_sent() {
        assert_eq!(
            super::request_target("/v1/tasks?limit=5"),
            "/v1/tasks?limit=5"
        );
        assert_eq!(super::request_target("v1/agents"), "/v1/agents");
        assert_eq!(super::request_target("/health?"), "/health");
        assert_eq!(
            super::request_target("/v1/permissions/a b/resolve"),
            "/v1/permissions/a%20b/resolve"
        );
    }

    #[test]
    fn a_launched_engine_gets_only_a_signature_and_nothing_before_it_is_verified() {
        use super::{engine_auth, EngineAuth};
        assert_eq!(
            engine_auth(true, Some("v1.sig".into()), Some("admin-bearer")),
            Ok(EngineAuth::Signed("v1.sig".into())),
            "the reusable admin bearer is never sent to a launched engine"
        );
        assert!(engine_auth(true, None, Some("admin-bearer")).is_err());
        assert_eq!(
            engine_auth(false, None, Some(" admin-bearer ")),
            Ok(EngineAuth::Bearer(Some("admin-bearer".into())))
        );
        assert_eq!(
            engine_auth(false, None, Some("  ")),
            Ok(EngineAuth::Bearer(None))
        );
    }

    #[test]
    fn websocket_request_carries_exactly_the_chosen_authentication() {
        use super::{build_websocket_request_with, EngineAuth};
        let signed =
            build_websocket_request_with(EngineAuth::Signed("v1.1.abc.def".into())).unwrap();
        assert_eq!(
            signed.headers().get("x-voxagent-local-auth").unwrap(),
            "v1.1.abc.def"
        );
        assert!(signed.headers().get("authorization").is_none());
        let bearer =
            build_websocket_request_with(EngineAuth::Bearer(Some("engine-admin-token".into())))
                .unwrap();
        assert_eq!(
            bearer.headers().get("authorization").unwrap(),
            "Bearer engine-admin-token"
        );
        assert!(bearer.headers().get("x-voxagent-local-auth").is_none());
    }

    #[test]
    fn operations_snapshot_decodes_engine_contracts() {
        let snapshot: EngineOperationsSnapshot = serde_json::from_value(serde_json::json!({
            "agents": [{
                "id": "codex",
                "name": "Codex",
                "emoji": "C",
                "provider": "openai",
                "parent_id": null,
                "external_system": null,
                "skills": ["code"],
                "max_concurrent": 2
            }],
            "tasks": [{
                "id": "task-1",
                "agent_id": "codex",
                "title": "Implement event bridge",
                "status": "running",
                "priority": 2,
                "created_at": "2026-07-19T00:00:00+00:00"
            }],
            "pendingApprovals": [{
                "approval_id": "approval-1",
                "task_id": "task-1",
                "agent_id": "codex",
                "tool": "write",
                "scope": {"file_path": "/tmp/example"},
                "requested_at": "2026-07-19T00:00:01+00:00",
                "expires_at": "2026-07-19T00:02:01+00:00"
            }]
        }))
        .unwrap();

        assert_eq!(snapshot.agents[0].id, "codex");
        assert_eq!(snapshot.tasks[0].status, "running");
        assert_eq!(snapshot.pending_approvals[0].tool, "write");
    }

    #[test]
    fn model_policy_decodes_admin_contract() {
        let policy: EngineModelPolicy = serde_json::from_value(serde_json::json!({
            "answerer": "openai",
            "asrAdapter": "whispercpp",
            "answererOverride": null,
            "asrAdapterOverride": "whispercpp",
            "availableAnswerers": ["local", "openai"],
            "availableAsrAdapters": ["whispercpp", "sherpa"],
            "llmConfigured": true,
            "llmModel": "gpt-5"
        }))
        .unwrap();

        assert_eq!(policy.answerer, "openai");
        assert_eq!(policy.asr_adapter_override.as_deref(), Some("whispercpp"));
        assert!(policy.llm_configured);
    }

    #[test]
    fn model_policy_rejects_an_incomplete_engine_contract() {
        let result = serde_json::from_value::<EngineModelPolicy>(serde_json::json!({
            "answerer": "openai",
            "asrAdapter": "whispercpp"
        }));

        assert!(result.is_err());
    }
}
