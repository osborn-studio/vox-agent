//! T05: bounded Context transport against a scripted loopback HTTP server.
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use vox_agent_desktop_tauri_lib::context_delivery::BackupPayload;
use vox_agent_desktop_tauri_lib::context_transport::{
    readback, submit, Outcome, ServerCode, TransportError,
};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;

const TOKEN: &str = "synthetic-token-value-for-tests";

/// A one-shot server: answers each connection with the next scripted reply.
/// `None` closes the connection after reading the request (lost response).
struct Server {
    endpoint: String,
    hits: Arc<AtomicUsize>,
    requests: Arc<std::sync::Mutex<Vec<String>>>,
}

fn serve(replies: Vec<Option<Vec<u8>>>) -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let hits = Arc::new(AtomicUsize::new(0));
    let requests = Arc::new(std::sync::Mutex::new(Vec::new()));
    let (h, r) = (hits.clone(), requests.clone());
    thread::spawn(move || {
        for reply in replies {
            let Ok((mut stream, _)) = listener.accept() else {
                return;
            };
            h.fetch_add(1, Ordering::SeqCst);
            r.lock().unwrap().push(read_request(&mut stream));
            if let Some(bytes) = reply {
                let _ = stream.write_all(&bytes);
            }
            let _ = stream.shutdown(std::net::Shutdown::Both);
        }
    });
    Server {
        endpoint,
        hits,
        requests,
    }
}

fn read_request(stream: &mut TcpStream) -> String {
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
        let text = String::from_utf8_lossy(&data);
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
                break;
            }
        }
    }
    String::from_utf8_lossy(&data).into_owned()
}

fn http(status: &str, body: &str) -> Option<Vec<u8>> {
    Some(format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).into_bytes())
}

fn payload() -> BackupPayload {
    BackupPayload::new(
        "event-1".into(),
        "team".into(),
        "synthetic context".into(),
        None,
    )
    .unwrap()
}

fn endpoint(server: &Server) -> ContextEndpoint {
    ContextEndpoint::parse(&server.endpoint).unwrap()
}

