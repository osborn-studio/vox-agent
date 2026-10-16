//! Private Context service (T18b create/readback, T19 list/search).
//!
//! Runs the private mutation journal (T18a) over the verified session: the
//! owner is always the session principal's scope, never an IPC argument.
//!
//! * `create` fixes a fresh eventId and saves the mutation before anything is
//!   sent. `step` takes exactly one action per call: send a never-sent
//!   mutation; read back an unsettled one; resend the same request when a
//!   readback could not decide (`NOT_OBSERVED`, `READBACK_INCONCLUSIVE`);
//!   fetch the current record after it applied (plan C-22: a readback is
//!   historical, so what is shown comes from a fresh GET).
//! * A reply is applied to the journal of the owner it was sent for. If the
//!   owner changed meanwhile, that journal is no longer open and the reply is
//!   dropped (the saved phase leads to a readback next time).
//! * Lists and searches are the server's `own-private-context` scope, bound
//!   to the session lease they were requested under: a result that arrives
//!   after sign-out or an account switch is discarded. Every item is checked
//!   against the requested window, tag and project.
//! * One operation per mutation at a time.
use std::collections::{HashMap, HashSet};
use std::sync::{Mutex, MutexGuard};

use serde::Serialize;
use zeroize::Zeroizing;

use crate::context_private::{
    decode_record, new_identifier, Phase, PrivateError, PrivateJournal, PrivateMutation,
    PrivateRecord, PrivateStore, RecordCache, Tag, WireRequest, MAX_SENDS,
};
use crate::context_session::{SessionLease, TokenStore};
use crate::context_transport::read_bounded;
use crate::context_wire::ContextEndpoint;

/// Request bodies the server accepts (`PRIVATE_CONTEXT_TOO_LARGE` above).
const MAX_REQUEST_BYTES: usize = 131_072;
const MAX_RECORD_BYTES: usize = 65_536;
const MAX_PAGE_BYTES: usize = 4_000_000;
pub const MAX_PAGE_SIZE: u8 = 50;
const MAX_WINDOW_MICROS: i64 = 31 * 86_400 * 1_000_000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case", tag = "error", content = "detail")]
pub enum PrivateServiceError {
    NotSignedIn,
    /// No journal: the key provider is unavailable on this platform (T07b).
    JournalUnavailable,
    /// The session changed while the request was in flight; nothing applied.
    StaleIdentity,
    /// The view was cleared (collapse, lock, sign-out) while the request was in
    /// flight; its result is not shown. Saved mutations are unaffected.
    ViewCleared,
    Busy,
    NotFound,
    /// The record changed since the user saw it; show the current one first.
    StaleRevision,
    /// The mutation's phase does not allow this step.
    NotAllowed,
    InvalidInput,
    Journal(PrivateError),
    /// The server refused: status and code, never its text.
    Refused {
        status: u16,
        code: String,
    },
    InvalidResponse,
    Network,
}

impl From<PrivateError> for PrivateServiceError {
    fn from(error: PrivateError) -> Self {
        match error {
            PrivateError::InvalidInput => Self::InvalidInput,
            PrivateError::NotFound => Self::NotFound,
            PrivateError::NotAllowed => Self::NotAllowed,
            other => Self::Journal(other),
        }
    }
}

/// Proof that the user explicitly asked to withdraw this record (a separate
/// confirmation in the UI). Built only by the IPC layer from a confirm action.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserConfirmed {
    record_id: String,
}

impl UserConfirmed {
    pub fn withdrawal_of(record_id: &str) -> Self {
        Self {
            record_id: record_id.to_string(),
        }
    }
}

/// The verified session as the private service needs it.
pub trait PrivateSession {
    type Lease;
    /// Lease, owner scope and a token copy for one request.
    fn current(&self) -> Result<(Self::Lease, String, Zeroizing<String>), PrivateServiceError>;
    fn still_current(&self, lease: &Self::Lease) -> bool;
}

impl<S: TokenStore> PrivateSession for crate::context_service::ContextService<S> {
    type Lease = SessionLease;
    fn current(&self) -> Result<(SessionLease, String, Zeroizing<String>), PrivateServiceError> {
        let (lease, token) = self
            .credentials()
            .map_err(|_| PrivateServiceError::NotSignedIn)?;
        let owner = lease.principal().scope();
        Ok((lease, owner, token))
    }
    fn still_current(&self, lease: &SessionLease) -> bool {
        crate::context_service::ContextService::still_current(self, lease)
    }
}

/// What one `step` did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StepResult {
    /// The mutation's phase after the step.
    Phase(Phase),
    /// Applied; this is the current record (possibly later than the mutation).
    Current(PrivateRecord),
}

