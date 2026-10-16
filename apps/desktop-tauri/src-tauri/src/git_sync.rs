use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::migration_bundle::{portable_git_url, write_bundle_at_path, MigrationBundle};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GitSyncResponse {
    pub device_id: String,
    pub repository: String,
    pub published: bool,
    pub imported_snapshots: usize,
    pub history_imported: usize,
    pub history_skipped: usize,
    pub corrections_imported: usize,
    pub preserved_terms_imported: usize,
}

#[derive(Debug)]
pub struct GitTransportOutcome {
    pub device_id: String,
    pub repository: PathBuf,
    pub published: bool,
    pub remote_snapshots: Vec<PathBuf>,
}

pub fn sync_transport(
    bundle: &MigrationBundle,
    repo_url: &str,
    local_path: &str,
    data_dir: &Path,
) -> Result<GitTransportOutcome, String> {
    let repo_url = validate_repo_url(repo_url)?;
    let repository = validate_local_path(local_path)?;
    ensure_repository(&repo_url, &repository)?;
    ensure_clean(&repository)?;
    configure_identity(&repository)?;
    pull_remote(&repository)?;

    let device_id = resolve_device_id(data_dir)?;
    let relative_snapshot = PathBuf::from("devices")
        .join(&device_id)
        .join("snapshot.voxagent.json");
    let snapshot = repository.join(&relative_snapshot);
    if snapshot_changed(&snapshot, bundle)? {
        write_bundle_at_path(bundle, &snapshot)?;
    }
    git(
        &repository,
        [
            OsStr::new("add"),
            OsStr::new("--"),
            relative_snapshot.as_os_str(),
        ],
    )?;
    let staged = git_output_status(
        &repository,
        [
            OsStr::new("diff"),
            OsStr::new("--cached"),
            OsStr::new("--quiet"),
        ],
    )?;
    let published = match staged.status.code() {
        Some(0) => false,
        Some(1) => {
            git(
                &repository,
                [
                    OsStr::new("commit"),
                    OsStr::new("-m"),
                    OsStr::new("sync: update Vox Agent snapshot"),
                ],
            )?;
            true
        }
        _ => return Err(git_error(&staged)),
    };
    git(
        &repository,
        [OsStr::new("push"), OsStr::new("origin"), OsStr::new("HEAD")],
    )?;

    let remote_snapshots = list_snapshots(&repository)?
        .into_iter()
        .filter(|path| *path != snapshot)
        .collect();
    Ok(GitTransportOutcome {
        device_id,
        repository,
        published,
        remote_snapshots,
    })
}

fn validate_repo_url(value: &str) -> Result<String, String> {
    let value = value.trim();
    #[cfg(test)]
    if value.starts_with("file://") {
        return Ok(value.to_string());
    }
    portable_git_url(value)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            "Git sync requires a credential-free HTTPS or SSH repository URL".to_string()
        })
}

fn validate_local_path(value: &str) -> Result<PathBuf, String> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > 4_000 || value.chars().any(char::is_control) {
        return Err("Git sync local path is invalid".to_string());
    }
    let path = PathBuf::from(value);
    if !path.is_absolute() {
        return Err("Git sync local path must be absolute".to_string());
    }
    Ok(path)
}

fn ensure_repository(repo_url: &str, repository: &Path) -> Result<(), String> {
    if repository.exists() {
        if !repository.join(".git").is_dir() {
            return Err("Git sync local path exists but is not a Git repository".to_string());
        }
    } else {
        let parent = repository
            .parent()
            .ok_or_else(|| "Git sync local path has no parent".to_string())?;
        fs::create_dir_all(parent)
            .map_err(|error| format!("failed to create Git sync directory: {error}"))?;
        git_without_repo([
            OsStr::new("clone"),
            OsStr::new("--"),
            OsStr::new(repo_url),
            repository.as_os_str(),
        ])?;
    }
    let remote = git(
        repository,
        [
            OsStr::new("remote"),
            OsStr::new("get-url"),
            OsStr::new("origin"),
        ],
    )?;
    let remote = String::from_utf8_lossy(&remote.stdout).trim().to_string();
    if remote != repo_url {
        return Err("Git sync repository origin does not match the configured URL".to_string());
    }
    Ok(())
}

fn ensure_clean(repository: &Path) -> Result<(), String> {
    let output = git(
        repository,
        [
            OsStr::new("status"),
            OsStr::new("--porcelain"),
            OsStr::new("--untracked-files=all"),
        ],
    )?;
    if !String::from_utf8_lossy(&output.stdout).trim().is_empty() {
        return Err(
            "Git sync repository has local changes; resolve them before syncing".to_string(),
        );
    }
    Ok(())
}

