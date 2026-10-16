//! Team Context record service (T10): reserve -> dispatch -> read back.
//!
//! Composes the session authority (T06), lease owner and verified receipts
//! (T04), bounded transport (T05), encrypted journal (T08) and response policy
//! (T09). The journal is the durable record; the in-memory lease owner exists
//! so that only a receipt verified against a lease for the exact frozen
//! payload, owner and identity generation can change state.
//!
//! * The endpoint is fixed at construction. Nothing here accepts a URL, an
//!   identity or a state from the caller.
//! * Dispatch only with an online-verified session for the same endpoint; the
//!   owner namespace is the principal scope (D8).
//! * `begin_dispatch` commits before any POST; after a possible send only
//!   read-back is used, per `context_policy`.
//! * One operation per record at a time (`Busy` otherwise), so polls of the
//!   same record never overlap.
//! * Every request keeps the `SessionLease` it was sent under; its result is
//!   applied only while that lease is still valid (same authority, principal
//!   and identity generation), so a sign-out/sign-in in between discards it.
//! * Limitation: time since the first dispatch is tracked in this process. After
//!   a restart the clock starts at the first read-back, so `unknown` records
//!   move to slow polling one server retry window after the restart.
use crate::context_delivery::{BackupPayload, ContextDeliveryOwner, DeliveryState};
use crate::context_journal::{
    DispatchPhase, Journal, JournalError, QuotaReport, RecoveryAction, ReserveOutcome,
};
use crate::context_policy::{decide, Effect, Next, Observation, Operation, RecordView};
use crate::context_session::{
    DestinationKey, SessionAuthority, SessionError, SessionLease, TokenStore,
};
use crate::context_transport::{fetch_projects, readback, submit, Outcome, TransportError};
use crate::context_wire::{decode_projects, verify_readback, ContextEndpoint, ProjectCatalog};
use std::collections::{HashMap, HashSet};
use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const LEASE_CAPACITY: usize = 5_000;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServiceError {
    NotSignedIn,
    /// Restored session not yet confirmed online; records stay queued.
    Unverified,
    /// The session belongs to a different endpoint than this service.
    WrongEndpoint,
    /// No journal (e.g. the key provider is unavailable on this platform).
    JournalUnavailable,
    /// Another operation on this record is in flight.
    Busy,
    NotFound,
    Journal(JournalError),
    Session(SessionError),
    /// A response could not be verified against the record's lease.
    UnverifiedReceipt,
    /// The in-memory lease table is full.
    Capacity,
    /// The payload names a destination this service was not configured for.
    DestinationNotAllowed,
    /// The project catalog could not be fetched or verified.
    CatalogUnavailable,
}

impl From<JournalError> for ServiceError {
    fn from(error: JournalError) -> Self {
        ServiceError::Journal(error)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RecordStatus {
    pub phase: DispatchPhase,
    pub server_state: Option<DeliveryState>,
    pub action: RecoveryAction,
}

#[derive(Default)]
struct Track {
    attempt: u32,
    readback_not_found: u32,
    first_dispatch: Option<Instant>,
}

struct Inner<S: TokenStore> {
    session: SessionAuthority<S>,
    delivery: ContextDeliveryOwner,
    delivery_scope: Option<String>,
    journal: Option<Journal>,
    tracks: HashMap<String, Track>,
    in_flight: HashSet<String>,
}

pub struct ContextService<S: TokenStore> {
    endpoint: ContextEndpoint,
    /// Destinations the signed-in user may record to; fixed by Rust, never by IPC.
    destinations: Vec<DestinationKey>,
    inner: Mutex<Inner<S>>,
}

/// Releases the per-record in-flight mark when an operation ends.
struct Flight<'a, S: TokenStore> {
    service: &'a ContextService<S>,
    event_id: String,
}

impl<S: TokenStore> Drop for Flight<'_, S> {
    fn drop(&mut self) {
        if let Ok(mut inner) = self.service.inner.lock() {
            inner.in_flight.remove(&self.event_id);
        }
    }
}

/// Everything one network call needs, captured under the lock.
struct Prepared {
    session: SessionLease,
    scope: String,
    token: Zeroizing<String>,
    payload: BackupPayload,
}

