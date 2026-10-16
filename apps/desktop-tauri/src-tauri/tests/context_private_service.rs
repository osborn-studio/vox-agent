//! T18b private create/readback and T19 list/search over a mock server
//! (scenarios private_intake_unknown, private_search_isolation). Replies are
//! the committed real-service fixture, with the client's eventId echoed.
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::Value;
use vox_agent_desktop_tauri_lib::context_private::{
    JournalEntry, Phase, PrivateError, PrivateStore, Tag,
};
use vox_agent_desktop_tauri_lib::context_private_service::{
    decode_list_page, ListQuery, PrivateService, PrivateServiceError, PrivateSession, StepResult,
};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;
use zeroize::Zeroizing;

const OWNER: &str = "endpoint|org|feishu-0123456789abcdef0123456789abcdef|1";
const OTHER: &str = "endpoint|org|feishu-fedcba9876543210fedcba9876543210|1";
const RECORD: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";
const STAMP: &str = "2026-10-08T01:02:03.456789+00:00";
const TOKEN: &str = "vox_sso_0123456789abcdefghijklmnopqrstuvwxyzAB";

fn cases() -> Vec<Value> {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../test/fixtures/context-extended-wire-v1.json"
    ))
    .unwrap()
    .replace("<id-1>", RECORD)
    .replace("<timestamp>", STAMP);
    let fixture: Value = serde_json::from_str(&text).unwrap();
    fixture["private"]["http_cases"].as_array().unwrap().clone()
}

fn body(name: &str) -> Value {
    cases().into_iter().find(|c| c["name"] == name).unwrap()["response"]["body"].clone()
}

// ------------------------------------------------------------ fakes

struct Session {
    owner: Mutex<String>,
    current: Mutex<bool>,
    signed_in: Mutex<bool>,
}

impl Session {
    fn new(owner: &str) -> Self {
        Self {
            owner: Mutex::new(owner.into()),
            current: Mutex::new(true),
            signed_in: Mutex::new(true),
        }
    }
    fn switch(&self, owner: &str) {
        *self.owner.lock().unwrap() = owner.into();
    }
}

impl PrivateSession for Session {
    type Lease = String;
    fn current(&self) -> Result<(String, String, Zeroizing<String>), PrivateServiceError> {
        if !*self.signed_in.lock().unwrap() {
            return Err(PrivateServiceError::NotSignedIn);
        }
        let owner = self.owner.lock().unwrap().clone();
        Ok((owner.clone(), owner, Zeroizing::new(TOKEN.into())))
    }
    fn still_current(&self, lease: &String) -> bool {
        *self.current.lock().unwrap() && *self.owner.lock().unwrap() == *lease
    }
}

/// Keeps saved entries like the sealed store, so a reopened journal sees them.
#[derive(Default, Clone)]
struct Store {
    rows: Arc<Mutex<Vec<JournalEntry>>>,
    fail_load: Arc<Mutex<bool>>,
}

impl PrivateStore for Store {
    fn save(&mut self, entry: &JournalEntry) -> Result<(), PrivateError> {
        let mut rows = self.rows.lock().unwrap();
        rows.retain(|r| !(r.owner == entry.owner && r.mutation.key() == entry.mutation.key()));
        rows.push(entry.clone());
        Ok(())
    }
    fn remove(&mut self, owner: &str, key: &str) -> Result<(), PrivateError> {
        self.rows
            .lock()
            .unwrap()
            .retain(|r| !(r.owner == owner && r.mutation.key() == key));
        Ok(())
    }
    fn load(&self, owner: &str) -> Result<Vec<JournalEntry>, PrivateError> {
        if *self.fail_load.lock().unwrap() {
            return Err(PrivateError::StoreUnavailable);
        }
        Ok(self
            .rows
            .lock()
            .unwrap()
            .iter()
            .filter(|r| r.owner == owner)
            .cloned()
            .collect())
    }
}

#[derive(Clone, Debug)]
struct Seen {
    line: String,
    head: String,
    body: String,
}

type Handler = Box<dyn FnMut(&str, &str) -> (u16, String) + Send>;

/// Each request goes to `handler(request line, body)`.
fn server(mut handler: Handler) -> (ContextEndpoint, Arc<Mutex<Vec<Seen>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let record = seen.clone();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { return };
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut data = Vec::new();
            let mut buffer = [0u8; 8192];
            let (line, head, body) = loop {
                let n = stream.read(&mut buffer).unwrap_or(0);
                if n == 0 {
                    break (String::new(), String::new(), String::new());
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
                        break (
                            text.lines().next().unwrap().to_string(),
                            text[..end].to_string(),
                            text[end + 4..].to_string(),
                        );
                    }
                }
            };
            record.lock().unwrap().push(Seen {
                line: line.clone(),
                head,
                body: body.clone(),
            });
            let (status, reply) = handler(&line, &body);
            let _ = write!(
                stream,
                "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}",
                reply.len()
            );
        }
    });
    (ContextEndpoint::parse(&origin).unwrap(), seen)
}

