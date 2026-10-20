//! Rust-owned cloud identity for Context requests (T06).
//!
//! `SessionAuthority` is the single owner of the desktop's cloud session. The
//! WebView never sees the token. Identity is a `PrincipalKey` (endpoint, org,
//! actor, contract version); it is also the namespace for local queues and
//! eventIds (owner decision D8). The destination (`DestinationKey`) is part of
//! each payload, not of the identity.
//!
//! * Every authority has a process-unique id; leases and restore tickets carry
//!   it, so they never work against another (or a rebuilt) authority.
//! * `generation` increments on every sign-in, sign-out, restore and identity
//!   change, so leases from before an A -> sign out -> A cycle are stale.
//! * `epoch` increments on a same-principal token refresh; in-flight leases stay
//!   valid and use the new token, but a refresh result from an older epoch is
//!   refused.
//! * After a restart the session is `Unverified`: queue scope is available for
//!   local enqueueing, but no dispatch lease is issued until the principal is
//!   confirmed online with the ticket `restore` returned.
use crate::context_wire::ContextEndpoint;
use crate::secrets::{validate_secret_size, NativeSecretStore, SecretError};
use std::sync::atomic::{AtomicU64, Ordering};

const TOKEN_ACCOUNT: &str = "context_session_token";
const PRINCIPAL_ACCOUNT: &str = "context_session_principal";
/// Credential-store entry for the Feishu refresh token (spec §3.6). Read
/// only when a renewal is due; never held in memory between renewals.
pub const REFRESH_ACCOUNT: &str = "context_refresh_token";

/// Process-unique id per authority, never reused.
static NEXT_AUTHORITY: AtomicU64 = AtomicU64::new(1);

fn valid_label(value: &str) -> bool {
    !value.is_empty() && value.chars().count() <= 128 && !value.chars().any(char::is_control)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PrincipalKey {
    endpoint: String,
    org: String,
    actor: String,
    contract: u32,
}

impl PrincipalKey {
    pub fn new(
        endpoint: &ContextEndpoint,
        org: &str,
        actor: &str,
        contract: u32,
    ) -> Result<Self, SessionError> {
        if !valid_label(org) || !valid_label(actor) || contract == 0 {
            return Err(SessionError::InvalidPrincipal);
        }
        Ok(Self {
            endpoint: endpoint.canonical().to_string(),
            org: org.to_string(),
            actor: actor.to_string(),
            contract,
        })
    }
    /// Server user id of this principal.
    pub fn actor(&self) -> &str {
        &self.actor
    }
    /// Canonical endpoint origin this principal belongs to.
    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }
    /// Stable, unambiguous key for local queues and eventId uniqueness.
    pub fn scope(&self) -> String {
        serde_json::to_string(&(self.contract, &self.endpoint, &self.org, &self.actor))
            .expect("plain strings")
    }
    fn from_scope(value: &str) -> Option<Self> {
        let (contract, endpoint, org, actor): (u32, String, String, String) =
            serde_json::from_str(value).ok()?;
        let parsed = ContextEndpoint::parse(&endpoint).ok()?;
        let key = Self::new(&parsed, &org, &actor, contract).ok()?;
        (key.endpoint == endpoint).then_some(key)
    }
}

/// Where a record is sent. Never part of the identity namespace.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DestinationKey(String);

