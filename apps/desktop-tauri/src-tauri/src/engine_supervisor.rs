use serde::Serialize;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use crate::engine_local;
use zeroize::Zeroizing;

const ENGINE_HOST: &str = "127.0.0.1";
const ENGINE_PORT: u16 = 18_900;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EngineLaunchPlan {
    pub executable: PathBuf,
    pub arguments: Vec<OsString>,
}

impl EngineLaunchPlan {
    fn bundled_arguments() -> Vec<OsString> {
        ["--host", ENGINE_HOST, "--port", "18900"]
            .into_iter()
            .map(OsString::from)
            .collect()
    }

    fn cli_arguments() -> Vec<OsString> {
        ["start", "--host", ENGINE_HOST, "--port", "18900"]
            .into_iter()
            .map(OsString::from)
            .collect()
    }
}

fn bundled_engine_name() -> &'static str {
    if cfg!(windows) {
        "voxagent-engine.exe"
    } else {
        "voxagent-engine"
    }
}

/// Which engine sources a build may launch (T29a). A release build starts
/// only the engine bundled beside the desktop executable: no environment
/// override and no `vox-engine` found on PATH, so a planted binary or a
/// developer install can never be picked up silently.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LaunchPolicy {
    pub allow_override: bool,
    pub allow_path_fallback: bool,
}

impl LaunchPolicy {
    pub const fn for_flags(debug_build: bool, dev_engine_feature: bool) -> Self {
        let dev = debug_build || dev_engine_feature;
        Self {
            allow_override: dev,
            allow_path_fallback: dev,
        }
    }

    pub const fn for_build() -> Self {
        Self::for_flags(cfg!(debug_assertions), cfg!(feature = "dev-engine"))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LaunchError {
    /// Release build without the bundled engine: shown, never worked around.
    EngineNotBundled(PathBuf),
}

pub fn resolve_launch_plan(
    override_executable: Option<OsString>,
    desktop_executable: PathBuf,
    bundled_exists: bool,
    policy: LaunchPolicy,
) -> Result<EngineLaunchPlan, LaunchError> {
    if policy.allow_override {
        if let Some(executable) = override_executable.filter(|value| !value.is_empty()) {
            return Ok(EngineLaunchPlan {
                executable: PathBuf::from(executable),
                arguments: EngineLaunchPlan::bundled_arguments(),
            });
        }
    }
    let bundled = desktop_executable
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(bundled_engine_name());
    if bundled_exists {
        return Ok(EngineLaunchPlan {
            executable: bundled,
            arguments: EngineLaunchPlan::bundled_arguments(),
        });
    }
    if !policy.allow_path_fallback {
        return Err(LaunchError::EngineNotBundled(bundled));
    }
    Ok(EngineLaunchPlan {
        executable: PathBuf::from("vox-engine"),
        arguments: EngineLaunchPlan::cli_arguments(),
    })
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineProcessStatus {
    pub state: String,
    pub pid: Option<u32>,
    pub exit_code: Option<i32>,
    pub executable: Option<String>,
    pub log_path: String,
    pub health_url: String,
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ManagedEngineHealth {
    pub process: EngineProcessStatus,
    pub reachable: bool,
    pub service_status: Option<String>,
    pub version: Option<String>,
    pub error: Option<String>,
}

struct SupervisorInner {
    child: Option<Child>,
    status: EngineProcessStatus,
}

pub struct EngineSupervisor {
    inner: Mutex<SupervisorInner>,
}

impl Default for EngineSupervisor {
    fn default() -> Self {
        let log_path = default_data_dir().join("logs").join("engine.log");
        Self {
            inner: Mutex::new(SupervisorInner {
                child: None,
                status: EngineProcessStatus {
                    state: "stopped".to_string(),
                    pid: None,
                    exit_code: None,
                    executable: None,
                    log_path: log_path.to_string_lossy().into_owned(),
                    health_url: engine_health_url(),
                    last_error: None,
                },
            }),
        }
    }
}

impl EngineSupervisor {
    pub fn start(&self) -> Result<EngineProcessStatus, String> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "engine supervisor lock failed".to_string())?;
        refresh_process(&mut inner)?;
        if inner.child.is_some() {
            return Ok(inner.status.clone());
        }

        let desktop_executable = std::env::current_exe()
            .map_err(|error| format!("failed to locate the Vox Agent executable: {error}"))?;
        let bundled = desktop_executable
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(bundled_engine_name());
        let plan = resolve_launch_plan(
            std::env::var_os("VOXAGENT_ENGINE_EXECUTABLE"),
            desktop_executable,
            bundled.is_file(),
            LaunchPolicy::for_build(),
        )
        .map_err(|LaunchError::EngineNotBundled(path)| {
            let message = format!(
                "The Vox Agent engine is missing from this installation ({}). Reinstall Vox Agent.",
                path.display()
            );
            inner.status.state = "error".to_string();
            inner.status.last_error = Some(message.clone());
            message
        })?;
        let log_path = PathBuf::from(&inner.status.log_path);
        if let Some(parent) = log_path.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("failed to create engine log directory: {error}"))?;
        }
        let stdout = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|error| format!("failed to open engine log: {error}"))?;
        let stderr = stdout
            .try_clone()
            .map_err(|error| format!("failed to clone engine log handle: {error}"))?;

