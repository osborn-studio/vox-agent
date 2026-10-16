//! T21: weekly report source coverage (scenario weekly_source_coverage).
//! Draft pages come from the committed real-service fixture
//! (scripts/context_extended_fixture.py, inputs_weekly section).
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::Value;
use vox_agent_desktop_tauri_lib::context_sources::{
    decode_draft_page, fetch_draft_page, week, CollectEvent, CoverageStatement, DraftPage, Recheck,
    SourceCollector, SourceError, MAX_PAGES,
};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;

const OWNER: &str = "https://context.example|org|feishu-0123456789abcdef0123456789abcdef|1";
const OTHER: &str = "https://context.example|org|feishu-fedcba9876543210fedcba9876543210|1";
const MONDAY: &str = "2026-10-05";
const INSIDE: &str = "2026-10-06T01:02:03.456789+00:00";
const CURSOR: &str = "WzE3NjAwMDAwMDAuMCwgImlkIl0";

fn draft() -> Value {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../test/fixtures/context-extended-wire-v1.json"
    ))
    .unwrap()
    .replace("<week-monday>", MONDAY)
    .replace("<timestamp>", INSIDE);
    let fixture: Value = serde_json::from_str(&text).unwrap();
    fixture["inputs_weekly"]["http_cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["name"] == "weekly_draft")
        .unwrap()["response"]["body"]
        .clone()
}

type Mutation = Box<dyn Fn(&mut Value)>;

fn bytes(value: &Value) -> Vec<u8> {
    serde_json::to_vec(value).unwrap()
}

/// The fixture draft split into pages of the given sizes, chained by cursors.
fn pages(sizes: &[usize]) -> Vec<Value> {
    let full = draft();
    let sources = full["sources"].as_array().unwrap().clone();
    let mut start = 0;
    sizes
        .iter()
        .enumerate()
        .map(|(i, size)| {
            let mut page = full.clone();
            page["sources"] = Value::Array(sources[start..start + size].to_vec());
            start += size;
            let last = i + 1 == sizes.len();
            page["hasMore"] = (!last).into();
            page["nextCursor"] = if last {
                Value::Null
            } else {
                format!("{CURSOR}{i}").into()
            };
            page
        })
        .collect()
}

fn collect(collector: &mut SourceCollector, pages: &[Value]) {
    let w = collector.week().clone();
    for page in pages {
        let request = collector.next_request().unwrap();
        let event = collector.apply(&request, decode_draft_page(&bytes(page), &w));
        assert!(matches!(event, CollectEvent::Appended(_)), "{event:?}");
    }
}

#[test]
fn weeks_are_monday_to_monday_in_asia_shanghai() {
    let w = week(MONDAY).unwrap();
    // 2026-10-05 00:00 +08:00 is 2026-10-04 16:00 UTC.
    assert_eq!(w.start_micros, 1_791_129_600 * 1_000_000);
    assert_eq!(w.end_micros - w.start_micros, 7 * 86_400 * 1_000_000);
    assert_eq!(week("2026-10-06"), Err(SourceError::NotMonday));
    assert_eq!(week("2026-02-30"), Err(SourceError::InvalidWeek));
    assert_eq!(week("2026-1-05"), Err(SourceError::InvalidWeek));
    assert!(week("2024-02-26").is_ok() && week("2000-01-03").is_ok());
    // Before 1992 Asia/Shanghai had DST and other offsets; the server uses the
    // IANA zone, so those weeks are refused rather than computed differently.
    assert_eq!(week("1991-09-09"), Err(SourceError::InvalidWeek));
    assert_eq!(week("1900-01-01"), Err(SourceError::InvalidWeek));
    assert!(week("1992-01-06").is_ok());

    let at = |stamp: &str| {
        let mut page = draft();
        page["sources"] = Value::Array(vec![page["sources"][0].clone()]);
        page["sources"][0]["submittedAt"] = stamp.into();
        decode_draft_page(&bytes(&page), &w)
    };
    assert!(at("2026-10-04T16:00:00+00:00").is_ok());
    assert!(at("2026-10-11T15:59:59.999999+00:00").is_ok());
    assert_eq!(
        at("2026-10-04T15:59:59.999999+00:00"),
        Err(SourceError::InvalidResponse)
    );
    assert_eq!(
        at("2026-10-11T16:00:00+00:00"),
        Err(SourceError::InvalidResponse)
    );
    assert_eq!(at("2026-10-06T01:02:03"), Err(SourceError::InvalidResponse));
}

