//! Windows sign-in through a one-shot loopback callback (T24a, owner decision D5).
//!
//! 1. Bind `127.0.0.1:0` and POST `/v1/auth/feishu/begin {loopbackPort}`.
//! 2. Open the server's browser URL, only if it is exactly
//!    `<endpoint>/v1/auth/feishu/browser?state=<requestId>`.
//! 3. Accept one callback on `/vox-agent/login-complete` with the matching
//!    `requestId` and `Host`; wrong requests get 400/404 (bounded), and the
//!    listener closes after the first valid callback (no replay).
//! 4. POST `/v1/auth/feishu/redeem` with the poll secret as Bearer. A captured
//!    redirect alone cannot redeem: it lacks the poll secret.
//!
//! The token never leaves Rust; `SignedIn`'s `Debug` is redacted.
use crate::context_session::{DestinationKey, PrincipalKey};
use crate::context_transport::read_bounded;
use crate::context_wire::ContextEndpoint;
use serde::Deserialize;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const CALLBACK_PATH: &str = "/vox-agent/login-complete";
const MAX_REQUEST_BYTES: usize = 8_192;
const MAX_BAD_ATTEMPTS: u32 = 32;
/// Total time allowed to receive one request head.
const HEAD_DEADLINE: Duration = Duration::from_secs(2);
const MAX_RESPONSE_BYTES: usize = 16_384;
const MAX_ERROR_BYTES: usize = 4_096;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LoginError {
    /// The loopback listener could not be opened.
    Listener,
    Network,
    /// The server refused with a status this client does not classify.
    Refused(u16),
    /// The account exists but is disabled or moved to another organization.
    AccountDisabled,
    /// The Feishu tenant is not mapped to an organization on this server.
    OrganizationNotConfigured,
    RateLimited,
    /// Feishu sign-in is not set up on this server.
    NotConfigured,
    /// The sign-in attempt expired on the server; start again.
    Expired,
    /// The user or provider declined the sign-in.
    Denied,
    Timeout,
    TooManyAttempts,
    InvalidResponse,
    /// The server sent a browser URL outside the expected origin and path.
    UnexpectedBrowserUrl,
    BrowserUnavailable,
}