        let memory_db = default_data_dir().join("voice_memory.db");
        let mut command = Command::new(&plan.executable);
        command
            .args(&plan.arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        if std::env::var_os("VOXAGENT_VOICE_MEMORY_DB").is_none() {
            command.env("VOXAGENT_VOICE_MEMORY_DB", memory_db);
        }
        // T29: a fresh secret per launch, written to the engine's stdin pipe
        // (never a file, argument or environment value).
        let token = engine_local::generate_token();
        command.env(engine_local::TOKEN_SOURCE_ENV, "stdin");
        // The launched engine is the local personal edition; requests to it
        // carry launch signatures, never the reusable admin bearer.
        command.env("VOXAGENT_EDITION", "personal");
        let mut child = command.spawn().map_err(|error| {
            let message = format!(
                "failed to start Vox Agent engine at {}: {error}",
                plan.executable.display()
            );
            inner.status.state = "error".to_string();
            inner.status.last_error = Some(message.clone());
            message
        })?;
        let delivered = child.stdin.take().map(|mut pipe| {
            use std::io::Write;
            let line = Zeroizing::new(format!("{}\n", token.as_str()));
            pipe.write_all(line.as_bytes()).and_then(|_| pipe.flush())
        });
        if !matches!(delivered, Some(Ok(()))) {
            let _ = child.kill();
            let _ = child.wait();
            let message = "failed to hand the launch secret to the Vox Agent engine".to_string();
            inner.status.state = "error".to_string();
            inner.status.last_error = Some(message.clone());
            return Err(message);
        }
        engine_local::begin_launch(token);
        let pid = child.id();
        inner.status = EngineProcessStatus {
            state: "running".to_string(),
            pid: Some(pid),
            exit_code: None,
            executable: Some(plan.executable.to_string_lossy().into_owned()),
            log_path: log_path.to_string_lossy().into_owned(),
            health_url: engine_health_url(),
            last_error: None,
        };
        inner.child = Some(child);
        Ok(inner.status.clone())
    }

