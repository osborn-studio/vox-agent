//! Per-launch loopback secret for the engine this shell starts (T29/T29b).
//!
//! * Every launch gets a fresh 256-bit secret, written to the engine's stdin
//!   pipe (`VOXAGENT_LOCAL_TOKEN_SOURCE=stdin`). No file, command line or
//!   environment value ever holds it; the engine detaches stdin after reading.
//! * The secret never goes over the network. The listener on the engine port
//!   must first answer a random challenge with HMAC-SHA256(secret, challenge);
//!   after that, every request carries a one-time signature over a timestamp,
//!   a nonce, the method and the exact target. A signature that reaches anyone
//!   else authorises only that one request, once.
//! * Requests are signed only while the current launch's handshake holds; a
//!   failed handshake revokes it.
use base64::Engine as _;
use chacha20poly1305::aead::{rand_core::RngCore, OsRng};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use zeroize::Zeroizing;

pub const AUTH_HEADER: &str = "X-VoxAgent-Local-Auth";
pub const CHALLENGE_HEADER: &str = "X-VoxAgent-Challenge";
pub const HANDSHAKE_PATH: &str = "/v1/local/handshake";
pub const TOKEN_SOURCE_ENV: &str = "VOXAGENT_LOCAL_TOKEN_SOURCE";
const MAX_HANDSHAKE_BYTES: usize = 1_024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandshakeError {
    Unreachable,
    /// The listener answered but could not prove it holds this launch's secret.
    IdentityUnverified,
}

/// A fresh 256-bit secret, base64url without padding (43 characters).
pub fn generate_token() -> Zeroizing<String> {
    let mut bytes = Zeroizing::new([0u8; 32]);
    OsRng.fill_bytes(&mut *bytes);
    Zeroizing::new(base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(*bytes))
}

fn random_hex(len: usize) -> String {
    let mut bytes = vec![0u8; len];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn random_challenge() -> String {
    random_hex(32)
}

fn hmac_hex(token: &str, message: &str) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(token.as_bytes()).expect("HMAC accepts any key");
    mac.update(message.as_bytes());
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn handshake_proof(token: &str, challenge: &str) -> String {
    hmac_hex(token, &format!("voxagent-local-handshake-v1|{challenge}"))
}

pub fn request_mac(token: &str, timestamp: u64, nonce: &str, method: &str, target: &str) -> String {
    hmac_hex(
        token,
        &format!("voxagent-local-request-v1|{timestamp}|{nonce}|{method}|{target}"),
    )
}

/// The `X-VoxAgent-Local-Auth` value for one request to `target` (path and
/// query exactly as sent).
pub fn sign_request(token: &str, method: &str, target: &str) -> String {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let nonce = random_hex(16);
    format!(
        "v1.{timestamp}.{nonce}.{}",
        request_mac(token, timestamp, &nonce, method, target)
    )
}

fn same(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes()
            .zip(b.bytes())
            .fold(0u8, |acc, (x, y)| acc | (x ^ y))
            == 0
}

#[derive(serde::Deserialize)]
struct HandshakeReply {
    proof: String,
    version: String,
}

/// Ask the listener at `base` (an `http://127.0.0.1:<port>` origin) to prove
/// it holds `token`. Nothing derived from the token is sent. Returns the
/// engine's reported version on success.
pub async fn verify_listener(base: &str, token: &str) -> Result<String, HandshakeError> {
    let client =
        loopback_client(Duration::from_secs(2)).map_err(|_| HandshakeError::Unreachable)?;
    let challenge = random_challenge();
    let url = format!("{}{HANDSHAKE_PATH}", base.trim_end_matches('/'));
    let response = client
        .get(url)
        .header(CHALLENGE_HEADER, &challenge)
        .send()
        .await
        .map_err(|_| HandshakeError::Unreachable)?;
    if response.status().as_u16() != 200 {
        return Err(HandshakeError::IdentityUnverified);
    }
    let body = crate::context_transport::read_bounded(response, MAX_HANDSHAKE_BYTES)
        .await
        .map_err(|_| HandshakeError::IdentityUnverified)?;
    let reply: HandshakeReply =
        serde_json::from_slice(&body).map_err(|_| HandshakeError::IdentityUnverified)?;
    if !same(&reply.proof, &handshake_proof(token, &challenge)) {
        return Err(HandshakeError::IdentityUnverified);
    }
    Ok(reply
        .version
        .chars()
        .filter(|c| !c.is_control())
        .take(40)
        .collect())
}

/// Client for the local engine: never a proxy, never a redirect.
pub fn loopback_client(timeout: Duration) -> reqwest::Result<reqwest::Client> {
    reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(timeout)
        .build()
}

/// The current launch's secret, whether its handshake holds, and the number
/// of the latest handshake attempt (only the latest may change `verified`).
struct Current {
    token: Option<Zeroizing<String>>,
    verified: bool,
    attempt: u64,
}

static CURRENT: Mutex<Current> = Mutex::new(Current {
    token: None,
    verified: false,
    attempt: 0,
});

fn current() -> std::sync::MutexGuard<'static, Current> {
    CURRENT.lock().unwrap_or_else(|p| p.into_inner())
}