impl DestinationKey {
    pub fn new(target_id: &str) -> Result<Self, SessionError> {
        if target_id.is_empty() || target_id.len() > 80 || target_id.chars().any(char::is_control) {
            return Err(SessionError::InvalidPrincipal);
        }
        Ok(Self(target_id.to_string()))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Bearer token. Not `Clone`, `Display` or `Serialize`; `Debug` is redacted.
pub struct SessionToken(String);

impl SessionToken {
    pub fn expose_for_request(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Debug for SessionToken {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("SessionToken(<redacted>)")
    }
}

/// Dispatch capability for one authority, identity generation and epoch.
/// Carries no token.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionLease {
    authority: u64,
    principal: PrincipalKey,
    generation: u64,
    epoch: u64,
}

impl SessionLease {
    pub fn principal(&self) -> &PrincipalKey {
        &self.principal
    }
    pub fn generation(&self) -> u64 {
        self.generation
    }
    pub fn epoch(&self) -> u64 {
        self.epoch
    }
}

/// Returned by `restore` and required by `confirm`, so a late online
/// confirmation cannot apply to a session that changed meanwhile.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RestoreTicket {
    authority: u64,
    generation: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionError {
    NotSignedIn,
    /// Restored after restart; confirm the principal online before dispatching.
    Unverified,
    StaleLease,
    /// A refresh or confirmation revealed a different principal; signed out.
    IdentityChanged,
    TokenTooLarge,
    InvalidPrincipal,
    /// Stored session data was partial or unreadable; nothing was restored.
    CorruptBinding,
    /// The generation or epoch counter is exhausted; no further leases.
    Exhausted,
    Store(SecretError),
}

/// Persistence for the session token and principal binding (OS credential store).
pub trait TokenStore {
    fn read(&self, account: &str) -> Result<Option<String>, SecretError>;
    fn write(&self, account: &str, value: &str) -> Result<(), SecretError>;
    fn delete(&self, account: &str) -> Result<(), SecretError>;
}

impl TokenStore for NativeSecretStore {
    fn read(&self, account: &str) -> Result<Option<String>, SecretError> {
        NativeSecretStore::read(self, account)
    }
    fn write(&self, account: &str, value: &str) -> Result<(), SecretError> {
        NativeSecretStore::write(self, account, value)
    }
    fn delete(&self, account: &str) -> Result<(), SecretError> {
        NativeSecretStore::delete(self, account)
    }
}

enum State {
    SignedOut,
    Unverified {
        principal: PrincipalKey,
        token: SessionToken,
    },
    Active {
        principal: PrincipalKey,
        token: SessionToken,
    },
}

pub struct SessionAuthority<S: TokenStore> {
    id: u64,
    store: S,
    state: State,
    generation: u64,
    epoch: u64,
}

impl<S: TokenStore> SessionAuthority<S> {
    pub fn new(store: S) -> Self {
        Self {
            id: NEXT_AUTHORITY.fetch_add(1, Ordering::Relaxed),
            store,
            state: State::SignedOut,
            generation: 0,
            epoch: 0,
        }
    }

    /// Sign out in memory and start a new generation.
    fn bump(&mut self) -> Result<(), SessionError> {
        self.state = State::SignedOut;
        self.generation = self
            .generation
            .checked_add(1)
            .ok_or(SessionError::Exhausted)?;
        self.epoch = 0;
        Ok(())
    }

    fn current(&self, lease: &SessionLease) -> bool {
        lease.authority == self.id && lease.generation == self.generation
    }

    /// Store the verified principal and token, then activate. The previous
    /// binding is removed first, so a failed write can never leave an older
    /// identity restorable. If the store cannot even delete that binding, the
    /// error is returned and memory is signed out; the old identity can then
    /// only come back as `Unverified` and still needs online confirmation.
    pub fn sign_in(&mut self, principal: PrincipalKey, token: String) -> Result<(), SessionError> {
        validate_secret_size(&token).map_err(|_| SessionError::TokenTooLarge)?;
        self.bump()?;
        self.store
            .delete(PRINCIPAL_ACCOUNT)
            .map_err(SessionError::Store)?;
        self.store
            .delete(REFRESH_ACCOUNT)
            .map_err(SessionError::Store)?;
        self.store
            .write(TOKEN_ACCOUNT, &token)
            .map_err(SessionError::Store)?;
        if let Err(error) = self.store.write(PRINCIPAL_ACCOUNT, &principal.scope()) {
            let _ = self.store.delete(TOKEN_ACCOUNT);
            return Err(SessionError::Store(error));
        }
        self.state = State::Active {
            principal,
            token: SessionToken(token),
        };
        Ok(())
    }

    /// Apply a token refresh made under `lease` (captured before the request).
    /// A result for another authority, an older generation or an older epoch is
    /// refused before anything else; a different principal signs out.
    pub fn refresh(
        &mut self,
        lease: &SessionLease,
        verified: &PrincipalKey,
        token: String,
    ) -> Result<(), SessionError> {
        if !self.current(lease) || lease.epoch != self.epoch {
            return Err(SessionError::StaleLease);
        }
        let current = match &self.state {
            State::Active { principal, .. } => principal.clone(),
            State::Unverified { .. } => return Err(SessionError::Unverified),
            State::SignedOut => return Err(SessionError::NotSignedIn),
        };
        if *verified != current || lease.principal != current {
            let _ = self.sign_out();
            return Err(SessionError::IdentityChanged);
        }
        validate_secret_size(&token).map_err(|_| SessionError::TokenTooLarge)?;
        let next_epoch = self.epoch.checked_add(1).ok_or(SessionError::Exhausted)?;
        self.store
            .write(TOKEN_ACCOUNT, &token)
            .map_err(SessionError::Store)?;
        self.epoch = next_epoch;
        self.state = State::Active {
            principal: current,
            token: SessionToken(token),
        };
        Ok(())
    }

    /// Store (or, with `None`, remove) the refresh token that renews this
    /// session's access token. Only meaningful for a signed-in session, but
    /// the entry is independent of the token and binding entries.
    pub fn set_refresh_token(&mut self, token: Option<&str>) -> Result<(), SessionError> {
        match token {
            Some(value) => {
                validate_secret_size(value).map_err(|_| SessionError::TokenTooLarge)?;
                self.store
                    .write(REFRESH_ACCOUNT, value)
                    .map_err(SessionError::Store)
            }
            None => self
                .store
                .delete(REFRESH_ACCOUNT)
                .map_err(SessionError::Store),
        }
    }

    /// The stored refresh token, read fresh from the credential store.
    pub fn refresh_token(&self) -> Result<Option<String>, SessionError> {
        self.store
            .read(REFRESH_ACCOUNT)
            .map_err(SessionError::Store)
    }

    /// Clears memory first, then the stored token, binding and refresh token.
    /// A store error is returned so the UI can warn, but the session is
    /// already signed out.
    pub fn sign_out(&mut self) -> Result<(), SessionError> {
        self.bump()?;
        let token = self.store.delete(TOKEN_ACCOUNT);
        let principal = self.store.delete(PRINCIPAL_ACCOUNT);
        let refresh = self.store.delete(REFRESH_ACCOUNT);
        token
            .and(principal)
            .and(refresh)
            .map_err(SessionError::Store)
    }

    /// Load the stored session after a restart as `Unverified`. Returns the
    /// ticket the online confirmation must present.
    pub fn restore(&mut self) -> Result<RestoreTicket, SessionError> {
        self.bump()?;
        let ticket = RestoreTicket {
            authority: self.id,
            generation: self.generation,
        };
        let token = self
            .store
            .read(TOKEN_ACCOUNT)
            .map_err(SessionError::Store)?;
        let binding = self
            .store
            .read(PRINCIPAL_ACCOUNT)
            .map_err(SessionError::Store)?;
        match (token, binding) {
            (None, None) => Ok(ticket),
            (Some(token), Some(binding)) => {
                let principal =
                    PrincipalKey::from_scope(&binding).ok_or(SessionError::CorruptBinding)?;
                self.state = State::Unverified {
                    principal,
                    token: SessionToken(token),
                };
                Ok(ticket)
            }
            _ => Err(SessionError::CorruptBinding),
        }
    }

    /// Online confirmation for the ticket `restore` returned. Only an
    /// `Unverified` session can be confirmed, so a ticket is spent by its first
    /// use; a stale ticket is refused; a different principal signs out.
    pub fn confirm(
        &mut self,
        ticket: RestoreTicket,
        verified: &PrincipalKey,
    ) -> Result<(), SessionError> {
        if ticket.authority != self.id || ticket.generation != self.generation {
            return Err(SessionError::StaleLease);
        }
        match std::mem::replace(&mut self.state, State::SignedOut) {
            State::Unverified { principal, token } if principal == *verified => {
                self.state = State::Active { principal, token };
                Ok(())
            }
            // The ticket was already spent by an earlier confirmation.
            active @ State::Active { .. } => {
                self.state = active;
                Err(SessionError::StaleLease)
            }
            State::SignedOut => Err(SessionError::NotSignedIn),
            _ => {
                let _ = self.sign_out();
                Err(SessionError::IdentityChanged)
            }
        }
    }

    /// Dispatch lease; only for an online-verified session.
    pub fn lease(&self) -> Result<SessionLease, SessionError> {
        match &self.state {
            State::Active { principal, .. } => Ok(SessionLease {
                authority: self.id,
                principal: principal.clone(),
                generation: self.generation,
                epoch: self.epoch,
            }),
            State::Unverified { .. } => Err(SessionError::Unverified),
            State::SignedOut => Err(SessionError::NotSignedIn),
        }
    }

    /// Current token for a still-valid lease (same authority, principal and
    /// generation; any epoch, so in-flight work uses the refreshed token).
    pub fn token(&self, lease: &SessionLease) -> Result<&SessionToken, SessionError> {
        match &self.state {
            State::Active { principal, token }
                if *principal == lease.principal && self.current(lease) =>
            {
                Ok(token)
            }
            _ => Err(SessionError::StaleLease),
        }
    }

    /// Identity namespace for local queues, also while unverified after restart.
    pub fn queue_scope(&self) -> Option<String> {
        match &self.state {
            State::Active { principal, .. } | State::Unverified { principal, .. } => {
                Some(principal.scope())
            }
            State::SignedOut => None,
        }
    }
}