impl<S: TokenStore> ContextService<S> {
    pub fn new(
        endpoint: ContextEndpoint,
        destinations: Vec<DestinationKey>,
        session: SessionAuthority<S>,
        journal: Option<Journal>,
    ) -> Self {
        Self {
            endpoint,
            destinations,
            inner: Mutex::new(Inner {
                session,
                delivery: ContextDeliveryOwner::new(LEASE_CAPACITY),
                delivery_scope: None,
                journal,
                tracks: HashMap::new(),
                in_flight: HashSet::new(),
            }),
        }
    }

    fn lock(&self) -> MutexGuard<'_, Inner<S>> {
        self.inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Rust-side session management (sign-in flows); never exposed over IPC.
    pub fn with_session<T>(&self, f: impl FnOnce(&mut SessionAuthority<S>) -> T) -> T {
        f(&mut self.lock().session)
    }

    /// Owner scope of the current verified session for this endpoint.
    fn scope(&self, inner: &mut Inner<S>) -> Result<String, ServiceError> {
        let lease = inner.session.lease().map_err(|error| match error {
            SessionError::NotSignedIn => ServiceError::NotSignedIn,
            SessionError::Unverified => ServiceError::Unverified,
            other => ServiceError::Session(other),
        })?;
        if lease.principal().endpoint() != self.endpoint.canonical() {
            return Err(ServiceError::WrongEndpoint);
        }
        let scope = lease.principal().scope();
        if inner.delivery_scope.as_deref() != Some(scope.as_str()) {
            inner
                .delivery
                .set_authenticated_scope(Some(scope.clone()))
                .map_err(|_| ServiceError::UnverifiedReceipt)?;
            inner.delivery_scope = Some(scope.clone());
        }
        Ok(scope)
    }

    /// Durably reserve a record. With a session restored but not yet verified,
    /// the record is queued under that owner and dispatched later.
    /// Journal state for diagnostics: `None` when there is no journal.
    pub fn journal_mode(&self) -> Option<crate::context_journal::RecoveryMode> {
        self.lock().journal.as_ref().map(Journal::mode)
    }

    pub fn destinations(&self) -> &[DestinationKey] {
        &self.destinations
    }

    /// Whether a session (verified or restored) exists, without exposing it.
    pub fn signed_in(&self) -> bool {
        self.lock().session.queue_scope().is_some()
    }

    pub fn reserve(&self, payload: &BackupPayload) -> Result<ReserveOutcome, ServiceError> {
        if !self
            .destinations
            .iter()
            .any(|d| d.as_str() == payload.target_id())
        {
            return Err(ServiceError::DestinationNotAllowed);
        }
        let mut inner = self.lock();
        let scope = match self.scope(&mut inner) {
            Ok(scope) => scope,
            Err(ServiceError::Unverified) => inner
                .session
                .queue_scope()
                .ok_or(ServiceError::NotSignedIn)?,
            Err(error) => return Err(error),
        };
        let journal = inner
            .journal
            .as_mut()
            .ok_or(ServiceError::JournalUnavailable)?;
        Ok(journal.reserve(&scope, payload)?)
    }

    pub fn status(&self, event_id: &str) -> Result<RecordStatus, ServiceError> {
        let mut inner = self.lock();
        let scope = self.queue_or_verified_scope(&mut inner)?;
        let journal = inner
            .journal
            .as_ref()
            .ok_or(ServiceError::JournalUnavailable)?;
        let action = journal
            .recovery(&scope)?
            .into_iter()
            .find(|(e, _)| e == event_id)
            .map(|(_, a)| a)
            .ok_or(ServiceError::NotFound)?;
        if action == RecoveryAction::Quarantine {
            // Never decrypted: its stored data failed authentication.
            return Ok(RecordStatus {
                phase: DispatchPhase::Restored,
                server_state: None,
                action,
            });
        }
        let record = journal
            .load(&scope, event_id)?
            .ok_or(ServiceError::NotFound)?;
        Ok(RecordStatus {
            phase: record.phase,
            server_state: record.server_state,
            action,
        })
    }

    /// A page of record ids, newest first.
    pub fn page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<(Vec<String>, Option<String>), ServiceError> {
        let mut inner = self.lock();
        let scope = self.queue_or_verified_scope(&mut inner)?;
        let journal = inner
            .journal
            .as_ref()
            .ok_or(ServiceError::JournalUnavailable)?;
        Ok(journal.page(&scope, cursor, limit)?)
    }

