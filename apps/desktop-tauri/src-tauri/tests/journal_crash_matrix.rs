//! T08: the process is aborted at each window of a submission; after restart
//! the record keeps its eventId and content, and recovery never allows a second
//! POST once one may have been sent.
use std::path::Path;
use std::process::Command;

use vox_agent_desktop_tauri_lib::context_delivery::{BackupPayload, DeliveryState};
use vox_agent_desktop_tauri_lib::context_journal::{
    DispatchPhase, FileAnchorStore, Journal, RecoveryAction,
};
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::KeyRing;

const WINDOW_ENV: &str = "VOX_JOURNAL_CRASH_WINDOW";
const DIR_ENV: &str = "VOX_JOURNAL_CRASH_DIR";
const OWNER: &str = "principal-A";
const WINDOWS: [&str; 7] = [
    "before_reserve",
    "after_reserve",
    "after_begin_dispatch",
    "after_post_sent",
    "after_accept",
    "after_poll",
    "after_not_sent",
];

fn payload() -> BackupPayload {
    BackupPayload::new(
        "event-1".into(),
        "team".into(),
        "synthetic 中文 context".into(),
        None,
    )
    .unwrap()
}

fn open(dir: &Path) -> Journal {
    let ring = KeyRing::new(&dir.join("keys"), FakeKeyProvider::new([7; 32]));
    let anchors = Box::new(FileAnchorStore::new(&dir.join("anchors")));
    Journal::open(&dir.join("journal"), &ring, anchors)
        .unwrap()
        .into_journal()
}

/// Append-only POST spy: each simulated POST adds a line.
fn spy_post(dir: &Path) {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("post-spy.log"))
        .unwrap();
    file.write_all(b"POST event-1\n").unwrap();
    file.sync_all().unwrap();
}

fn spy_posts(dir: &Path) -> usize {
    std::fs::read_to_string(dir.join("post-spy.log"))
        .map(|s| s.lines().count())
        .unwrap_or(0)
}

/// Runs only as the child process: perform the submission and abort at the window.
#[test]
fn crash_child() {
    let (Ok(window), Ok(dir)) = (std::env::var(WINDOW_ENV), std::env::var(DIR_ENV)) else {
        return;
    };
    let dir = Path::new(&dir);
    let crash = |at: &str| {
        if window == at {
            use std::io::Write;
            println!("REACHED {at}");
            std::io::stdout().flush().unwrap();
            std::process::abort();
        }
    };
    let mut journal = open(dir);
    crash("before_reserve");
    journal.reserve(OWNER, &payload()).unwrap();
    crash("after_reserve");
    journal.begin_dispatch(OWNER, "event-1").unwrap();
    crash("after_begin_dispatch");
    if window == "after_not_sent" {
        // The transport reported PreDispatch: no bytes left the process.
        journal.dispatch_not_sent(OWNER, "event-1").unwrap();
        crash("after_not_sent");
    }
    spy_post(dir);
    crash("after_post_sent");
    journal
        .record_server_state(OWNER, "event-1", DeliveryState::Pending, 1)
        .unwrap();
    crash("after_accept");
    journal
        .record_server_state(OWNER, "event-1", DeliveryState::Sending, 2)
        .unwrap();
    crash("after_poll");
}

#[test]
fn every_crash_window_preserves_identity_content_and_the_no_second_post_rule() {
    if std::env::var_os(WINDOW_ENV).is_some() {
        return;
    }
    for window in WINDOWS {
        let dir = tempfile::tempdir().unwrap();
        let output = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "crash_child", "--nocapture", "--test-threads=1"])
            .env(WINDOW_ENV, window)
            .env(DIR_ENV, dir.path())
            .output()
            .unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(
            stdout.contains(&format!("REACHED {window}")),
            "{window}: child did not reach the window: {stdout}"
        );
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            assert_eq!(
                output.status.signal(),
                Some(6),
                "{window}: must die by SIGABRT"
            );
        }
        assert!(!output.status.success(), "{window}: child must abort");

        let journal = open(dir.path());
        let stored = journal.load(OWNER, "event-1").unwrap();
        let actions = journal.recovery(OWNER).unwrap();
        let action = actions
            .iter()
            .find(|(e, _)| e == "event-1")
            .map(|(_, a)| *a);
        match window {
            "before_reserve" => {
                assert!(stored.is_none() && action.is_none(), "{window}");
                continue;
            }
            _ => {
                let record = stored.unwrap_or_else(|| panic!("{window}: record lost"));
                assert!(
                    record.payload == payload(),
                    "{window}: identity and content"
                );
                let (phase, expected_action, state) = match window {
                    "after_reserve" | "after_not_sent" => {
                        (DispatchPhase::Reserved, RecoveryAction::MayPost, None)
                    }
                    "after_begin_dispatch" | "after_post_sent" => (
                        DispatchPhase::Dispatching,
                        RecoveryAction::ReadBackOnly,
                        None,
                    ),
                    "after_accept" => (
                        DispatchPhase::Accepted,
                        RecoveryAction::Poll,
                        Some(DeliveryState::Pending),
                    ),
                    "after_poll" => (
                        DispatchPhase::Accepted,
                        RecoveryAction::Poll,
                        Some(DeliveryState::Sending),
                    ),
                    other => unreachable!("{other}"),
                };
                assert_eq!(record.phase, phase, "{window}");
                assert_eq!(record.server_state, state, "{window}");
                assert_eq!(action, Some(expected_action), "{window}");
            }
        }
        if window == "after_post_sent" {
            assert_eq!(spy_posts(dir.path()), 1);
        }
        if window == "after_reserve" || window == "after_not_sent" {
            assert_eq!(spy_posts(dir.path()), 0, "{window}: nothing was sent");
        }
    }
}
