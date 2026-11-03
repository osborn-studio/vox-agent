//! Bundled voice backend sidecar (PR-B, spec §3.1).
//!
//! An installed build ships `voxagent-backend[.exe]` (the frozen
//! `apps/desktop/server.py`) beside the desktop executable. The shell starts it
//! on loopback port 7902 in bundled mode and hands it two fresh secrets on
//! stdin: a bearer token for every request and a launch id that `/health`
//! must echo. The shell routes voice/provider/history calls to it only after
//! that echo proves the listener is the process this launch started, so a
//! foreign program on the port never receives provider keys.
//!
//! Development builds without the bundled backend keep the configured
//! gateway URL; a configured non-default URL always wins.

use std::fs::{self, OpenOptions};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use zeroize::Zeroizing;

use crate::engine_local;
use crate::sidecar_job::SidecarJob;
use crate::state::AppConfig;

pub const DEFAULT_BACKEND_URL: &str = "http://127.0.0.1:7901";
const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 7_902;

fn bundled_backend_name() -> &'static str {
    if cfg!(windows) {
        "voxagent-backend.exe"
    } else {
        "voxagent-backend"
    }
}

pub fn bundled_backend_path(desktop_executable: &Path) -> PathBuf {
    desktop_executable
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(bundled_backend_name())
}

pub fn bundled_base_url() -> String {
    format!("http://{BACKEND_HOST}:{BACKEND_PORT}")
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RouteError {
    /// The bundled backend is running but has not proven its identity yet.
    NotVerified(String),
}

impl std::fmt::Display for RouteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RouteError::NotVerified(reason) => write!(f, "voice backend unavailable: {reason}"),
        }
    }
}

impl std::error::Error for RouteError {}

/// Where a bridge request goes and with which bearer token.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Route {
    pub base_url: String,
    pub token: Option<String>,
}

/// Pure routing rule: the bundled backend takes over only the default
/// gateway URL, and only once its launch has been verified.
pub fn choose_route(
    config: &AppConfig,
    launch: Option<&LaunchSnapshot>,
) -> Result<Route, RouteError> {
    let configured = Route {
        base_url: config.backend_base_url.clone(),
        token: config.api_token.clone(),
    };
    let uses_default = config.backend_base_url.trim_end_matches('/') == DEFAULT_BACKEND_URL;
    match launch {
        Some(launch) if uses_default => {
            if launch.verified {
                Ok(Route {
                    base_url: bundled_base_url(),
                    token: Some(launch.token.to_string()),
                })
            } else {
                Err(RouteError::NotVerified(
                    "the bundled voice backend is still starting".to_string(),
                ))
            }
        }
        _ => Ok(configured),
    }
}

/// No `Debug`: the snapshot carries the launch secrets.
#[derive(Clone)]
pub struct LaunchSnapshot {
    pub token: Zeroizing<String>,
    pub launch_id: Zeroizing<String>,
    pub verified: bool,
}

struct Inner {
    child: Option<Child>,
    /// Owns the backend's process tree on Windows (see `sidecar_job`).
    job: Option<SidecarJob>,
    launch: Option<LaunchSnapshot>,
    last_error: Option<String>,
}

static CURRENT: Mutex<Inner> = Mutex::new(Inner {
    child: None,
    job: None,
    launch: None,
    last_error: None,
});

fn snapshot() -> Option<LaunchSnapshot> {
    let mut inner = CURRENT.lock().ok()?;
    refresh(&mut inner);
    inner.launch.clone()
}

fn refresh(inner: &mut Inner) {
    if let Some(child) = inner.child.as_mut() {
        if let Ok(Some(exit)) = child.try_wait() {
            inner.child = None;
            inner.job = None;
            inner.launch = None;
            inner.last_error = Some(format!("Vox Agent voice backend exited with {exit}"));
        }
    }
}

/// Resolve the route for one bridge request.
pub fn route(config: &AppConfig) -> Result<Route, RouteError> {
    choose_route(config, snapshot().as_ref())
}