    /// The verified original of one record, for an explicit user action.
    pub fn original(
        &self,
        event_id: &str,
    ) -> Result<crate::context_journal::Original, ServiceError> {
        let mut inner = self.lock();
        let scope = self.queue_or_verified_scope(&mut inner)?;
        let journal = inner
            .journal
            .as_ref()
            .ok_or(ServiceError::JournalUnavailable)?;
        Ok(journal.read_original(&scope, event_id)?)
    }

    pub fn list(&self) -> Result<Vec<(String, RecoveryAction)>, ServiceError> {
        let mut inner = self.lock();
        let scope = self.queue_or_verified_scope(&mut inner)?;
        let journal = inner
            .journal
            .as_ref()
            .ok_or(ServiceError::JournalUnavailable)?;
        Ok(journal.recovery(&scope)?)
    }

    pub fn quota(&self) -> Result<QuotaReport, ServiceError> {
        let mut inner = self.lock();
        let scope = self.queue_or_verified_scope(&mut inner)?;
        let journal = inner
            .journal
            .as_ref()
            .ok_or(ServiceError::JournalUnavailable)?;
        Ok(journal.quota(&scope)?)
    }

    fn queue_or_verified_scope(&self, inner: &mut Inner<S>) -> Result<String, ServiceError> {
        match self.scope(inner) {
            Err(ServiceError::Unverified) => {
                inner.session.queue_scope().ok_or(ServiceError::NotSignedIn)
            }
            other => other,
        }
    }

    pub fn endpoint(&self) -> &ContextEndpoint {
        &self.endpoint
    }

    /// Lease and token of the verified session, for account requests outside
    /// the record pipeline (T24). Check `still_current` with the lease after
    /// the request; the token copy is zeroized on drop.
    pub fn credentials(&self) -> Result<(SessionLease, Zeroizing<String>), ServiceError> {
        let mut inner = self.lock();
        self.scope(&mut inner)?;
        let lease = inner.session.lease().map_err(ServiceError::Session)?;
        let token = Zeroizing::new(
            inner
                .session
                .token(&lease)
                .map_err(ServiceError::Session)?
                .expose_for_request()
                .to_string(),
        );
        Ok((lease, token))
    }

    /// Whether a lease from `credentials` still names the current session.
    pub fn still_current(&self, lease: &SessionLease) -> bool {
        self.lock().session.token(lease).is_ok()
    }

    /// The projects this verified session may attribute records to. Fetched
    /// under the current session lease; a result that arrives after the
    /// identity changed is discarded.
    pub async fn projects(&self) -> Result<ProjectCatalog, ServiceError> {
        let (lease, token) = {
            let mut inner = self.lock();
            self.scope(&mut inner)?;
            let lease = inner.session.lease().map_err(ServiceError::Session)?;
            let token = Zeroizing::new(
                inner
                    .session
                    .token(&lease)
                    .map_err(ServiceError::Session)?
                    .expose_for_request()
                    .to_string(),
            );
            (lease, token)
        };
        let result = fetch_projects(&self.endpoint, &token).await;
        if self.lock().session.token(&lease).is_err() {
            return Err(ServiceError::Session(SessionError::StaleLease));
        }
        match result {
            Ok(Outcome::Receipt(bytes)) => {
                decode_projects(&bytes).map_err(|_| ServiceError::CatalogUnavailable)
            }
            Ok(Outcome::Rejected { status: 401, .. }) => Err(ServiceError::NotSignedIn),
            _ => Err(ServiceError::CatalogUnavailable),
        }
    }

    /// User-requested re-check: read back only, never POST. A record that was
    /// never sent, or is final, is left as it is.
    pub async fn check(&self, event_id: &str) -> Result<Option<Next>, ServiceError> {
        let action = {
            let mut inner = self.lock();
            let scope = self.scope(&mut inner)?;
            let journal = inner
                .journal
                .as_ref()
                .ok_or(ServiceError::JournalUnavailable)?;
            journal
                .recovery(&scope)?
                .into_iter()
                .find(|(e, _)| e == event_id)
                .map(|(_, a)| a)
                .ok_or(ServiceError::NotFound)?
        };
        match action {
            RecoveryAction::ReadBackOnly | RecoveryAction::Poll => {
                self.read_back(event_id).await.map(Some)
            }
            _ => Ok(None),
        }
    }