/// A list or search query. Times are `YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`.
/// No `Debug`: the search text is private.
#[derive(Clone, PartialEq, Eq)]
pub struct ListQuery {
    pub start: String,
    pub end: String,
    pub limit: u8,
    pub cursor: Option<String>,
    pub tag: Option<Tag>,
    pub project_id: Option<String>,
    /// Text search (POST `/v1/context-private-search`); `None` lists.
    pub text: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListPage {
    pub items: Vec<PrivateRecord>,
    pub next_cursor: Option<String>,
    /// The server left records out of this page (no longer authorized, ...).
    pub omitted_records: bool,
}

fn token_chars(value: &str, max: usize) -> bool {
    (1..=max).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn tag_wire(tag: Tag) -> &'static str {
    match tag {
        Tag::Decision => "decision",
        Tag::Progress => "progress",
        Tag::Blocker => "blocker",
        Tag::Question => "question",
        Tag::Verification => "verification",
    }
}

impl ListQuery {
    fn window(&self) -> Option<(i64, i64)> {
        let start = crate::context_sources::utc_micros(&self.start)?;
        let end = crate::context_sources::utc_micros(&self.end)?;
        (end > start && end - start <= MAX_WINDOW_MICROS).then_some((start, end))
    }

    fn validate(&self) -> Result<(i64, i64), PrivateServiceError> {
        let text_ok = self
            .text
            .as_deref()
            .is_none_or(|q| (1..=128).contains(&q.chars().count()) && !q.contains('\0'));
        let ok = (1..=MAX_PAGE_SIZE).contains(&self.limit)
            && self.cursor.as_deref().is_none_or(|c| token_chars(c, 256))
            && self
                .project_id
                .as_deref()
                .is_none_or(|p| token_chars(p, 80))
            && text_ok;
        match self.window() {
            Some(window) if ok => Ok(window),
            _ => Err(PrivateServiceError::InvalidInput),
        }
    }
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawPage {
    version: u32,
    scope: String,
    items: Vec<serde_json::Value>,
    has_more: bool,
    next_cursor: Option<String>,
    omitted_records: bool,
    evidence_kind: String,
}

/// `{"version":1,"valid":bool,"requiresUserReview":true,"meaning":
/// "current-authorized-private-revisions-only","evidenceKind":"user-statement"}`.
pub fn decode_reference_check(bytes: &[u8]) -> Option<bool> {
    #[derive(serde::Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct Raw {
        version: u32,
        valid: bool,
        requires_user_review: bool,
        meaning: String,
        evidence_kind: String,
    }
    let raw: Raw = serde_json::from_slice(bytes).ok()?;
    (raw.version == 1
        && raw.requires_user_review
        && raw.meaning == "current-authorized-private-revisions-only"
        && raw.evidence_kind == "user-statement")
        .then_some(raw.valid)
}

/// Decode a list or search page and check every item against the query.
pub fn decode_list_page(bytes: &[u8], query: &ListQuery) -> Result<ListPage, PrivateServiceError> {
    let (start, end) = query.validate()?;
    if bytes.len() > MAX_PAGE_BYTES {
        return Err(PrivateServiceError::InvalidResponse);
    }
    let raw: RawPage =
        serde_json::from_slice(bytes).map_err(|_| PrivateServiceError::InvalidResponse)?;
    let cursor_ok = match (&raw.next_cursor, raw.has_more) {
        (Some(cursor), true) => token_chars(cursor, 256),
        (None, false) => true,
        _ => false,
    };
    if raw.version != 1
        || raw.scope != "own-private-context"
        || raw.evidence_kind != "user-statement"
        || !cursor_ok
        || raw.items.len() > usize::from(query.limit)
    {
        return Err(PrivateServiceError::InvalidResponse);
    }
    let mut seen = HashSet::new();
    let mut items = Vec::with_capacity(raw.items.len());
    for value in raw.items {
        let bytes = serde_json::to_vec(&value).map_err(|_| PrivateServiceError::InvalidResponse)?;
        let record = decode_record(&bytes).ok_or(PrivateServiceError::InvalidResponse)?;
        let matches = item_matches(&record, query, (start, end)) && seen.insert(record.id.clone());
        if !matches {
            return Err(PrivateServiceError::InvalidResponse);
        }
        items.push(record);
    }
    Ok(ListPage {
        items,
        next_cursor: raw.next_cursor,
        omitted_records: raw.omitted_records,
    })
}

struct Inner<S: PrivateStore> {
    /// The open journal and its owner; reopened when the owner changes.
    journal: Option<(String, PrivateJournal<S>)>,
    /// A store waiting for its first owner.
    idle_store: Option<S>,
    cache: RecordCache,
    /// Operations in flight by (owner, key), each with its own id, so a late
    /// cleanup never removes a newer operation's mark.
    in_flight: HashMap<(String, String), u64>,
    next_operation: u64,
    /// Advanced by `clear_view`: a result started before it is never shown.
    view_epoch: u64,
}

pub struct PrivateService<S: PrivateStore> {
    endpoint: ContextEndpoint,
    inner: Mutex<Inner<S>>,
}

/// Releases a mutation's in-flight mark.
struct Flight<'a, S: PrivateStore> {
    service: &'a PrivateService<S>,
    slot: (String, String),
    id: u64,
}