#[test]
fn the_real_service_draft_decodes_and_its_contract_is_enforced() {
    let w = week(MONDAY).unwrap();
    let page = decode_draft_page(&bytes(&draft()), &w).unwrap();
    assert_eq!(page.sources.len(), 3);
    assert!(page.next_cursor.is_none() && !page.omitted_records);
    assert!(!format!("{:?}", page.sources[0]).contains("team note"));

    let bad: Vec<Mutation> = vec![
        Box::new(|v| v["week"] = "2026-10-12".into()),
        Box::new(|v| v["timezone"] = "UTC".into()),
        Box::new(|v| v["kind"] = "weekly-report".into()),
        Box::new(|v| v["requiresUserReview"] = false.into()),
        Box::new(|v| v["coverage"] = "complete-week".into()),
        Box::new(|v| v["hasMore"] = true.into()),
        Box::new(|v| v["sources"][0]["evidenceKind"] = "verified-work".into()),
        Box::new(|v| v["sources"][0]["shareIntent"] = "private".into()),
        Box::new(|v| v["sources"][0]["deliveryStatus"] = "done".into()),
        Box::new(|v| v["sources"][1]["eventId"] = "team-2".into()),
        Box::new(|v| {
            v["sources"][0]["attribution"] =
                serde_json::json!({"projectId": "p", "repositoryId": "../x", "source": "manual"})
        }),
    ];
    for mutate in bad {
        let mut page = draft();
        mutate(&mut page);
        assert_eq!(
            decode_draft_page(&bytes(&page), &w),
            Err(SourceError::InvalidResponse)
        );
    }
    let mut attributed = draft();
    attributed["sources"][0]["attribution"] =
        serde_json::json!({"projectId": "vox", "repositoryId": "studio/vox", "source": "manual"});
    let page = decode_draft_page(&bytes(&attributed), &w).unwrap();
    assert_eq!(page.sources[0].repository_id.as_deref(), Some("studio/vox"));
}

#[test]
fn coverage_is_stated_never_inflated() {
    let mut collector = SourceCollector::new(OWNER, week(MONDAY).unwrap());
    assert_eq!(collector.coverage().statement, CoverageStatement::Partial);
    let chain = pages(&[2, 1]);
    collect(&mut collector, &chain[..1]);
    let coverage = collector.coverage();
    assert_eq!(
        (coverage.statement, coverage.pages, coverage.sources),
        (CoverageStatement::Partial, 1, 2)
    );
    collect(&mut collector, &chain[1..]);
    let coverage = collector.coverage();
    assert_eq!(
        coverage.statement,
        CoverageStatement::AllPagesAuthorizedBackupsOnly
    );
    assert_eq!((coverage.timezone, coverage.sources), ("Asia/Shanghai", 3));
    assert!(collector.next_request().is_none());

    // An omission flag on any page holds for the whole collection.
    let mut collector = SourceCollector::new(OWNER, week(MONDAY).unwrap());
    let mut chain = pages(&[1, 1, 1]);
    chain[1]["omittedRecords"] = true.into();
    collect(&mut collector, &chain);
    assert_eq!(
        collector.coverage().statement,
        CoverageStatement::AllPagesWithOmissions
    );
    assert!(collector.coverage().omitted_records);
}

