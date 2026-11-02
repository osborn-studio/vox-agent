//! Spec §3.6: the Feishu session survives the one-hour access token while the
//! app runs. `redeem` keeps the refresh token and expiry, `renew` rotates the
//! access token for the same principal, refusals map to sign-out or retry,
//! and the result is applied under the lease captured before the request.
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use vox_agent_desktop_tauri_lib::context_account::{apply_renewal, RenewalApplied};
use vox_agent_desktop_tauri_lib::context_login::{
    renew, renewal_due, sign_in, RenewOutcome, SignedIn,
};
use vox_agent_desktop_tauri_lib::context_service::ContextService;
use vox_agent_desktop_tauri_lib::context_session::{
    DestinationKey, PrincipalKey, SessionAuthority, TokenStore, REFRESH_ACCOUNT,
};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;
use vox_agent_desktop_tauri_lib::secrets::SecretError;
use zeroize::Zeroizing;

const REQUEST: &str = "req_0123456789abcdefghijklmnopqrstuvwxyzABCDEF";
const COMPLETION: &str = "cmp_0123456789abcdefghijklmnopqrstuvwxyzABCDEF";
const POLL: &str = "poll_0123456789abcdefghijklmnopqrstuvwxyzABCDEF";
const ACCESS_1: &str = "vox_sso_0123456789abcdefghijklmnopqrstuvwxyzAB";
const ACCESS_2: &str = "vox_sso_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab";
const REFRESH: &str = "vox_refresh_0123456789abcdefghijklmnopqrstuvwxyz";

fn profile(user: &str) -> String {
    format!(
        r#""profile":{{"userId":"{user}","orgId":"default-org","name":"Alice","appId":"cli_test","tenantKey":"team","openId":"ou_alice"}}"#
    )
}