    /// Take the next step for one record: POST if it was never sent, read back
    /// otherwise. Returns what should happen next.
    pub async fn step(&self, event_id: &str) -> Result<Next, ServiceError> {
        let action = {
            let mut inner = self.lock();
            let scope = self.scope(&mut inner)?;
            let journal = inner
                .journal
                .as_ref()
                .ok_or(ServiceError::JournalUnavailable)?;
            journal
                .recovery(&scope)?
                .into_iter()
                .find(|(e, _)| e == event_id)
                .map(|(_, a)| a)
                .ok_or(ServiceError::NotFound)?
        };
        match action {
            RecoveryAction::MayPost => self.dispatch(event_id).await,
            RecoveryAction::ReadBackOnly | RecoveryAction::Poll => self.read_back(event_id).await,
            RecoveryAction::Final => Ok(Next::Stop(crate::context_policy::Stop::Final)),
            RecoveryAction::Quarantine => Err(ServiceError::Journal(JournalError::Corrupt)),
        }
    }

    /// Load the record, register it with the lease owner and capture the token.
    fn prepare(&self, inner: &mut Inner<S>, event_id: &str) -> Result<Prepared, ServiceError> {
        let scope = self.scope(inner)?;
        let journal = inner
            .journal
            .as_ref()
            .ok_or(ServiceError::JournalUnavailable)?;
        let record = journal
            .load(&scope, event_id)?
            .ok_or(ServiceError::NotFound)?;
        if inner.delivery.state(event_id).is_none() {
            // After a restart (or release) the lease owner does not know it;
            // re-register the frozen payload before any request.
            inner
                .delivery
                .reserve_submission(record.payload.clone())
                .map_err(|error| match error {
                    crate::context_delivery::DeliveryError::Capacity => ServiceError::Capacity,
                    _ => ServiceError::UnverifiedReceipt,
                })?;
        }
        let session_lease = inner.session.lease().map_err(ServiceError::Session)?;
        let token = Zeroizing::new(
            inner
                .session
                .token(&session_lease)
                .map_err(ServiceError::Session)?
                .expose_for_request()
                .to_string(),
        );
        Ok(Prepared {
            session: session_lease,
            scope,
            token,
            payload: record.payload,
        })
    }

    fn view(inner: &Inner<S>, scope: &str, event_id: &str) -> Result<RecordView, ServiceError> {
        let journal = inner
            .journal
            .as_ref()
            .ok_or(ServiceError::JournalUnavailable)?;
        let record = journal
            .load(scope, event_id)?
            .ok_or(ServiceError::NotFound)?;
        let track = inner.tracks.get(event_id);
        Ok(RecordView {
            phase: record.phase,
            server_state: record.server_state,
            since_first_dispatch: track
                .and_then(|t| t.first_dispatch)
                .map(|t| t.elapsed())
                .unwrap_or(Duration::ZERO),
            attempt: track.map_or(0, |t| t.attempt),
            readback_not_found: track.map_or(0, |t| t.readback_not_found),
        })
    }

    /// Mark the record in flight and run `setup` under the lock. On error the
    /// mark is cleared inside the same critical section; the RAII guard that
    /// clears it later is only created after the lock is released.
    fn start(
        &self,
        event_id: &str,
        setup: impl FnOnce(&Self, &mut Inner<S>) -> Result<Prepared, ServiceError>,
    ) -> Result<(Prepared, Flight<'_, S>), ServiceError> {
        let prepared = {
            let mut inner = self.lock();
            if !inner.in_flight.insert(event_id.to_string()) {
                return Err(ServiceError::Busy);
            }
            let result = setup(self, &mut inner);
            if result.is_err() {
                inner.in_flight.remove(event_id);
            }
            result?
        };
        Ok((
            prepared,
            Flight {
                service: self,
                event_id: event_id.to_string(),
            },
        ))
    }

