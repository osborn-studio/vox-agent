//! T08: encrypted journal storage, isolation, rollback and locking.
use std::path::{Path, PathBuf};

use vox_agent_desktop_tauri_lib::context_delivery::{
    BackupPayload, DeliveryState, ProjectSnapshot,
};
use vox_agent_desktop_tauri_lib::context_journal::{
    DispatchPhase, FileAnchorStore, Journal, JournalError, Opened, RecoveryAction, RecoveryMode,
    ReserveOutcome,
};
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::KeyRing;

const SECRET: &str = "synthetic-secret-marker";

/// A journal directory plus key and anchor directories kept outside it.
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
    fn db(&self) -> PathBuf {
        self.journal.join("db").join("journal.sqlite")
    }
    fn sql(&self, statement: &str) {
        let conn = rusqlite::Connection::open(self.db()).unwrap();
        conn.execute_batch(statement).unwrap();
    }
}

fn payload(event: &str, text: &str) -> BackupPayload {
    let project = ProjectSnapshot::new("p1".into(), "studio/repo".into()).unwrap();
    BackupPayload::new(event.into(), "team".into(), text.into(), Some(project)).unwrap()
}

fn files_contain(dir: &Path, needle: &[u8]) -> bool {
    std::fs::read_dir(dir).unwrap().flatten().any(|entry| {
        entry.path().is_file()
            && std::fs::read(entry.path())
                .is_ok_and(|bytes| bytes.windows(needle.len()).any(|w| w == needle))
    })
}

fn snapshot(dir: &Path) -> Vec<(String, Vec<u8>)> {
    let mut files: Vec<_> = std::fs::read_dir(dir)
        .unwrap()
        .flatten()
        .map(|e| {
            (
                e.file_name().to_string_lossy().into_owned(),
                std::fs::read(e.path()).unwrap(),
            )
        })
        .collect();
    files.sort();
    files
}

#[test]
fn record_content_never_reaches_the_database_files_in_plaintext() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", SECRET)).unwrap();
    let db_dir = env.journal.join("db");
    assert!(!files_contain(&db_dir, SECRET.as_bytes()));
    assert!(!files_contain(&db_dir, b"studio/repo"));
    drop(journal);
    assert!(!files_contain(&db_dir, SECRET.as_bytes()));
}

#[test]
fn reserve_is_idempotent_per_owner_and_conflicts_on_changed_content() {
    let env = env();
    let mut journal = env.open();
    assert_eq!(
        journal.reserve("A", &payload("event-1", "x")).unwrap(),
        ReserveOutcome::Reserved
    );
    assert_eq!(
        journal.reserve("A", &payload("event-1", "x")).unwrap(),
        ReserveOutcome::AlreadyReserved
    );
    assert_eq!(
        journal.reserve("A", &payload("event-1", "y")).err(),
        Some(JournalError::Conflict)
    );
    assert_eq!(
        journal.reserve("B", &payload("event-1", "y")).unwrap(),
        ReserveOutcome::Reserved
    );
    assert_eq!(
        journal
            .load("B", "event-1")
            .unwrap()
            .unwrap()
            .payload
            .text(),
        "y"
    );
    assert!(journal.load("C", "event-1").unwrap().is_none());
}

#[test]
fn dispatch_phases_only_move_forward_and_server_state_needs_a_newer_sequence() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    assert_eq!(
        journal.dispatch_not_sent("A", "event-1").err(),
        Some(JournalError::InvalidTransition)
    );
    journal.begin_dispatch("A", "event-1").unwrap();
    assert_eq!(
        journal.begin_dispatch("A", "event-1").err(),
        Some(JournalError::InvalidTransition)
    );
    journal
        .record_server_state("A", "event-1", DeliveryState::Sending, 5)
        .unwrap();
    assert_eq!(
        journal
            .record_server_state("A", "event-1", DeliveryState::Pending, 4)
            .err(),
        Some(JournalError::StaleSequence)
    );
    journal
        .record_server_state("A", "event-1", DeliveryState::Delivered, 6)
        .unwrap();
    assert_eq!(
        journal
            .record_server_state("A", "event-1", DeliveryState::Pending, 7)
            .err(),
        Some(JournalError::InvalidTransition)
    );
    let record = journal.load("A", "event-1").unwrap().unwrap();
    assert_eq!(
        (record.phase, record.server_state),
        (DispatchPhase::Accepted, Some(DeliveryState::Delivered))
    );
    assert_eq!(
        journal.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::Final)]
    );
}

