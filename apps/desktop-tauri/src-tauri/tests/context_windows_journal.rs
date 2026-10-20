//! On Windows a signed-in account owns a DPAPI-backed journal under
//! `<data_dir>/context`; elsewhere it stays `None` (`NotSupported`).
use vox_agent_desktop_tauri_lib::context_commands::{open_journal, JournalOpenReport};

#[test]
fn open_journal_reports_platform_support() {
    let root = tempfile::tempdir().unwrap();
    let report = open_journal(root.path());
    if cfg!(windows) {
        assert!(matches!(report, JournalOpenReport::Opened(_)), "{report:?}");
        assert_eq!(report.key_store_label(), "dpapi-user");
        assert!(root.path().join("context").join("journal").is_dir());
        assert!(root.path().join("context").join("keys").is_dir());
        assert!(root.path().join("context").join("anchors").is_dir());
    } else {
        assert!(matches!(report, JournalOpenReport::NotSupported));
        assert_eq!(report.key_store_label(), "not-available-in-this-build");
        assert!(!root.path().join("context").exists());
    }
}

#[test]
fn report_debug_and_labels_never_carry_paths() {
    let root = tempfile::tempdir().unwrap();
    let report = open_journal(root.path());
    let text = format!("{report:?}");
    assert_eq!(text, report.key_store_label());
    assert!(!text.contains(&*root.path().to_string_lossy()));
    assert_eq!(JournalOpenReport::UnsafeAcl.key_store_label(), "unsafe-acl");
    assert_eq!(JournalOpenReport::Busy.key_store_label(), "unavailable");
    assert_eq!(JournalOpenReport::Failed.key_store_label(), "unavailable");
}

#[cfg(windows)]
#[test]
fn open_journal_refuses_a_widened_directory() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("context");
    std::fs::create_dir_all(&dir).unwrap();
    let status = std::process::Command::new("icacls")
        .arg(&dir)
        .args(["/grant", "Everyone:(OI)(CI)R"])
        .status()
        .unwrap();
    assert!(status.success());
    assert!(matches!(
        open_journal(root.path()),
        JournalOpenReport::UnsafeAcl
    ));
    // Nothing was created inside the refused directory.
    assert!(!dir.join("journal").exists());
    assert!(!dir.join("keys").exists());
}

#[cfg(windows)]
#[test]
fn open_journal_reopens_the_same_key_set_and_reports_busy_while_open() {
    let root = tempfile::tempdir().unwrap();
    let first = open_journal(root.path());
    assert!(matches!(first, JournalOpenReport::Opened(_)), "{first:?}");
    // The first journal still holds the lock.
    assert!(matches!(open_journal(root.path()), JournalOpenReport::Busy));
    drop(first);
    let again = open_journal(root.path());
    assert!(matches!(again, JournalOpenReport::Opened(_)), "{again:?}");
    let keys: Vec<_> = std::fs::read_dir(root.path().join("context").join("keys"))
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().extension().is_some_and(|x| x == "dpapi"))
        .collect();
    assert_eq!(keys.len(), 1, "one key set is reused across opens");
}