/// Start the bundled backend if this installation ships one. Returns
/// `Ok(false)` when there is nothing bundled (development builds).
pub fn start() -> Result<bool, String> {
    let desktop = std::env::current_exe()
        .map_err(|error| format!("failed to locate the Vox Agent executable: {error}"))?;
    let executable = bundled_backend_path(&desktop);
    if !executable.is_file() {
        return Ok(false);
    }
    let mut inner = CURRENT
        .lock()
        .map_err(|_| "voice backend lock failed".to_string())?;
    refresh(&mut inner);
    if inner.child.is_some() {
        return Ok(true);
    }
    let log_path = crate::state::vox_agent_data_dir()
        .join("logs")
        .join("voice-backend.log");
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("failed to create voice backend log directory: {error}"))?;
    }
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| format!("failed to open voice backend log: {error}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|error| format!("failed to clone voice backend log handle: {error}"))?;
    let mut command = Command::new(&executable);
    command
        .args([
            "--host",
            BACKEND_HOST,
            "--port",
            &BACKEND_PORT.to_string(),
            "--require-auth",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().map_err(|error| {
        let message = format!(
            "failed to start the Vox Agent voice backend at {}: {error}",
            executable.display()
        );
        inner.last_error = Some(message.clone());
        message
    })?;
    let job = SidecarJob::attach(&child);
    let token = engine_local::generate_token();
    let launch_id = engine_local::generate_token();
    let delivered = child.stdin.take().map(|mut pipe| {
        use std::io::Write;
        let lines = Zeroizing::new(format!("{}\n{}\n", token.as_str(), launch_id.as_str()));
        pipe.write_all(lines.as_bytes()).and_then(|_| pipe.flush())
    });
    if !matches!(delivered, Some(Ok(()))) {
        let _ = child.kill();
        let _ = child.wait();
        let message = "failed to hand the launch secrets to the voice backend".to_string();
        inner.last_error = Some(message.clone());
        return Err(message);
    }
    inner.child = Some(child);
    inner.job = job;
    inner.launch = Some(LaunchSnapshot {
        token,
        launch_id,
        verified: false,
    });
    inner.last_error = None;
    Ok(true)
}

/// Probe `/health` with the launch token; trust the listener only if it
/// echoes this launch's id. Returns whether the launch is now verified.
pub async fn verify() -> Result<bool, String> {
    let Some(launch) = snapshot() else {
        return Ok(false);
    };
    if launch.verified {
        return Ok(true);
    }
    let client = engine_local::loopback_client(Duration::from_secs(2))
        .map_err(|error| format!("failed to create voice backend client: {error}"))?;
    let response = client
        .get(format!("{}/health", bundled_base_url()))
        .bearer_auth(launch.token.as_str())
        .send()
        .await
        .map_err(|_| "voice backend is not reachable yet".to_string())?;
    if !response.status().is_success() {
        return Err(format!(
            "voice backend health returned HTTP {}",
            response.status()
        ));
    }
    let payload = response
        .json::<serde_json::Value>()
        .await
        .map_err(|_| "voice backend health returned invalid JSON".to_string())?;
    if !echoes_launch(&payload, launch.launch_id.as_str()) {
        return Err(
            "another program is answering on the voice backend port; it was not trusted"
                .to_string(),
        );
    }
    let mut inner = CURRENT
        .lock()
        .map_err(|_| "voice backend lock failed".to_string())?;
    match inner.launch.as_mut() {
        // Only the launch that was probed may be marked verified.
        Some(current) if current.launch_id.as_str() == launch.launch_id.as_str() => {
            current.verified = true;
            Ok(true)
        }
        _ => Ok(false),
    }
}

pub fn echoes_launch(payload: &serde_json::Value, launch_id: &str) -> bool {
    payload.get("launch_id").and_then(serde_json::Value::as_str) == Some(launch_id)
}

/// Poll until the launch verifies or the deadline passes.
pub async fn wait_until_verified(deadline: Duration) -> Result<(), String> {
    let started = std::time::Instant::now();
    let mut last = "voice backend did not start".to_string();
    while started.elapsed() < deadline {
        match verify().await {
            Ok(true) => return Ok(()),
            Ok(false) => return Err("voice backend is not running".to_string()),
            Err(error) => last = error,
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    if let Ok(mut inner) = CURRENT.lock() {
        inner.last_error = Some(last.clone());
    }
    Err(last)
}

pub fn stop() {
    if let Ok(mut inner) = CURRENT.lock() {
        inner.launch = None;
        inner.job = None;
        if let Some(mut child) = inner.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn launch(verified: bool) -> LaunchSnapshot {
        LaunchSnapshot {
            token: Zeroizing::new("launch-token".to_string()),
            launch_id: Zeroizing::new("launch-id".to_string()),
            verified,
        }
    }

    fn config(url: &str, token: Option<&str>) -> AppConfig {
        AppConfig {
            backend_base_url: url.to_string(),
            api_token: token.map(str::to_string),
            ..AppConfig::default()
        }
    }

    #[test]
    fn without_a_bundled_backend_the_configured_gateway_is_used() {
        let route = choose_route(&config(DEFAULT_BACKEND_URL, Some("dev")), None).unwrap();
        assert_eq!(route.base_url, DEFAULT_BACKEND_URL);
        assert_eq!(route.token.as_deref(), Some("dev"));
    }

    #[test]
    fn a_verified_bundled_backend_replaces_the_default_url_and_token() {
        let route = choose_route(
            &config(DEFAULT_BACKEND_URL, Some("dev")),
            Some(&launch(true)),
        )
        .unwrap();
        assert_eq!(route.base_url, "http://127.0.0.1:7902");
        assert_eq!(route.token.as_deref(), Some("launch-token"));
        let trailing = format!("{DEFAULT_BACKEND_URL}/");
        assert!(choose_route(&config(&trailing, None), Some(&launch(true))).is_ok());
    }

    #[test]
    fn an_unverified_bundled_backend_fails_closed_instead_of_falling_back() {
        let error = choose_route(&config(DEFAULT_BACKEND_URL, None), Some(&launch(false)));
        assert!(matches!(error, Err(RouteError::NotVerified(_))));
    }

    #[test]
    fn an_explicitly_configured_backend_url_always_wins() {
        let route = choose_route(
            &config("https://voice.example.test", Some("remote")),
            Some(&launch(true)),
        )
        .unwrap();
        assert_eq!(route.base_url, "https://voice.example.test");
        assert_eq!(route.token.as_deref(), Some("remote"));
    }

    #[test]
    fn only_the_exact_launch_id_is_trusted() {
        let ok = serde_json::json!({"status": "ok", "launch_id": "launch-id"});
        assert!(echoes_launch(&ok, "launch-id"));
        assert!(!echoes_launch(
            &serde_json::json!({"launch_id": "other"}),
            "launch-id"
        ));
        assert!(!echoes_launch(
            &serde_json::json!({"status": "ok"}),
            "launch-id"
        ));
    }

    #[test]
    fn the_bundled_backend_sits_beside_the_desktop_executable() {
        let path = bundled_backend_path(Path::new("/opt/Vox Agent/Vox Agent"));
        assert_eq!(
            path,
            Path::new("/opt/Vox Agent").join(bundled_backend_name())
        );
    }
}
