//! T24a: Windows sign-in through a one-shot loopback callback (owner decision D5).
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use vox_agent_desktop_tauri_lib::context_login::{sign_in, LoginError, LoopbackReceiver};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;

const REQUEST: &str = "req_0123456789abcdefghijklmnopqrstuvwxyzABCDEF";
const COMPLETION: &str = "cmp_0123456789abcdefghijklmnopqrstuvwxyzABCDEF";
const POLL: &str = "poll_0123456789abcdefghijklmnopqrstuvwxyzABCDEF";

fn get(port: u16, target: &str, host: Option<&str>) -> String {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    let host = host
        .map(str::to_string)
        .unwrap_or_else(|| format!("127.0.0.1:{port}"));
    write!(stream, "GET {target} HTTP/1.1\r\nHost: {host}\r\n\r\n").unwrap();
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
    response
}

fn complete_path(request: &str, completion: &str) -> String {
    format!("/vox-agent/login-complete?requestId={request}&completion={completion}")
}

#[test]
fn the_receiver_accepts_one_matching_callback_and_then_closes() {
    let receiver = LoopbackReceiver::bind().unwrap();
    let port = receiver.port();
    let waiter = thread::spawn(move || receiver.receive(REQUEST, Duration::from_secs(5)));
    let page = get(port, &complete_path(REQUEST, COMPLETION), None);
    assert!(page.starts_with("HTTP/1.1 200"));
    assert!(page.contains("Content-Security-Policy: default-src 'none'"));
    assert!(!page.to_lowercase().contains("<script"));
    assert_eq!(waiter.join().unwrap().unwrap(), COMPLETION);
    assert!(
        TcpStream::connect(("127.0.0.1", port)).is_err(),
        "replay cannot even connect"
    );
}

#[test]
fn wrong_requests_are_refused_and_waiting_continues() {
    let receiver = LoopbackReceiver::bind().unwrap();
    let port = receiver.port();
    let waiter = thread::spawn(move || receiver.receive(REQUEST, Duration::from_secs(5)));
    assert!(get(port, "/favicon.ico", None).starts_with("HTTP/1.1 404"));
    assert!(get(
        port,
        &complete_path("req_other0123456789abcdefghijklmnopqrstuv", COMPLETION),
        None
    )
    .starts_with("HTTP/1.1 400"));
    assert!(get(port, &complete_path(REQUEST, "short"), None).starts_with("HTTP/1.1 400"));
    assert!(get(
        port,
        &complete_path(REQUEST, COMPLETION),
        Some("evil.example")
    )
    .starts_with("HTTP/1.1 400"));
    assert!(get(port, &complete_path(REQUEST, COMPLETION), None).starts_with("HTTP/1.1 200"));
    assert_eq!(waiter.join().unwrap().unwrap(), COMPLETION);
}

#[test]
fn a_denied_login_and_a_timeout_end_the_wait() {
    let receiver = LoopbackReceiver::bind().unwrap();
    let port = receiver.port();
    let waiter = thread::spawn(move || receiver.receive(REQUEST, Duration::from_secs(5)));
    let page = get(
        port,
        &format!("/vox-agent/login-complete?requestId={REQUEST}&error=login_failed"),
        None,
    );
    assert!(page.starts_with("HTTP/1.1 200"));
    assert_eq!(waiter.join().unwrap().err(), Some(LoginError::Denied));

    let receiver = LoopbackReceiver::bind().unwrap();
    assert_eq!(
        receiver.receive(REQUEST, Duration::from_millis(200)).err(),
        Some(LoginError::Timeout)
    );
}

#[test]
fn too_many_bad_attempts_end_the_wait() {
    let receiver = LoopbackReceiver::bind().unwrap();
    let port = receiver.port();
    let waiter = thread::spawn(move || receiver.receive(REQUEST, Duration::from_secs(10)));
    // One request per connection: a separate connect-only probe itself counts
    // as a bad attempt and races the listener shutdown on Windows.
    for _ in 0..32 {
        let page = get(
            port,
            &complete_path("req_wrong0123456789abcdefghijklmnopqrstuv", COMPLETION),
            None,
        );
        assert!(page.starts_with("HTTP/1.1 400"));
    }
    assert_eq!(
        waiter.join().unwrap().err(),
        Some(LoginError::TooManyAttempts)
    );
    assert!(TcpStream::connect(("127.0.0.1", port)).is_err());
}

#[test]
fn the_receiver_listens_on_loopback_only() {
    let receiver = LoopbackReceiver::bind().unwrap();
    assert!(receiver.port() > 0);
    assert!(TcpStream::connect(("127.0.0.1", receiver.port())).is_ok());
}

// ---------- full flow against a mock sign-in server ----------