    async fn dispatch(&self, event_id: &str) -> Result<Next, ServiceError> {
        let (prepared, _flight) = self.start(event_id, |service, inner| {
            let prepared = service.prepare(inner, event_id)?;
            // Durable before any byte leaves the process.
            inner
                .journal
                .as_mut()
                .ok_or(ServiceError::JournalUnavailable)?
                .begin_dispatch(&prepared.scope, event_id)?;
            inner
                .tracks
                .entry(event_id.to_string())
                .or_default()
                .first_dispatch
                .get_or_insert_with(Instant::now);
            Ok(prepared)
        })?;
        let result = submit(&self.endpoint, &prepared.token, &prepared.payload).await;
        self.apply(Operation::Submit, event_id, &prepared, result)
    }

    async fn read_back(&self, event_id: &str) -> Result<Next, ServiceError> {
        let (prepared, _flight) = self.start(event_id, |service, inner| {
            let prepared = service.prepare(inner, event_id)?;
            inner
                .tracks
                .entry(event_id.to_string())
                .or_default()
                .first_dispatch
                .get_or_insert_with(Instant::now);
            Ok(prepared)
        })?;
        let result = readback(&self.endpoint, &prepared.token, &prepared.payload).await;
        self.apply(Operation::Readback, event_id, &prepared, result)
    }

    /// Verify, decide and record one result.
    fn apply(
        &self,
        operation: Operation,
        event_id: &str,
        prepared: &Prepared,
        result: Result<Outcome, TransportError>,
    ) -> Result<Next, ServiceError> {
        let mut inner = self.lock();
        // The identity may have changed while the request was in flight,
        // including a sign-out and sign-in as the same principal.
        if inner.session.token(&prepared.session).is_err()
            || self.scope(&mut inner)? != prepared.scope
        {
            return Err(ServiceError::Session(SessionError::StaleLease));
        }
        let observation = match result {
            Ok(Outcome::Receipt(bytes)) => {
                let lease = inner
                    .delivery
                    .reserve_readback(event_id)
                    .map_err(|_| ServiceError::UnverifiedReceipt)?;
                let receipt =
                    verify_readback(&lease, &bytes).map_err(|_| ServiceError::UnverifiedReceipt)?;
                // The lease carries the frozen payload; apply re-checks it.
                inner
                    .delivery
                    .apply_verified(&receipt)
                    .map_err(|_| ServiceError::UnverifiedReceipt)?;
                Observation::Receipt(receipt.state())
            }
            Ok(Outcome::Rejected { status, code }) => Observation::Refused { status, code },
            Err(error) => Observation::Transport(error),
        };
        let view = Self::view(&inner, &prepared.scope, event_id)?;
        let decision = decide(operation, observation, &view);
        let track = inner.tracks.entry(event_id.to_string()).or_default();
        track.attempt = track.attempt.saturating_add(1);
        if matches!(observation, Observation::Refused { status: 404, .. }) {
            track.readback_not_found = track.readback_not_found.saturating_add(1);
        } else if matches!(observation, Observation::Receipt(_)) {
            track.readback_not_found = 0;
        }
        let journal = inner
            .journal
            .as_mut()
            .ok_or(ServiceError::JournalUnavailable)?;
        match decision.effect {
            Effect::None => {}
            Effect::MarkNotSent => journal.dispatch_not_sent(&prepared.scope, event_id)?,
            Effect::RecordState(state) => {
                if view.server_state != Some(state) {
                    journal.record_server_state(
                        &prepared.scope,
                        event_id,
                        state,
                        view_seq(journal, &prepared.scope, event_id)? + 1,
                    )?;
                }
            }
        }
        if matches!(decision.next, Next::Stop(_) | Next::WaitForUser(_)) {
            inner.tracks.remove(event_id);
            inner.delivery.release(event_id);
        } else if matches!(observation, Observation::Receipt(s) if s != view.server_state.unwrap_or(DeliveryState::Unknown))
        {
            // Progress resets the backoff.
            if let Some(track) = inner.tracks.get_mut(event_id) {
                track.attempt = 0;
            }
        }
        Ok(decision.next)
    }
}

fn view_seq(journal: &Journal, scope: &str, event_id: &str) -> Result<u64, ServiceError> {
    Ok(journal
        .load(scope, event_id)?
        .ok_or(ServiceError::NotFound)?
        .poll_seq)
}