fn configure_identity(repository: &Path) -> Result<(), String> {
    let email = git_status(
        repository,
        [
            OsStr::new("config"),
            OsStr::new("--get"),
            OsStr::new("user.email"),
        ],
    )?;
    if !email.success() {
        git(
            repository,
            [
                OsStr::new("config"),
                OsStr::new("user.email"),
                OsStr::new("sync@vox-agent.local"),
            ],
        )?;
    }
    let name = git_status(
        repository,
        [
            OsStr::new("config"),
            OsStr::new("--get"),
            OsStr::new("user.name"),
        ],
    )?;
    if !name.success() {
        git(
            repository,
            [
                OsStr::new("config"),
                OsStr::new("user.name"),
                OsStr::new("Vox Agent Sync"),
            ],
        )?;
    }
    Ok(())
}

fn pull_remote(repository: &Path) -> Result<(), String> {
    let branch = git(
        repository,
        [
            OsStr::new("symbolic-ref"),
            OsStr::new("--short"),
            OsStr::new("HEAD"),
        ],
    )?;
    let branch = String::from_utf8_lossy(&branch.stdout).trim().to_string();
    if branch.is_empty() {
        return Err("Git sync repository is in detached HEAD state".to_string());
    }
    let remote_ref = format!("refs/heads/{branch}");
    let remote = git_output_status(
        repository,
        [
            OsStr::new("ls-remote"),
            OsStr::new("--exit-code"),
            OsStr::new("origin"),
            OsStr::new(&remote_ref),
        ],
    )?;
    if remote.status.success() {
        git(
            repository,
            [
                OsStr::new("pull"),
                OsStr::new("--rebase"),
                OsStr::new("origin"),
                OsStr::new(&branch),
            ],
        )?;
    } else if remote.status.code() != Some(2) {
        return Err(git_error(&remote));
    }
    Ok(())
}

fn resolve_device_id(data_dir: &Path) -> Result<String, String> {
    fs::create_dir_all(data_dir)
        .map_err(|error| format!("failed to create Vox Agent data directory: {error}"))?;
    let path = data_dir.join("sync-device-id");
    if path.exists() {
        let value = fs::read_to_string(&path)
            .map_err(|error| format!("failed to read sync device ID: {error}"))?;
        let value = value.trim();
        if valid_device_id(value) {
            return Ok(value.to_string());
        }
        return Err("stored sync device ID is invalid".to_string());
    }
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("system clock is before Unix epoch: {error}"))?
        .as_nanos();
    let value = format!("device-{timestamp:x}-{:x}", std::process::id());
    let mut temporary = tempfile::NamedTempFile::new_in(data_dir)
        .map_err(|error| format!("failed to create sync device ID: {error}"))?;
    use std::io::Write;
    writeln!(temporary, "{value}")
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|error| format!("failed to write sync device ID: {error}"))?;
    temporary
        .persist(&path)
        .map_err(|error| format!("failed to persist sync device ID: {}", error.error))?;
    Ok(value)
}

fn valid_device_id(value: &str) -> bool {
    value.starts_with("device-")
        && value.len() <= 80
        && value.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-'
        })
}

fn snapshot_changed(path: &Path, bundle: &MigrationBundle) -> Result<bool, String> {
    if !path.exists() {
        return Ok(true);
    }
    let existing = crate::migration_bundle::read_bundle(path)?;
    let mut existing_value = serde_json::to_value(existing)
        .map_err(|error| format!("failed to compare Git sync snapshot: {error}"))?;
    let mut next_value = serde_json::to_value(bundle)
        .map_err(|error| format!("failed to compare Git sync snapshot: {error}"))?;
    existing_value["exported_at_unix_ms"] = serde_json::json!(0);
    next_value["exported_at_unix_ms"] = serde_json::json!(0);
    Ok(existing_value != next_value)
}

fn list_snapshots(repository: &Path) -> Result<Vec<PathBuf>, String> {
    let devices = repository.join("devices");
    if !devices.exists() {
        return Ok(Vec::new());
    }
    let mut snapshots = Vec::new();
    for entry in fs::read_dir(&devices)
        .map_err(|error| format!("failed to inspect Git sync devices: {error}"))?
    {
        let entry = entry.map_err(|error| format!("failed to inspect Git sync device: {error}"))?;
        if !entry
            .file_type()
            .map_err(|error| format!("failed to inspect Git sync device type: {error}"))?
            .is_dir()
        {
            continue;
        }
        let path = entry.path().join("snapshot.voxagent.json");
        if path.is_file() {
            snapshots.push(path);
        }
    }
    snapshots.sort();
    Ok(snapshots)
}