#[derive(Default)]
struct Seen {
    begin_body: String,
    redeem_auth: String,
    redeem_body: String,
}

fn respond(stream: &mut TcpStream, status: &str, body: &str) {
    let _ = write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
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
                    text.lines().next().unwrap().to_string(),
                    text[..end].to_string(),
                    text[end + 4..].to_string(),
                );
            }
        }
    }
    (String::new(), String::new(), String::new())
}

fn mock_server(
    browser_url: impl Fn(&str) -> String + Send + 'static,
    redeem: &'static str,
) -> (String, Arc<Mutex<Seen>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Seen::default()));
    let record = seen.clone();
    let base = origin.clone();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { return };
            let (line, head, body) = read_request(&mut stream);
            if line.starts_with("POST /v1/auth/feishu/begin ") {
                record.lock().unwrap().begin_body = body;
                let url = browser_url(&base);
                respond(
                    &mut stream,
                    "200 OK",
                    &format!(
                        r#"{{"requestId":"{REQUEST}","pollSecret":"{POLL}","expiresIn":300,"browserURL":"{url}"}}"#
                    ),
                );
            } else if line.starts_with("POST /v1/auth/feishu/redeem ") {
                let auth = head
                    .lines()
                    .find(|l| l.to_ascii_lowercase().starts_with("authorization:"))
                    .unwrap_or("")
                    .to_string();
                let mut seen = record.lock().unwrap();
                seen.redeem_auth = auth;
                seen.redeem_body = body;
                respond(&mut stream, "200 OK", redeem);
            } else {
                respond(&mut stream, "404 Not Found", r#"{"detail":"NOT_FOUND"}"#);
            }
        }
    });
    (origin, seen)
}

const REDEEMED: &str = r#"{"token":"vox_sso_0123456789abcdefghijklmnopqrstuvwxyzAB","expiresAt":1,"expiresIn":3600,
  "profile":{"userId":"feishu-0123","orgId":"default-org","name":"Alice","appId":"cli_test","tenantKey":"team","openId":"ou_alice"},
  "targetIds":["vox","bad target\n"]}"#;