    pub fn status(&self) -> Result<EngineProcessStatus, String> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "engine supervisor lock failed".to_string())?;
        refresh_process(&mut inner)?;
        Ok(inner.status.clone())
    }

    pub fn stop(&self) -> Result<EngineProcessStatus, String> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "engine supervisor lock failed".to_string())?;
        refresh_process(&mut inner)?;
        let Some(mut child) = inner.child.take() else {
            inner.status.state = "stopped".to_string();
            inner.status.pid = None;
            return Ok(inner.status.clone());
        };
        forget_launch();
        child
            .kill()
            .map_err(|error| format!("failed to stop Vox Agent engine: {error}"))?;
        let exit = child
            .wait()
            .map_err(|error| format!("failed to reap Vox Agent engine: {error}"))?;
        inner.status.state = "stopped".to_string();
        inner.status.pid = None;
        inner.status.exit_code = exit.code();
        inner.status.last_error = None;
        Ok(inner.status.clone())
    }

    pub async fn health(&self) -> Result<ManagedEngineHealth, String> {
        let process = self.status()?;
        // T29b: the listener must prove it holds this launch's secret before
        // any signed request is sent to it; a failure revokes trust.
        let token = engine_local::launch_token();
        if let Some(token) = token.as_deref() {
            let attempt = engine_local::begin_handshake();
            let result = engine_local::verify_listener(&engine_base_url(), token).await;
            engine_local::record_handshake(token, attempt, result.is_ok());
            if let Err(error) = result {
                let reason = match error {
                    engine_local::HandshakeError::Unreachable => "engine is not reachable yet",
                    engine_local::HandshakeError::IdentityUnverified => {
                        "another program is answering on the engine port; it was not trusted"
                    }
                };
                return Ok(ManagedEngineHealth {
                    process,
                    reachable: false,
                    service_status: None,
                    version: None,
                    error: Some(reason.to_string()),
                });
            }
        }
        let client = engine_local::loopback_client(Duration::from_secs(2))
            .map_err(|error| format!("failed to create engine health client: {error}"))?;
        let mut request = client.get(&process.health_url);
        if token.is_some() {
            match engine_local::signed_header("GET", "/health") {
                Some(value) => request = request.header(engine_local::AUTH_HEADER, value),
                None => {
                    return Ok(ManagedEngineHealth {
                        process,
                        reachable: false,
                        service_status: None,
                        version: None,
                        error: Some("engine launch changed during the health check".to_string()),
                    })
                }
            }
        }
        match request.send().await {
            Ok(response) if response.status().is_success() => {
                let payload = response
                    .json::<serde_json::Value>()
                    .await
                    .map_err(|error| format!("engine health returned invalid JSON: {error}"))?;
                Ok(ManagedEngineHealth {
                    process,
                    reachable: true,
                    service_status: payload
                        .get("status")
                        .and_then(serde_json::Value::as_str)
                        .map(str::to_string),
                    version: payload
                        .get("version")
                        .and_then(serde_json::Value::as_str)
                        .map(str::to_string),
                    error: None,
                })
            }
            Ok(response) => Ok(ManagedEngineHealth {
                process,
                reachable: false,
                service_status: None,
                version: None,
                error: Some(format!("engine health returned HTTP {}", response.status())),
            }),
            Err(error) => Ok(ManagedEngineHealth {
                process,
                reachable: false,
                service_status: None,
                version: None,
                error: Some(error.to_string()),
            }),
        }
    }
}

impl Drop for EngineSupervisor {
    fn drop(&mut self) {
        if let Ok(mut inner) = self.inner.lock() {
            if let Some(mut child) = inner.child.take() {
                let _ = child.kill();
                let _ = child.wait();
                forget_launch();
            }
        }
    }
}

/// The launched engine is gone: drop its secret.
fn forget_launch() {
    engine_local::end_launch();
}

fn engine_base_url() -> String {
    format!("http://{ENGINE_HOST}:{ENGINE_PORT}")
}

