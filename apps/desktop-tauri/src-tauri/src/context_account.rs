//! Feishu account: sign-in outcome codes, conversation discovery and server
//! sign-out (T24, contract fixture T03p).
//!
//! * Discovery is `GET /v1/conversations` with the session token, decoded
//!   strictly. The listing must name the signed-in user; any other identity
//!   is `IdentityChanged` and the caller signs out.
//! * Refusals become codes. Server bodies are never shown or kept.
//! * Only server-issued conversation ids (`fc_…`) are accepted; raw Feishu
//!   chat ids never reach the WebView.
use crate::context_journal::Journal;
use crate::context_login::{refusal_code, LoginError, RenewOutcome, SignedIn};
use crate::context_service::{ContextService, ServiceError};
use crate::context_session::{SessionAuthority, SessionError, SessionLease, TokenStore};
use crate::context_transport::read_bounded;
use crate::context_wire::ContextEndpoint;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::sync::{Arc, Mutex};
use zeroize::Zeroizing;

const MAX_LISTING_BYTES: usize = 262_144;
const MAX_ERROR_BYTES: usize = 4_096;
const MAX_CONVERSATIONS: usize = 500;

/// What the Context panel may say about a sign-in or discovery failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum AccountError {
    /// No Context server is configured for this build.
    NotAvailable,
    SignInInProgress,
    AlreadySignedIn,
    /// Signed out (or replaced) while this request was running.
    Cancelled,
    NotSignedIn,
    /// The server no longer accepts the session.
    SignInRequired,
    AccountDisabled,
    OrganizationNotConfigured,
    /// Signed in, but not allowed to list conversations.
    NotPermitted,
    /// The listing named a different user; signed out.
    IdentityChanged,
    RateLimited,
    LoginUnavailable,
    ConversationsUnavailable,
    Denied,
    Timeout,
    Expired,
    Network,
    BrowserUnavailable,
    /// The OS credential store could not save the session.
    StoreUnavailable,
    InvalidResponse,
}