fn git<I, S>(repository: &Path, args: I) -> Result<Output, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut full_args = vec![OsStr::new("-C"), repository.as_os_str()];
    let collected: Vec<S> = args.into_iter().collect();
    full_args.extend(collected.iter().map(AsRef::as_ref));
    let output = git_without_repo_status(full_args)?;
    if output.status.success() {
        Ok(output)
    } else {
        Err(git_error(&output))
    }
}

fn git_status<I, S>(repository: &Path, args: I) -> Result<std::process::ExitStatus, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Ok(git_output_status(repository, args)?.status)
}

fn git_output_status<I, S>(repository: &Path, args: I) -> Result<Output, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut full_args = vec![OsStr::new("-C"), repository.as_os_str()];
    let collected: Vec<S> = args.into_iter().collect();
    full_args.extend(collected.iter().map(AsRef::as_ref));
    git_without_repo_status(full_args)
}

fn git_without_repo<I, S>(args: I) -> Result<Output, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let output = git_without_repo_status(args)?;
    if output.status.success() {
        Ok(output)
    } else {
        Err(git_error(&output))
    }
}

fn git_without_repo_status<I, S>(args: I) -> Result<Output, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new("git")
        .args(args)
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GCM_INTERACTIVE", "never")
        .env("GIT_HTTP_LOW_SPEED_LIMIT", "1")
        .env("GIT_HTTP_LOW_SPEED_TIME", "30")
        .output()
        .map_err(|error| format!("failed to run Git: {error}"))
}

fn git_error(output: &Output) -> String {
    let stderr: String = String::from_utf8_lossy(&output.stderr)
        .chars()
        .take(2_000)
        .collect();
    let stdout: String = String::from_utf8_lossy(&output.stdout)
        .chars()
        .take(2_000)
        .collect();
    let detail = if stderr.trim().is_empty() {
        stdout
    } else {
        stderr
    };
    format!("Git command failed: {}", detail.trim())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::DictionaryPayload;
    use crate::state::StoredSettings;

    fn init_bare_remote(root: &Path) -> PathBuf {
        let remote = root.join("remote.git");
        git_without_repo([
            OsStr::new("init"),
            OsStr::new("--bare"),
            OsStr::new("--initial-branch=main"),
            remote.as_os_str(),
        ])
        .unwrap();
        remote
    }

    #[test]
    fn git_transport_clones_commits_and_reuses_stable_device_snapshot() {
        let root = tempfile::tempdir().unwrap();
        let remote = init_bare_remote(root.path());
        let local = root.path().join("sync");
        let data = root.path().join("data");
        let bundle = MigrationBundle::new(
            &StoredSettings::default(),
            Vec::new(),
            DictionaryPayload::default(),
        )
        .unwrap();
        let url = format!("file://{}", remote.display());

        let first = sync_transport(&bundle, &url, local.to_str().unwrap(), &data).unwrap();
        let second = sync_transport(&bundle, &url, local.to_str().unwrap(), &data).unwrap();

        assert!(first.published);
        assert!(!second.published);
        assert_eq!(first.device_id, second.device_id);
        assert!(local
            .join("devices")
            .join(&first.device_id)
            .join("snapshot.voxagent.json")
            .is_file());
    }

    #[test]
    fn git_transport_refuses_dirty_dedicated_repository() {
        let root = tempfile::tempdir().unwrap();
        let remote = init_bare_remote(root.path());
        let local = root.path().join("sync");
        let data = root.path().join("data");
        let bundle = MigrationBundle::new(
            &StoredSettings::default(),
            Vec::new(),
            DictionaryPayload::default(),
        )
        .unwrap();
        let url = format!("file://{}", remote.display());
        sync_transport(&bundle, &url, local.to_str().unwrap(), &data).unwrap();
        fs::write(local.join("untracked.txt"), "do not overwrite").unwrap();

        let error = sync_transport(&bundle, &url, local.to_str().unwrap(), &data).unwrap_err();

        assert!(error.contains("local changes"));
        assert_eq!(
            fs::read_to_string(local.join("untracked.txt")).unwrap(),
            "do not overwrite"
        );
    }
}