impl<S: PrivateStore> Drop for Flight<'_, S> {
    fn drop(&mut self) {
        let mut inner = self.service.lock();
        if inner.in_flight.get(&self.slot) == Some(&self.id) {
            inner.in_flight.remove(&self.slot);
        }
    }
}

enum Action {
    Send,
    Readback,
    Refresh(String),
}

impl<S: PrivateStore> PrivateService<S> {
    /// `store` is `None` when no journal can be opened (no key provider).
    pub fn new(endpoint: ContextEndpoint, store: Option<S>) -> Self {
        Self {
            endpoint,
            inner: Mutex::new(Inner {
                journal: None,
                idle_store: store,
                cache: RecordCache::default(),
                in_flight: HashMap::new(),
                next_operation: 0,
                view_epoch: 0,
            }),
        }
    }

    fn lock(&self) -> MutexGuard<'_, Inner<S>> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Open (or reopen) the journal for `owner`; clears the record cache when
    /// the owner changes.
    fn journal_for<'a>(
        inner: &'a mut Inner<S>,
        owner: &str,
    ) -> Result<&'a mut PrivateJournal<S>, PrivateServiceError> {
        let current = inner.journal.as_ref().map(|(o, _)| o.as_str());
        if current != Some(owner) {
            let store = match inner.journal.take() {
                Some((_, journal)) => journal.into_store(),
                None => inner
                    .idle_store
                    .take()
                    .ok_or(PrivateServiceError::JournalUnavailable)?,
            };
            inner.cache.clear();
            match PrivateJournal::try_open(store, owner) {
                Ok(journal) => inner.journal = Some((owner.to_string(), journal)),
                Err((store, error)) => {
                    // Keep the store for the next attempt.
                    inner.idle_store = Some(store);
                    return Err(error.into());
                }
            }
        }
        Ok(&mut inner.journal.as_mut().expect("opened above").1)
    }

    /// Forget the visible plaintext (sign-out, panel collapsed, window hidden or
    /// locked; T23b). Saved mutations stay, unsettled ones included; results
    /// of requests started before this are dropped instead of shown.
    pub fn clear_view(&self) {
        let mut inner = self.lock();
        inner.cache.clear();
        inner.view_epoch += 1;
        // Decrypted mutation text goes too: the journal is closed and reopened
        // (decrypting from the sealed store) by the next operation.
        if let Some((_, journal)) = inner.journal.take() {
            inner.idle_store = Some(journal.into_store());
        }
    }

    /// The record the user is looking at, as last seen by this session: an
    /// edit or withdrawal is written against exactly that revision (T20).
    fn seen<'a>(
        inner: &'a Inner<S>,
        record_id: &str,
        seen_revision: u32,
    ) -> Result<&'a PrivateRecord, PrivateServiceError> {
        let record = inner
            .cache
            .get(record_id)
            .ok_or(PrivateServiceError::NotFound)?;
        if record.withdrawn {
            return Err(PrivateServiceError::NotAllowed);
        }
        if record.revision != seen_revision {
            // A newer revision arrived since the user looked: show it first.
            return Err(PrivateServiceError::StaleRevision);
        }
        Ok(record)
    }

    /// Queue an edit of a record the user saw at `seen_revision`. Returns its
    /// mutation key; a server revision conflict keeps the text (see `kept`).
    pub fn edit<A: PrivateSession>(
        &self,
        session: &A,
        record_id: &str,
        seen_revision: u32,
        text: &str,
        tags: Vec<Tag>,
    ) -> Result<String, PrivateServiceError> {
        let (_, owner, _) = session.current()?;
        let mut inner = self.lock();
        Self::journal_for(&mut inner, &owner)?;
        let event_id = Self::seen(&inner, record_id, seen_revision)?
            .event_id
            .clone();
        let mutation = PrivateMutation::Edit {
            record_id: record_id.to_string(),
            event_id,
            request_id: new_identifier("pr_")?,
            expected_revision: seen_revision,
            text: Zeroizing::new(text.to_string()),
            tags,
        };
        mutation.validate()?;
        if mutation
            .request()
            .body
            .as_ref()
            .is_some_and(|b| b.len() > MAX_REQUEST_BYTES)
        {
            return Err(PrivateServiceError::InvalidInput);
        }
        let key = mutation.key();
        let journal = &mut inner.journal.as_mut().expect("opened").1;
        journal.reserve(&owner, mutation)?;
        Ok(key)
    }

    /// Queue a withdrawal. Only an explicit user action may withdraw: the
    /// caller must pass the confirmation the user gave for this record.
    pub fn withdraw<A: PrivateSession>(
        &self,
        session: &A,
        record_id: &str,
        seen_revision: u32,
        confirmed: UserConfirmed,
    ) -> Result<String, PrivateServiceError> {
        if confirmed.record_id != record_id {
            return Err(PrivateServiceError::NotAllowed);
        }
        let (_, owner, _) = session.current()?;
        let mut inner = self.lock();
        Self::journal_for(&mut inner, &owner)?;
        let event_id = Self::seen(&inner, record_id, seen_revision)?
            .event_id
            .clone();
        let mutation = PrivateMutation::Withdraw {
            record_id: record_id.to_string(),
            event_id,
            request_id: new_identifier("pr_")?,
            expected_revision: seen_revision,
        };
        mutation.validate()?;
        let key = mutation.key();
        let journal = &mut inner.journal.as_mut().expect("opened").1;
        journal.reserve(&owner, mutation)?;
        Ok(key)
    }

    /// The text and tags a conflicted or refused edit/create kept, so the user
    /// can apply them again on top of the current record.
    pub fn kept<A: PrivateSession>(
        &self,
        session: &A,
        key: &str,
    ) -> Result<(Zeroizing<String>, Vec<Tag>), PrivateServiceError> {
        let (_, owner, _) = session.current()?;
        let mut inner = self.lock();
        let journal = Self::journal_for(&mut inner, &owner)?;
        let entry = journal
            .entry(&owner, key)
            .ok_or(PrivateServiceError::NotFound)?;
        if !matches!(entry.phase, Phase::Conflict(_) | Phase::Rejected(_)) {
            return Err(PrivateServiceError::NotAllowed);
        }
        match &entry.mutation {
            PrivateMutation::Create { text, tags, .. }
            | PrivateMutation::Edit { text, tags, .. } => Ok((text.clone(), tags.clone())),
            PrivateMutation::Withdraw { .. } => Err(PrivateServiceError::NotAllowed),
        }
    }

    /// Remove a settled mutation the user dismissed (never an unsettled one).
    pub fn dismiss<A: PrivateSession>(
        &self,
        session: &A,
        key: &str,
    ) -> Result<(), PrivateServiceError> {
        let (_, owner, _) = session.current()?;
        let mut inner = self.lock();
        if inner
            .in_flight
            .contains_key(&(owner.clone(), key.to_string()))
        {
            return Err(PrivateServiceError::Busy);
        }
        Self::journal_for(&mut inner, &owner)?.dismiss(&owner, key)?;
        Ok(())
    }

    /// Queue a new private record. Returns its mutation key.
    pub fn create<A: PrivateSession>(
        &self,
        session: &A,
        text: &str,
        tags: Vec<Tag>,
        project_id: Option<String>,
        repository_id: Option<String>,
    ) -> Result<String, PrivateServiceError> {
        let (_, owner, _) = session.current()?;
        let mutation = PrivateMutation::Create {
            event_id: new_identifier("pv_")?,
            text: Zeroizing::new(text.to_string()),
            tags,
            project_id,
            repository_id,
        };
        mutation.validate()?;
        if mutation
            .request()
            .body
            .as_ref()
            .is_some_and(|b| b.len() > MAX_REQUEST_BYTES)
        {
            return Err(PrivateServiceError::InvalidInput);
        }
        let key = mutation.key();
        let mut inner = self.lock();
        Self::journal_for(&mut inner, &owner)?.reserve(&owner, mutation)?;
        Ok(key)
    }

    /// The phase of a mutation for the current owner.
    pub fn phase<A: PrivateSession>(
        &self,
        session: &A,
        key: &str,
    ) -> Result<Phase, PrivateServiceError> {
        let (_, owner, _) = session.current()?;
        let mut inner = self.lock();
        Self::journal_for(&mut inner, &owner)?
            .phase(&owner, key)
            .cloned()
            .ok_or(PrivateServiceError::NotFound)
    }

    /// Take the next action for one mutation (see the module notes). A
    /// `Blocked` mutation moves only with `retry` (an explicit user action).
    pub async fn step<A: PrivateSession>(
        &self,
        session: &A,
        key: &str,
        retry: bool,
    ) -> Result<StepResult, PrivateServiceError> {
        let (lease, owner, token) = session.current()?;
        let slot = (owner.clone(), key.to_string());
        let (action, request, id, epoch) = {
            let mut inner = self.lock();
            let journal = Self::journal_for(&mut inner, &owner)?;
            let entry = journal
                .entry(&owner, key)
                .ok_or(PrivateServiceError::NotFound)?;
            let (phase, sends) = (entry.phase.clone(), entry.sends);
            let action = match phase {
                Phase::Reserved => Action::Send,
                // A readback could not decide: the same request decides, until
                // the send limit; after that only readbacks continue.
                Phase::Unknown(ref reason)
                    if (reason == "NOT_OBSERVED" || reason == "READBACK_INCONCLUSIVE")
                        && sends < MAX_SENDS =>
                {
                    Action::Send
                }
                Phase::Unknown(_) => Action::Readback,
                Phase::Blocked(_) if retry => {
                    if journal.readback_request(&owner, key).is_some() {
                        Action::Readback
                    } else {
                        Action::Send
                    }
                }
                Phase::Applied { record_id, .. } => Action::Refresh(record_id),
                Phase::Dispatching
                | Phase::Blocked(_)
                | Phase::Conflict(_)
                | Phase::Rejected(_) => {
                    return Ok(StepResult::Phase(phase));
                }
            };
            if inner.in_flight.contains_key(&slot) {
                return Err(PrivateServiceError::Busy);
            }
            let journal = &mut inner.journal.as_mut().expect("opened").1;
            let request = match &action {
                Action::Send => Some(journal.begin_send(&owner, key)?),
                Action::Readback => journal.readback_request(&owner, key),
                Action::Refresh(_) => None,
            };
            inner.next_operation += 1;
            let id = inner.next_operation;
            inner.in_flight.insert(slot.clone(), id);
            (action, request, id, inner.view_epoch)
        };
        let _flight = Flight {
            service: self,
            slot,
            id,
        };
        match action {
            Action::Refresh(record_id) => {
                let record = self
                    .get_with(session, &lease, &owner, &token, &record_id, epoch)
                    .await?;
                Ok(StepResult::Current(record))
            }
            Action::Send | Action::Readback => {
                let request = request.ok_or(PrivateServiceError::NotAllowed)?;
                let reply = call(&self.endpoint, &token, &request, MAX_RECORD_BYTES).await;
                let reply = reply
                    .as_ref()
                    .map(|(status, body)| (*status, body.as_slice()));
                let mut inner = self.lock();
                let inner = &mut *inner;
                let reopened = inner.journal.is_none() && inner.idle_store.is_some();
                if reopened {
                    // A clear closed the journal meanwhile: reopen it to save
                    // what the reply proved (the cleared view stays empty).
                    Self::journal_for(inner, &owner)?;
                }
                // Whoever reopened the journal, this operation still holds the
                // request: take its entry back from the RESTARTED marking.
                if inner.in_flight.get(&(owner.clone(), key.to_string())) == Some(&id)
                    && matches!(action, Action::Send)
                {
                    if let Some((journal_owner, journal)) = inner.journal.as_mut() {
                        if *journal_owner == owner {
                            journal.reclaim_in_flight(&owner, key);
                        }
                    }
                }
                let Some((journal_owner, journal)) = inner.journal.as_mut() else {
                    return Err(PrivateServiceError::StaleIdentity);
                };
                if *journal_owner != owner {
                    // That owner's journal is closed; its saved phase leads to a
                    // readback when it is opened again.
                    return Err(PrivateServiceError::StaleIdentity);
                }
                // What the reply proved is saved for this owner even if the
                // session ended meanwhile; the result is returned only to the
                // session that asked.
                let mut scratch = RecordCache::default();
                let cache = if session.still_current(&lease) && inner.view_epoch == epoch {
                    &mut inner.cache
                } else {
                    &mut scratch
                };
                match action {
                    Action::Send => journal.finish_send(&owner, key, reply, cache),
                    _ => journal.finish_readback(&owner, key, reply, cache),
                }?;
                let still_current = session.still_current(&lease);
                if reopened || !still_current || inner.view_epoch != epoch {
                    // The proof is saved; the view was cleared or the session
                    // ended, so the decrypted journal does not stay in memory.
                    if inner.view_epoch != epoch || !still_current {
                        if let Some((_, journal)) = inner.journal.take() {
                            inner.idle_store = Some(journal.into_store());
                        }
                    }
                    return Err(if still_current {
                        PrivateServiceError::ViewCleared
                    } else {
                        PrivateServiceError::StaleIdentity
                    });
                }
                let phase = journal
                    .phase(&owner, key)
                    .cloned()
                    .ok_or(PrivateServiceError::NotFound)?;
                Ok(StepResult::Phase(phase))
            }
        }
    }

    /// The current record (never older than one already shown).
    pub async fn get<A: PrivateSession>(
        &self,
        session: &A,
        record_id: &str,
    ) -> Result<PrivateRecord, PrivateServiceError> {
        let (lease, owner, token) = session.current()?;
        let epoch = {
            let mut inner = self.lock();
            Self::journal_for(&mut inner, &owner)?;
            inner.view_epoch
        };
        self.get_with(session, &lease, &owner, &token, record_id, epoch)
            .await
    }

    /// Whether a result for `owner` under `lease` may be committed and
    /// returned. Checked while holding the service lock, so no account switch
    /// can come between the check and the commit.
    fn may_commit<A: PrivateSession>(
        inner: &Inner<S>,
        session: &A,
        lease: &A::Lease,
        owner: &str,
        epoch: u64,
    ) -> Result<(), PrivateServiceError> {
        // A clear comes first: it also closes the journal.
        if inner.view_epoch != epoch {
            return Err(PrivateServiceError::ViewCleared);
        }
        if inner.journal.as_ref().map(|(o, _)| o.as_str()) != Some(owner)
            || !session.still_current(lease)
        {
            return Err(PrivateServiceError::StaleIdentity);
        }
        Ok(())
    }

    async fn get_with<A: PrivateSession>(
        &self,
        session: &A,
        lease: &A::Lease,
        owner: &str,
        token: &str,
        record_id: &str,
        epoch: u64,
    ) -> Result<PrivateRecord, PrivateServiceError> {
        let probe = PrivateMutation::Withdraw {
            record_id: record_id.to_string(),
            event_id: "x".into(),
            request_id: "x".into(),
            expected_revision: 1,
        };
        probe
            .validate()
            .map_err(|_| PrivateServiceError::InvalidInput)?;
        let request = WireRequest {
            method: crate::context_private::Method::Get,
            path: format!("v1/context-private/{record_id}"),
            body: None,
        };
        let reply = call(&self.endpoint, token, &request, MAX_RECORD_BYTES).await;
        let (status, body) = reply.ok_or(PrivateServiceError::Network)?;
        let record = if status == 200 {
            let record = decode_record(&body).ok_or(PrivateServiceError::InvalidResponse)?;
            if record.id != record_id {
                return Err(PrivateServiceError::InvalidResponse);
            }
            Some(record)
        } else {
            None
        };
        let mut inner = self.lock();
        Self::may_commit(&inner, session, lease, owner, epoch)?;
        let Some(record) = record else {
            return Err(refused(status, &body));
        };
        inner.cache.observe(record.clone());
        // Show the newest revision seen, never an older one.
        Ok(inner.cache.get(record_id).cloned().unwrap_or(record))
    }

    /// Whether these exact revisions are still the current, authorized
    /// private records (`/v1/context-private-reference-check`). Used before a
    /// weekly draft that quotes them is copied (T22). At most 100, unique ids.
    pub async fn reference_check<A: PrivateSession>(
        &self,
        session: &A,
        sources: &[(String, u32)],
    ) -> Result<crate::context_weekly_draft::PrivateCheck, PrivateServiceError> {
        let mut ids = HashSet::new();
        let ok = (1..=100).contains(&sources.len())
            && sources.iter().all(|(id, revision)| {
                (1..=crate::context_private::MAX_REVISION).contains(revision)
                    && id.len() <= 128
                    && id
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                    && ids.insert(id.clone())
            });
        if !ok {
            return Err(PrivateServiceError::InvalidInput);
        }
        let (lease, owner, token) = session.current()?;
        let epoch = {
            let mut inner = self.lock();
            Self::journal_for(&mut inner, &owner)?;
            inner.view_epoch
        };
        self.reference_check_with(session, &lease, &owner, &token, sources, epoch)
            .await
    }

    async fn reference_check_with<A: PrivateSession>(
        &self,
        session: &A,
        lease: &A::Lease,
        owner: &str,
        token: &str,
        sources: &[(String, u32)],
        epoch: u64,
    ) -> Result<crate::context_weekly_draft::PrivateCheck, PrivateServiceError> {
        let body = serde_json::json!({
            "sources": sources.iter().map(|(id, revision)| serde_json::json!({"id": id, "revision": revision})).collect::<Vec<_>>(),
        });
        let request = WireRequest {
            method: crate::context_private::Method::Post,
            path: "v1/context-private-reference-check".into(),
            body: Some(Zeroizing::new(serde_json::to_vec(&body).expect("json"))),
        };
        let reply = call(&self.endpoint, token, &request, MAX_RECORD_BYTES).await;
        let (status, body) = reply.ok_or(PrivateServiceError::Network)?;
        let inner = self.lock();
        Self::may_commit(&inner, session, lease, owner, epoch)?;
        if status != 200 {
            return Err(refused(status, &body));
        }
        crate::context_weekly_draft::PrivateCheck::from_reply(sources.to_vec(), &body)
            .ok_or(PrivateServiceError::InvalidResponse)
    }

    /// Copy a weekly draft (T22). This is the only way to copy: it reads every
    /// page of the draft's week again now, for the signed-in owner, and checks
    /// the picked private revisions now; the draft's text is returned only if
    /// both still match what the user reviewed.
    pub async fn copy_draft<A: PrivateSession>(
        &self,
        session: &A,
        draft: &crate::context_weekly_draft::WeeklyDraft,
    ) -> Result<
        Result<Zeroizing<String>, crate::context_weekly_draft::CopyBlocked>,
        PrivateServiceError,
    > {
        let (lease, owner, token) = session.current()?;
        if owner != draft.owner() {
            return Err(PrivateServiceError::StaleIdentity);
        }
        // One epoch for the whole copy: a clear at any point cancels it.
        let epoch = {
            let mut inner = self.lock();
            Self::journal_for(&mut inner, &owner)?;
            inner.view_epoch
        };
        let fresh = match read_week_fresh(
            &self.endpoint,
            &token,
            session,
            &lease,
            &owner,
            draft.week(),
        )
        .await?
        {
            Ok(fresh) => fresh,
            Err(_) => {
                return Ok(Err(
                    crate::context_weekly_draft::CopyBlocked::TeamCannotConfirm,
                ))
            }
        };
        let refs = draft.private_refs();
        let check = if refs.is_empty() {
            None
        } else {
            Some(
                self.reference_check_with(session, &lease, &owner, &token, &refs, epoch)
                    .await?,
            )
        };
        let inner = self.lock();
        Self::may_commit(&inner, session, &lease, &owner, epoch)?;
        Ok(draft.copy(&fresh, check))
    }

    /// One page of the user's own private records (list or text search).
    pub async fn list<A: PrivateSession>(
        &self,
        session: &A,
        query: &ListQuery,
    ) -> Result<ListPage, PrivateServiceError> {
        let window = query.validate()?;
        let (lease, owner, token) = session.current()?;
        let epoch = {
            let mut inner = self.lock();
            Self::journal_for(&mut inner, &owner)?;
            inner.view_epoch
        };
        let request = list_request(query);
        let reply = call(&self.endpoint, &token, &request, MAX_PAGE_BYTES).await;
        let (status, body) = reply.ok_or(PrivateServiceError::Network)?;
        let page = if status == 200 {
            Some(decode_list_page(&body, query)?)
        } else {
            None
        };
        let mut inner = self.lock();
        Self::may_commit(&inner, session, &lease, &owner, epoch)?;
        let Some(page) = page else {
            return Err(refused(status, &body));
        };
        let mut omitted = page.omitted_records;
        let mut items = Vec::with_capacity(page.items.len());
        for record in page.items {
            let id = record.id.clone();
            inner.cache.observe(record.clone());
            let shown = inner.cache.get(&id).cloned().unwrap_or(record);
            // A newer revision seen elsewhere may no longer match (withdrawn,
            // retagged, edited): leave it out and say records were left out.
            if item_matches(&shown, query, window) {
                items.push(shown);
            } else {
                omitted = true;
            }
        }
        Ok(ListPage {
            items,
            next_cursor: page.next_cursor,
            omitted_records: omitted,
        })
    }
}