impl From<LoginError> for AccountError {
    fn from(error: LoginError) -> Self {
        match error {
            LoginError::AccountDisabled => Self::AccountDisabled,
            LoginError::OrganizationNotConfigured => Self::OrganizationNotConfigured,
            LoginError::RateLimited => Self::RateLimited,
            LoginError::NotConfigured | LoginError::Refused(_) => Self::LoginUnavailable,
            LoginError::Denied => Self::Denied,
            LoginError::Timeout | LoginError::TooManyAttempts => Self::Timeout,
            LoginError::Expired => Self::Expired,
            LoginError::Network | LoginError::Listener => Self::Network,
            LoginError::BrowserUnavailable => Self::BrowserUnavailable,
            LoginError::InvalidResponse | LoginError::UnexpectedBrowserUrl => Self::InvalidResponse,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiscoveryError {
    SignInRequired,
    AccountDisabled,
    NotPermitted,
    RateLimited,
    Unavailable,
    IdentityChanged,
    InvalidResponse,
    Network,
}

impl From<DiscoveryError> for AccountError {
    fn from(error: DiscoveryError) -> Self {
        match error {
            DiscoveryError::SignInRequired => Self::SignInRequired,
            DiscoveryError::AccountDisabled => Self::AccountDisabled,
            DiscoveryError::NotPermitted => Self::NotPermitted,
            DiscoveryError::RateLimited => Self::RateLimited,
            DiscoveryError::Unavailable => Self::ConversationsUnavailable,
            DiscoveryError::IdentityChanged => Self::IdentityChanged,
            DiscoveryError::InvalidResponse => Self::InvalidResponse,
            DiscoveryError::Network => Self::Network,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ConversationKind {
    Personal,
    Group,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Conversation {
    pub id: String,
    pub kind: ConversationKind,
    pub name: String,
    /// `text_send`; `discovery_only` conversations are listed but not writable.
    pub can_send: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Discovery {
    pub conversations: Vec<Conversation>,
    /// Entries that failed validation and were left out.
    pub omitted: usize,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawIdentity {
    user_id: String,
}

#[derive(Deserialize)]
struct RawListing {
    identity: RawIdentity,
    conversations: Vec<serde_json::Value>,
}

/// Additive fields (e.g. `readCapability`) are allowed and ignored.
#[derive(Deserialize)]
struct RawConversation {
    id: String,
    kind: String,
    name: String,
    capability: String,
}

fn valid_id(id: &str) -> bool {
    id.strip_prefix("fc_").is_some_and(|rest| {
        (16..=64).contains(&rest.len())
            && rest
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    })
}

fn valid_name(name: &str) -> bool {
    !name.trim().is_empty() && name.chars().count() <= 120 && !name.chars().any(char::is_control)
}

fn conversation(raw: RawConversation) -> Option<Conversation> {
    let kind = match raw.kind.as_str() {
        "personal" => ConversationKind::Personal,
        "group" => ConversationKind::Group,
        _ => return None,
    };
    let can_send = match raw.capability.as_str() {
        "text_send" => true,
        "discovery_only" => false,
        _ => return None,
    };
    (valid_id(&raw.id) && valid_name(&raw.name)).then(|| Conversation {
        id: raw.id,
        kind,
        name: raw.name.trim().to_string(),
        can_send,
    })
}

/// Decode a `GET /v1/conversations` listing for the signed-in `actor`.
pub fn decode_conversations(bytes: &[u8], actor: &str) -> Result<Discovery, DiscoveryError> {
    if bytes.len() > MAX_LISTING_BYTES {
        return Err(DiscoveryError::InvalidResponse);
    }
    let raw: RawListing =
        serde_json::from_slice(bytes).map_err(|_| DiscoveryError::InvalidResponse)?;
    if raw.identity.user_id != actor {
        return Err(DiscoveryError::IdentityChanged);
    }
    if raw.conversations.len() > MAX_CONVERSATIONS {
        return Err(DiscoveryError::InvalidResponse);
    }
    let mut seen = HashSet::new();
    let mut found = Discovery {
        conversations: Vec::new(),
        omitted: 0,
    };
    for value in raw.conversations {
        match serde_json::from_value::<RawConversation>(value)
            .ok()
            .and_then(conversation)
            .filter(|c| seen.insert(c.id.clone()))
        {
            Some(item) => found.conversations.push(item),
            None => found.omitted += 1,
        }
    }
    Ok(found)
}

fn discovery_refusal(status: u16, code: Option<&str>) -> DiscoveryError {
    match (status, code) {
        (401, _) => DiscoveryError::SignInRequired,
        (403, Some("ACCOUNT_DISABLED")) => DiscoveryError::AccountDisabled,
        (403, _) => DiscoveryError::NotPermitted,
        (429, _) => DiscoveryError::RateLimited,
        _ => DiscoveryError::Unavailable,
    }
}

pub(crate) fn join(endpoint: &ContextEndpoint, path: &str) -> Option<reqwest::Url> {
    reqwest::Url::parse(endpoint.canonical())
        .ok()?
        .join(path)
        .ok()
}

/// List the conversations `actor` may see. Redirects are never followed.
pub async fn discover(
    endpoint: &ContextEndpoint,
    token: &str,
    actor: &str,
) -> Result<Discovery, DiscoveryError> {
    let client = endpoint
        .transport_client()
        .map_err(|_| DiscoveryError::Network)?;
    let url = join(endpoint, "v1/conversations").ok_or(DiscoveryError::Network)?;
    let response = client
        .get(url)
        .bearer_auth(token)
        .send()
        .await
        .map_err(|_| DiscoveryError::Network)?;
    let status = response.status().as_u16();
    if status != 200 {
        let code = read_bounded(response, MAX_ERROR_BYTES)
            .await
            .ok()
            .and_then(|body| refusal_code(&body));
        return Err(discovery_refusal(status, code.as_deref()));
    }
    let body = read_bounded(response, MAX_LISTING_BYTES)
        .await
        .map_err(|_| DiscoveryError::InvalidResponse)?;
    decode_conversations(&body, actor)
}

/// Ask the server to end the session. Returns whether it confirmed; the local
/// sign-out never depends on this.
pub async fn logout(endpoint: &ContextEndpoint, token: &str) -> bool {
    let (Ok(client), Some(url)) = (
        endpoint.transport_client(),
        join(endpoint, "v1/auth/feishu/logout"),
    ) else {
        return false;
    };
    match client.post(url).bearer_auth(token).send().await {
        Ok(response) => response.status().as_u16() == 200,
        Err(_) => false,
    }
}

/// Discovery for the service's verified session. A result for a session
/// that changed meanwhile is dropped. The caller ends the account on
/// `IdentityChanged`, `SignInRequired` and `AccountDisabled`, under the lock
/// that decides which session is current.
pub async fn discover_for<S: TokenStore>(
    service: &ContextService<S>,
) -> Result<Discovery, AccountError> {
    let (lease, token) = service.credentials().map_err(|error| match error {
        ServiceError::Unverified => AccountError::SignInRequired,
        _ => AccountError::NotSignedIn,
    })?;
    let result = discover(service.endpoint(), &token, lease.principal().actor()).await;
    if !service.still_current(&lease) {
        return Err(AccountError::Cancelled);
    }
    result.map_err(AccountError::from)
}

/// One sign-in at a time, and sign-out cancels a sign-in still running.
/// Callers check `current` and apply the result under the same lock that
/// `end` is called under, so a cancelled sign-in can never take effect.
#[derive(Default)]
pub struct AccountGate {
    signing_in: std::sync::atomic::AtomicBool,
    epoch: std::sync::atomic::AtomicU64,
}

/// Held while a sign-in runs; releases the gate on drop.
pub struct SignInTicket<'a> {
    gate: &'a AccountGate,
    epoch: u64,
}

impl Drop for SignInTicket<'_> {
    fn drop(&mut self) {
        self.gate
            .signing_in
            .store(false, std::sync::atomic::Ordering::SeqCst);
    }
}

impl AccountGate {
    pub fn begin(&self) -> Result<SignInTicket<'_>, AccountError> {
        use std::sync::atomic::Ordering::SeqCst;
        if self.signing_in.swap(true, SeqCst) {
            return Err(AccountError::SignInInProgress);
        }
        Ok(SignInTicket {
            gate: self,
            epoch: self.epoch.load(SeqCst),
        })
    }
    /// Sign-out: any sign-in started before this no longer applies.
    pub fn end(&self) {
        self.epoch.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    }
    pub fn current(&self, ticket: &SignInTicket<'_>) -> bool {
        std::ptr::eq(ticket.gate, self)
            && ticket.epoch == self.epoch.load(std::sync::atomic::Ordering::SeqCst)
    }
}

struct SlotInner<S: TokenStore> {
    service: Option<Arc<ContextService<S>>>,
    display_name: Option<String>,
}

/// The single account of this app. Sign-in publishing, sign-out and the
/// credential-store writes and deletes all happen under one lock, so a
/// sign-out can never delete the entries of a sign-in that completed after it,
/// and a sign-in can never replace an active account.
pub struct AccountSlot<S: TokenStore> {
    inner: Mutex<SlotInner<S>>,
    gate: AccountGate,
}

impl<S: TokenStore> Default for AccountSlot<S> {
    fn default() -> Self {
        Self {
            inner: Mutex::new(SlotInner {
                service: None,
                display_name: None,
            }),
            gate: AccountGate::default(),
        }
    }
}

/// What a sign-out did. `logout` carries the old token only so the caller
/// can ask the server to end that session; it is zeroized on drop.
pub struct Ended {
    pub store_cleared: bool,
    pub logout: Option<(ContextEndpoint, Zeroizing<String>)>,
}

impl<S: TokenStore> AccountSlot<S> {
    fn lock(&self) -> std::sync::MutexGuard<'_, SlotInner<S>> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    pub fn current(&self) -> Option<Arc<ContextService<S>>> {
        self.lock().service.clone()
    }

    pub fn display_name(&self) -> Option<String> {
        self.lock().display_name.clone()
    }

    /// Configure a service directly (Rust-side setup and tests).
    pub fn configure(&self, service: ContextService<S>) {
        self.lock().service = Some(Arc::new(service));
    }

    /// Take the gate first, then check: two sign-ins can never both pass.
    pub fn begin_sign_in(&self) -> Result<SignInTicket<'_>, AccountError> {
        let ticket = self.gate.begin()?;
        if self.lock().service.as_ref().is_some_and(|s| s.signed_in()) {
            return Err(AccountError::AlreadySignedIn);
        }
        Ok(ticket)
    }

    /// Store the session and make it current, unless a sign-out happened
    /// since `begin_sign_in` or another account is active.
    ///
    /// `journal` is the encrypted journal this session may deliver from
    /// (`context_commands::open_journal`); `None` keeps delivery blocked.
    pub fn publish(
        &self,
        ticket: &SignInTicket<'_>,
        endpoint: ContextEndpoint,
        signed: SignedIn,
        store: S,
        journal: Option<Journal>,
    ) -> Result<Option<String>, AccountError> {
        let mut inner = self.lock();
        if !self.gate.current(ticket) {
            return Err(AccountError::Cancelled);
        }
        if inner.service.as_ref().is_some_and(|s| s.signed_in()) {
            return Err(AccountError::AlreadySignedIn);
        }
        let mut session = SessionAuthority::new(store);
        session
            .sign_in(signed.principal.clone(), signed.token.to_string())
            .map_err(|_| AccountError::StoreUnavailable)?;
        // The refresh token lives beside the session; a server without one
        // leaves no stale entry behind. A store that cannot hold it signs
        // the session out again rather than keeping a half-stored session.
        if session
            .set_refresh_token(signed.refresh_token.as_deref().map(String::as_str))
            .is_err()
        {
            let _ = session.sign_out();
            return Err(AccountError::StoreUnavailable);
        }
        let name = (!signed.display_name.trim().is_empty()).then(|| signed.display_name.clone());
        inner.service = Some(Arc::new(ContextService::new(
            endpoint,
            signed.destinations,
            session,
            journal,
        )));
        inner.display_name = name.clone();
        Ok(name)
    }

    /// Sign out: cancel a running sign-in, sign the session out and clear the
    /// credential store, all before another sign-in can publish. Without a
    /// session, a store `fresh` opens is cleared, so leftovers from an earlier
    /// run or a failed delete are removed too.
    pub fn end(&self, fresh: impl FnOnce() -> S) -> Ended {
        let mut inner = self.lock();
        self.gate.end();
        inner.display_name = None;
        match inner.service.take() {
            Some(service) => {
                let logout = service
                    .credentials()
                    .ok()
                    .map(|(_, token)| (service.endpoint().clone(), token));
                let store_cleared = service.with_session(|s| s.sign_out()).is_ok();
                Ended {
                    store_cleared,
                    logout,
                }
            }
            None => Ended {
                store_cleared: SessionAuthority::new(fresh()).sign_out().is_ok(),
                logout: None,
            },
        }
    }

    /// End the account only if `service` is still current: a late refusal
    /// for an old session never signs out a newer one. Returns whether this
    /// call ended it, decided under the slot lock, so callers can tie their
    /// own follow-up (views, schedules, events) to the real transition.
    pub fn end_if(&self, service: &Arc<ContextService<S>>) -> bool {
        let mut inner = self.lock();
        let current = inner
            .service
            .as_ref()
            .is_some_and(|current| Arc::ptr_eq(current, service));
        if current {
            self.gate.end();
            inner.service = None;
            inner.display_name = None;
            let _ = service.with_session(|s| s.sign_out());
        }
        current
    }
}

/// What applying a `RenewOutcome` did to the session.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RenewalApplied {
    /// The access token was rotated; schedule the next renewal.
    /// `refresh_stored` is false when the credential store refused the
    /// refresh token the server returned (the old one stays stored).
    Renewed {
        expires_in: u64,
        refresh_stored: bool,
    },
    /// A newer sign-in or renewal happened meanwhile; nothing changed.
    Stale,
    /// The caller must end the account (the session itself is already
    /// signed out for `IDENTITY_CHANGED`; the server ended it otherwise).
    SignOut(&'static str),
    /// Nothing changed; try again at the next tick.
    RetryLater(&'static str),
}

/// Apply a renewal result under the lease captured before the request. The
/// principal the server reports must match the session's; a different one
/// signs the session out. A stale lease (sign-out, sign-in or another
/// renewal meanwhile) is dropped silently.
pub fn apply_renewal<S: TokenStore>(
    service: &ContextService<S>,
    lease: &SessionLease,
    outcome: RenewOutcome,
) -> RenewalApplied {
    match outcome {
        RenewOutcome::Renewed {
            token,
            refresh_token,
            expires_in,
            principal,
        } => service.with_session(|session| {
            match session.refresh(lease, &principal, token.to_string()) {
                Ok(()) => RenewalApplied::Renewed {
                    expires_in,
                    // The old refresh token stays stored on failure; if the
                    // server rotated it, the next renewal fails closed (401).
                    refresh_stored: session.set_refresh_token(Some(&refresh_token)).is_ok(),
                },
                Err(SessionError::IdentityChanged) => RenewalApplied::SignOut("IDENTITY_CHANGED"),
                Err(SessionError::StaleLease | SessionError::NotSignedIn) => RenewalApplied::Stale,
                Err(_) => RenewalApplied::RetryLater("SESSION"),
            }
        }),
        RenewOutcome::SignedOut(code) => RenewalApplied::SignOut(code),
        RenewOutcome::RetryLater(code) => RenewalApplied::RetryLater(code),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_sign_in_at_a_time_and_sign_out_cancels_it() {
        let gate = AccountGate::default();
        let ticket = gate.begin().unwrap();
        assert_eq!(gate.begin().err(), Some(AccountError::SignInInProgress));
        assert!(gate.current(&ticket));
        gate.end();
        assert!(!gate.current(&ticket), "signed out meanwhile");
        drop(ticket);
        let again = gate.begin().unwrap();
        assert!(gate.current(&again));
        let other = AccountGate::default();
        assert!(!other.current(&again));
    }
}