#[test]
fn a_second_instance_is_refused_while_the_first_holds_the_journal() {
    let env = env();
    let first = env.open();
    assert_eq!(env.try_open(7).err(), Some(JournalError::Busy));
    drop(first);
    assert!(env.try_open(7).is_ok());
}

#[test]
fn a_lost_key_isolates_the_whole_database_directory_with_one_rename() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    let before = snapshot(&env.journal.join("db"));

    let Opened::IsolatedPrevious { journal, archive } = env.try_open(9).unwrap() else {
        panic!("expected isolation")
    };
    assert!(journal.load("A", "event-1").unwrap().is_none());
    assert!(archive.starts_with(env.journal.join("isolated")));
    assert_eq!(snapshot(&archive), before);
}

#[test]
fn an_unavailable_key_leaves_every_file_byte_identical() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    let before = snapshot(&env.journal.join("db"));
    let locked = KeyRing::new(&env.keys, FakeKeyProvider::unavailable([7; 32]));
    assert_eq!(
        Journal::open(
            &env.journal,
            &locked,
            Box::new(FileAnchorStore::new(&env.anchors))
        )
        .err(),
        Some(JournalError::KeyUnavailable)
    );
    assert_eq!(snapshot(&env.journal.join("db")), before);
    assert!(!env.journal.join("isolated").exists());
}

#[test]
fn restoring_an_older_copy_demotes_reserved_records_for_good() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    let copy = snapshot(&env.journal.join("db"));

    let mut journal = env.open();
    journal.begin_dispatch("A", "event-1").unwrap(); // a POST may now be sent
    drop(journal);
    let db_dir = env.journal.join("db");
    std::fs::remove_dir_all(&db_dir).unwrap();
    std::fs::create_dir_all(&db_dir).unwrap();
    for (name, bytes) in copy {
        std::fs::write(db_dir.join(name), bytes).unwrap();
    }

    let mut restored = env.open();
    assert_eq!(restored.mode(), RecoveryMode::VerifyOnly);
    assert_eq!(
        restored.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::ReadBackOnly)]
    );
    assert_eq!(
        restored.begin_dispatch("A", "event-1").err(),
        Some(JournalError::VerifyOnly)
    );
    restored.finish_verification().unwrap();
    assert_eq!(restored.mode(), RecoveryMode::Normal);
    // Acknowledging never re-authorises the restored record.
    assert_eq!(
        restored.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::ReadBackOnly)]
    );
    drop(restored);
    assert_eq!(env.open().mode(), RecoveryMode::Normal);
}

#[test]
fn a_forged_generation_or_a_missing_anchor_is_treated_as_restored() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    env.sql("UPDATE meta SET v='999' WHERE k='generation'");
    let journal = env.open();
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert_eq!(
        journal.recovery("A").unwrap()[0].1,
        RecoveryAction::ReadBackOnly
    );
    drop(journal);

    let other = self::env();
    let mut journal = other.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    std::fs::remove_dir_all(&other.anchors).unwrap();
    assert_eq!(other.open().mode(), RecoveryMode::VerifyOnly);
}

#[test]
fn an_anchor_failure_refuses_dispatch_and_leaves_the_record_read_back_only() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    std::fs::remove_dir_all(&env.anchors).unwrap();
    std::fs::write(&env.anchors, b"not a directory").unwrap();
    assert_eq!(
        journal.begin_dispatch("A", "event-1").err(),
        Some(JournalError::AnchorFailed)
    );
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert_eq!(
        journal.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::ReadBackOnly)]
    );
    assert_eq!(
        journal.finish_verification().err(),
        Some(JournalError::AnchorFailed)
    );
}

#[test]
fn an_edited_clear_column_quarantines_the_record() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    journal.begin_dispatch("A", "event-1").unwrap();
    drop(journal);
    env.sql("UPDATE records SET phase='reserved'");
    let journal = env.open();
    assert_eq!(
        journal.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::Quarantine)]
    );
    assert_eq!(
        journal.load("A", "event-1").err(),
        Some(JournalError::Corrupt)
    );
}

fn restore(env: &Env, copy: &[(String, Vec<u8>)]) {
    let db_dir = env.journal.join("db");
    std::fs::remove_dir_all(&db_dir).unwrap();
    std::fs::create_dir_all(&db_dir).unwrap();
    for (name, bytes) in copy {
        std::fs::write(db_dir.join(name), bytes).unwrap();
    }
}