#[tokio::test]
async fn accepted_submission_returns_the_receipt_and_sends_only_server_fields() {
    let server = serve(vec![http(
        "202 Accepted",
        r#"{"eventId":"event-1","status":"pending","messageId":null}"#,
    )]);
    let outcome = submit(&endpoint(&server), TOKEN, &payload()).await.unwrap();
    assert_eq!(
        outcome,
        Outcome::Receipt(br#"{"eventId":"event-1","status":"pending","messageId":null}"#.to_vec())
    );
    let request = server.requests.lock().unwrap()[0].clone();
    assert!(request.starts_with("POST /v1/input-backups HTTP/1.1"));
    assert!(
        request.contains(&format!("authorization: Bearer {TOKEN}"))
            || request.contains(&format!("Authorization: Bearer {TOKEN}"))
    );
    assert!(
        request.ends_with(r#"{"eventId":"event-1","targetId":"team","text":"synthetic context"}"#)
    );
}

#[tokio::test]
async fn readback_uses_the_event_path_and_expects_200() {
    let server = serve(vec![http(
        "200 OK",
        r#"{"eventId":"event-1","status":"sending","messageId":null}"#,
    )]);
    let outcome = readback(&endpoint(&server), TOKEN, &payload())
        .await
        .unwrap();
    assert!(matches!(outcome, Outcome::Receipt(_)));
    assert!(
        server.requests.lock().unwrap()[0].starts_with("GET /v1/input-backups/event-1 HTTP/1.1")
    );
}

#[tokio::test]
async fn both_error_envelopes_map_to_whitelisted_codes() {
    let server = serve(vec![
        http("409 Conflict", r#"{"detail":"EVENT_PAYLOAD_CONFLICT"}"#),
        http(
            "401 Unauthorized",
            r#"{"detail":{"code":"INVALID_CREDENTIAL","message":"Invalid bearer credential"}}"#,
        ),
        http(
            "413 Payload Too Large",
            r#"{"detail":"RENDERED_MESSAGE_TOO_LARGE"}"#,
        ),
        http(
            "422 Unprocessable Entity",
            r#"{"detail":"INVALID_BACKUP_INPUT"}"#,
        ),
        http("503 Service Unavailable", r#"{"detail":"BACKUP_DISABLED"}"#),
        http("403 Forbidden", r#"{"detail":"SOMETHING_NEW"}"#),
    ]);
    let ep = endpoint(&server);
    let expected = [
        (409, ServerCode::EventPayloadConflict),
        (401, ServerCode::InvalidCredential),
        (413, ServerCode::RenderedMessageTooLarge),
        (422, ServerCode::InvalidBackupInput),
        (503, ServerCode::BackupDisabled),
        (403, ServerCode::Unrecognized),
    ];
    for (status, code) in expected {
        assert_eq!(
            submit(&ep, TOKEN, &payload()).await.unwrap(),
            Outcome::Rejected { status, code }
        );
    }
}

#[tokio::test]
async fn readback_404_is_not_found_not_a_failure_signal() {
    let server = serve(vec![http(
        "404 Not Found",
        r#"{"detail":"BACKUP_NOT_FOUND"}"#,
    )]);
    assert_eq!(
        readback(&endpoint(&server), TOKEN, &payload())
            .await
            .unwrap(),
        Outcome::Rejected {
            status: 404,
            code: ServerCode::BackupNotFound
        }
    );
}

#[tokio::test]
async fn redirects_are_never_followed() {
    let target = serve(vec![http("202 Accepted", "{}")]);
    let location = format!("{}/v1/input-backups", target.endpoint);
    let server = serve(vec![Some(format!("HTTP/1.1 307 Temporary Redirect\r\nLocation: {location}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").into_bytes())]);
    assert_eq!(
        submit(&endpoint(&server), TOKEN, &payload()).await,
        Err(TransportError::UnexpectedStatus(307))
    );
    thread::sleep(Duration::from_millis(100));
    assert_eq!(
        target.hits.load(Ordering::SeqCst),
        0,
        "credentials must never follow a redirect"
    );
}

#[tokio::test]
async fn oversized_declared_or_streamed_bodies_are_refused() {
    let declared = serve(vec![Some(
        b"HTTP/1.1 202 Accepted\r\nContent-Length: 999999\r\nConnection: close\r\n\r\n{".to_vec(),
    )]);
    assert_eq!(
        submit(&endpoint(&declared), TOKEN, &payload()).await,
        Err(TransportError::TooLarge)
    );
    let mut chunked =
        b"HTTP/1.1 202 Accepted\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            .to_vec();
    for _ in 0..20 {
        chunked.extend_from_slice(b"400\r\n");
        chunked.extend_from_slice(&[b'a'; 1024]);
        chunked.extend_from_slice(b"\r\n");
    }
    chunked.extend_from_slice(b"0\r\n\r\n");
    let streamed = serve(vec![Some(chunked)]);
    assert_eq!(
        submit(&endpoint(&streamed), TOKEN, &payload()).await,
        Err(TransportError::TooLarge)
    );
    let big_error = serve(vec![http(
        "409 Conflict",
        &format!(r#"{{"detail":"{}"}}"#, "A".repeat(8000)),
    )]);
    assert_eq!(
        submit(&endpoint(&big_error), TOKEN, &payload()).await,
        Err(TransportError::TooLarge)
    );
}

#[tokio::test]
async fn connection_refused_is_pre_dispatch_and_a_lost_response_is_post_dispatch() {
    let closed = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = closed.local_addr().unwrap();
    drop(closed);
    let refused = ContextEndpoint::parse(&format!("http://{address}")).unwrap();
    assert_eq!(
        submit(&refused, TOKEN, &payload()).await,
        Err(TransportError::PreDispatch)
    );

    let lost = serve(vec![None]);
    assert_eq!(
        submit(&endpoint(&lost), TOKEN, &payload()).await,
        Err(TransportError::PostDispatch)
    );
    assert_eq!(
        lost.hits.load(Ordering::SeqCst),
        1,
        "the request body did reach the server"
    );
}

const PROXY_CHILD_ENV: &str = "VOX_PROXY_CHILD_PROXY";
const PROXY_VARS: [&str; 8] = [
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
];

/// Runs only in the child spawned below, whose environment points every
/// proxy variable at a counting listener and has no NO_PROXY bypass.
#[tokio::test]
async fn proxy_child() {
    if std::env::var_os(PROXY_CHILD_ENV).is_none() {
        return;
    }
    // The target answers exactly once. If the control request bypassed the
    // proxy it would consume that answer and the subject below would fail.
    let target = serve(vec![http(
        "202 Accepted",
        r#"{"eventId":"event-1","status":"pending","messageId":null}"#,
    )]);
    // Control: a default client honours the proxy environment.
    let control = reqwest::Client::new()
        .get(format!("{}/", target.endpoint))
        .send()
        .await;
    println!(
        "\nCONTROL_DIRECT_HITS={}",
        target.hits.load(Ordering::SeqCst)
    );
    println!("CONTROL_RESULT_OK={}", control.is_ok());
    // Subject: the Context transport ignores the proxy environment.
    let result = submit(&endpoint(&target), TOKEN, &payload()).await;
    println!("SUBJECT_OK={}", matches!(result, Ok(Outcome::Receipt(_))));
    println!("SUBJECT_DIRECT_HITS={}", target.hits.load(Ordering::SeqCst));
}

#[test]
fn proxy_environment_is_ignored_while_a_default_client_uses_it() {
    if std::env::var_os(PROXY_CHILD_ENV).is_some() {
        return;
    }
    let proxy = TcpListener::bind("127.0.0.1:0").unwrap();
    let proxy_url = format!("http://{}", proxy.local_addr().unwrap());
    let proxy_hits = Arc::new(AtomicUsize::new(0));
    let counter = proxy_hits.clone();
    thread::spawn(move || {
        for stream in proxy.incoming() {
            let Ok(mut stream) = stream else { return };
            counter.fetch_add(1, Ordering::SeqCst);
            let _ = read_request(&mut stream);
            let _ = stream.write_all(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
            );
        }
    });
    let mut command = std::process::Command::new(std::env::current_exe().unwrap());
    command.args(["--exact", "proxy_child", "--nocapture"]);
    for var in PROXY_VARS {
        command.env_remove(var);
    }
    for var in ["HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"] {
        command.env(var, &proxy_url);
    }
    command.env(PROXY_CHILD_ENV, "1");
    let output = command.output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(output.status.success(), "child failed: {stdout}");
    let line = |key: &str| {
        stdout
            .lines()
            .find_map(|l| l.trim().strip_prefix(key).map(str::to_string))
            .unwrap_or_default()
    };
    assert!(
        proxy_hits.load(Ordering::SeqCst) >= 1,
        "control: the default client must reach the proxy; {stdout}"
    );
    assert_eq!(
        line("CONTROL_DIRECT_HITS="),
        "0",
        "control request went to the proxy, not the target"
    );
    assert_eq!(line("SUBJECT_OK="), "true", "{stdout}");
    assert_eq!(
        line("SUBJECT_DIRECT_HITS="),
        "1",
        "the Context transport connected directly"
    );
}

#[tokio::test]
async fn transport_errors_never_carry_the_token_or_body() {
    let server = serve(vec![http(
        "500 Internal Server Error",
        &format!(r#"{{"detail":"{TOKEN} synthetic context"}}"#),
    )]);
    let error = submit(&endpoint(&server), TOKEN, &payload())
        .await
        .unwrap_err();
    let text = format!("{error:?}");
    assert!(!text.contains(TOKEN) && !text.contains("synthetic context"));
    assert_eq!(error, TransportError::UnexpectedStatus(500));
}