fn redeemed(user: &str, refresh: Option<&str>) -> String {
    let refresh = refresh
        .map(|r| format!(r#","refreshToken":"{r}","refreshExpiresAt":99999"#))
        .unwrap_or_default();
    format!(
        r#"{{"token":"{ACCESS_1}","expiresAt":1,"expiresIn":3600{refresh},{},"targetIds":["vox"]}}"#,
        profile(user)
    )
}

fn renewed(user: &str, token: &str, expires_in: &str) -> String {
    format!(
        r#"{{"token":"{token}","expiresAt":2,"expiresIn":{expires_in},"refreshToken":"{REFRESH}","refreshExpiresAt":99999,{},"targetIds":["vox"]}}"#,
        profile(user)
    )
}

// ---------- loopback mock of the Context server ----------

#[derive(Default)]
struct Seen {
    begin_body: String,
    renew_auth: String,
    renew_body: String,
    renew_line: String,
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

/// `redeem` and `renew` answer with the given status and body.
fn mock_server(
    redeem: (&'static str, String),
    renew: (&'static str, String),
) -> (ContextEndpoint, Arc<Mutex<Seen>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Seen::default()));
    let record = seen.clone();
    let base = origin.clone();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { return };
            let (line, head, body) = read_request(&mut stream);
            let auth = head
                .lines()
                .find(|l| l.to_ascii_lowercase().starts_with("authorization:"))
                .unwrap_or("")
                .to_string();
            if line.starts_with("POST /v1/auth/feishu/begin ") {
                record.lock().unwrap().begin_body = body;
                respond(
                    &mut stream,
                    "200 OK",
                    &format!(
                        r#"{{"requestId":"{REQUEST}","pollSecret":"{POLL}","expiresIn":300,"browserURL":"{base}/v1/auth/feishu/browser?state={REQUEST}"}}"#
                    ),
                );
            } else if line.starts_with("POST /v1/auth/feishu/redeem ") {
                respond(&mut stream, redeem.0, &redeem.1);
            } else if line.starts_with("POST /v1/auth/feishu/renew ") {
                let mut seen = record.lock().unwrap();
                seen.renew_auth = auth;
                seen.renew_body = body;
                seen.renew_line = line;
                respond(&mut stream, renew.0, &renew.1);
            } else {
                respond(&mut stream, "404 Not Found", r#"{"detail":"NOT_FOUND"}"#);
            }
        }
    });
    (ContextEndpoint::parse(&origin).unwrap(), seen)
}

fn callback(port: u16) {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    write!(
        stream,
        "GET /vox-agent/login-complete?requestId={REQUEST}&completion={COMPLETION} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n"
    )
    .unwrap();
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
}

async fn full_sign_in(endpoint: &ContextEndpoint, seen: &Arc<Mutex<Seen>>) -> SignedIn {
    let seen = seen.clone();
    let opener = move |_: &reqwest::Url| {
        let body = seen.lock().unwrap().begin_body.clone();
        let port = serde_json::from_str::<serde_json::Value>(&body).unwrap()["loopbackPort"]
            .as_u64()
            .unwrap() as u16;
        thread::spawn(move || callback(port));
        Ok(())
    };
    sign_in(endpoint, opener, Duration::from_secs(10))
        .await
        .unwrap()
}

// ---------- redeem keeps the renewal inputs ----------

#[tokio::test(flavor = "multi_thread")]
async fn redeem_keeps_refresh_token_and_expiry() {
    let (endpoint, seen) = mock_server(
        ("200 OK", redeemed("feishu-0123", Some(REFRESH))),
        ("404 Not Found", "{}".into()),
    );
    let signed = full_sign_in(&endpoint, &seen).await;
    assert_eq!(
        signed.refresh_token.as_deref().map(String::as_str),
        Some(REFRESH)
    );
    assert_eq!(signed.expires_in, Some(3600));
    let debug = format!("{signed:?}");
    assert!(
        !debug.contains("vox_refresh_") && !debug.contains("vox_sso_"),
        "{debug}"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn an_older_server_without_refresh_token_disables_renewal() {
    let (endpoint, seen) = mock_server(
        ("200 OK", redeemed("feishu-0123", None)),
        ("404 Not Found", "{}".into()),
    );
    let signed = full_sign_in(&endpoint, &seen).await;
    assert!(signed.refresh_token.is_none());
    assert_eq!(signed.expires_in, Some(3600));

    // A malformed refresh token is dropped, never stored.
    let (endpoint, seen) = mock_server(
        ("200 OK", redeemed("feishu-0123", Some("vox_refresh_short"))),
        ("404 Not Found", "{}".into()),
    );
    let signed = full_sign_in(&endpoint, &seen).await;
    assert!(signed.refresh_token.is_none());
}

// ---------- renew wire ----------

#[tokio::test(flavor = "multi_thread")]
async fn renew_replaces_access_token_and_keeps_principal() {
    let (endpoint, seen) = mock_server(
        ("404 Not Found", "{}".into()),
        ("200 OK", renewed("feishu-0123", ACCESS_2, "3600")),
    );
    let outcome = renew(&endpoint, REFRESH).await;
    let RenewOutcome::Renewed {
        token,
        refresh_token,
        expires_in,
        principal,
    } = &outcome
    else {
        panic!("{outcome:?}");
    };
    assert_eq!(token.as_str(), ACCESS_2);
    assert_eq!(refresh_token.as_str(), REFRESH);
    assert_eq!(*expires_in, 3600);
    assert_eq!(
        *principal,
        PrincipalKey::new(&endpoint, "default-org", "feishu-0123", 1).unwrap()
    );
    let debug = format!("{outcome:?}");
    assert!(!debug.contains("vox_"), "{debug}");
    let seen = seen.lock().unwrap();
    assert_eq!(
        seen.renew_auth.to_ascii_lowercase(),
        format!("authorization: bearer {REFRESH}").to_ascii_lowercase()
    );
    assert_eq!(
        seen.renew_body, "",
        "the refresh token travels only as Bearer"
    );
    assert!(seen
        .renew_line
        .starts_with("POST /v1/auth/feishu/renew HTTP/1.1"));
}

#[tokio::test(flavor = "multi_thread")]
async fn renew_caps_and_floors_a_strange_expiry() {
    let (endpoint, _) = mock_server(
        ("404 Not Found", "{}".into()),
        ("200 OK", renewed("feishu-0123", ACCESS_2, "86400")),
    );
    let RenewOutcome::Renewed { expires_in, .. } = renew(&endpoint, REFRESH).await else {
        panic!()
    };
    assert_eq!(expires_in, 3600);
    let (endpoint, _) = mock_server(
        ("404 Not Found", "{}".into()),
        ("200 OK", renewed("feishu-0123", ACCESS_2, "3599.5")),
    );
    let RenewOutcome::Renewed { expires_in, .. } = renew(&endpoint, REFRESH).await else {
        panic!()
    };
    assert_eq!(expires_in, 3599);
}

#[tokio::test(flavor = "multi_thread")]
async fn renew_401_signals_sign_out_and_503_signals_retry() {
    for (status, body, expected) in [
        (
            "401 Unauthorized",
            r#"{"detail":"SESSION_EXPIRED"}"#,
            RenewOutcome::SignedOut("SESSION_EXPIRED"),
        ),
        (
            "401 Unauthorized",
            r#"{"detail":"ACCOUNT_DISABLED"}"#,
            RenewOutcome::SignedOut("ACCOUNT_DISABLED"),
        ),
        (
            "401 Unauthorized",
            "garbage",
            RenewOutcome::SignedOut("SESSION_EXPIRED"),
        ),
        (
            "503 Service Unavailable",
            r#"{"detail":"MEMBERSHIP_CHECK_UNAVAILABLE"}"#,
            RenewOutcome::RetryLater("MEMBERSHIP_CHECK_UNAVAILABLE"),
        ),
        (
            "429 Too Many Requests",
            r#"{"detail":"RENEWAL_RATE_LIMIT"}"#,
            RenewOutcome::RetryLater("RENEWAL_RATE_LIMIT"),
        ),
        (
            "409 Conflict",
            r#"{"detail":"RENEWAL_IN_PROGRESS"}"#,
            RenewOutcome::RetryLater("RENEWAL_IN_PROGRESS"),
        ),
        (
            "500 Internal Server Error",
            "",
            RenewOutcome::RetryLater("UNEXPECTED_STATUS"),
        ),
    ] {
        let (endpoint, _) = mock_server(("404 Not Found", "{}".into()), (status, body.into()));
        let outcome = renew(&endpoint, REFRESH).await;
        assert!(
            matches!(
                (&outcome, &expected),
                (RenewOutcome::SignedOut(a), RenewOutcome::SignedOut(b))
                    | (RenewOutcome::RetryLater(a), RenewOutcome::RetryLater(b))
                    if a == b
            ),
            "{status}: {outcome:?} != {expected:?}"
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn renew_refuses_a_malformed_200_and_an_unreachable_server() {
    for body in [
        renewed("feishu-0123", "not-a-token", "3600"),
        // Same shape, refresh token missing.
        format!(
            r#"{{"token":"{ACCESS_2}","expiresIn":3600,{},"targetIds":[]}}"#,
            profile("feishu-0123")
        ),
        // Profile missing.
        format!(r#"{{"token":"{ACCESS_2}","expiresIn":3600,"refreshToken":"{REFRESH}"}}"#),
        "{".into(),
    ] {
        let (endpoint, _) = mock_server(("404 Not Found", "{}".into()), ("200 OK", body.clone()));
        let outcome = renew(&endpoint, REFRESH).await;
        assert!(
            matches!(outcome, RenewOutcome::RetryLater("INVALID_RESPONSE")),
            "{body}: {outcome:?}"
        );
    }
    let closed = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint =
        ContextEndpoint::parse(&format!("http://{}", closed.local_addr().unwrap())).unwrap();
    drop(closed);
    assert!(matches!(
        renew(&endpoint, REFRESH).await,
        RenewOutcome::RetryLater("NETWORK")
    ));
}

#[test]
fn renewal_is_due_five_minutes_before_expiry() {
    assert!(!renewal_due(3600, 0));
    assert!(!renewal_due(3600, 3600 - 301));
    assert!(renewal_due(3600, 3600 - 300));
    assert!(renewal_due(3600, 3600 - 299));
    assert!(renewal_due(3600, 4000));
    assert!(renewal_due(0, 0));
    assert!(
        renewal_due(60, 0),
        "a token shorter than the margin renews at once"
    );
}

// ---------- applying a renewal under the captured lease ----------

#[derive(Clone, Default)]
struct MemoryStore {
    values: Arc<Mutex<HashMap<String, String>>>,
    /// Writes to this account fail (`"*"`: every write).
    broken: Arc<Mutex<Option<String>>>,
}

impl TokenStore for MemoryStore {
    fn read(&self, account: &str) -> Result<Option<String>, SecretError> {
        Ok(self.values.lock().unwrap().get(account).cloned())
    }
    fn write(&self, account: &str, value: &str) -> Result<(), SecretError> {
        if self
            .broken
            .lock()
            .unwrap()
            .as_deref()
            .is_some_and(|b| b == "*" || b == account)
        {
            return Err(SecretError::Backend);
        }
        self.values
            .lock()
            .unwrap()
            .insert(account.into(), value.into());
        Ok(())
    }
    fn delete(&self, account: &str) -> Result<(), SecretError> {
        self.values.lock().unwrap().remove(account);
        Ok(())
    }
}

fn endpoint() -> ContextEndpoint {
    ContextEndpoint::parse("https://api.example.test").unwrap()
}

fn principal(actor: &str) -> PrincipalKey {
    PrincipalKey::new(&endpoint(), "default-org", actor, 1).unwrap()
}

fn service(store: &MemoryStore) -> ContextService<MemoryStore> {
    let mut session = SessionAuthority::new(store.clone());
    session
        .sign_in(principal("alice"), ACCESS_1.to_string())
        .unwrap();
    session.set_refresh_token(Some(REFRESH)).unwrap();
    ContextService::new(
        endpoint(),
        vec![DestinationKey::new("vox").unwrap()],
        session,
        None,
    )
}

fn renewed_outcome(actor: &str) -> RenewOutcome {
    RenewOutcome::Renewed {
        token: Zeroizing::new(ACCESS_2.to_string()),
        refresh_token: Zeroizing::new(REFRESH.to_string()),
        expires_in: 3600,
        principal: principal(actor),
    }
}

#[test]
fn a_renewal_rotates_the_token_under_the_captured_lease() {
    let store = MemoryStore::default();
    let service = service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    assert!(matches!(
        apply_renewal(&service, &lease, renewed_outcome("alice")),
        RenewalApplied::Renewed {
            expires_in: 3600,
            refresh_stored: true
        }
    ));
    let (_, token) = service.credentials().unwrap();
    assert_eq!(token.as_str(), ACCESS_2);
    assert!(
        service.still_current(&lease),
        "in-flight leases survive a refresh"
    );
    assert_eq!(
        store.read(REFRESH_ACCOUNT).unwrap().as_deref(),
        Some(REFRESH)
    );
    // A second result for the same (now older) epoch is stale.
    assert!(matches!(
        apply_renewal(&service, &lease, renewed_outcome("alice")),
        RenewalApplied::Stale
    ));
    assert_eq!(service.credentials().unwrap().1.as_str(), ACCESS_2);
}

#[test]
fn a_renewal_for_another_principal_signs_out() {
    let store = MemoryStore::default();
    let service = service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    assert!(matches!(
        apply_renewal(&service, &lease, renewed_outcome("mallory")),
        RenewalApplied::SignOut("IDENTITY_CHANGED")
    ));
    assert!(!service.signed_in());
    assert!(
        store.values.lock().unwrap().is_empty(),
        "every stored entry is gone"
    );
}

#[test]
fn refusals_map_to_sign_out_or_retry_without_touching_the_session() {
    let store = MemoryStore::default();
    let service = service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    assert!(matches!(
        apply_renewal(
            &service,
            &lease,
            RenewOutcome::RetryLater("MEMBERSHIP_CHECK_UNAVAILABLE")
        ),
        RenewalApplied::RetryLater("MEMBERSHIP_CHECK_UNAVAILABLE")
    ));
    assert_eq!(service.credentials().unwrap().1.as_str(), ACCESS_1);
    assert!(matches!(
        apply_renewal(&service, &lease, RenewOutcome::SignedOut("SESSION_EXPIRED")),
        RenewalApplied::SignOut("SESSION_EXPIRED")
    ));
    // The caller ends the account; the session itself is untouched here.
    assert!(service.signed_in());
}

#[test]
fn a_renewal_after_sign_out_and_back_in_is_stale() {
    let store = MemoryStore::default();
    let service = service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    service.with_session(|s| {
        s.sign_out().unwrap();
        s.sign_in(principal("alice"), ACCESS_1.to_string()).unwrap();
    });
    assert!(matches!(
        apply_renewal(&service, &lease, renewed_outcome("alice")),
        RenewalApplied::Stale
    ));
    assert_eq!(service.credentials().unwrap().1.as_str(), ACCESS_1);
}

#[test]
fn a_renewal_whose_refresh_token_cannot_be_stored_still_counts_as_renewed() {
    let store = MemoryStore::default();
    let service = service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    // Only the refresh-token write is refused: the access token rotates and
    // the caller must reschedule, with the store problem on the label.
    *store.broken.lock().unwrap() = Some(REFRESH_ACCOUNT.to_string());
    assert!(matches!(
        apply_renewal(&service, &lease, renewed_outcome("alice")),
        RenewalApplied::Renewed {
            expires_in: 3600,
            refresh_stored: false
        }
    ));
    assert_eq!(service.credentials().unwrap().1.as_str(), ACCESS_2);
    assert_eq!(
        store.read(REFRESH_ACCOUNT).unwrap().as_deref(),
        Some(REFRESH),
        "the old refresh token stays stored"
    );
    // With the access-token write refused too, nothing rotates.
    let lease = service.with_session(|s| s.lease()).unwrap();
    *store.broken.lock().unwrap() = Some("*".to_string());
    assert!(matches!(
        apply_renewal(&service, &lease, renewed_outcome("alice")),
        RenewalApplied::RetryLater("SESSION")
    ));
    assert_eq!(service.credentials().unwrap().1.as_str(), ACCESS_2);
}

#[test]
fn an_unverified_session_is_not_renewed() {
    let store = MemoryStore::default();
    let service = service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    service.with_session(|s| {
        s.restore().unwrap();
    });
    // `restore` bumps the generation, so the old lease is stale first.
    assert!(matches!(
        apply_renewal(&service, &lease, renewed_outcome("alice")),
        RenewalApplied::Stale
    ));
}

#[test]
fn the_refresh_token_is_read_only_under_a_current_lease() {
    let store = MemoryStore::default();
    let service = service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    assert_eq!(
        service
            .with_session(|s| s.refresh_token_for(&lease))
            .unwrap()
            .as_deref(),
        Some(REFRESH)
    );
    // Sign out and back in as someone else: the old lease must not read the
    // new session's refresh token.
    service.with_session(|s| {
        s.sign_out().unwrap();
        s.sign_in(principal("bob"), ACCESS_2.to_string()).unwrap();
        s.set_refresh_token(Some("vox_refresh_bobbobbobbobbobbobbobbobbobbobbob"))
            .unwrap();
    });
    assert!(matches!(
        service.with_session(|s| s.refresh_token_for(&lease)),
        Err(vox_agent_desktop_tauri_lib::context_session::SessionError::StaleLease)
    ));
    // After a renewal (new epoch) the pre-renewal lease is stale for reading too.
    let service = self::service(&store);
    let lease = service.with_session(|s| s.lease()).unwrap();
    apply_renewal(&service, &lease, renewed_outcome("alice"));
    assert!(matches!(
        service.with_session(|s| s.refresh_token_for(&lease)),
        Err(vox_agent_desktop_tauri_lib::context_session::SessionError::StaleLease)
    ));
}