#[test]
fn a_second_restore_of_a_newer_copy_is_still_detected() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-a", "x")).unwrap();
    drop(journal);
    let first = snapshot(&env.journal.join("db"));
    let mut journal = env.open();
    journal.reserve("A", &payload("event-b", "y")).unwrap();
    drop(journal);
    let second = snapshot(&env.journal.join("db"));
    let mut journal = env.open();
    journal.begin_dispatch("A", "event-a").unwrap();
    journal.begin_dispatch("A", "event-b").unwrap();
    drop(journal);

    restore(&env, &first);
    let mut journal = env.open();
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    journal.finish_verification().unwrap();
    drop(journal);

    restore(&env, &second);
    let journal = env.open();
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert!(journal
        .recovery("A")
        .unwrap()
        .iter()
        .all(|(_, action)| *action == RecoveryAction::ReadBackOnly));
}

#[test]
fn an_old_row_spliced_into_a_current_database_is_detected() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    let old = env.journal.join("old.sqlite");
    std::fs::copy(env.db(), &old).unwrap();
    let mut journal = env.open();
    journal.begin_dispatch("A", "event-1").unwrap();
    drop(journal);
    env.sql(&format!(
        "ATTACH '{}' AS old; DELETE FROM records; INSERT INTO records SELECT * FROM old.records;",
        old.display()
    ));
    std::fs::remove_file(&old).unwrap();

    let mut journal = env.open();
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert_eq!(
        journal.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::ReadBackOnly)]
    );
    assert_eq!(
        journal.load("A", "event-1").unwrap().unwrap().phase,
        DispatchPhase::Restored
    );
    journal.finish_verification().unwrap();
    assert_eq!(
        journal.dispatch_not_sent("A", "event-1").err(),
        Some(JournalError::InvalidTransition),
        "a restored record never returns to Reserved"
    );
    assert_eq!(
        journal.begin_dispatch("A", "event-1").err(),
        Some(JournalError::InvalidTransition)
    );
}

#[test]
fn moving_a_row_to_another_zone_is_detected() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    env.sql("UPDATE records SET zone='tombstone'");
    let journal = env.open();
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert_eq!(
        journal.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::Quarantine)]
    );
}

#[test]
fn a_tampered_row_seen_during_a_restore_stays_quarantined_after_its_fields_are_repaired() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    let old = env.journal.join("old.sqlite");
    std::fs::copy(env.db(), &old).unwrap();
    let mut journal = env.open();
    journal.begin_dispatch("A", "event-1").unwrap();
    drop(journal);
    // Splice the old Reserved row back but break its authentication.
    env.sql(&format!(
        "ATTACH '{}' AS old; DELETE FROM records; INSERT INTO records SELECT * FROM old.records;
         UPDATE records SET phase='dispatching';",
        old.display()
    ));
    std::fs::remove_file(&old).unwrap();
    let mut journal = env.open();
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert_eq!(
        journal.recovery("A").unwrap()[0].1,
        RecoveryAction::Quarantine
    );
    journal.finish_verification().unwrap();
    drop(journal);
    // "Repair" the edited field: the row must not come back as Reserved.
    env.sql("UPDATE records SET phase='reserved', zone='active'");
    let mut journal = env.open();
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert_ne!(journal.recovery("A").unwrap()[0].1, RecoveryAction::MayPost);
    journal.finish_verification().unwrap();
    assert!(journal.begin_dispatch("A", "event-1").is_err());
}

#[test]
fn an_old_row_spliced_in_while_the_journal_is_open_is_never_re_signed() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    journal.checkpoint().unwrap();
    let old = env.journal.join("old.sqlite");
    std::fs::copy(env.db(), &old).unwrap();
    journal.begin_dispatch("A", "event-1").unwrap();
    // Another connection splices the old Reserved row in while we hold the journal.
    env.sql(&format!(
        "ATTACH '{}' AS old; DELETE FROM records; INSERT INTO records SELECT * FROM old.records;",
        old.display()
    ));
    assert_eq!(journal.recovery("A").err(), Some(JournalError::VerifyOnly));
    assert_eq!(
        journal.finish_verification().err(),
        Some(JournalError::VerifyOnly)
    );
    assert_eq!(journal.mode(), RecoveryMode::VerifyOnly);
    assert_eq!(
        journal.recovery("A").unwrap(),
        vec![("event-1".to_string(), RecoveryAction::ReadBackOnly)]
    );
    journal.finish_verification().unwrap();
    assert!(journal.begin_dispatch("A", "event-1").is_err());
    drop(journal);
    assert_ne!(
        env.open().recovery("A").unwrap()[0].1,
        RecoveryAction::MayPost
    );
}