/// Whether `record` belongs in the answer to `query`, as the server filters:
/// not withdrawn, created inside the window, with the tag and project asked
/// for, and containing the search text (SQLite `lower`: ASCII case only).
fn item_matches(record: &PrivateRecord, query: &ListQuery, (start, end): (i64, i64)) -> bool {
    let created = crate::context_sources::utc_micros(&record.created_at);
    let text_ok = query.text.as_ref().is_none_or(|q| {
        record
            .text
            .as_ref()
            .is_some_and(|t| t.to_ascii_lowercase().contains(&q.to_ascii_lowercase()))
    });
    !record.withdrawn
        && created.is_some_and(|c| (start..end).contains(&c))
        && query.tag.is_none_or(|t| record.tags.contains(&t))
        && query
            .project_id
            .as_ref()
            .is_none_or(|p| record.project_id.as_ref() == Some(p))
        && text_ok
}

/// Read every page of `week` now, for `owner`, under `lease` (T21/T22). The
/// inner error says why the read cannot vouch for the week (a page failed or
/// the listing moved); sign-in loss and network failure are outer errors.
pub(crate) async fn read_week_fresh<A: PrivateSession>(
    endpoint: &ContextEndpoint,
    token: &str,
    session: &A,
    lease: &A::Lease,
    owner: &str,
    week: &crate::context_sources::Week,
) -> Result<
    Result<crate::context_sources::SourceCollector, crate::context_sources::SourceError>,
    PrivateServiceError,