#[tokio::test(flavor = "multi_thread")]
async fn a_full_sign_in_yields_a_principal_token_and_destinations() {
    let (origin, seen) = mock_server(
        |base| format!("{base}/v1/auth/feishu/browser?state={REQUEST}"),
        REDEEMED,
    );
    let endpoint = ContextEndpoint::parse(&origin).unwrap();
    let seen_for_browser = seen.clone();
    let opener = move |url: &reqwest::Url| {
        assert_eq!(url.path(), "/v1/auth/feishu/browser");
        let body = seen_for_browser.lock().unwrap().begin_body.clone();
        let port: u16 = serde_json::from_str::<serde_json::Value>(&body).unwrap()["loopbackPort"]
            .as_u64()
            .unwrap() as u16;
        thread::spawn(move || {
            get(port, &complete_path(REQUEST, COMPLETION), None);
        });
        Ok(())
    };
    let signed = sign_in(&endpoint, opener, Duration::from_secs(10))
        .await
        .unwrap();
    assert!(signed.token.starts_with("vox_sso_"));
    assert_eq!(
        signed.principal,
        vox_agent_desktop_tauri_lib::context_session::PrincipalKey::new(
            &endpoint,
            "default-org",
            "feishu-0123",
            1
        )
        .unwrap()
    );
    assert_eq!(
        signed
            .destinations
            .iter()
            .map(|d| d.as_str())
            .collect::<Vec<_>>(),
        vec!["vox"]
    );
    assert_eq!(signed.display_name, "Alice");
    let seen = seen.lock().unwrap();
    assert_eq!(
        seen.redeem_auth.to_ascii_lowercase(),
        format!("authorization: bearer {POLL}").to_ascii_lowercase()
    );
    let redeem: serde_json::Value = serde_json::from_str(&seen.redeem_body).unwrap();
    assert_eq!(
        redeem,
        serde_json::json!({"requestId": REQUEST, "completion": COMPLETION})
    );
    assert!(
        format!("{signed:?}").contains("redacted") && !format!("{signed:?}").contains("vox_sso_")
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_browser_url_for_another_origin_or_path_is_never_opened() {
    for url in [
        |_: &str| format!("https://evil.example/v1/auth/feishu/browser?state={REQUEST}"),
        |base: &str| format!("{base}/somewhere-else?state={REQUEST}"),
        |base: &str| {
            format!("{base}/v1/auth/feishu/browser?state=req_other0123456789abcdefghijklmnopqrstuv")
        },
        |base: &str| {
            format!("{base}/v1/auth/feishu/browser?state={REQUEST}&next=https://evil.example")
        },
    ] {
        let (origin, _) = mock_server(url, REDEEMED);
        let endpoint = ContextEndpoint::parse(&origin).unwrap();
        let opened = Arc::new(Mutex::new(false));
        let flag = opened.clone();
        let result = sign_in(
            &endpoint,
            move |_| {
                *flag.lock().unwrap() = true;
                Ok(())
            },
            Duration::from_secs(2),
        )
        .await;
        assert_eq!(result.err(), Some(LoginError::UnexpectedBrowserUrl));
        assert!(!*opened.lock().unwrap());
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_malformed_redeem_response_is_refused() {
    for redeem in [
        r#"{"token":"not-a-session-token","profile":{"userId":"u","orgId":"o","name":"A"},"targetIds":[]}"#,
        r#"{"token":"vox_sso_0123456789abcdefghijklmnopqrstuvwxyzAB","profile":{"userId":"","orgId":"o","name":"A"},"targetIds":[]}"#,
        r#"{"token":"vox_sso_","profile":{"userId":"u","orgId":"o","name":"A"},"targetIds":[]}"#,
        r#"{"token":"vox_sso_0123456789abcdefghijklmnopqrstuv wxyz","profile":{"userId":"u","orgId":"o","name":"A"},"targetIds":[]}"#,
        r#"{"token":"vox_sso_0123456789abcdefghijklmnopqrstuvwxyzAB"}"#,
    ] {
        let (origin, seen) = mock_server(
            |base| format!("{base}/v1/auth/feishu/browser?state={REQUEST}"),
            redeem,
        );
        let endpoint = ContextEndpoint::parse(&origin).unwrap();
        let opener = move |_: &reqwest::Url| {
            let body = seen.lock().unwrap().begin_body.clone();
            let port = serde_json::from_str::<serde_json::Value>(&body).unwrap()["loopbackPort"]
                .as_u64()
                .unwrap() as u16;
            thread::spawn(move || {
                get(port, &complete_path(REQUEST, COMPLETION), None);
            });
            Ok(())
        };
        assert_eq!(
            sign_in(&endpoint, opener, Duration::from_secs(10))
                .await
                .err(),
            Some(LoginError::InvalidResponse)
        );
    }
}

fn raw(port: u16, request: &[u8]) -> String {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    stream.write_all(request).unwrap();
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
    response
}

#[test]
fn a_client_trickling_bytes_cannot_hold_the_listener() {
    let receiver = LoopbackReceiver::bind().unwrap();
    let port = receiver.port();
    let waiter = thread::spawn(move || receiver.receive(REQUEST, Duration::from_secs(8)));
    // Slow client: one byte at a time, never finishing the head.
    let slow = thread::spawn(move || {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
        for byte in b"GET /vox-agent/login-complete?".iter().cycle().take(40) {
            if stream.write_all(&[*byte]).is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(150));
        }
    });
    thread::sleep(Duration::from_millis(300));
    let started = std::time::Instant::now();
    let page = get(port, &complete_path(REQUEST, COMPLETION), None);
    assert!(page.starts_with("HTTP/1.1 200"), "{page}");
    assert!(
        started.elapsed() < Duration::from_secs(4),
        "the slow client held the listener"
    );
    assert_eq!(waiter.join().unwrap().unwrap(), COMPLETION);
    let _ = slow.join();
}

#[test]
fn strict_request_parsing() {
    let receiver = LoopbackReceiver::bind().unwrap();
    let port = receiver.port();
    let waiter = thread::spawn(move || receiver.receive(REQUEST, Duration::from_secs(5)));
    let path = complete_path(REQUEST, COMPLETION);
    let host = format!("Host: 127.0.0.1:{port}");
    for request in [
        format!("GET {path}\r\n{host}\r\n\r\n"),
        format!("GET {path} HTTP/1.1\r\nHost: evil.example\r\n{host}\r\n\r\n"),
        format!("GET {path} HTTP/1.1\r\nHost: evil.example\r\n\r\n{host}\r\n"),
        format!("GET {path} HTTP/1.1\r\n{host}\r\nContent-Length: 5\r\n\r\nhello"),
        format!("GET {path}&requestId={REQUEST} HTTP/1.1\r\n{host}\r\n\r\n"),
    ] {
        let response = raw(port, request.as_bytes());
        assert!(
            !response.starts_with("HTTP/1.1 200"),
            "{request:?} -> {response}"
        );
    }
    assert!(raw(
        port,
        format!("GET {path} HTTP/1.1\r\n{host}\r\n\r\n").as_bytes()
    )
    .starts_with("HTTP/1.1 200"));
    assert_eq!(waiter.join().unwrap().unwrap(), COMPLETION);
}
