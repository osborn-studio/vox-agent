//! Read-only local repository inspection (T16).
//!
//! Matches macOS `ContextRepositoryResolver`: remote URLs become `owner/repo`
//! suggestions only; the user still chooses a catalog project explicitly and
//! nothing here changes attribution. Unlike macOS it does not run Git (which
//! may be absent on Windows): it reads `.git` files directly and starts no
//! process, so it cannot write anything. Supported layouts: a `.git`
//! directory, a `.git` file with `gitdir:` (linked worktrees, submodules)
//! including `commondir`, and any path form the OS accepts (UNC, spaces,
//! non-ASCII). Config `include` directives are ignored, like `--no-includes`.
use std::path::{Path, PathBuf};

const MAX_FILE_BYTES: u64 = 65_536;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Inspection {
    /// Sorted, de-duplicated `owner/repo` slugs from standard remote URLs.
    pub repositories: Vec<String>,
    pub branch: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RepositoryError {
    /// The directory is readable but is not a Git working tree.
    NotARepository,
    /// The path is missing, not a directory, or unreadable.
    Unavailable,
    TooLarge,
}

fn read_small(path: &Path) -> Result<Option<String>, RepositoryError> {
    let meta = match std::fs::metadata(path) {
        Ok(meta) => meta,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(RepositoryError::Unavailable),
    };
    if !meta.is_file() {
        return Ok(None);
    }
    if meta.len() > MAX_FILE_BYTES {
        return Err(RepositoryError::TooLarge);
    }
    // Bounded read: a file that grows or is replaced after the size check
    // still never allocates more than the limit plus one byte.
    use std::io::Read;
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(MAX_FILE_BYTES + 1).read_to_end(&mut bytes))
        .map_err(|_| RepositoryError::Unavailable)?;
    if bytes.len() as u64 > MAX_FILE_BYTES {
        return Err(RepositoryError::TooLarge);
    }
    String::from_utf8(bytes)
        .map(Some)
        .map_err(|_| RepositoryError::Unavailable)
}

/// Resolve `value` against `base` when it is relative.
fn resolve(base: &Path, value: &str) -> PathBuf {
    let path = Path::new(value);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        base.join(path)
    }
}

/// The Git directory for a working tree, following a `gitdir:` file.
fn git_dir(dir: &Path) -> Result<PathBuf, RepositoryError> {
    let dot_git = dir.join(".git");
    if dot_git.is_dir() {
        return Ok(dot_git);
    }
    let Some(text) = read_small(&dot_git)? else {
        return Err(RepositoryError::NotARepository);
    };
    let line = text.lines().next().unwrap_or("").trim();
    let target = line
        .strip_prefix("gitdir:")
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .ok_or(RepositoryError::NotARepository)?;
    let path = resolve(dir, target);
    if path.is_dir() {
        Ok(path)
    } else {
        Err(RepositoryError::NotARepository)
    }
}

fn branch(head: &str) -> Option<String> {
    let name = head.trim().strip_prefix("ref: refs/heads/")?;
    (!name.is_empty() && name.len() <= 256 && !name.chars().any(char::is_control))
        .then(|| name.to_string())
}

/// Remote URLs from a Git config file: `url` keys in `[remote "…"]` sections.
fn remote_urls(config: &str) -> Vec<String> {
    let mut urls = Vec::new();
    let mut in_remote = false;
    for raw in config.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
            continue;
        }
        if let Some(section) = line.strip_prefix('[') {
            // A malformed header ends any remote section rather than guessing.
            in_remote = match section.split_once(']') {
                Some((name, _)) => {
                    let lowered = name.trim().to_ascii_lowercase();
                    (lowered.starts_with("remote \"")
                        && lowered.ends_with('"')
                        && lowered.len() > 8)
                        || lowered.starts_with("remote.")
                }
                None => false,
            };
            continue;
        }
        if !in_remote {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if !key.trim().eq_ignore_ascii_case("url") {
            continue;
        }
        let value = value.trim();
        // Escapes and line continuations are not interpreted: such values are
        // skipped instead of guessed.
        if value.contains('\\') {
            continue;
        }
        let value = match value.strip_prefix('"') {
            Some(quoted) => match quoted.split_once('"') {
                Some((inner, _)) => inner,
                None => continue, // unterminated quote
            },
            None => value.split([';', '#']).next().unwrap_or("").trim(),
        };
        urls.push(value.to_string());
    }
    urls
}

fn valid_slug(slug: &str) -> bool {
    let parts: Vec<&str> = slug.split('/').collect();
    slug.len() <= 200
        && parts.len() == 2
        && parts.iter().all(|part| {
            part.as_bytes()
                .first()
                .is_some_and(u8::is_ascii_alphanumeric)
                && part
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
        })
}

/// `owner/repo` for standard HTTPS/SSH remotes; credentials, hosts and local
/// paths are discarded. `None` for anything else.
pub fn repository_slug(remote: &str) -> Option<String> {
    if remote.is_empty()
        || remote != remote.trim()
        || remote.len() > 4096
        || remote.chars().any(char::is_control)
    {
        return None;
    }
    let path = if remote.contains("://") {
        let url = reqwest::Url::parse(remote).ok()?;
        if !matches!(url.scheme(), "https" | "ssh")
            || url.host_str().is_none_or(str::is_empty)
            || url.query().is_some()
            || url.fragment().is_some()
            || url.path().contains('%')
        {
            return None;
        }
        // Exactly one leading slash, like macOS; "//owner/repo" is refused.
        url.path()
            .strip_prefix('/')
            .unwrap_or(url.path())
            .to_string()
    } else {
        let (host, path) = remote.split_once(':')?;
        if remote.starts_with('/')
            || remote.starts_with('.')
            || host.contains('/')
            || !host.contains('@')
            || host.ends_with('@')
            || host.matches('@').count() != 1
        {
            return None;
        }
        path.to_string()
    };
    let slug = path.strip_suffix(".git").unwrap_or(&path);
    valid_slug(slug).then(|| slug.to_string())
}

/// Inspect `dir` read-only.
pub fn inspect(dir: &Path) -> Result<Inspection, RepositoryError> {
    if !std::fs::metadata(dir).is_ok_and(|m| m.is_dir()) {
        return Err(RepositoryError::Unavailable);
    }
    let git = git_dir(dir)?;
    let head = read_small(&git.join("HEAD"))?.ok_or(RepositoryError::NotARepository)?;
    let common = match read_small(&git.join("commondir"))? {
        Some(text) => resolve(&git, text.trim()),
        None => git.clone(),
    };
    let config = read_small(&common.join("config"))?.unwrap_or_default();
    let mut repositories: Vec<String> = remote_urls(&config)
        .iter()
        .filter_map(|url| repository_slug(url))
        .collect();
    repositories.sort();
    repositories.dedup();
    Ok(Inspection {
        repositories,
        branch: branch(&head),
    })
}