fn valid_token(value: &str) -> bool {
    (32..=128).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// Constant-time comparison for the request id.
fn same(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes()
            .zip(b.bytes())
            .fold(0u8, |acc, (x, y)| acc | (x ^ y))
            == 0
}

pub struct LoopbackReceiver {
    listener: TcpListener,
    port: u16,
}

enum Callback {
    Completion(String),
    Denied,
    Bad,
    NotFound,
}

impl LoopbackReceiver {
    pub fn bind() -> Result<Self, LoginError> {
        let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|_| LoginError::Listener)?;
        let port = listener
            .local_addr()
            .map_err(|_| LoginError::Listener)?
            .port();
        Ok(Self { listener, port })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    fn page(stream: &mut TcpStream, status: &str, message: &str) {
        let body = format!(
            "<!doctype html><meta charset=\"utf-8\"><title>Vox Agent</title><p>{message}</p>"
        );
        let _ = write!(
            stream,
            "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n\
             Content-Security-Policy: default-src 'none'\r\nCache-Control: no-store\r\n\
             Referrer-Policy: no-referrer\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
    }

    /// Read the request head with an absolute deadline: a client trickling
    /// bytes cannot hold the (serial) listener longer than `HEAD_DEADLINE`.
    /// Only the bytes up to the first blank line are returned.
    fn read_head(stream: &mut TcpStream) -> Option<String> {
        let deadline = Instant::now() + HEAD_DEADLINE;
        let mut data = Vec::new();
        let mut buffer = [0u8; 1024];
        loop {
            if let Some(end) = data.windows(4).position(|w| w == b"\r\n\r\n") {
                data.truncate(end);
                return String::from_utf8(data).ok();
            }
            let remaining = deadline.checked_duration_since(Instant::now())?;
            stream
                .set_read_timeout(Some(remaining.max(Duration::from_millis(1))))
                .ok()?;
            let n = stream.read(&mut buffer).ok()?;
            if n == 0 || data.len() + n > MAX_REQUEST_BYTES {
                return None;
            }
            data.extend_from_slice(&buffer[..n]);
        }
    }

    fn classify(&self, head: &str, request_id: &str) -> Callback {
        let mut lines = head.split("\r\n");
        let request_line: Vec<&str> = lines.next().unwrap_or("").split(' ').collect();
        let [method, target, version] = request_line[..] else {
            return Callback::Bad;
        };
        if method != "GET"
            || !matches!(version, "HTTP/1.1" | "HTTP/1.0")
            || !target.starts_with('/')
        {
            return Callback::NotFound;
        }
        let mut hosts = Vec::new();
        for line in lines {
            let Some((name, value)) = line.split_once(':') else {
                return Callback::Bad;
            };
            let name = name.trim();
            if name.eq_ignore_ascii_case("host") {
                hosts.push(value.trim());
            }
            // A callback is a bare GET: any body framing is refused.
            if name.eq_ignore_ascii_case("content-length") && value.trim() != "0"
                || name.eq_ignore_ascii_case("transfer-encoding")
            {
                return Callback::Bad;
            }
        }
        let Ok(url) = reqwest::Url::parse(&format!("http://127.0.0.1{target}")) else {
            return Callback::Bad;
        };
        if url.path() != CALLBACK_PATH {
            return Callback::NotFound;
        }
        if hosts.len() != 1 || hosts[0] != format!("127.0.0.1:{}", self.port) {
            return Callback::Bad;
        }
        let query: Vec<(String, String)> = url.query_pairs().into_owned().collect();
        let mut names: Vec<&str> = query.iter().map(|(k, _)| k.as_str()).collect();
        names.sort_unstable();
        if names.windows(2).any(|w| w[0] == w[1]) {
            return Callback::Bad; // duplicate parameters are ambiguous
        }
        let get = |key: &str| {
            query
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v.as_str())
        };
        if !get("requestId").is_some_and(|id| same(id, request_id)) {
            return Callback::Bad;
        }
        if get("error").is_some() {
            return Callback::Denied;
        }
        match get("completion") {
            Some(completion) if valid_token(completion) => {
                Callback::Completion(completion.to_string())
            }
            _ => Callback::Bad,
        }
    }

    /// Wait for the one valid callback for `request_id`, then close.
    pub fn receive(self, request_id: &str, timeout: Duration) -> Result<String, LoginError> {
        self.listener
            .set_nonblocking(true)
            .map_err(|_| LoginError::Listener)?;
        let deadline = Instant::now() + timeout;
        let mut bad = 0u32;
        while Instant::now() < deadline {
            let mut stream = match self.listener.accept() {
                Ok((stream, _)) => stream,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(20));
                    continue;
                }
                Err(_) => return Err(LoginError::Listener),
            };
            let _ = stream.set_nonblocking(false);
            let head = Self::read_head(&mut stream).unwrap_or_default();
            let callback = self.classify(&head, request_id);
            // A request that finished reading after the deadline is too late.
            if Instant::now() >= deadline {
                Self::page(
                    &mut stream,
                    "400 Bad Request",
                    "This sign-in link has expired.",
                );
                return Err(LoginError::Timeout);
            }
            match callback {
                Callback::Completion(completion) => {
                    Self::page(
                        &mut stream,
                        "200 OK",
                        "Signed in. You can return to Vox Agent.",
                    );
                    return Ok(completion);
                }
                Callback::Denied => {
                    Self::page(
                        &mut stream,
                        "200 OK",
                        "Sign-in was not completed. Return to Vox Agent to try again.",
                    );
                    return Err(LoginError::Denied);
                }
                Callback::NotFound => Self::page(&mut stream, "404 Not Found", "Not found."),
                Callback::Bad => Self::page(
                    &mut stream,
                    "400 Bad Request",
                    "This sign-in link is not valid.",
                ),
            }
            bad += 1;
            if bad >= MAX_BAD_ATTEMPTS {
                return Err(LoginError::TooManyAttempts);
            }
        }
        Err(LoginError::Timeout)
    }
}

/// The result of a sign-in. `Debug` never shows the token.
pub struct SignedIn {
    pub token: Zeroizing<String>,
    pub principal: PrincipalKey,
    pub destinations: Vec<DestinationKey>,
    pub display_name: String,
}