mod quota {
    use super::*;
    use vox_agent_desktop_tauri_lib::context_journal::{QuotaKind, QuotaLimits};

    fn small() -> QuotaLimits {
        QuotaLimits {
            max_active_records_per_owner: 3,
            max_active_bytes_per_owner: 1_000_000,
            max_isolated_bytes_per_owner: 1_000_000,
            max_total_bytes: 200 * 1024 * 1024,
            emergency_reserve_bytes: 20 * 1024 * 1024,
            max_tombstones_per_owner: 100_000,
        }
    }

    #[test]
    fn the_active_record_limit_is_per_owner_and_never_evicts_anything() {
        let env = env();
        let mut journal = env.open();
        journal.set_limits(small());
        for i in 0..3 {
            journal
                .reserve("A", &payload(&format!("e{i}"), "x"))
                .unwrap();
        }
        assert_eq!(
            journal.reserve("A", &payload("e3", "x")).err(),
            Some(JournalError::QuotaExceeded(QuotaKind::ActiveRecords))
        );
        // Another owner is unaffected and nothing of A's was dropped.
        journal.reserve("B", &payload("e0", "x")).unwrap();
        assert_eq!(journal.recovery("A").unwrap().len(), 3);
        // An idempotent re-reserve of an existing record is not a new record.
        assert_eq!(
            journal.reserve("A", &payload("e0", "x")).unwrap(),
            ReserveOutcome::AlreadyReserved
        );
    }

    #[test]
    fn the_active_byte_limit_counts_sealed_payload_bytes() {
        let env = env();
        let mut journal = env.open();
        journal.set_limits(QuotaLimits {
            max_active_records_per_owner: 100,
            max_active_bytes_per_owner: 2_500,
            ..small()
        });
        journal
            .reserve("A", &payload("e0", &"x".repeat(1_000)))
            .unwrap();
        journal
            .reserve("A", &payload("e1", &"x".repeat(1_000)))
            .unwrap();
        assert_eq!(
            journal
                .reserve("A", &payload("e2", &"x".repeat(1_000)))
                .err(),
            Some(JournalError::QuotaExceeded(QuotaKind::ActiveBytes))
        );
        let report = journal.quota("A").unwrap();
        assert_eq!(report.active_records, 2);
        assert!(report.active_bytes > 2_000 && report.active_bytes <= 2_500);
    }

    #[test]
    fn new_records_may_not_use_the_emergency_reserve() {
        let env = env();
        let mut journal = env.open();
        let used = journal.quota("A").unwrap().total_disk_bytes;
        journal.set_limits(QuotaLimits {
            max_total_bytes: used + 30_000,
            emergency_reserve_bytes: 29_000,
            ..small()
        });
        assert_eq!(
            journal
                .reserve("A", &payload("e0", &"x".repeat(2_000)))
                .err(),
            Some(JournalError::QuotaExceeded(QuotaKind::DiskBudget))
        );
    }

    #[test]
    fn the_report_counts_isolated_rows_separately() {
        let env = env();
        let mut journal = env.open();
        journal.reserve("A", &payload("e0", "x")).unwrap();
        drop(journal);
        env.sql("UPDATE records SET phase='dispatching'"); // breaks the row tag
        env.sql("UPDATE meta SET v='0' WHERE k='generation'"); // force the restore path
        let journal = env.open();
        let report = journal.quota("A").unwrap();
        assert_eq!(report.active_records, 0);
        assert!(report.isolated_bytes > 0);
    }
}

#[cfg(unix)]
#[test]
fn a_symlinked_journal_root_or_isolated_directory_is_refused() {
    let env = env();
    let target = env.journal.parent().unwrap().join("elsewhere");
    std::fs::create_dir_all(&target).unwrap();
    std::os::unix::fs::symlink(&target, &env.journal).unwrap();
    assert_eq!(env.try_open(7).err(), Some(JournalError::UnsafePath));
    assert!(
        std::fs::read_dir(&target).unwrap().next().is_none(),
        "nothing written through the link"
    );

    let other = self::env();
    let mut journal = other.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    let outside = other.journal.parent().unwrap().join("outside");
    std::fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, other.journal.join("isolated")).unwrap();
    assert_eq!(other.try_open(9).err(), Some(JournalError::UnsafePath));
    assert!(
        other.db().exists(),
        "the database was not moved through the link"
    );
    assert!(std::fs::read_dir(&outside).unwrap().next().is_none());
}

mod originals {
    use super::*;

