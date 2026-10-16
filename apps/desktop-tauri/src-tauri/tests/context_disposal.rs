//! T09d: the user explicitly gives up the local copy. Synthetic data only.
use std::path::{Path, PathBuf};

use vox_agent_desktop_tauri_lib::context_delivery::{BackupPayload, DeliveryState};
use vox_agent_desktop_tauri_lib::context_journal::{
    Disposal, ExportChoice, FileAnchorStore, Journal, JournalError, Opened, RecoveryAction,
};
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::KeyRing;

const SECRET: &str = "synthetic-disposal-marker";

struct Env {
    _root: tempfile::TempDir,
    journal: PathBuf,
    keys: PathBuf,
    anchors: PathBuf,
}

fn env() -> Env {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().to_path_buf();
    Env {
        _root: root,
        journal: path.join("journal"),
        keys: path.join("keys"),
        anchors: path.join("anchors"),
    }
}

impl Env {
    fn try_open(&self, seed: u8) -> Result<Opened, JournalError> {
        let ring = KeyRing::new(&self.keys, FakeKeyProvider::new([seed; 32]));
        Journal::open(
            &self.journal,
            &ring,
            Box::new(FileAnchorStore::new(&self.anchors)),
        )
    }
    fn open(&self) -> Journal {
        self.try_open(7).unwrap().into_journal()
    }
}

fn payload(event: &str) -> BackupPayload {
    BackupPayload::new(
        event.into(),
        "team".into(),
        format!("{SECRET} {event}"),
        None,
    )
    .unwrap()
}

fn tree_contains(dir: &Path, needle: &[u8]) -> bool {
    std::fs::read_dir(dir).unwrap().flatten().any(|entry| {
        let path = entry.path();
        if path.is_dir() {
            tree_contains(&path, needle)
        } else {
            std::fs::read(&path).is_ok_and(|b| b.windows(needle.len()).any(|w| w == needle))
        }
    })
}

fn confirm(token: String, export: ExportChoice, acknowledge: bool) -> Disposal {
    Disposal {
        token,
        export,
        acknowledge_possible_delivery: acknowledge,
        reason: "synthetic disposal test".into(),
        actor_local: "local-user".into(),
    }
}

/// e1 never sent, e2 may have been sent, e3 delivered.
fn seeded(env: &Env) -> Journal {
    let mut journal = env.open();
    for e in ["e1", "e2", "e3"] {
        journal.reserve("A", &payload(e)).unwrap();
    }
    journal.begin_dispatch("A", "e2").unwrap();
    journal.begin_dispatch("A", "e3").unwrap();
    journal
        .record_server_state("A", "e3", DeliveryState::Delivered, 1)
        .unwrap();
    journal
}

#[test]
fn preview_reports_scope_and_risk_and_changes_nothing() {
    let env = env();
    let mut journal = seeded(&env);
    let before = journal.recovery("A").unwrap();
    let preview = journal.preview_disposal("A", &["e1", "e2", "e3"]).unwrap();
    assert_eq!(preview.count, 3);
    assert_eq!(
        preview.may_have_been_delivered, 2,
        "e2 uncertain and e3 delivered"
    );
    assert!(preview.sealed_bytes > 0);
    assert!(preview.already_disposed.is_empty());
    assert_eq!(journal.recovery("A").unwrap(), before);
    assert!(journal.load("A", "e1").unwrap().is_some());
}

