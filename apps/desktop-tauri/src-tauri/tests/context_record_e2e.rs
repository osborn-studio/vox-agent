//! T10: record -> encrypted journal -> mock cloud -> verified receipt ->
//! restart recovery, through `ContextService` only.
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use vox_agent_desktop_tauri_lib::context_delivery::{BackupPayload, DeliveryState};
use vox_agent_desktop_tauri_lib::context_journal::{
    DispatchPhase, FileAnchorStore, Journal, RecoveryAction, ReserveOutcome,
};
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::KeyRing;
use vox_agent_desktop_tauri_lib::context_policy::{Next, Stop, UserAction};
use vox_agent_desktop_tauri_lib::context_service::{ContextService, ServiceError};
use vox_agent_desktop_tauri_lib::context_session::{
    DestinationKey, PrincipalKey, SessionAuthority, TokenStore,
};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;
use vox_agent_desktop_tauri_lib::secrets::SecretError;

const TOKEN: &str = "synthetic-token-1";

// ---------- mock cloud ----------

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    /// Read the POST and close without answering (lost response).
    DropPost,
    /// Answer every request only after this many milliseconds.
    Delay(u64),
}

#[derive(Default)]
struct Cloud {
    records: HashMap<String, (String, u32)>, // eventId -> (body, readbacks)
    accepted_posts: u32,
    raw_posts: u32,
    mode: Option<Mode>,
}

struct MockCloud {
    addr: SocketAddr,
    state: Arc<Mutex<Cloud>>,
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
                let head = text[..end].to_string();
                let body = text[end + 4..].to_string();
                let request_line = head.lines().next().unwrap_or("").to_string();
                return (request_line, head, body);
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

fn event_of(body: &str) -> String {
    let value: serde_json::Value = serde_json::from_str(body).unwrap();
    value["eventId"].as_str().unwrap().to_string()
}

impl MockCloud {
    fn start_on(listener: TcpListener) -> Self {
        let addr = listener.local_addr().unwrap();
        let state = Arc::new(Mutex::new(Cloud::default()));
        let shared = state.clone();
        thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { return };
                let (line, head, body) = read_request(&mut stream);
                let delay = match shared.lock().unwrap().mode {
                    Some(Mode::Delay(ms)) => ms,
                    _ => 0,
                };
                thread::sleep(Duration::from_millis(delay));
                let mut cloud = shared.lock().unwrap();
                if !head
                    .lines()
                    .any(|l| l.eq_ignore_ascii_case(&format!("authorization: Bearer {TOKEN}")))
                {
                    respond(
                        &mut stream,
                        "401 Unauthorized",
                        r#"{"detail":{"code":"INVALID_CREDENTIAL","message":"x"}}"#,
                    );
                    continue;
                }
                if line.starts_with("POST /v1/input-backups ") {
                    cloud.raw_posts += 1;
                    if cloud.mode == Some(Mode::DropPost) {
                        let event = event_of(&body);
                        cloud.records.entry(event).or_insert((body, 0));
                        continue; // accepted, but the response is lost
                    }
                    let event = event_of(&body);
                    match cloud.records.get(&event) {
                        Some((stored, _)) if *stored != body => {
                            respond(
                                &mut stream,
                                "409 Conflict",
                                r#"{"detail":"EVENT_PAYLOAD_CONFLICT"}"#,
                            );
                        }
                        Some(_) => respond(
                            &mut stream,
                            "202 Accepted",
                            &format!(
                                r#"{{"eventId":"{event}","status":"pending","messageId":null}}"#
                            ),
                        ),
                        None => {
                            cloud.accepted_posts += 1;
                            cloud.records.insert(event.clone(), (body, 0));
                            respond(
                                &mut stream,
                                "202 Accepted",
                                &format!(
                                    r#"{{"eventId":"{event}","status":"pending","messageId":null}}"#
                                ),
                            );
                        }
                    }
                } else if line.starts_with("GET /v1/context-projects ") {
                    respond(
                        &mut stream,
                        "200 OK",
                        r#"{"version":1,"projects":[{"id":"p1","name":"Studio","repositoryId":"studio/repo"},{"id":"bad id","name":"x","repositoryId":"a/b"}]}"#,
                    );
                } else if let Some(rest) = line.strip_prefix("GET /v1/input-backups/") {
                    let event = rest.split(' ').next().unwrap().to_string();
                    match cloud.records.get_mut(&event) {
                        None => respond(
                            &mut stream,
                            "404 Not Found",
                            r#"{"detail":"BACKUP_NOT_FOUND"}"#,
                        ),
                        Some((_, readbacks)) => {
                            *readbacks += 1;
                            let body = if *readbacks >= 2 {
                                format!(
                                    r#"{{"eventId":"{event}","status":"delivered","messageId":"om_mock"}}"#
                                )
                            } else {
                                format!(
                                    r#"{{"eventId":"{event}","status":"sending","messageId":null}}"#
                                )
                            };
                            respond(&mut stream, "200 OK", &body);
                        }
                    }
                } else {
                    respond(&mut stream, "404 Not Found", r#"{"detail":"NOT_FOUND"}"#);
                }
            }
        });
        Self { addr, state }
    }

    fn start() -> Self {
        Self::start_on(TcpListener::bind("127.0.0.1:0").unwrap())
    }

    fn endpoint(&self) -> String {
        format!("http://{}", self.addr)
    }

    fn set_mode(&self, mode: Mode) {
        self.state.lock().unwrap().mode = Some(mode);
    }

    fn accepted_posts(&self) -> u32 {
        self.state.lock().unwrap().accepted_posts
    }

    fn raw_posts(&self) -> u32 {
        self.state.lock().unwrap().raw_posts
    }
}

