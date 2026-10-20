//! Owner-only Context directories on Windows (spec §3.5). Windows only:
//! elsewhere this file compiles to an empty test binary.
#![cfg(windows)]

use std::process::Command;
use vox_agent_desktop_tauri_lib::context_acl::{ensure_private_dir, verify_private_dir, AclError};

fn grant_everyone_read(dir: &std::path::Path) {
    let status = Command::new("icacls")
        .arg(dir)
        .args(["/grant", "Everyone:(OI)(CI)R"])
        .status()
        .unwrap();
    assert!(status.success(), "icacls /grant failed");
}

#[test]
fn creates_directory_readable_only_by_owner_and_system() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("context");
    ensure_private_dir(&dir).unwrap();
    assert!(dir.is_dir());
    verify_private_dir(&dir).unwrap();
    // icacls must list neither Users nor Everyone nor Administrators: the
    // DACL is protected (no inheritance) and names only the owner and SYSTEM.
    let out = Command::new("icacls").arg(&dir).output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout).to_lowercase();
    for forbidden in [
        "builtin\\users",
        "everyone",
        "builtin\\administrators",
        "(i)",
    ] {
        assert!(!text.contains(forbidden), "{forbidden}: {text}");
    }
    assert!(text.contains("nt authority\\system"), "{text}");
}

#[test]
fn rejects_a_directory_granted_to_everyone() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("open");
    std::fs::create_dir(&dir).unwrap();
    grant_everyone_read(&dir);
    assert_eq!(
        verify_private_dir(&dir).unwrap_err(),
        AclError::TooPermissive
    );
}

#[test]
fn rejects_a_directory_with_an_inherited_dacl() {
    // A plain `create_dir` inherits the parent's ACEs. Inherited ACEs change
    // with the parent, so the verifier refuses them whatever they name (on a
    // profile directory they also include Administrators).
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("inherited");
    std::fs::create_dir(&dir).unwrap();
    assert_eq!(
        verify_private_dir(&dir).unwrap_err(),
        AclError::TooPermissive
    );
}

#[test]
fn ensure_is_idempotent_and_never_repairs_an_existing_directory() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("ctx");
    ensure_private_dir(&dir).unwrap();
    ensure_private_dir(&dir).unwrap();
    verify_private_dir(&dir).unwrap();
    grant_everyone_read(&dir);
    assert_eq!(
        ensure_private_dir(&dir).unwrap_err(),
        AclError::TooPermissive
    );
    // Still widened afterwards: nothing was silently tightened.
    let out = Command::new("icacls").arg(&dir).output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout).to_lowercase();
    assert!(text.contains("everyone"), "{text}");
}

#[test]
fn a_missing_directory_does_not_verify() {
    let root = tempfile::tempdir().unwrap();
    assert_eq!(
        verify_private_dir(&root.path().join("absent")).unwrap_err(),
        AclError::Io
    );
}