> {
    use crate::context_sources::{fetch_draft_page, CollectEvent, SourceCollector, SourceError};
    let mut fresh = SourceCollector::new(owner, week.clone());
    while let Some(request) = fresh.next_request() {
        let page = fetch_draft_page(endpoint, token, week, request.cursor.as_deref()).await;
        if !session.still_current(lease) {
            return Err(PrivateServiceError::StaleIdentity);
        }
        match fresh.apply(&request, page) {
            CollectEvent::Appended(_) => {}
            CollectEvent::Failed(SourceError::SignInRequired) => {
                return Err(PrivateServiceError::NotSignedIn)
            }
            CollectEvent::Failed(SourceError::Network) => return Err(PrivateServiceError::Network),
            CollectEvent::Failed(error) => return Ok(Err(error)),
            CollectEvent::Restarted | CollectEvent::Stale => {
                return Ok(Err(SourceError::InvalidCursor))
            }
        }
    }
    Ok(Ok(fresh))
}

/// Copy a weekly draft without private picks (T22). Reads every page of the
/// week again now for the signed-in owner and returns the text only if the
/// team sources still match the draft. Drafts with private picks go through
/// `PrivateService::copy_draft`, which also checks those revisions.
pub async fn copy_team_draft<A: PrivateSession>(
    endpoint: &ContextEndpoint,
    session: &A,
    draft: &crate::context_weekly_draft::WeeklyDraft,
) -> Result<Result<Zeroizing<String>, crate::context_weekly_draft::CopyBlocked>, PrivateServiceError>
{
    if !draft.private_refs().is_empty() {
        return Err(PrivateServiceError::NotAllowed);
    }
    let (lease, owner, token) = session.current()?;
    if owner != draft.owner() {
        return Err(PrivateServiceError::StaleIdentity);
    }
    let fresh =
        match read_week_fresh(endpoint, &token, session, &lease, &owner, draft.week()).await? {
            Ok(fresh) => fresh,
            Err(_) => {
                return Ok(Err(
                    crate::context_weekly_draft::CopyBlocked::TeamCannotConfirm,
                ))
            }
        };
    if !session.still_current(&lease) {
        return Err(PrivateServiceError::StaleIdentity);
    }
    Ok(draft.copy(&fresh, None))
}