// ---------- client side ----------

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

struct Dirs {
    _root: tempfile::TempDir,
    root: PathBuf,
}

fn dirs() -> Dirs {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().to_path_buf();
    Dirs {
        _root: root,
        root: path,
    }
}

fn journal(root: &Path) -> Journal {
    let ring = KeyRing::new(&root.join("keys"), FakeKeyProvider::new([7; 32]));
    Journal::open(
        &root.join("journal"),
        &ring,
        Box::new(FileAnchorStore::new(&root.join("anchors"))),
    )
    .unwrap()
    .into_journal()
}

fn principal(endpoint: &ContextEndpoint, actor: &str) -> PrincipalKey {
    PrincipalKey::new(endpoint, "default-org", actor, 1).unwrap()
}

fn service(
    cloud_endpoint: &str,
    root: &Path,
    store: &MemoryStore,
    signed_in: bool,
) -> ContextService<MemoryStore> {
    let endpoint = ContextEndpoint::parse(cloud_endpoint).unwrap();
    let mut session = SessionAuthority::new(store.clone());
    if signed_in {
        session
            .sign_in(principal(&endpoint, "alice"), TOKEN.into())
            .unwrap();
    }
    ContextService::new(endpoint, team(), session, Some(journal(root)))
}

fn team() -> Vec<DestinationKey> {
    vec![DestinationKey::new("team").unwrap()]
}

fn payload(event: &str) -> BackupPayload {
    BackupPayload::new(
        event.into(),
        "team".into(),
        "synthetic context".into(),
        None,
    )
    .unwrap()
}

fn rt() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}

/// Drive a record until it stops or needs the user, without real sleeps.
fn drive(service: &ContextService<MemoryStore>, event: &str) -> Next {
    let rt = rt();
    for _ in 0..10 {
        let next = rt.block_on(service.step(event)).unwrap();
        if !matches!(next, Next::PollAfter(_) | Next::ResendAfter(_)) {
            return next;
        }
    }
    panic!("record did not settle");
}