#[test]
fn disposal_tombstones_first_removes_ciphertext_and_blocks_event_reuse() {
    let env = env();
    let mut journal = seeded(&env);
    let quota_before = journal.quota("A").unwrap();
    let preview = journal.preview_disposal("A", &["e1", "e2", "e3"]).unwrap();
    let outcome = journal
        .dispose("A", confirm(preview.token, ExportChoice::Exported, true))
        .unwrap();
    assert_eq!(outcome.disposed, 3);
    assert!(journal.recovery("A").unwrap().is_empty());
    assert!(journal.load("A", "e2").unwrap().is_none());
    assert_eq!(journal.quota("A").unwrap().active_records, 0);
    assert!(journal.quota("A").unwrap().active_bytes < quota_before.active_bytes);
    assert_eq!(
        journal.reserve("A", &payload("e2")).err(),
        Some(JournalError::Tombstoned)
    );
    assert_eq!(journal.disposal_audit("A").unwrap().len(), 3);
    drop(journal);
    // Tombstones survive a restart and pass integrity checks.
    let mut journal = env.open();
    assert_eq!(
        journal.reserve("A", &payload("e1")).err(),
        Some(JournalError::Tombstoned)
    );
    assert!(journal.recovery("A").unwrap().is_empty());
}

#[test]
fn disposed_ciphertext_does_not_linger_in_database_or_wal() {
    let env = env();
    let mut journal = seeded(&env);
    let sealed_before: Vec<Vec<u8>> = {
        let conn =
            rusqlite::Connection::open(env.journal.join("db").join("journal.sqlite")).unwrap();
        let mut s = conn.prepare("SELECT sealed FROM records").unwrap();
        s.query_map([], |r| r.get(0))
            .unwrap()
            .map(Result::unwrap)
            .collect()
    };
    let preview = journal.preview_disposal("A", &["e1", "e2", "e3"]).unwrap();
    journal
        .dispose("A", confirm(preview.token, ExportChoice::Exported, true))
        .unwrap();
    for sealed in sealed_before {
        assert!(
            !tree_contains(&env.journal.join("db"), &sealed[24..56]),
            "ciphertext remains"
        );
    }
    assert!(!tree_contains(&env.journal.join("db"), SECRET.as_bytes()));
}

#[test]
fn a_stale_preview_or_missing_acknowledgement_disposes_nothing() {
    let env = env();
    let mut journal = seeded(&env);
    let preview = journal.preview_disposal("A", &["e1", "e2"]).unwrap();
    assert_eq!(
        journal
            .dispose(
                "A",
                confirm(preview.token.clone(), ExportChoice::Exported, false)
            )
            .err(),
        Some(JournalError::ConfirmationRequired)
    );
    journal
        .record_server_state("A", "e2", DeliveryState::Pending, 1)
        .unwrap();
    assert_eq!(
        journal
            .dispose("A", confirm(preview.token, ExportChoice::Exported, true))
            .err(),
        Some(JournalError::StalePreview)
    );
    assert_eq!(journal.recovery("A").unwrap().len(), 3);
    // A never-sent record alone needs no delivery acknowledgement.
    let preview = journal.preview_disposal("A", &["e1"]).unwrap();
    assert_eq!(preview.may_have_been_delivered, 0);
    journal
        .dispose(
            "A",
            confirm(preview.token, ExportChoice::DeclinedAfterWarning, false),
        )
        .unwrap();
}

#[test]
fn a_preview_cannot_be_used_for_another_owner_and_inputs_are_bounded() {
    let env = env();
    let mut journal = seeded(&env);
    journal.reserve("B", &payload("e1")).unwrap();
    let preview = journal.preview_disposal("A", &["e1"]).unwrap();
    assert_eq!(
        journal
            .dispose(
                "B",
                confirm(preview.token.clone(), ExportChoice::Exported, true)
            )
            .err(),
        Some(JournalError::StalePreview)
    );
    assert_eq!(
        journal.preview_disposal("A", &[]).err(),
        Some(JournalError::InvalidInput)
    );
    assert_eq!(
        journal.preview_disposal("A", &["missing"]).err(),
        Some(JournalError::NotFound)
    );
    let many: Vec<String> = (0..501).map(|i| format!("e{i}")).collect();
    let many: Vec<&str> = many.iter().map(String::as_str).collect();
    assert_eq!(
        journal.preview_disposal("A", &many).err(),
        Some(JournalError::InvalidInput)
    );
    let mut bad = confirm(preview.token, ExportChoice::Exported, true);
    bad.reason = String::new();
    assert_eq!(
        journal.dispose("A", bad).err(),
        Some(JournalError::InvalidInput)
    );
    assert!(journal.load("A", "e1").unwrap().is_some());
}

