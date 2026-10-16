//! T44c: run the Context flow with synthetic sensitive markers and leave its
//! files for `scripts/scan_plaintext_leaks.py --cargo-flow`. Standalone, it
//! also checks itself that no marker is stored in plaintext (UTF-8).
//!
//! Markers come from `VOX_LEAK_MARKERS` (JSON: text, repository, token) and
//! the data directory from `VOX_LEAK_SCAN_DIR`; both default when unset.
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use vox_agent_desktop_tauri_lib::context_delivery::{BackupPayload, ProjectSnapshot};
use vox_agent_desktop_tauri_lib::context_journal::{
    Disposal, ExportChoice, FileAnchorStore, Journal,
};
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::KeyRing;
use vox_agent_desktop_tauri_lib::context_policy::Next;
use vox_agent_desktop_tauri_lib::context_service::ContextService;
use vox_agent_desktop_tauri_lib::context_session::{
    DestinationKey, PrincipalKey, SessionAuthority, TokenStore,
};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;
use vox_agent_desktop_tauri_lib::secrets::SecretError;

struct Markers {
    text: String,
    repository: String,
    token: String,
}

fn markers() -> Markers {
    let raw = std::env::var("VOX_LEAK_MARKERS").unwrap_or_else(|_| {
        r#"{"text":"leak-text-0d1e2f3a","repository":"leak0d1e/repo2f3a","token":"leak0d1e2f3a4b5c6d7e8f9a0b"}"#.into()
    });
    let value: serde_json::Value = serde_json::from_str(&raw).unwrap();
    let get = |key: &str| value[key].as_str().unwrap().to_string();
    Markers {
        text: get("text"),
        repository: get("repository"),
        token: get("token"),
    }
}

#[derive(Clone, Default)]
struct MemoryStore(Arc<Mutex<HashMap<String, String>>>);

impl TokenStore for MemoryStore {
    fn read(&self, account: &str) -> Result<Option<String>, SecretError> {
        Ok(self.0.lock().unwrap().get(account).cloned())
    }
    fn write(&self, account: &str, value: &str) -> Result<(), SecretError> {
        self.0.lock().unwrap().insert(account.into(), value.into());
        Ok(())
    }
    fn delete(&self, account: &str) -> Result<(), SecretError> {
        self.0.lock().unwrap().remove(account);
        Ok(())
    }
}

/// POST -> 202 pending (the event named "lost" gets no response);
/// GET -> delivered.
fn mock_cloud(token: String) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { return };
            let (line, head, body) = read_request(&mut stream);
            if !head.contains(&format!("Bearer {token}")) {
                respond(
                    &mut stream,
                    "401 Unauthorized",
                    r#"{"detail":{"code":"INVALID_CREDENTIAL","message":"x"}}"#,
                );
                continue;
            }
            if line.starts_with("POST ") {
                let event = serde_json::from_str::<serde_json::Value>(&body).unwrap()["eventId"]
                    .as_str()
                    .unwrap()
                    .to_string();
                if event == "lost" {
                    continue;
                }
                respond(
                    &mut stream,
                    "202 Accepted",
                    &format!(r#"{{"eventId":"{event}","status":"pending","messageId":null}}"#),
                );
            } else if let Some(rest) = line.strip_prefix("GET /v1/input-backups/") {
                let event = rest.split(' ').next().unwrap();
                respond(
                    &mut stream,
                    "200 OK",
                    &format!(
                        r#"{{"eventId":"{event}","status":"delivered","messageId":"om_leak"}}"#
                    ),
                );
            }
        }
    });
    format!("http://{address}")
}

fn read_request(stream: &mut TcpStream) -> (String, String, String) {
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut data = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        let n = stream.read(&mut buffer).unwrap_or(0);
        if n == 0 {
            break;
        }
        data.extend_from_slice(&buffer[..n]);
        let text = String::from_utf8_lossy(&data).to_string();
        if let Some(end) = text.find("\r\n\r\n") {
            let length = text[..end]
                .lines()
                .find_map(|l| {
                    l.to_ascii_lowercase()
                        .strip_prefix("content-length:")
                        .map(|v| v.trim().parse::<usize>().unwrap())
                })
                .unwrap_or(0);
            if data.len() >= end + 4 + length {
                return (
                    text.lines().next().unwrap_or("").to_string(),
                    text[..end].to_string(),
                    text[end + 4..].to_string(),
                );
            }
        }
    }
    (String::new(), String::new(), String::new())
}

fn respond(stream: &mut TcpStream, status: &str, body: &str) {
    let _ = stream.write_all(
        format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len())
            .as_bytes(),
    );
}