#[test]
fn a_record_is_reserved_posted_once_and_confirmed_delivered() {
    let cloud = MockCloud::start();
    let d = dirs();
    let store = MemoryStore::default();
    let service = service(&cloud.endpoint(), &d.root, &store, true);
    assert_eq!(
        service.reserve(&payload("event-1")).unwrap(),
        ReserveOutcome::Reserved
    );
    let first = rt().block_on(service.step("event-1")).unwrap();
    assert!(matches!(first, Next::PollAfter(_)));
    assert_eq!(
        service.status("event-1").unwrap().server_state,
        Some(DeliveryState::Pending)
    );
    assert_eq!(drive(&service, "event-1"), Next::Stop(Stop::Final));
    let status = service.status("event-1").unwrap();
    assert_eq!(
        (status.server_state, status.action),
        (Some(DeliveryState::Delivered), RecoveryAction::Final)
    );
    assert_eq!((cloud.raw_posts(), cloud.accepted_posts()), (1, 1));
}

#[test]
fn a_lost_post_response_is_only_ever_read_back() {
    let cloud = MockCloud::start();
    cloud.set_mode(Mode::DropPost);
    let d = dirs();
    let store = MemoryStore::default();
    let service = service(&cloud.endpoint(), &d.root, &store, true);
    service.reserve(&payload("event-1")).unwrap();
    let next = rt().block_on(service.step("event-1")).unwrap();
    assert!(
        matches!(next, Next::PollAfter(_)),
        "post-dispatch: read back only"
    );
    assert_eq!(
        service.status("event-1").unwrap().action,
        RecoveryAction::ReadBackOnly
    );
    assert_eq!(drive(&service, "event-1"), Next::Stop(Stop::Final));
    assert_eq!(cloud.raw_posts(), 1, "never a second POST");
}

#[test]
fn an_unreachable_cloud_allows_a_resend_of_the_same_record() {
    let placeholder = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = placeholder.local_addr().unwrap();
    drop(placeholder); // nothing listens: connections are refused
    let d = dirs();
    let store = MemoryStore::default();
    let service = service(&format!("http://{addr}"), &d.root, &store, true);
    service.reserve(&payload("event-1")).unwrap();
    let next = rt().block_on(service.step("event-1")).unwrap();
    assert!(matches!(next, Next::ResendAfter(_)));
    assert_eq!(
        service.status("event-1").unwrap().phase,
        DispatchPhase::Reserved
    );
    let cloud = MockCloud::start_on(TcpListener::bind(addr).unwrap());
    assert_eq!(drive(&service, "event-1"), Next::Stop(Stop::Final));
    assert_eq!(cloud.accepted_posts(), 1);
}

#[test]
fn after_a_restart_mid_flight_the_record_is_recovered_without_a_second_post() {
    let cloud = MockCloud::start();
    cloud.set_mode(Mode::DropPost);
    let d = dirs();
    let store = MemoryStore::default();
    {
        let service = service(&cloud.endpoint(), &d.root, &store, true);
        service.reserve(&payload("event-1")).unwrap();
        rt().block_on(service.step("event-1")).unwrap();
    } // process ends; journal and session store survive
    let endpoint = ContextEndpoint::parse(&cloud.endpoint()).unwrap();
    let mut session = SessionAuthority::new(store.clone());
    let ticket = session.restore().unwrap();
    let restarted = ContextService::new(endpoint.clone(), team(), session, Some(journal(&d.root)));
    // Restored but unverified: nothing is dispatched or read back yet.
    assert_eq!(
        rt().block_on(restarted.step("event-1")).err(),
        Some(ServiceError::Unverified)
    );
    restarted
        .with_session(|s| s.confirm(ticket, &principal(&endpoint, "alice")))
        .unwrap();
    assert_eq!(
        restarted.list().unwrap(),
        vec![("event-1".to_string(), RecoveryAction::ReadBackOnly)]
    );
    assert_eq!(drive(&restarted, "event-1"), Next::Stop(Stop::Final));
    assert_eq!(cloud.raw_posts(), 1);
}