#[test]
fn a_retried_disposal_after_success_reports_already_disposed() {
    let env = env();
    let mut journal = seeded(&env);
    let preview = journal.preview_disposal("A", &["e1"]).unwrap();
    journal
        .dispose("A", confirm(preview.token, ExportChoice::Exported, true))
        .unwrap();
    let again = journal.preview_disposal("A", &["e1"]).unwrap();
    assert_eq!(
        (again.count, again.already_disposed),
        (0, vec!["e1".to_string()])
    );
}

#[test]
fn a_lost_key_archive_is_disposed_whole_with_an_audit_record_first() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("e1")).unwrap();
    drop(journal);
    let Opened::IsolatedPrevious {
        mut journal,
        archive,
    } = env.try_open(9).unwrap()
    else {
        panic!("expected isolation")
    };
    let name = archive.file_name().unwrap().to_str().unwrap().to_string();
    let listed = journal.isolated_archives().unwrap();
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].name, name);
    assert!(listed[0].bytes > 0);

    let preview = journal.preview_archive_disposal(&name).unwrap();
    let outcome = journal
        .dispose_archive(
            &name,
            confirm(preview.token, ExportChoice::DeclinedAfterWarning, true),
        )
        .unwrap();
    assert_eq!(outcome.disposed, 1);
    assert!(!archive.exists());
    assert!(journal.isolated_archives().unwrap().is_empty());
    assert_eq!(
        journal.archive_audit().unwrap(),
        vec![(name, "done".to_string())]
    );
    assert!(!tree_contains(&env.journal, SECRET.as_bytes()));
}

#[test]
fn archive_names_cannot_escape_and_symlinks_are_refused() {
    let env = env();
    let mut journal = env.open();
    for name in ["../db", "..", "", "a/b", "a\\b"] {
        assert_eq!(
            journal.preview_archive_disposal(name).err(),
            Some(JournalError::InvalidInput),
            "{name}"
        );
    }
    let outside = env.journal.parent().unwrap().join("outside");
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::write(outside.join("keep.txt"), b"keep").unwrap();
    std::fs::create_dir_all(env.journal.join("isolated")).unwrap();
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(&outside, env.journal.join("isolated").join("link")).unwrap();
        assert_eq!(
            journal.preview_archive_disposal("link").err(),
            Some(JournalError::InvalidInput)
        );
    }
    let _ = &mut journal;
    assert!(outside.join("keep.txt").exists());
}

#[test]
fn recovery_ignores_disposed_records_for_other_owners_too() {
    let env = env();
    let mut journal = seeded(&env);
    journal.reserve("B", &payload("e9")).unwrap();
    let preview = journal.preview_disposal("A", &["e1"]).unwrap();
    journal
        .dispose("A", confirm(preview.token, ExportChoice::Exported, true))
        .unwrap();
    assert_eq!(
        journal.recovery("B").unwrap(),
        vec![("e9".to_string(), RecoveryAction::MayPost)]
    );
}

#[test]
fn at_the_tombstone_limit_disposal_is_refused_rather_than_forgetting_tombstones() {
    use vox_agent_desktop_tauri_lib::context_journal::{QuotaKind, QuotaLimits};
    let env = env();
    let mut journal = seeded(&env);
    journal.set_limits(QuotaLimits {
        max_tombstones_per_owner: 1,
        ..QuotaLimits::default()
    });
    let preview = journal.preview_disposal("A", &["e1", "e2"]).unwrap();
    assert_eq!(
        journal
            .dispose("A", confirm(preview.token, ExportChoice::Exported, true))
            .err(),
        Some(JournalError::QuotaExceeded(QuotaKind::Tombstones))
    );
    assert!(
        journal.load("A", "e1").unwrap().is_some(),
        "nothing disposed"
    );
}

fn generation(env: &Env) -> String {
    let conn = rusqlite::Connection::open(env.journal.join("db").join("journal.sqlite")).unwrap();
    conn.query_row("SELECT v FROM meta WHERE k='generation'", [], |r| r.get(0))
        .unwrap()
}