#[test]
fn the_page_cap_marks_the_result_truncated() {
    let mut collector = SourceCollector::new(OWNER, week(MONDAY).unwrap());
    let w = collector.week().clone();
    for i in 0..MAX_PAGES {
        let mut page = draft();
        page["sources"] = Value::Array(vec![]);
        page["hasMore"] = true.into();
        page["nextCursor"] = format!("{CURSOR}x{i}").into();
        let request = collector.next_request().unwrap();
        collector.apply(&request, decode_draft_page(&bytes(&page), &w));
    }
    assert!(collector.next_request().is_none());
    let coverage = collector.coverage();
    assert!(coverage.truncated);
    assert_eq!(coverage.statement, CoverageStatement::Partial);
}

#[test]
fn repeats_bad_cursors_and_late_pages_never_mix_collections() {
    let w = week(MONDAY).unwrap();
    let chain = pages(&[2, 1]);
    let mut collector = SourceCollector::new(OWNER, w.clone());
    collect(&mut collector, &chain[..1]);
    // The same source again on the next page: the listing moved; start over.
    let request = collector.next_request().unwrap();
    assert_eq!(
        collector.apply(&request, decode_draft_page(&bytes(&chain[0]), &w)),
        CollectEvent::Restarted
    );
    assert_eq!(collector.coverage().sources, 0);

    collect(&mut collector, &chain[..1]);
    let request = collector.next_request().unwrap();
    assert_eq!(
        collector.apply(&request, Err(SourceError::InvalidCursor)),
        CollectEvent::Restarted
    );

    let old = collector.next_request().unwrap();
    collector.restart();
    let fresh = collector.next_request().unwrap();
    assert_eq!(
        collector.apply(&old, decode_draft_page(&bytes(&chain[0]), &w)),
        CollectEvent::Stale
    );
    assert!(matches!(
        collector.apply(&fresh, decode_draft_page(&bytes(&chain[0]), &w)),
        CollectEvent::Appended(2)
    ));
    // Other failures keep what was read and retry the same cursor.
    let next = collector.next_request().unwrap();
    assert_eq!(
        collector.apply(&next, Err(SourceError::Unavailable)),
        CollectEvent::Failed(SourceError::Unavailable)
    );
    assert_eq!(collector.next_request().unwrap().cursor, next.cursor);
}

#[test]
fn exclusions_are_explicit_and_copy_rechecks_the_sources() {
    let w = week(MONDAY).unwrap();
    let mut reviewed = SourceCollector::new(OWNER, w.clone());
    collect(&mut reviewed, &pages(&[3]));
    assert!(!reviewed.set_excluded("never-read", true));
    assert!(reviewed.set_excluded("team-1", true));
    let coverage = reviewed.coverage();
    assert_eq!((coverage.included, coverage.excluded), (2, 1));
    assert_eq!(
        reviewed
            .included()
            .map(|s| s.event_id.as_str())
            .collect::<Vec<_>>(),
        ["team-2", "team-0"]
    );

    let mut same = SourceCollector::new(OWNER, w.clone());
    collect(&mut same, &pages(&[2, 1]));
    assert_eq!(reviewed.recheck(&same), Recheck::Unchanged);

    // A changed excluded source does not matter; a changed included one does.
    let mut changed = pages(&[3]);
    changed[0]["sources"][1]["text"] = "edited elsewhere".into();
    let mut fresh = SourceCollector::new(OWNER, w.clone());
    collect(&mut fresh, &changed);
    assert_eq!(reviewed.recheck(&fresh), Recheck::Unchanged);
    changed[0]["sources"][0]["text"] = "edited elsewhere".into();
    changed[0]["sources"].as_array_mut().unwrap().pop();
    let mut fresh = SourceCollector::new(OWNER, w.clone());
    collect(&mut fresh, &changed);
    assert_eq!(
        reviewed.recheck(&fresh),
        Recheck::Changed {
            added: vec![],
            removed: vec!["team-0".into()],
            modified: vec!["team-2".into()]
        }
    );

    // Another owner, another week or an incomplete read cannot confirm.
    let mut other = SourceCollector::new(OTHER, w.clone());
    collect(&mut other, &pages(&[3]));
    assert_eq!(reviewed.recheck(&other), Recheck::CannotConfirm);
    let mut partial = SourceCollector::new(OWNER, w.clone());
    collect(&mut partial, &pages(&[2, 1])[..1]);
    assert_eq!(reviewed.recheck(&partial), Recheck::CannotConfirm);
    let next = SourceCollector::new(OWNER, week("2026-10-12").unwrap());
    assert_eq!(reviewed.recheck(&next), Recheck::CannotConfirm);
}