/// A new launch: replaces the secret and resets verification.
pub fn begin_launch(token: Zeroizing<String>) {
    let mut state = current();
    state.token = Some(token);
    state.verified = false;
}

/// The engine stopped or exited: forget the secret.
pub fn end_launch() {
    let mut state = current();
    state.token = None;
    state.verified = false;
}

pub fn launch_token() -> Option<Zeroizing<String>> {
    current().token.clone()
}

/// Number a new handshake attempt; pass it to `record_handshake`.
pub fn begin_handshake() -> u64 {
    let mut state = current();
    state.attempt = state.attempt.wrapping_add(1);
    state.attempt
}

/// Record a handshake result for `token`, if it is still the current launch
/// and `attempt` is the latest one: an older attempt finishing late can never
/// restore trust a newer failure revoked. A failure revokes trust.
pub fn record_handshake(token: &str, attempt: u64, passed: bool) {
    let mut state = current();
    if state.attempt == attempt && state.token.as_deref().is_some_and(|t| same(t, token)) {
        state.verified = passed;
    }
}

/// Whether this shell launched the engine (and so must sign every request).
pub fn is_managed() -> bool {
    current().token.is_some()
}

/// The signature header for one request, only while this launch's handshake
/// holds. None for an engine this shell did not start (development).
pub fn signed_header(method: &str, target: &str) -> Option<String> {
    let state = current();
    if !state.verified {
        return None;
    }
    state
        .token
        .as_deref()
        .map(|token| sign_request(token, method, target))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_are_fresh_256_bit_url_safe_values() {
        let a = generate_token();
        let b = generate_token();
        assert_eq!(a.len(), 43);
        assert!(a
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'));
        assert_ne!(*a, *b);
        assert_eq!(random_challenge().len(), 64);
    }

    #[test]
    fn proofs_and_signatures_match_the_engine_algorithm() {
        // Vectors computed with engine.local_auth.
        let token = "Wq3y_local-token-0123456789abcdefghijklmnop";
        assert_eq!(
            handshake_proof(token, "0123456789abcdef0123456789abcdef"),
            "6fe16d448a67accc4f235b998fe213bb8210be8e52e11fe204fb89df777af4a1"
        );
        assert_eq!(
            request_mac(
                token,
                1_760_000_000,
                "0123456789abcdef0123456789abcdef",
                "GET",
                "/v1/tasks?limit=5"
            ),
            "34c73386d22515b599c53631ab5d6478c591dde8d190e46c40eb267a1120d5d3"
        );
    }

    #[test]
    fn a_signature_never_contains_the_secret_and_is_unique() {
        let token = "launch-secret-0123456789abcdefghijklmnopqrstu";
        let a = sign_request(token, "GET", "/health");
        let b = sign_request(token, "GET", "/health");
        assert!(!a.contains(token));
        assert_ne!(a, b, "fresh nonce per request");
        let parts: Vec<&str> = a.split('.').collect();
        assert_eq!(parts.len(), 4);
        assert_eq!(parts[0], "v1");
        assert_eq!(parts[2].len(), 32);
        assert_eq!(
            parts[3],
            request_mac(token, parts[1].parse().unwrap(), parts[2], "GET", "/health")
        );
    }

    #[test]
    fn requests_are_signed_only_while_this_launchs_handshake_holds() {
        end_launch();
        assert!(signed_header("GET", "/health").is_none());
        begin_launch(Zeroizing::new("launch-one".into()));
        assert!(
            signed_header("GET", "/health").is_none(),
            "not verified yet"
        );
        let first = begin_handshake();
        record_handshake("some-other-token", first, true);
        assert!(signed_header("GET", "/health").is_none());
        record_handshake("launch-one", first, true);
        assert!(signed_header("GET", "/health").is_some());
        assert!(is_managed());
        let older = begin_handshake();
        let newer = begin_handshake();
        record_handshake("launch-one", newer, false);
        assert!(
            signed_header("GET", "/health").is_none(),
            "a failed handshake revokes trust"
        );
        record_handshake("launch-one", older, true);
        assert!(
            signed_header("GET", "/health").is_none(),
            "an older attempt finishing late cannot restore trust"
        );
        let again = begin_handshake();
        record_handshake("launch-one", again, true);
        begin_launch(Zeroizing::new("launch-two".into()));
        assert!(
            signed_header("GET", "/health").is_none(),
            "a new launch must verify again"
        );
        end_launch();
        assert!(launch_token().is_none());
        assert!(!is_managed());
    }
}