fn journal(dir: &Path) -> Journal {
    let ring = KeyRing::new(&dir.join("keys"), FakeKeyProvider::new([7; 32]));
    Journal::open(
        &dir.join("journal"),
        &ring,
        Box::new(FileAnchorStore::new(&dir.join("anchors"))),
    )
    .unwrap()
    .into_journal()
}

fn files(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        let path = entry.path();
        if path.is_dir() {
            out.extend(files(&path));
        } else {
            out.push(path);
        }
    }
    out
}

fn assert_no_markers(dir: &Path, paths: &[PathBuf], m: &Markers) {
    for path in paths {
        // Windows enforces the journal's exclusive byte-range lock even for
        // a read of its empty lock file. An empty file cannot contain a marker;
        // do not suppress read errors for any file that actually contains data.
        if std::fs::metadata(path).unwrap().len() == 0 {
            continue;
        }
        let bytes = std::fs::read(path)
            .unwrap_or_else(|error| panic!("cannot scan {}: {error}", path.display()));
        for marker in [&m.text, &m.repository, &m.token] {
            assert!(
                !bytes.windows(marker.len()).any(|w| w == marker.as_bytes()),
                "plaintext marker found in {}",
                path.strip_prefix(dir).unwrap().display()
            );
        }
    }
}

#[test]
fn the_context_flow_leaves_no_plaintext_on_disk() {
    let m = markers();
    let kept = std::env::var("VOX_LEAK_SCAN_DIR").ok().map(PathBuf::from);
    let temp = tempfile::tempdir().unwrap();
    let dir = kept.clone().unwrap_or_else(|| temp.path().to_path_buf());
    std::fs::create_dir_all(&dir).unwrap();

    let endpoint = ContextEndpoint::parse(&mock_cloud(m.token.clone())).unwrap();
    let principal = PrincipalKey::new(&endpoint, "default-org", "leak-actor", 1).unwrap();
    let store = MemoryStore::default();
    let team = vec![DestinationKey::new("team").unwrap()];
    let project = ProjectSnapshot::new("leak-project".into(), m.repository.clone()).unwrap();
    let records = [
        BackupPayload::new(
            "first".into(),
            "team".into(),
            format!("{} first", m.text),
            Some(project),
        )
        .unwrap(),
        BackupPayload::new(
            "second".into(),
            "team".into(),
            format!("{} second", m.text),
            None,
        )
        .unwrap(),
        BackupPayload::new(
            "lost".into(),
            "team".into(),
            format!("{} lost", m.text),
            None,
        )
        .unwrap(),
    ];
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    {
        let mut session = SessionAuthority::new(store.clone());
        session.sign_in(principal.clone(), m.token.clone()).unwrap();
        let service =
            ContextService::new(endpoint.clone(), team.clone(), session, Some(journal(&dir)));
        for payload in &records {
            service.reserve(payload).unwrap();
            for _ in 0..4 {
                match runtime.block_on(service.step(payload.event_id())) {
                    Ok(Next::PollAfter(_) | Next::ResendAfter(_)) => continue,
                    _ => break,
                }
            }
        }
        // Scan while the journal is open: the live WAL holds every recent page.
        let live = files(&dir);
        assert!(
            live.iter()
                .any(|p| p.to_string_lossy().ends_with("journal.sqlite-wal")),
            "the live scan must include the WAL"
        );
        assert_no_markers(&dir, &live, &m);
    }
    // Restart, read an original back, page, and dispose of one record.
    {
        let mut session = SessionAuthority::new(store.clone());
        let ticket = session.restore().unwrap();
        session.confirm(ticket, &principal).unwrap();
        let service =
            ContextService::new(endpoint.clone(), team.clone(), session, Some(journal(&dir)));
        assert!(service
            .original("first")
            .unwrap()
            .payload
            .text()
            .contains(&m.text));
        assert_eq!(service.page(None, 10).unwrap().0.len(), 3);
    }
    {
        let mut journal = journal(&dir);
        let preview = journal
            .preview_disposal(&principal.scope(), &["second"])
            .unwrap();
        journal
            .dispose(
                &principal.scope(),
                Disposal {
                    token: preview.token,
                    export: ExportChoice::DeclinedAfterWarning,
                    acknowledge_possible_delivery: true,
                    reason: "synthetic leak scan".into(),
                    actor_local: "leak-test".into(),
                },
            )
            .unwrap();
    }

    let scanned = files(&dir);
    assert!(
        scanned.iter().any(|p| p.ends_with("journal.sqlite"))
            && scanned.iter().any(|p| p.starts_with(dir.join("anchors"))),
        "the scan must cover the journal database and anchors"
    );
    assert_no_markers(&dir, &scanned, &m);
    if kept.is_some() {
        // Keep the files for the external scanner.
        std::mem::forget(temp);
    }
}