    #[test]
    fn records_page_newest_first_with_an_opaque_cursor() {
        let env = env();
        let mut journal = env.open();
        for i in 0..5 {
            journal
                .reserve("A", &payload(&format!("e{i}"), "x"))
                .unwrap();
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        journal.reserve("B", &payload("other", "x")).unwrap();
        let (first, cursor) = journal.page("A", None, 2).unwrap();
        assert_eq!(first.len(), 2);
        let cursor = cursor.expect("more pages");
        let (second, cursor2) = journal.page("A", Some(&cursor), 2).unwrap();
        let (third, end) = journal.page("A", cursor2.as_deref(), 2).unwrap();
        let all: Vec<String> = first.into_iter().chain(second).chain(third).collect();
        assert_eq!(all.len(), 5, "every record of A exactly once: {all:?}");
        assert!(!all.contains(&"other".to_string()));
        assert_eq!(end, None);
        assert_eq!(
            journal.page("A", Some("not a cursor"), 2).err(),
            Some(JournalError::InvalidInput)
        );
        assert_eq!(
            journal.page("A", None, 0).err(),
            Some(JournalError::InvalidInput)
        );
        assert_eq!(
            journal.page("A", None, 201).err(),
            Some(JournalError::InvalidInput)
        );
    }

    #[test]
    fn the_original_is_returned_only_after_owner_ciphertext_and_digest_checks() {
        let env = env();
        let mut journal = env.open();
        journal.reserve("A", &payload("event-1", SECRET)).unwrap();
        let original = journal.read_original("A", "event-1").unwrap();
        assert_eq!(original.payload.text(), SECRET);
        assert_eq!(
            original.payload.project().unwrap().repository(),
            "studio/repo"
        );
        assert_eq!(
            journal.read_original("B", "event-1").err(),
            Some(JournalError::NotFound)
        );
        assert_eq!(
            journal.read_original("A", "missing").err(),
            Some(JournalError::NotFound)
        );
    }

    #[test]
    fn a_corrupt_or_swapped_ciphertext_is_never_decrypted_or_returned() {
        let env = env();
        let mut journal = env.open();
        journal.reserve("A", &payload("event-1", "one")).unwrap();
        journal.reserve("A", &payload("event-2", "two")).unwrap();
        drop(journal);
        // Swap the two ciphertexts: each row tag and AAD binds its own event.
        env.sql(
            "UPDATE records SET sealed = (SELECT sealed FROM records r2 WHERE r2.event_id='event-2')
             WHERE event_id='event-1'",
        );
        let journal = env.open();
        assert!(journal.read_original("A", "event-1").is_err());
        assert!(
            journal.read_original("A", "event-2").is_ok()
                || journal.mode() == RecoveryMode::VerifyOnly
        );
    }

    #[test]
    fn a_disposed_record_has_no_original() {
        use vox_agent_desktop_tauri_lib::context_journal::{Disposal, ExportChoice};
        let env = env();
        let mut journal = env.open();
        journal.reserve("A", &payload("event-1", "x")).unwrap();
        let preview = journal.preview_disposal("A", &["event-1"]).unwrap();
        journal
            .dispose(
                "A",
                Disposal {
                    token: preview.token,
                    export: ExportChoice::Exported,
                    acknowledge_possible_delivery: true,
                    reason: "synthetic".into(),
                    actor_local: "local".into(),
                },
            )
            .unwrap();
        assert_eq!(
            journal.read_original("A", "event-1").err(),
            Some(JournalError::NotFound)
        );
        assert!(journal.page("A", None, 10).unwrap().0.is_empty());
    }

    #[test]
    fn reading_refuses_a_database_changed_underneath() {
        let env = env();
        let mut journal = env.open();
        journal.reserve("A", &payload("event-1", "x")).unwrap();
        env.sql("UPDATE meta SET v='999' WHERE k='generation'");
        assert_eq!(
            journal.read_original("A", "event-1").err(),
            Some(JournalError::VerifyOnly)
        );
    }
}

#[test]
fn an_edited_paging_timestamp_is_detected() {
    let env = env();
    let mut journal = env.open();
    journal.reserve("A", &payload("event-1", "x")).unwrap();
    drop(journal);
    env.sql("UPDATE records SET created_at = created_at - 1000");
    let journal = env.open();
    // The row no longer authenticates: it is quarantined, never read or sent.
    assert_eq!(
        journal.recovery("A").unwrap()[0].1,
        RecoveryAction::Quarantine
    );
    assert!(journal.read_original("A", "event-1").is_err());
}