#[test]
fn records_are_isolated_per_principal_and_need_a_session() {
    let cloud = MockCloud::start();
    let d = dirs();
    let store = MemoryStore::default();
    let signed_out = service(&cloud.endpoint(), &d.root, &store, false);
    assert_eq!(
        signed_out.reserve(&payload("event-1")).err(),
        Some(ServiceError::NotSignedIn)
    );
    drop(signed_out);
    let service = service(&cloud.endpoint(), &d.root, &store, true);
    service.reserve(&payload("event-1")).unwrap();
    let endpoint = ContextEndpoint::parse(&cloud.endpoint()).unwrap();
    service
        .with_session(|s| {
            s.sign_out().unwrap();
            s.sign_in(principal(&endpoint, "bob"), TOKEN.into())
        })
        .unwrap();
    assert!(
        service.list().unwrap().is_empty(),
        "bob cannot see alice's records"
    );
    assert_eq!(
        rt().block_on(service.step("event-1")).err(),
        Some(ServiceError::NotFound)
    );
}

#[test]
fn a_session_for_another_endpoint_is_refused() {
    let cloud = MockCloud::start();
    let d = dirs();
    let store = MemoryStore::default();
    let endpoint = ContextEndpoint::parse(&cloud.endpoint()).unwrap();
    let other = ContextEndpoint::parse("https://other.example.test").unwrap();
    let mut session = SessionAuthority::new(store);
    session
        .sign_in(principal(&other, "alice"), TOKEN.into())
        .unwrap();
    let service = ContextService::new(endpoint, team(), session, Some(journal(&d.root)));
    assert_eq!(
        service.reserve(&payload("event-1")).err(),
        Some(ServiceError::WrongEndpoint)
    );
}

#[test]
fn a_rejected_token_returns_the_record_to_reserved_and_asks_for_sign_in() {
    let cloud = MockCloud::start();
    let d = dirs();
    let store = MemoryStore::default();
    let endpoint = ContextEndpoint::parse(&cloud.endpoint()).unwrap();
    let mut session = SessionAuthority::new(store);
    session
        .sign_in(principal(&endpoint, "alice"), "wrong-token".into())
        .unwrap();
    let service = ContextService::new(endpoint, team(), session, Some(journal(&d.root)));
    service.reserve(&payload("event-1")).unwrap();
    assert_eq!(
        rt().block_on(service.step("event-1")).unwrap(),
        Next::WaitForUser(UserAction::SignIn)
    );
    assert_eq!(
        service.status("event-1").unwrap().phase,
        DispatchPhase::Reserved
    );
}

#[test]
fn without_a_journal_nothing_is_accepted() {
    let endpoint = ContextEndpoint::parse("http://127.0.0.1:9").unwrap();
    let mut session = SessionAuthority::new(MemoryStore::default());
    session
        .sign_in(principal(&endpoint, "alice"), TOKEN.into())
        .unwrap();
    let service = ContextService::new(endpoint, team(), session, None);
    assert_eq!(
        service.reserve(&payload("event-1")).err(),
        Some(ServiceError::JournalUnavailable)
    );
}

#[test]
fn a_failure_while_starting_a_dispatch_never_deadlocks_the_service() {
    let cloud = MockCloud::start();
    let d = dirs();
    let store = MemoryStore::default();
    let service = Arc::new(service(&cloud.endpoint(), &d.root, &store, true));
    service.reserve(&payload("event-1")).unwrap();
    // Make the anchor store unwritable: begin_dispatch fails after its commit.
    let anchors = d.root.join("anchors");
    std::fs::remove_dir_all(&anchors).unwrap();
    std::fs::write(&anchors, b"not a directory").unwrap();
    let error = rt().block_on(service.step("event-1")).unwrap_err();
    assert!(matches!(error, ServiceError::Journal(_)), "{error:?}");
    let probe = service.clone();
    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || tx.send(probe.status("event-1").is_ok()).unwrap());
    assert_eq!(
        rx.recv_timeout(Duration::from_secs(5)),
        Ok(true),
        "service must stay responsive"
    );
    assert_eq!(
        cloud.raw_posts(),
        0,
        "nothing is sent when dispatch could not be anchored"
    );
}