fn server(
    routes: Vec<(&'static str, &'static str, Vec<u8>)>,
) -> (ContextEndpoint, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let record = seen.clone();
    thread::spawn(move || {
        let mut routes = routes;
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { return };
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut data = Vec::new();
            let mut buffer = [0u8; 4096];
            while !String::from_utf8_lossy(&data).contains("\r\n\r\n") {
                let n = stream.read(&mut buffer).unwrap_or(0);
                if n == 0 {
                    break;
                }
                data.extend_from_slice(&buffer[..n]);
            }
            let text = String::from_utf8_lossy(&data).to_string();
            let line = text.lines().next().unwrap_or("").to_string();
            record.lock().unwrap().push(text.clone());
            let index = routes.iter().position(|r| line.starts_with(r.0));
            let (status, body) = match index {
                Some(i) => {
                    let r = routes.remove(i);
                    (r.1, r.2)
                }
                None => ("404 Not Found", br#"{"detail":"NOT_FOUND"}"#.to_vec()),
            };
            let _ = write!(
                stream,
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            let _ = stream.write_all(&body);
        }
    });
    (ContextEndpoint::parse(&origin).unwrap(), seen)
}

#[tokio::test(flavor = "multi_thread")]
async fn draft_pages_are_fetched_for_the_week_with_the_cursor() {
    let w = week(MONDAY).unwrap();
    let chain = pages(&[2, 1]);
    let (endpoint, seen) = server(vec![
        (
            "GET /v1/context-weekly-draft?week=2026-10-05&cursor=",
            "200 OK",
            bytes(&chain[1]),
        ),
        (
            "GET /v1/context-weekly-draft?week=2026-10-05 ",
            "200 OK",
            bytes(&chain[0]),
        ),
    ]);
    let first: DraftPage = fetch_draft_page(&endpoint, "token", &w, None)
        .await
        .unwrap();
    let cursor = first.next_cursor.clone().unwrap();
    let last = fetch_draft_page(&endpoint, "token", &w, Some(&cursor))
        .await
        .unwrap();
    assert!(last.next_cursor.is_none());
    let seen = seen.lock().unwrap().clone();
    assert!(seen[1].contains(&format!("cursor={cursor}")));
    assert!(seen
        .iter()
        .all(|r| r.contains("authorization: Bearer token")));

    for (status, body, expected) in [
        (
            "401 Unauthorized",
            br#"{"detail":{"code":"AUTH_REQUIRED"}}"#.to_vec(),
            SourceError::SignInRequired,
        ),
        (
            "422 Unprocessable Entity",
            br#"{"detail":"INVALID_CONTEXT_WEEK"}"#.to_vec(),
            SourceError::InvalidWeek,
        ),
        (
            "422 Unprocessable Entity",
            br#"{"detail":"INVALID_CONTEXT_CURSOR"}"#.to_vec(),
            SourceError::InvalidCursor,
        ),
        (
            "503 Service Unavailable",
            b"{}".to_vec(),
            SourceError::Unavailable,
        ),
        ("302 Found", b"{}".to_vec(), SourceError::Unavailable),
    ] {
        let (endpoint, _) = server(vec![("GET /v1/context-weekly-draft", status, body)]);
        assert_eq!(
            fetch_draft_page(&endpoint, "token", &w, None).await,
            Err(expected)
        );
    }
    let (endpoint, seen) = server(vec![]);
    assert_eq!(
        fetch_draft_page(&endpoint, "token", &w, Some("a&week=2020-01-06")).await,
        Err(SourceError::InvalidCursor)
    );
    assert!(seen.lock().unwrap().is_empty());
}