fn refresh_process(inner: &mut SupervisorInner) -> Result<(), String> {
    let Some(child) = inner.child.as_mut() else {
        return Ok(());
    };
    match child
        .try_wait()
        .map_err(|error| format!("failed to inspect Vox Agent engine: {error}"))?
    {
        None => {
            inner.status.state = "running".to_string();
        }
        Some(exit) => {
            inner.child = None;
            forget_launch();
            inner.status.state = "exited".to_string();
            inner.status.pid = None;
            inner.status.exit_code = exit.code();
            if !exit.success() {
                inner.status.last_error = Some(format!(
                    "Vox Agent engine exited with {}. Inspect {}",
                    exit, inner.status.log_path
                ));
            }
        }
    }
    Ok(())
}

fn default_data_dir() -> PathBuf {
    crate::state::vox_agent_data_dir()
}

fn engine_health_url() -> String {
    format!("http://{ENGINE_HOST}:{ENGINE_PORT}/health")
}

#[cfg(test)]
mod tests {
    use super::{resolve_launch_plan, EngineLaunchPlan, LaunchError, LaunchPolicy};

    const DEV: LaunchPolicy = LaunchPolicy::for_flags(true, false);
    const RELEASE: LaunchPolicy = LaunchPolicy::for_flags(false, false);
    use std::ffi::OsString;
    use std::path::PathBuf;

    #[test]
    fn environment_override_changes_only_the_executable_not_fixed_server_arguments() {
        let plan = resolve_launch_plan(
            Some(OsString::from(r"C:\Program Files\Vox Agent\engine.exe")),
            PathBuf::from(r"C:\Program Files\Vox Agent\Vox Agent.exe"),
            false,
            DEV,
        )
        .unwrap();

        assert_eq!(
            plan.executable,
            PathBuf::from(r"C:\Program Files\Vox Agent\engine.exe")
        );
        assert_eq!(plan.arguments, EngineLaunchPlan::bundled_arguments());
    }

    #[test]
    fn selects_bundled_engine_when_it_exists_beside_the_desktop_binary() {
        let plan = resolve_launch_plan(
            None,
            PathBuf::from("/opt/Vox Agent/Vox Agent"),
            true,
            RELEASE,
        )
        .unwrap();

        assert_eq!(
            plan.executable,
            PathBuf::from("/opt/Vox Agent").join(super::bundled_engine_name())
        );
        assert_eq!(plan.arguments, EngineLaunchPlan::bundled_arguments());
    }

    #[test]
    fn falls_back_to_installed_vox_engine_command_for_development() {
        let plan = resolve_launch_plan(None, PathBuf::from("/tmp/vox-agent"), false, DEV).unwrap();

        assert_eq!(plan.executable, PathBuf::from("vox-engine"));
        assert_eq!(plan.arguments, EngineLaunchPlan::cli_arguments());
    }

    #[test]
    fn a_release_build_never_uses_an_override_or_path_and_reports_a_missing_engine() {
        let desktop = PathBuf::from("/opt/Vox Agent/Vox Agent");
        let bundled = PathBuf::from("/opt/Vox Agent").join(super::bundled_engine_name());
        let planted = Some(OsString::from("/tmp/planted-engine"));
        assert_eq!(
            resolve_launch_plan(planted.clone(), desktop.clone(), false, RELEASE),
            Err(LaunchError::EngineNotBundled(bundled.clone()))
        );
        let plan = resolve_launch_plan(planted, desktop, true, RELEASE).unwrap();
        assert_eq!(
            plan.executable, bundled,
            "the override is ignored even when the bundled engine exists"
        );
    }

    #[test]
    fn only_debug_or_the_dev_engine_feature_relax_the_policy() {
        assert_eq!(LaunchPolicy::for_flags(false, false), RELEASE);
        assert_eq!(LaunchPolicy::for_flags(true, false), DEV);
        assert_eq!(LaunchPolicy::for_flags(false, true), DEV);
        let source = include_str!("../Cargo.toml");
        let default = source
            .lines()
            .find(|l| l.trim_start().starts_with("default"))
            .unwrap_or("");
        assert!(
            !default.contains("dev-engine"),
            "dev-engine is never a default feature"
        );
    }
}