impl std::fmt::Debug for SignedIn {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SignedIn")
            .field("principal", &self.principal)
            .field("destinations", &self.destinations)
            .field("token", &"<redacted>")
            .finish_non_exhaustive()
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Begun {
    request_id: String,
    poll_secret: String,
    #[serde(rename = "browserURL")]
    browser_url: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Profile {
    user_id: String,
    org_id: String,
    name: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Redeemed {
    token: String,
    profile: Profile,
    #[serde(default)]
    target_ids: Vec<String>,
}

/// Exactly `<endpoint>/v1/auth/feishu/browser?state=<request_id>`.
fn expected_browser_url(
    endpoint: &ContextEndpoint,
    value: &str,
    request_id: &str,
) -> Option<reqwest::Url> {
    let url = reqwest::Url::parse(value).ok()?;
    let base = reqwest::Url::parse(endpoint.canonical()).ok()?;
    let pairs: Vec<(String, String)> = url.query_pairs().into_owned().collect();
    (url.origin() == base.origin()
        && url.path() == "/v1/auth/feishu/browser"
        && url.fragment().is_none()
        && url.username().is_empty()
        && pairs.len() == 1
        && pairs[0].0 == "state"
        && same(&pairs[0].1, request_id))
    .then_some(url)
}

async fn post_json(
    endpoint: &ContextEndpoint,
    path: &str,
    bearer: Option<&str>,
    body: serde_json::Value,
) -> Result<Vec<u8>, LoginError> {
    let client = endpoint
        .transport_client()
        .map_err(|_| LoginError::Network)?;
    let url = reqwest::Url::parse(endpoint.canonical())
        .and_then(|base| base.join(path))
        .map_err(|_| LoginError::Network)?;
    let mut request = client.post(url).json(&body);
    if let Some(token) = bearer {
        request = request.bearer_auth(token);
    }
    let response = request.send().await.map_err(|_| LoginError::Network)?;
    let status = response.status().as_u16();
    if status != 200 {
        // Only a short, known code is read from the body; the body itself is
        // never kept or shown.
        let code = read_bounded(response, MAX_ERROR_BYTES)
            .await
            .ok()
            .and_then(|body| refusal_code(&body));
        return Err(classify_refusal(status, code.as_deref()));
    }
    read_bounded(response, MAX_RESPONSE_BYTES)
        .await
        .map_err(|_| LoginError::InvalidResponse)
}

/// `{"detail": "CODE"}` or `{"detail": {"code": "CODE"}}`, as FastAPI sends.
pub(crate) fn refusal_code(body: &[u8]) -> Option<String> {
    let value: serde_json::Value = serde_json::from_slice(body).ok()?;
    let detail = value.get("detail")?;
    let code = detail
        .as_str()
        .or_else(|| detail.get("code").and_then(serde_json::Value::as_str))?;
    (code.len() <= 64 && code.bytes().all(|b| b.is_ascii_uppercase() || b == b'_'))
        .then(|| code.to_string())
}

fn classify_refusal(status: u16, code: Option<&str>) -> LoginError {
    match (status, code) {
        (403, Some("ACCOUNT_DISABLED")) => LoginError::AccountDisabled,
        (403, Some("ORGANIZATION_NOT_CONFIGURED")) => LoginError::OrganizationNotConfigured,
        (410, _) => LoginError::Expired,
        (429, _) => LoginError::RateLimited,
        (503, Some("FEISHU_LOGIN_NOT_CONFIGURED")) => LoginError::NotConfigured,
        _ => LoginError::Refused(status),
    }
}

/// Run the whole sign-in. `open_browser` opens the (validated) URL in the
/// user's browser; `timeout` bounds the wait for the callback.
pub async fn sign_in(
    endpoint: &ContextEndpoint,
    open_browser: impl FnOnce(&reqwest::Url) -> Result<(), ()>,
    timeout: Duration,
) -> Result<SignedIn, LoginError> {
    let receiver = LoopbackReceiver::bind()?;
    let begun: Begun = serde_json::from_slice(
        &post_json(
            endpoint,
            "/v1/auth/feishu/begin",
            None,
            serde_json::json!({ "loopbackPort": receiver.port() }),
        )
        .await?,
    )
    .map_err(|_| LoginError::InvalidResponse)?;
    if !valid_token(&begun.request_id) || !valid_token(&begun.poll_secret) {
        return Err(LoginError::InvalidResponse);
    }
    let poll_secret = Zeroizing::new(begun.poll_secret);
    let url = expected_browser_url(endpoint, &begun.browser_url, &begun.request_id)
        .ok_or(LoginError::UnexpectedBrowserUrl)?;
    open_browser(&url).map_err(|_| LoginError::BrowserUnavailable)?;
    let request_id = begun.request_id.clone();
    let completion = tokio::task::spawn_blocking(move || receiver.receive(&request_id, timeout))
        .await
        .map_err(|_| LoginError::Listener)??;
    let redeemed: Redeemed = serde_json::from_slice(
        &post_json(
            endpoint,
            "/v1/auth/feishu/redeem",
            Some(&poll_secret),
            serde_json::json!({ "requestId": begun.request_id, "completion": completion }),
        )
        .await?,
    )
    .map_err(|_| LoginError::InvalidResponse)?;
    let token = Zeroizing::new(redeemed.token);
    // Server contract: vox_sso_ followed by 32-128 URL-safe characters.
    if !token.strip_prefix("vox_sso_").is_some_and(valid_token) {
        return Err(LoginError::InvalidResponse);
    }
    let principal = PrincipalKey::new(
        endpoint,
        &redeemed.profile.org_id,
        &redeemed.profile.user_id,
        1,
    )
    .map_err(|_| LoginError::InvalidResponse)?;
    let display_name: String = redeemed
        .profile
        .name
        .chars()
        .filter(|c| !c.is_control())
        .take(120)
        .collect();
    let destinations = redeemed
        .target_ids
        .iter()
        .filter_map(|id| DestinationKey::new(id).ok())
        .collect();
    Ok(SignedIn {
        token,
        principal,
        destinations,
        display_name,
    })
}