#[test]
fn a_response_that_arrives_after_sign_out_and_sign_in_as_the_same_user_is_discarded() {
    let cloud = MockCloud::start();
    cloud.set_mode(Mode::Delay(400));
    let d = dirs();
    let store = MemoryStore::default();
    let service = Arc::new(service(&cloud.endpoint(), &d.root, &store, true));
    service.reserve(&payload("event-1")).unwrap();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap();
    let worker = service.clone();
    let pending = runtime.spawn(async move { worker.step("event-1").await });
    thread::sleep(Duration::from_millis(150));
    let endpoint = ContextEndpoint::parse(&cloud.endpoint()).unwrap();
    service
        .with_session(|s| {
            s.sign_out().unwrap();
            s.sign_in(principal(&endpoint, "alice"), TOKEN.into())
        })
        .unwrap();
    let result = runtime.block_on(pending).unwrap();
    assert!(
        matches!(result, Err(ServiceError::Session(_))),
        "late response must be discarded: {result:?}"
    );
    let status = service.status("event-1").unwrap();
    assert_eq!(
        (status.phase, status.server_state),
        (DispatchPhase::Dispatching, None)
    );
    assert_eq!(status.action, RecoveryAction::ReadBackOnly);
}

#[test]
fn only_configured_destinations_are_accepted() {
    let cloud = MockCloud::start();
    let d = dirs();
    let service = service(&cloud.endpoint(), &d.root, &MemoryStore::default(), true);
    let other =
        BackupPayload::new("event-1".into(), "other-team".into(), "x".into(), None).unwrap();
    assert_eq!(
        service.reserve(&other).err(),
        Some(ServiceError::DestinationNotAllowed)
    );
}

#[test]
fn a_user_check_reads_back_but_never_posts() {
    let cloud = MockCloud::start();
    let d = dirs();
    let service = service(&cloud.endpoint(), &d.root, &MemoryStore::default(), true);
    service.reserve(&payload("event-1")).unwrap();
    // Never sent: a check does nothing.
    assert_eq!(rt().block_on(service.check("event-1")).unwrap(), None);
    assert_eq!(cloud.raw_posts(), 0);
    cloud.set_mode(Mode::DropPost);
    rt().block_on(service.step("event-1")).unwrap();
    assert_eq!(cloud.raw_posts(), 1);
    assert!(rt().block_on(service.check("event-1")).unwrap().is_some());
    assert_eq!(cloud.raw_posts(), 1, "a check only reads back");
}

#[test]
fn the_project_catalog_is_fetched_verified_and_needs_a_session() {
    let cloud = MockCloud::start();
    let d = dirs();
    let service = service(&cloud.endpoint(), &d.root, &MemoryStore::default(), true);
    let catalog = rt().block_on(service.projects()).unwrap();
    assert_eq!(catalog.projects.len(), 1);
    assert_eq!(catalog.projects[0].snapshot.repository(), "studio/repo");
    assert_eq!(catalog.omitted, 1);
    let other = dirs();
    let signed_out = self::service(
        &cloud.endpoint(),
        &other.root,
        &MemoryStore::default(),
        false,
    );
    assert_eq!(
        rt().block_on(signed_out.projects()).err(),
        Some(ServiceError::NotSignedIn)
    );
}

#[test]
fn records_page_through_the_service_and_originals_come_back_verified() {
    let cloud = MockCloud::start();
    let d = dirs();
    let service = service(&cloud.endpoint(), &d.root, &MemoryStore::default(), true);
    for i in 0..3 {
        service.reserve(&payload(&format!("event-{i}"))).unwrap();
    }
    let (first, cursor) = service.page(None, 2).unwrap();
    let (rest, end) = service.page(cursor.as_deref(), 2).unwrap();
    assert_eq!(first.len() + rest.len(), 3);
    assert_eq!(end, None);
    let original = service.original("event-1").unwrap();
    assert_eq!(original.payload.text(), "synthetic context");
    assert!(matches!(
        service.original("missing"),
        Err(ServiceError::Journal(_))
    ));
}
