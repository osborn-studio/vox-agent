//! Owner-only Context directories on Windows (spec §3.5). Windows only:
//! elsewhere this file compiles to an empty test binary.
#![cfg(windows)]

use std::process::Command;
use vox_agent_desktop_tauri_lib::context_acl::{
    ensure_private_dir, verify_private_child, verify_private_dir, AclError,
};

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
    // Owner-only first, so the refusal is for the grant alone.
    ensure_private_dir(&dir).unwrap();
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

#[test]
fn a_deny_ace_only_narrows_access_and_is_accepted() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("ctx");
    ensure_private_dir(&dir).unwrap();
    let status = Command::new("icacls")
        .arg(&dir)
        .args(["/deny", "Everyone:(OI)(CI)W"])
        .status()
        .unwrap();
    assert!(status.success(), "icacls /deny failed");
    verify_private_dir(&dir).unwrap();
}

#[test]
fn a_junction_is_never_a_private_directory() {
    let root = tempfile::tempdir().unwrap();
    let target = root.path().join("target");
    ensure_private_dir(&target).unwrap();
    let link = root.path().join("link");
    let status = Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(&link)
        .arg(&target)
        .status()
        .unwrap();
    assert!(status.success(), "mklink /J");
    assert_eq!(
        verify_private_dir(&link).unwrap_err(),
        AclError::TooPermissive
    );
    assert_eq!(
        ensure_private_dir(&link).unwrap_err(),
        AclError::TooPermissive
    );
    assert!(target.is_dir(), "the target is untouched");
}

#[test]
fn ensure_creates_missing_parents_owner_only_too() {
    let root = tempfile::tempdir().unwrap();
    let leaf = root.path().join("a").join("b");
    ensure_private_dir(&leaf).unwrap();
    verify_private_dir(&leaf).unwrap();
    // Only the leaf carries the protected DACL; the parent is an ordinary
    // directory the caller must secure itself if it matters.
    assert!(root.path().join("a").is_dir());
}

#[test]
fn a_child_created_inside_a_private_directory_passes_the_child_check_only() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("ctx");
    ensure_private_dir(&dir).unwrap();
    let child = dir.join("db");
    std::fs::create_dir(&child).unwrap();
    // Inherited owner-only ACEs: fine as a child, refused as a top level.
    verify_private_child(&child).unwrap();
    assert_eq!(
        verify_private_dir(&child).unwrap_err(),
        AclError::TooPermissive
    );
    // An explicit grant on the child is refused either way.
    grant_everyone_read(&child);
    assert_eq!(
        verify_private_child(&child).unwrap_err(),
        AclError::TooPermissive
    );
    // A child of an ordinary (inherited) directory is refused: its inherited
    // ACEs name Administrators and whoever else the profile grants.
    let plain = root.path().join("plain");
    std::fs::create_dir(&plain).unwrap();
    let plain_child = plain.join("db");
    std::fs::create_dir(&plain_child).unwrap();
    assert_eq!(
        verify_private_child(&plain_child).unwrap_err(),
        AclError::TooPermissive
    );
}

#[test]
fn re_enabling_inheritance_on_a_private_directory_is_refused() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("ctx");
    ensure_private_dir(&dir).unwrap();
    let status = Command::new("icacls")
        .arg(&dir)
        .args(["/inheritance:e"])
        .status()
        .unwrap();
    assert!(status.success(), "icacls /inheritance:e failed");
    assert_eq!(
        verify_private_dir(&dir).unwrap_err(),
        AclError::TooPermissive
    );
    assert_eq!(
        ensure_private_dir(&dir).unwrap_err(),
        AclError::TooPermissive
    );
}