fn with_event(mut value: Value, event: &str) -> String {
    if value.get("eventId").is_some() {
        value["eventId"] = event.into();
    }
    if let Some(source) = value.get_mut("source").filter(|s| s.is_object()) {
        source["eventId"] = event.into();
    }
    value.to_string()
}

fn sent_event(body: &str) -> String {
    serde_json::from_str::<Value>(body).unwrap()["eventId"]
        .as_str()
        .unwrap()
        .to_string()
}

// ------------------------------------------------------------ T18b

#[tokio::test(flavor = "multi_thread")]
async fn create_sends_once_then_shows_the_current_record() {
    let (endpoint, seen) = server(Box::new(|line, body| {
        if line.starts_with("POST /v1/context-private ") {
            (200, with_event(body_of("create"), &sent_event(body)))
        } else if line.starts_with(&format!("GET /v1/context-private/{RECORD} ")) {
            (200, body_of("edit").to_string())
        } else {
            (404, r#"{"detail":"NOT_FOUND"}"#.into())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let key = service
        .create(&session, "synthetic note", vec![Tag::Question], None, None)
        .unwrap();
    assert_eq!(service.phase(&session, &key).unwrap(), Phase::Reserved);

    let step = service.step(&session, &key, false).await.unwrap();
    assert!(matches!(
        step,
        StepResult::Phase(Phase::Applied { revision: 1, .. })
    ));
    // The next step shows the current record (here already edited elsewhere).
    match service.step(&session, &key, false).await.unwrap() {
        StepResult::Current(record) => assert_eq!(record.revision, 2),
        other => panic!("{other:?}"),
    }
    let seen = seen.lock().unwrap().clone();
    assert_eq!(seen.len(), 2, "one POST, one GET");
    let sent: Value = serde_json::from_str(&seen[0].body).unwrap();
    assert_eq!(sent["text"], "synthetic note");
    assert_eq!(sent["tags"], serde_json::json!(["question"]));
    assert!(sent["eventId"].as_str().unwrap().starts_with("pv_"));
    for request in &seen {
        assert!(request
            .head
            .contains(&format!("authorization: Bearer {TOKEN}")));
        assert!(
            !request.head.to_ascii_lowercase().contains("\norigin:"),
            "native client: no Origin"
        );
    }
}

fn body_of(name: &str) -> Value {
    body(name)
}

#[tokio::test(flavor = "multi_thread")]
async fn a_lost_reply_is_read_back_then_resent_as_the_same_request() {
    let posts = Arc::new(Mutex::new(Vec::<String>::new()));
    let record = posts.clone();
    let (endpoint, seen) = server(Box::new(move |line, body| {
        if line.starts_with("POST /v1/context-private ") {
            record.lock().unwrap().push(body.to_string());
            if record.lock().unwrap().len() == 1 {
                (503, r#"{"detail":"PRIVATE_CONTEXT_UNAVAILABLE"}"#.into())
            } else {
                (200, with_event(body_of("create_replay"), &sent_event(body)))
            }
        } else if line.starts_with("GET /v1/context-private-events/pv_") {
            (200, body_of("event_readback_not_observed").to_string())
        } else {
            (404, r#"{"detail":"NOT_FOUND"}"#.into())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let key = service
        .create(&session, "synthetic note", vec![Tag::Question], None, None)
        .unwrap();
    assert_eq!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Unknown("PRIVATE_CONTEXT_UNAVAILABLE".into()))
    );
    // Unknown -> read back; not-observed is not "not executed".
    assert_eq!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Unknown("NOT_OBSERVED".into()))
    );
    // Then the same request again: the server replays.
    assert!(matches!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Applied { .. })
    ));
    let posts = posts.lock().unwrap();
    assert_eq!(posts.len(), 2);
    assert_eq!(posts[0], posts[1], "identical request bytes");
    assert!(seen.lock().unwrap()[1]
        .line
        .starts_with("GET /v1/context-private-events/pv_"));
}

#[tokio::test(flavor = "multi_thread")]
async fn a_blocked_mutation_moves_only_on_an_explicit_retry() {
    let (endpoint, seen) = server(Box::new(|line, body| {
        if line.starts_with("POST /v1/context-private ") {
            (401, body_of("no_auth").to_string())
        } else if line.starts_with("GET /v1/context-private-events/") {
            (200, body_of("event_readback_not_observed").to_string())
        } else {
            let _ = body;
            (404, "{}".into())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let key = service
        .create(&session, "synthetic note", vec![], None, None)
        .unwrap();
    assert_eq!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Blocked("AUTH_REQUIRED".into()))
    );
    assert_eq!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Blocked("AUTH_REQUIRED".into()))
    );
    assert_eq!(
        seen.lock().unwrap().len(),
        1,
        "no automatic request while blocked"
    );
    // The first POST may have been applied: the retry reads back first.
    service.step(&session, &key, true).await.unwrap();
    assert!(seen.lock().unwrap()[1]
        .line
        .starts_with("GET /v1/context-private-events/"));
}

#[tokio::test(flavor = "multi_thread")]
async fn mutations_belong_to_the_signed_in_owner() {
    let (endpoint, seen) = server(Box::new(|_, _| (404, "{}".into())));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let key = service
        .create(&session, "mine", vec![], None, None)
        .unwrap();
    session.switch(OTHER);
    assert_eq!(
        service.phase(&session, &key),
        Err(PrivateServiceError::NotFound)
    );
    assert_eq!(
        service.step(&session, &key, false).await,
        Err(PrivateServiceError::NotFound)
    );
    session.switch(OWNER);
    assert_eq!(service.phase(&session, &key).unwrap(), Phase::Reserved);
    assert!(seen.lock().unwrap().is_empty());

    *session.signed_in.lock().unwrap() = false;
    assert_eq!(
        service.create(&session, "x", vec![], None, None),
        Err(PrivateServiceError::NotSignedIn)
    );
    let unavailable =
        PrivateService::<Store>::new(ContextEndpoint::parse("http://127.0.0.1:9").unwrap(), None);
    assert_eq!(
        unavailable.create(&Session::new(OWNER), "x", vec![], None, None),
        Err(PrivateServiceError::JournalUnavailable)
    );
}

// ------------------------------------------------------------ T19

fn query() -> ListQuery {
    ListQuery {
        start: "2026-10-01T00:00:00+00:00".into(),
        end: "2026-10-15T00:00:00+00:00".into(),
        limit: 20,
        cursor: None,
        tag: None,
        project_id: None,
        text: None,
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn list_and_search_use_the_real_contract_and_check_every_item() {
    let (endpoint, seen) = server(Box::new(|line, _| {
        if line.starts_with("GET /v1/context-private?") {
            (200, body_of("list").to_string())
        } else if line.starts_with("POST /v1/context-private-search ") {
            (200, body_of("search").to_string())
        } else {
            (404, "{}".into())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let mut q = query();
    q.tag = Some(Tag::Question);
    let page = service.list(&session, &q).await.unwrap();
    assert_eq!(page.items.len(), 1);
    assert!(page.next_cursor.is_none() && !page.omitted_records);

    let mut search = query();
    search.text = Some("edit".into());
    let page = service.search_or_list(&session, &search).await;
    assert_eq!(page.unwrap().items[0].revision, 2);
    let seen = seen.lock().unwrap().clone();
    assert!(seen[0]
        .line
        .contains("start=2026-10-01T00%3A00%3A00%2B00%3A00"));
    assert!(seen[0].line.contains("&limit=20&tag=question "));
    let sent: Value = serde_json::from_str(&seen[1].body).unwrap();
    assert_eq!(
        sent,
        serde_json::json!({
            "start": "2026-10-01T00:00:00+00:00", "end": "2026-10-15T00:00:00+00:00", "limit": 20, "q": "edit"
        })
    );
}

trait SearchOrList {
    async fn search_or_list(
        &self,
        session: &Session,
        query: &ListQuery,
    ) -> Result<vox_agent_desktop_tauri_lib::context_private_service::ListPage, PrivateServiceError>;
}

impl SearchOrList for PrivateService<Store> {
    async fn search_or_list(
        &self,
        session: &Session,
        query: &ListQuery,
    ) -> Result<vox_agent_desktop_tauri_lib::context_private_service::ListPage, PrivateServiceError>
    {
        self.list(session, query).await
    }
}

#[test]
fn pages_that_do_not_match_the_query_are_refused_whole() {
    let list = body("list");
    let ok = decode_list_page(list.to_string().as_bytes(), &query()).unwrap();
    assert_eq!(ok.items.len(), 1);
    let mutate = |f: &dyn Fn(&mut Value), q: &ListQuery| {
        let mut v = list.clone();
        f(&mut v);
        decode_list_page(v.to_string().as_bytes(), q)
    };
    let invalid = Err(PrivateServiceError::InvalidResponse);
    assert_eq!(
        mutate(&|v| v["scope"] = "team-context".into(), &query()),
        invalid
    );
    assert_eq!(mutate(&|v| v["hasMore"] = true.into(), &query()), invalid);
    assert_eq!(
        mutate(
            &|v| v["items"][0]["createdAt"] = "2026-09-30T23:59:59+00:00".into(),
            &query()
        ),
        invalid
    );
    assert_eq!(
        mutate(&|v| v["items"][0]["visibility"] = "team".into(), &query()),
        invalid
    );
    let first = list["items"][0].clone();
    assert_eq!(
        mutate(
            &|v| v["items"].as_array_mut().unwrap().push(first.clone()),
            &query()
        ),
        invalid
    );
    let mut tagged = query();
    tagged.tag = Some(Tag::Blocker);
    assert_eq!(mutate(&|_| {}, &tagged), invalid);
    let mut project = query();
    project.project_id = Some("vox".into());
    assert_eq!(mutate(&|_| {}, &project), invalid);
}

#[tokio::test(flavor = "multi_thread")]
async fn bad_queries_never_leave_the_process_and_late_results_are_dropped() {
    let (endpoint, seen) = server(Box::new(|_, _| (200, body_of("list").to_string())));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    for bad in [
        ListQuery {
            end: "2026-11-15T00:00:00+00:00".into(),
            ..query()
        },
        ListQuery {
            end: "2026-10-01T00:00:00+00:00".into(),
            ..query()
        },
        ListQuery {
            start: "2026-10-01".into(),
            ..query()
        },
        ListQuery {
            limit: 51,
            ..query()
        },
        ListQuery {
            cursor: Some("../x".into()),
            ..query()
        },
        ListQuery {
            project_id: Some("a/b".into()),
            ..query()
        },
        ListQuery {
            text: Some("x".repeat(129)),
            ..query()
        },
    ] {
        assert_eq!(
            service.list(&session, &bad).await,
            Err(PrivateServiceError::InvalidInput)
        );
    }
    assert!(seen.lock().unwrap().is_empty());

    // Signed out or switched while the request was in flight: discarded.
    *session.current.lock().unwrap() = false;
    assert_eq!(
        service.list(&session, &query()).await,
        Err(PrivateServiceError::StaleIdentity)
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_record_withdrawn_since_is_not_listed_from_an_older_page() {
    let (endpoint, _) = server(Box::new(|line, _| {
        if line.starts_with(&format!("GET /v1/context-private/{RECORD} ")) {
            (200, body_of("get_after_withdraw").to_string())
        } else {
            (200, body_of("list").to_string())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let current = service.get(&session, RECORD).await.unwrap();
    assert!(current.withdrawn && current.revision == 3);
    let page = service.list(&session, &query()).await.unwrap();
    assert!(
        page.items.is_empty(),
        "an older revision never replaces the withdrawal"
    );
    // Refusals keep their code, never the server's text.
    let (endpoint, _) = server(Box::new(|_, _| {
        (404, body_of("get_other_owner").to_string())
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    assert_eq!(
        service.get(&session, RECORD).await,
        Err(PrivateServiceError::Refused {
            status: 404,
            code: "PRIVATE_CONTEXT_NOT_FOUND".into()
        })
    );
    assert_eq!(
        service.get(&session, "not-a-uuid").await,
        Err(PrivateServiceError::InvalidInput)
    );
}

// ------------------------------------------------------------ audit fixes

#[tokio::test(flavor = "multi_thread")]
async fn a_result_arriving_after_the_session_ended_is_never_returned() {
    let session = Arc::new(Session::new(OWNER));
    let flip = session.clone();
    let (endpoint, _) = server(Box::new(move |line, body| {
        // The session ends while each request is in flight.
        *flip.current.lock().unwrap() = false;
        if line.starts_with("POST /v1/context-private ") {
            (200, with_event(body_of("create"), &sent_event(body)))
        } else if line.starts_with("GET /v1/context-private?") {
            (200, body_of("list").to_string())
        } else {
            (200, body_of("edit").to_string())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    assert_eq!(
        service.get(&*session, RECORD).await,
        Err(PrivateServiceError::StaleIdentity)
    );
    *session.current.lock().unwrap() = true;
    assert_eq!(
        service.list(&*session, &query()).await,
        Err(PrivateServiceError::StaleIdentity)
    );

    *session.current.lock().unwrap() = true;
    let key = service
        .create(&*session, "synthetic note", vec![Tag::Question], None, None)
        .unwrap();
    assert_eq!(
        service.step(&*session, &key, false).await,
        Err(PrivateServiceError::StaleIdentity)
    );
    // What the reply proved was still saved for that owner.
    *session.current.lock().unwrap() = true;
    assert!(matches!(
        service.phase(&*session, &key).unwrap(),
        Phase::Applied { .. }
    ));
}

#[tokio::test(flavor = "multi_thread")]
async fn one_operation_per_mutation_at_a_time() {
    let (endpoint, _) = server(Box::new(|_, body| {
        thread::sleep(Duration::from_millis(300));
        (200, with_event(body_of("create"), &sent_event(body)))
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let key = service
        .create(&session, "synthetic note", vec![Tag::Question], None, None)
        .unwrap();
    let (first, second) = tokio::join!(service.step(&session, &key, false), async {
        tokio::time::sleep(Duration::from_millis(50)).await;
        service.step(&session, &key, false).await
    });
    assert!(first.is_ok());
    assert!(matches!(
        second,
        Err(PrivateServiceError::Busy) | Ok(StepResult::Phase(Phase::Dispatching))
    ));
}

#[tokio::test(flavor = "multi_thread")]
async fn a_failed_journal_reopen_keeps_the_store() {
    let (endpoint, _) = server(Box::new(|_, _| (404, "{}".into())));
    let store = Store::default();
    let service = PrivateService::new(endpoint, Some(store.clone()));
    let session = Session::new(OWNER);
    service
        .create(&session, "mine", vec![], None, None)
        .unwrap();
    *store.fail_load.lock().unwrap() = true;
    session.switch(OTHER);
    assert_eq!(
        service.create(&session, "x", vec![], None, None),
        Err(PrivateServiceError::Journal(PrivateError::StoreUnavailable))
    );
    *store.fail_load.lock().unwrap() = false;
    assert!(service
        .create(&session, "theirs", vec![], None, None)
        .is_ok());
    session.switch(OWNER);
    assert!(service
        .create(&session, "mine again", vec![], None, None)
        .is_ok());
}

#[tokio::test(flavor = "multi_thread")]
async fn after_the_send_limit_readbacks_continue() {
    let (endpoint, seen) = server(Box::new(|line, _| {
        if line.starts_with("POST ") {
            (503, r#"{"detail":"PRIVATE_CONTEXT_UNAVAILABLE"}"#.into())
        } else {
            (200, body_of("event_readback_not_observed").to_string())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let key = service
        .create(&session, "synthetic note", vec![], None, None)
        .unwrap();
    for _ in 0..12 {
        service.step(&session, &key, false).await.unwrap();
    }
    let seen = seen.lock().unwrap();
    let posts = seen.iter().filter(|r| r.line.starts_with("POST ")).count();
    assert_eq!(posts, 5, "never more than the send limit");
    assert!(seen
        .last()
        .unwrap()
        .line
        .starts_with("GET /v1/context-private-events/"));
}

#[tokio::test(flavor = "multi_thread")]
async fn listed_items_are_rechecked_against_newer_revisions_and_the_search_text() {
    let (endpoint, _) = server(Box::new(|line, _| {
        if line.starts_with(&format!("GET /v1/context-private/{RECORD} ")) {
            (200, body_of("edit").to_string()) // revision 2: tags [] now
        } else {
            (200, body_of("list").to_string()) // revision 1: tag question
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    service.get(&session, RECORD).await.unwrap();
    let mut q = query();
    q.tag = Some(Tag::Question);
    let page = service.list(&session, &q).await.unwrap();
    assert!(
        page.items.is_empty() && page.omitted_records,
        "retagged since: left out and said so"
    );

    let mut search = query();
    search.text = Some("nothing like it".into());
    assert_eq!(
        decode_list_page(body("list").to_string().as_bytes(), &search),
        Err(PrivateServiceError::InvalidResponse)
    );
    search.text = Some("SYNTHETIC".into());
    assert!(decode_list_page(body("list").to_string().as_bytes(), &search).is_ok());
}

// ------------------------------------------------------------ T20

use vox_agent_desktop_tauri_lib::context_private_service::{decode_reference_check, UserConfirmed};

#[tokio::test(flavor = "multi_thread")]
async fn an_edit_is_written_against_the_revision_the_user_saw() {
    let (endpoint, seen) = server(Box::new(|line, body| {
        if line.starts_with(&format!("GET /v1/context-private/{RECORD} ")) {
            (200, body_of("get").to_string())
        } else if line.starts_with(&format!("PUT /v1/context-private/{RECORD} ")) {
            let sent: Value = serde_json::from_str(body).unwrap();
            if sent["expectedRevision"] == 1 && sent["text"] == "synthetic edit" {
                (200, body_of("edit").to_string())
            } else {
                (409, body_of("edit_stale_revision").to_string())
            }
        } else {
            (404, "{}".into())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    // Only a record this session has seen can be edited.
    assert_eq!(
        service.edit(&session, RECORD, 1, "synthetic edit", vec![]),
        Err(PrivateServiceError::NotFound)
    );
    service.get(&session, RECORD).await.unwrap();
    assert_eq!(
        service.edit(&session, RECORD, 2, "x", vec![]),
        Err(PrivateServiceError::StaleRevision)
    );
    let key = service
        .edit(&session, RECORD, 1, "synthetic edit", vec![])
        .unwrap();
    assert!(key.starts_with("request:pr_"));
    assert!(matches!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Applied { revision: 2, .. })
    ));
    let put = seen
        .lock()
        .unwrap()
        .iter()
        .find(|r| r.line.starts_with("PUT "))
        .cloned()
        .unwrap();
    let sent: Value = serde_json::from_str(&put.body).unwrap();
    assert_eq!(sent["expectedRevision"], 1);
    assert!(sent["requestId"].as_str().unwrap().starts_with("pr_"));
    // The cache now holds revision 2; an edit from the old view is stale.
    assert_eq!(
        service.edit(&session, RECORD, 1, "again", vec![]),
        Err(PrivateServiceError::StaleRevision)
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_revision_conflict_keeps_the_text_for_recovery() {
    let (endpoint, _) = server(Box::new(|line, _| {
        if line.starts_with("GET ") {
            (200, body_of("get").to_string())
        } else {
            (409, body_of("edit_stale_revision").to_string())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    service.get(&session, RECORD).await.unwrap();
    let key = service
        .edit(
            &session,
            RECORD,
            1,
            "my careful wording",
            vec![Tag::Decision],
        )
        .unwrap();
    assert_eq!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Conflict("PRIVATE_CONTEXT_REVISION_CONFLICT".into()))
    );
    let (text, tags) = service.kept(&session, &key).unwrap();
    assert_eq!(
        (text.as_str(), tags),
        ("my careful wording", vec![Tag::Decision])
    );
    service.dismiss(&session, &key).unwrap();
    assert_eq!(
        service.phase(&session, &key),
        Err(PrivateServiceError::NotFound)
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_withdrawal_needs_the_users_confirmation_for_that_record() {
    let (endpoint, seen) = server(Box::new(|line, _| {
        if line.starts_with("GET ") {
            (200, body_of("edit").to_string())
        } else if line.starts_with(&format!("POST /v1/context-private/{RECORD}/withdraw ")) {
            (200, body_of("withdraw").to_string())
        } else {
            (404, "{}".into())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    service.get(&session, RECORD).await.unwrap();
    let other = UserConfirmed::withdrawal_of("11111111-1111-4111-8111-111111111111");
    assert_eq!(
        service.withdraw(&session, RECORD, 2, other),
        Err(PrivateServiceError::NotAllowed)
    );
    let key = service
        .withdraw(&session, RECORD, 2, UserConfirmed::withdrawal_of(RECORD))
        .unwrap();
    assert!(matches!(
        service.step(&session, &key, false).await.unwrap(),
        StepResult::Phase(Phase::Applied { revision: 3, .. })
    ));
    assert!(seen
        .lock()
        .unwrap()
        .iter()
        .any(|r| r.line.contains("/withdraw ")));
    assert_eq!(
        service.kept(&session, &key),
        Err(PrivateServiceError::NotAllowed)
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn reference_checks_use_the_real_contract() {
    let (endpoint, seen) = server(Box::new(|line, body| {
        assert!(line.starts_with("POST /v1/context-private-reference-check "));
        let sent: Value = serde_json::from_str(body).unwrap();
        if sent["sources"][0]["revision"] == 2 {
            (200, body_of("reference_check_current").to_string())
        } else {
            (200, body_of("reference_check_stale").to_string())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    // The check is a value for exactly these revisions, used up by one copy.
    let current = service
        .reference_check(&session, &[(RECORD.into(), 2)])
        .await
        .unwrap();
    let stale = service
        .reference_check(&session, &[(RECORD.into(), 1)])
        .await
        .unwrap();
    assert_ne!(current, stale);
    let sent: Value = serde_json::from_str(&seen.lock().unwrap()[0].body).unwrap();
    assert_eq!(
        sent,
        serde_json::json!({"sources": [{"id": RECORD, "revision": 2}]})
    );
    for bad in [
        vec![],
        vec![(RECORD.to_string(), 0)],
        vec![(RECORD.to_string(), 1), (RECORD.to_string(), 2)],
    ] {
        assert_eq!(
            service.reference_check(&session, &bad).await,
            Err(PrivateServiceError::InvalidInput)
        );
    }
    let mut odd = body("reference_check_current");
    odd["meaning"] = "complete".into();
    assert!(decode_reference_check(odd.to_string().as_bytes()).is_none());
}

// ------------------------------------------------------------ T22 copy

fn weekly_draft_body(monday: &str) -> Value {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../test/fixtures/context-extended-wire-v1.json"
    ))
    .unwrap()
    .replace("<week-monday>", monday)
    .replace("<timestamp>", STAMP);
    let fixture: Value = serde_json::from_str(&text).unwrap();
    fixture["inputs_weekly"]["http_cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["name"] == "weekly_draft")
        .unwrap()["response"]["body"]
        .clone()
}

#[tokio::test(flavor = "multi_thread")]
async fn copying_a_draft_reads_the_week_and_checks_private_notes_now() {
    use vox_agent_desktop_tauri_lib::context_sources::{decode_draft_page, week, SourceCollector};
    use vox_agent_desktop_tauri_lib::context_weekly_draft::{CopyBlocked, Lang, WeeklyDraft};
    let page = Arc::new(Mutex::new(weekly_draft_body("2026-10-05")));
    let valid = Arc::new(Mutex::new(true));
    let (served, check) = (page.clone(), valid.clone());
    let (endpoint, seen) = server(Box::new(move |line, _| {
        if line.starts_with("GET /v1/context-weekly-draft?week=2026-10-05 ") {
            (200, served.lock().unwrap().to_string())
        } else if line.starts_with("POST /v1/context-private-reference-check ") {
            let body = if *check.lock().unwrap() {
                body("reference_check_current")
            } else {
                body("reference_check_stale")
            };
            (200, body.to_string())
        } else {
            (404, "{}".into())
        }
    }));
    let service = PrivateService::new(endpoint, Some(Store::default()));
    let session = Session::new(OWNER);
    let w = week("2026-10-05").unwrap();
    let mut reviewed = SourceCollector::new(OWNER, w.clone());
    let request = reviewed.next_request().unwrap();
    reviewed.apply(
        &request,
        decode_draft_page(page.lock().unwrap().to_string().as_bytes(), &w),
    );
    let note = vox_agent_desktop_tauri_lib::context_private::decode_record(
        body("edit").to_string().as_bytes(),
    )
    .unwrap();
    let draft = WeeklyDraft::compose(&reviewed, &[note], Lang::En).unwrap();

    let copied = service.copy_draft(&session, &draft).await.unwrap().unwrap();
    assert_eq!(copied.as_str(), draft.text());
    {
        let seen = seen.lock().unwrap();
        assert!(seen[0]
            .line
            .starts_with("GET /v1/context-weekly-draft?week=2026-10-05 "));
        let sent: Value = serde_json::from_str(&seen[1].body).unwrap();
        assert_eq!(
            sent,
            serde_json::json!({"sources": [{"id": RECORD, "revision": 2}]})
        );
    }
    // Every copy reads again: a source changed since review blocks it.
    page.lock().unwrap()["sources"][0]["text"] = "changed elsewhere".into();
    assert_eq!(
        service.copy_draft(&session, &draft).await.unwrap().err(),
        Some(CopyBlocked::TeamChanged)
    );
    page.lock().unwrap()["sources"][0]["text"] = "team note 2".into();
    *valid.lock().unwrap() = false;
    assert_eq!(
        service.copy_draft(&session, &draft).await.unwrap().err(),
        Some(CopyBlocked::PrivateChanged)
    );
    // Another account cannot copy this draft.
    session.switch(OTHER);
    assert_eq!(
        service.copy_draft(&session, &draft).await.err(),
        Some(PrivateServiceError::StaleIdentity)
    );
}

// ------------------------------------------------------------ T23b

#[tokio::test(flavor = "multi_thread")]
async fn clearing_the_view_drops_late_results_but_keeps_saved_mutations() {
    let service = Arc::new(Mutex::new(None::<Arc<PrivateService<Store>>>));
    let clear = service.clone();
    let (endpoint, _) = server(Box::new(move |line, _| {
        // The panel collapses while the request is in flight.
        if let Some(s) = clear.lock().unwrap().as_ref() {
            s.clear_view();
        }
        if line.starts_with("GET /v1/context-private?") {
            (200, body_of("list").to_string())
        } else {
            (200, body_of("get").to_string())
        }
    }));
    let private = Arc::new(PrivateService::new(endpoint, Some(Store::default())));
    let session = Session::new(OWNER);
    let key = private
        .create(&session, "unsent note", vec![], None, None)
        .unwrap();
    *service.lock().unwrap() = Some(private.clone());
    assert_eq!(
        private.get(&session, RECORD).await,
        Err(PrivateServiceError::ViewCleared)
    );
    assert_eq!(
        private.list(&session, &query()).await,
        Err(PrivateServiceError::ViewCleared)
    );
    // The cleared cache does not know the record: an edit needs it shown again.
    assert_eq!(
        private.edit(&session, RECORD, 1, "x", vec![]),
        Err(PrivateServiceError::NotFound)
    );
    // The unsent mutation is still saved.
    assert_eq!(private.phase(&session, &key).unwrap(), Phase::Reserved);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_clear_at_any_point_of_a_copy_or_refresh_cancels_it() {
    use vox_agent_desktop_tauri_lib::context_sources::{decode_draft_page, week, SourceCollector};
    use vox_agent_desktop_tauri_lib::context_weekly_draft::{Lang, WeeklyDraft};
    let holder = Arc::new(Mutex::new(None::<Arc<PrivateService<Store>>>));
    let clear_on = Arc::new(Mutex::new(String::new()));
    let (h, c) = (holder.clone(), clear_on.clone());
    let (endpoint, _) = server(Box::new(move |line, body| {
        if !c.lock().unwrap().is_empty() && line.starts_with(c.lock().unwrap().as_str()) {
            if let Some(s) = h.lock().unwrap().as_ref() {
                s.clear_view();
            }
        }
        if line.starts_with("GET /v1/context-weekly-draft") {
            (200, weekly_draft_body("2026-10-05").to_string())
        } else if line.starts_with("POST /v1/context-private-reference-check ") {
            (200, body_of("reference_check_current").to_string())
        } else if line.starts_with("POST /v1/context-private ") {
            (200, with_event(body_of("create"), &sent_event(body)))
        } else {
            (200, body_of("get").to_string())
        }
    }));
    let service = Arc::new(PrivateService::new(endpoint, Some(Store::default())));
    *holder.lock().unwrap() = Some(service.clone());
    let session = Session::new(OWNER);
    let w = week("2026-10-05").unwrap();
    let mut reviewed = SourceCollector::new(OWNER, w.clone());
    let request = reviewed.next_request().unwrap();
    reviewed.apply(
        &request,
        decode_draft_page(weekly_draft_body("2026-10-05").to_string().as_bytes(), &w),
    );
    let note = vox_agent_desktop_tauri_lib::context_private::decode_record(
        body("edit").to_string().as_bytes(),
    )
    .unwrap();
    let draft = WeeklyDraft::compose(&reviewed, &[note], Lang::En).unwrap();

    // Cleared while the team pages are re-read: the later reference check
    // must not revive the copy.
    *clear_on.lock().unwrap() = "GET /v1/context-weekly-draft".into();
    assert_eq!(
        service.copy_draft(&session, &draft).await.err(),
        Some(PrivateServiceError::ViewCleared)
    );
    *clear_on.lock().unwrap() = "POST /v1/context-private-reference-check".into();
    assert_eq!(
        service.copy_draft(&session, &draft).await.err(),
        Some(PrivateServiceError::ViewCleared)
    );
    *clear_on.lock().unwrap() = String::new();
    assert!(service.copy_draft(&session, &draft).await.unwrap().is_ok());

    // A send whose reply arrives after a clear still records its proof; the
    // following refresh, cleared again, shows nothing.
    let key = service
        .create(&session, "synthetic note", vec![Tag::Question], None, None)
        .unwrap();
    *clear_on.lock().unwrap() = "POST /v1/context-private ".into();
    assert_eq!(
        service.step(&session, &key, false).await,
        Err(PrivateServiceError::ViewCleared)
    );
    assert!(matches!(
        service.phase(&session, &key).unwrap(),
        Phase::Applied { .. }
    ));
    *clear_on.lock().unwrap() = format!("GET /v1/context-private/{RECORD}");
    assert_eq!(
        service.step(&session, &key, false).await,
        Err(PrivateServiceError::ViewCleared)
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_team_only_draft_copies_after_a_fresh_read_without_a_private_journal() {
    use vox_agent_desktop_tauri_lib::context_private_service::copy_team_draft;
    use vox_agent_desktop_tauri_lib::context_sources::{decode_draft_page, week, SourceCollector};
    use vox_agent_desktop_tauri_lib::context_weekly_draft::{CopyBlocked, Lang, WeeklyDraft};
    let page = Arc::new(Mutex::new(weekly_draft_body("2026-10-05")));
    let served = page.clone();
    let (endpoint, _) = server(Box::new(move |line, _| {
        assert!(
            line.starts_with("GET /v1/context-weekly-draft?week=2026-10-05 "),
            "{line}"
        );
        (200, served.lock().unwrap().to_string())
    }));
    let session = Session::new(OWNER);
    let w = week("2026-10-05").unwrap();
    let mut reviewed = SourceCollector::new(OWNER, w.clone());
    let request = reviewed.next_request().unwrap();
    reviewed.apply(
        &request,
        decode_draft_page(page.lock().unwrap().to_string().as_bytes(), &w),
    );
    let draft = WeeklyDraft::compose(&reviewed, &[], Lang::Zh).unwrap();
    assert_eq!(
        copy_team_draft(&endpoint, &session, &draft)
            .await
            .unwrap()
            .unwrap()
            .as_str(),
        draft.text()
    );
    page.lock().unwrap()["sources"][0]["text"] = "changed".into();
    assert_eq!(
        copy_team_draft(&endpoint, &session, &draft)
            .await
            .unwrap()
            .err(),
        Some(CopyBlocked::TeamChanged)
    );
    session.switch(OTHER);
    assert_eq!(
        copy_team_draft(&endpoint, &session, &draft).await.err(),
        Some(PrivateServiceError::StaleIdentity)
    );
    // A draft with private picks must go through the private check.
    session.switch(OWNER);
    let note = vox_agent_desktop_tauri_lib::context_private::decode_record(
        body("edit").to_string().as_bytes(),
    )
    .unwrap();
    let with_pick = WeeklyDraft::compose(&reviewed, &[note], Lang::En).unwrap();
    assert_eq!(
        copy_team_draft(&endpoint, &session, &with_pick).await.err(),
        Some(PrivateServiceError::NotAllowed)
    );
}
