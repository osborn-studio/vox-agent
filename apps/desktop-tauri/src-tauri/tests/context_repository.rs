//! T16: read-only local repository inspection without Git or any process.
use std::path::Path;

use vox_agent_desktop_tauri_lib::context_repository::{inspect, repository_slug, RepositoryError};

fn write(path: &Path, text: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

fn snapshot(dir: &Path) -> Vec<(String, Vec<u8>)> {
    let mut out = Vec::new();
    for entry in walk(dir) {
        if entry.is_file() {
            out.push((entry.display().to_string(), std::fs::read(&entry).unwrap()));
        }
    }
    out.sort();
    out
}

fn walk(dir: &Path) -> Vec<std::path::PathBuf> {
    let mut out = vec![dir.to_path_buf()];
    if dir.is_dir() {
        for entry in std::fs::read_dir(dir).unwrap().flatten() {
            out.extend(walk(&entry.path()));
        }
    }
    out
}

const CONFIG: &str = r#"[core]
	repositoryformatversion = 0
[remote "origin"]
	url = git@github.com:studio/repo.git
	fetch = +refs/heads/*:refs/remotes/origin/*
[remote "upstream"]
	url = https://user:secret@example.com/team/Other-Repo.git
[remote "local"]
	url = /srv/git/mirror
[include]
	path = ~/.gitconfig-evil
"#;

#[test]
fn a_normal_repository_lists_remote_slugs_and_branch_without_changing_anything() {
    let root = tempfile::tempdir().unwrap();
    write(&root.path().join(".git/config"), CONFIG);
    write(
        &root.path().join(".git/HEAD"),
        "ref: refs/heads/feature/x\n",
    );
    let before = snapshot(root.path());
    let inspection = inspect(root.path()).unwrap();
    assert_eq!(
        inspection.repositories,
        vec!["studio/repo".to_string(), "team/Other-Repo".to_string()]
    );
    assert_eq!(inspection.branch.as_deref(), Some("feature/x"));
    assert_eq!(snapshot(root.path()), before, "inspection is read-only");
}

#[test]
fn a_linked_worktree_reads_remotes_from_the_common_directory() {
    let root = tempfile::tempdir().unwrap();
    let main = root.path().join("main repo 中文");
    write(&main.join(".git/config"), CONFIG);
    write(
        &main.join(".git/worktrees/wt/HEAD"),
        "ref: refs/heads/wt-branch\n",
    );
    write(&main.join(".git/worktrees/wt/commondir"), "../..\n");
    let worktree = root.path().join("my worktree");
    let gitdir = main.join(".git/worktrees/wt");
    write(
        &worktree.join(".git"),
        &format!("gitdir: {}\n", gitdir.display()),
    );
    let inspection = inspect(&worktree).unwrap();
    assert_eq!(inspection.repositories[0], "studio/repo");
    assert_eq!(inspection.branch.as_deref(), Some("wt-branch"));
}

#[test]
fn a_relative_gitdir_and_a_detached_head_work() {
    let root = tempfile::tempdir().unwrap();
    write(&root.path().join("real/.git/config"), CONFIG);
    write(
        &root.path().join("real/.git/HEAD"),
        "0123456789abcdef0123456789abcdef01234567\n",
    );
    write(&root.path().join("link/.git"), "gitdir: ../real/.git\n");
    let inspection = inspect(&root.path().join("link")).unwrap();
    assert_eq!(inspection.repositories.len(), 2);
    assert_eq!(inspection.branch, None, "detached HEAD has no branch");
}

#[test]
fn a_directory_without_git_or_with_junk_is_reported_not_guessed() {
    let root = tempfile::tempdir().unwrap();
    assert_eq!(
        inspect(root.path()).err(),
        Some(RepositoryError::NotARepository)
    );
    write(&root.path().join(".git"), "not a gitdir line\n");
    assert_eq!(
        inspect(root.path()).err(),
        Some(RepositoryError::NotARepository)
    );
    assert_eq!(
        inspect(&root.path().join("missing")).err(),
        Some(RepositoryError::Unavailable)
    );
    let file = root.path().join("file.txt");
    write(&file, "x");
    assert_eq!(inspect(&file).err(), Some(RepositoryError::Unavailable));
}

#[test]
fn oversized_config_is_refused() {
    let root = tempfile::tempdir().unwrap();
    write(&root.path().join(".git/config"), &"#".repeat(70_000));
    write(&root.path().join(".git/HEAD"), "ref: refs/heads/main\n");
    assert_eq!(inspect(root.path()).err(), Some(RepositoryError::TooLarge));
}

#[test]
fn a_repository_without_remotes_has_no_suggestions() {
    let root = tempfile::tempdir().unwrap();
    write(&root.path().join(".git/config"), "[core]\n\tbare = false\n");
    write(&root.path().join(".git/HEAD"), "ref: refs/heads/main\n");
    let inspection = inspect(root.path()).unwrap();
    assert!(inspection.repositories.is_empty());
}

#[test]
fn remote_urls_become_slugs_only_for_standard_forms() {
    for (url, slug) in [
        ("git@github.com:studio/repo.git", Some("studio/repo")),
        ("https://github.com/studio/repo", Some("studio/repo")),
        (
            "ssh://git@host.example/studio/repo.git",
            Some("studio/repo"),
        ),
        ("https://user:pw@host/studio/repo.git", Some("studio/repo")),
        ("http://host/studio/repo.git", None),
        ("file:///srv/repo", None),
        ("/srv/git/repo", None),
        ("../relative/repo", None),
        ("https://host/a/b/c", None),
        ("https://host/studio/repo?x=1", None),
        ("https://host/studio/re%2fpo", None),
        ("git@host:-oProxyCommand/x", None),
        ("", None),
    ] {
        assert_eq!(repository_slug(url).as_deref(), slug, "{url}");
    }
}

#[test]
fn the_resolver_starts_no_process() {
    let source = include_str!("../src/context_repository.rs");
    assert!(!source.contains("std::process") && !source.contains("Command::new"));
}

#[test]
fn malformed_config_is_never_guessed() {
    let root = tempfile::tempdir().unwrap();
    write(&root.path().join(".git/HEAD"), "ref: refs/heads/main\n");
    for config in [
        "[remote \"origin\"\n\turl = git@github.com:studio/repo.git\n",
        "[remote \"origin\"]\n\turl = \"git@github.com:studio/repo.git\n",
        "[remote \"origin\"]\n\turl = git@github.com:studio/\\\nrepo.git\n",
    ] {
        write(&root.path().join(".git/config"), config);
        assert!(
            inspect(root.path()).unwrap().repositories.is_empty(),
            "{config:?}"
        );
    }
    assert_eq!(repository_slug("https://host//studio/repo.git"), None);
}

#[test]
fn context_code_never_logs() {
    for (name, source) in [
        (
            "context_commands",
            include_str!("../src/context_commands.rs"),
        ),
        ("context_service", include_str!("../src/context_service.rs")),
        ("context_journal", include_str!("../src/context_journal.rs")),
        (
            "context_transport",
            include_str!("../src/context_transport.rs"),
        ),
        ("context_wire", include_str!("../src/context_wire.rs")),
        ("context_session", include_str!("../src/context_session.rs")),
        ("context_login", include_str!("../src/context_login.rs")),
        ("context_account", include_str!("../src/context_account.rs")),
        (
            "context_delivery",
            include_str!("../src/context_delivery.rs"),
        ),
        ("context_policy", include_str!("../src/context_policy.rs")),
        (
            "context_repository",
            include_str!("../src/context_repository.rs"),
        ),
        ("context_keys", include_str!("../src/context_keys/mod.rs")),
    ] {
        for macro_name in [
            "println!",
            "eprintln!",
            "print!",
            "eprint!",
            "dbg!",
            "log::",
            "tracing::",
        ] {
            // Match only at an identifier boundary ("dialog::" is not "log::").
            let bytes = source.as_bytes();
            let found = source.match_indices(macro_name).any(|(at, _)| {
                at == 0 || !(bytes[at - 1].is_ascii_alphanumeric() || bytes[at - 1] == b'_')
            });
            assert!(!found, "{name} must not log ({macro_name})");
        }
    }
}