/// The private records this session has shown, by id, for composing a weekly
/// draft from the user's explicit picks.
impl<S: PrivateStore> PrivateService<S> {
    pub fn shown_records<A: PrivateSession>(
        &self,
        session: &A,
        ids: &[String],
    ) -> Result<Vec<PrivateRecord>, PrivateServiceError> {
        let (_, owner, _) = session.current()?;
        let inner = self.lock();
        if inner.journal.as_ref().map(|(o, _)| o.as_str()) != Some(owner.as_str()) {
            return Err(PrivateServiceError::NotFound);
        }
        ids.iter()
            .map(|id| {
                inner
                    .cache
                    .get(id)
                    .cloned()
                    .ok_or(PrivateServiceError::NotFound)
            })
            .collect()
    }
}

fn list_request(query: &ListQuery) -> WireRequest {
    match &query.text {
        None => {
            let mut pairs = vec![
                ("start".to_string(), query.start.clone()),
                ("end".to_string(), query.end.clone()),
                ("limit".to_string(), query.limit.to_string()),
            ];
            if let Some(cursor) = &query.cursor {
                pairs.push(("cursor".into(), cursor.clone()));
            }
            if let Some(tag) = query.tag {
                pairs.push(("tag".into(), tag_wire(tag).into()));
            }
            if let Some(project) = &query.project_id {
                pairs.push(("projectId".into(), project.clone()));
            }
            let encoded = pairs
                .iter()
                .map(|(k, v)| format!("{k}={}", urlencoding::encode(v)))
                .collect::<Vec<_>>()
                .join("&");
            WireRequest {
                method: crate::context_private::Method::Get,
                path: format!("v1/context-private?{encoded}"),
                body: None,
            }
        }
        Some(text) => {
            let mut body = serde_json::json!({
                "start": query.start,
                "end": query.end,
                "limit": query.limit,
                "q": text,
            });
            if let Some(cursor) = &query.cursor {
                body["cursor"] = cursor.as_str().into();
            }
            if let Some(tag) = query.tag {
                body["tag"] = tag_wire(tag).into();
            }
            if let Some(project) = &query.project_id {
                body["projectId"] = project.as_str().into();
            }
            WireRequest {
                method: crate::context_private::Method::Post,
                path: "v1/context-private-search".into(),
                body: Some(Zeroizing::new(serde_json::to_vec(&body).expect("json"))),
            }
        }
    }
}