fn lose_key(env: &Env) -> (Journal, String) {
    let mut journal = env.open();
    journal.reserve("A", &payload("e1")).unwrap();
    drop(journal);
    let Opened::IsolatedPrevious { journal, archive } = env.try_open(9).unwrap() else {
        panic!("expected isolation")
    };
    (
        journal,
        archive.file_name().unwrap().to_str().unwrap().to_string(),
    )
}

#[test]
fn an_idempotent_reserve_writes_nothing() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("e1")).unwrap();
    let before = generation(&env);
    journal.reserve("A", &payload("e1")).unwrap();
    assert_eq!(generation(&env), before);
}

#[test]
fn a_forged_pending_archive_decision_never_deletes_anything() {
    let env = env();
    let (journal, name) = lose_key(&env);
    drop(journal);
    let conn = rusqlite::Connection::open(env.journal.join("db").join("journal.sqlite")).unwrap();
    conn.execute(
        "INSERT INTO archive_disposals(name, export, reason, actor_local, status, fingerprint, at, tag)
         VALUES (?1, 'exported', 'forged', 'x', 'pending', '', 0, x'00')",
        [&name],
    )
    .unwrap();
    drop(conn);
    let _journal = env.try_open(9).unwrap().into_journal();
    assert!(
        env.journal.join("isolated").join(&name).exists(),
        "forged decision must not delete"
    );
}

#[test]
fn a_swapped_archive_of_the_same_size_invalidates_the_preview() {
    let env = env();
    let (mut journal, name) = lose_key(&env);
    let preview = journal.preview_archive_disposal(&name).unwrap();
    let archive = env.journal.join("isolated").join(&name);
    std::fs::write(archive.join("extra"), b"").unwrap(); // same byte total, new entry
    assert_eq!(
        journal
            .dispose_archive(&name, confirm(preview.token, ExportChoice::Exported, true))
            .err(),
        Some(JournalError::StalePreview)
    );
    assert!(archive.exists());
}

#[cfg(unix)]
#[test]
fn a_symlinked_isolated_directory_is_never_used_for_deletion() {
    let env = env();
    let mut journal = env.open();
    let outside = env.journal.parent().unwrap().join("outside");
    std::fs::create_dir_all(outside.join("victim")).unwrap();
    std::fs::write(outside.join("victim").join("keep.txt"), b"keep").unwrap();
    std::os::unix::fs::symlink(&outside, env.journal.join("isolated")).unwrap();
    assert!(journal.isolated_archives().unwrap().is_empty());
    assert_eq!(
        journal.preview_archive_disposal("victim").err(),
        Some(JournalError::InvalidInput)
    );
    assert!(outside.join("victim").join("keep.txt").exists());
}

#[test]
fn a_reader_holding_the_wal_defers_cleanup_and_the_next_open_finishes_it() {
    let env = env();
    let mut journal = seeded(&env);
    let reader = rusqlite::Connection::open(env.journal.join("db").join("journal.sqlite")).unwrap();
    reader.execute_batch("BEGIN").unwrap();
    let _: i64 = reader
        .query_row("SELECT COUNT(*) FROM records", [], |r| r.get(0))
        .unwrap();
    let preview = journal.preview_disposal("A", &["e1"]).unwrap();
    let outcome = journal
        .dispose("A", confirm(preview.token, ExportChoice::Exported, true))
        .unwrap();
    assert!(!outcome.wal_cleanup_complete);
    reader.execute_batch("COMMIT").unwrap();
    drop(reader);
    drop(journal);
    let _journal = env.open();
    let conn = rusqlite::Connection::open(env.journal.join("db").join("journal.sqlite")).unwrap();
    let pending: Option<String> = conn
        .query_row(
            "SELECT v FROM meta WHERE k='wal_cleanup_pending'",
            [],
            |r| r.get(0),
        )
        .ok();
    assert_eq!(pending, None);
}