fn refused(status: u16, body: &[u8]) -> PrivateServiceError {
    match status {
        401 => PrivateServiceError::NotSignedIn,
        _ => PrivateServiceError::Refused {
            status,
            code: crate::context_login::refusal_code(body).unwrap_or_else(|| "UNSPECIFIED".into()),
        },
    }
}

/// One request; `None` when no complete response was read. Redirects are
/// never followed and no Origin header is sent (the server requires a native
/// client).
async fn call(
    endpoint: &ContextEndpoint,
    token: &str,
    request: &WireRequest,
    limit: usize,
) -> Option<(u16, Vec<u8>)> {
    use crate::context_private::Method;
    let url = reqwest::Url::parse(endpoint.canonical())
        .ok()?
        .join(&request.path)
        .ok()?;
    let client = endpoint.transport_client().ok()?;
    let builder = match request.method {
        Method::Get => client.get(url),
        Method::Post => client.post(url),
        Method::Put => client.put(url),
    };
    let builder = builder.bearer_auth(token);
    let builder = match &request.body {
        Some(body) => builder
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body.to_vec()),
        None => builder,
    };
    let response = builder.send().await.ok()?;
    let status = response.status().as_u16();
    let limit = if status == 200 { limit } else { 4_096 };
    let body = read_bounded(response, limit).await.ok()?;
    Some((status, body))
}
