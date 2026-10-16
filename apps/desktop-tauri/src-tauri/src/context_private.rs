//! Private Context mutation journal (T18a).
//!
//! Private records (`/v1/context-private*`) are the user's own notes: never
//! dispatched to a team destination. Their mutations have a state machine and
//! a store of their own, separate from the team backup journal
//! (`context_journal`), sealed under `RecordKind::PrivateMutation` so a team
//! row can never be opened as a private one or the reverse.
//!
//! * Identity is fixed before the first request: a create carries its
//!   `eventId`, an edit or withdrawal its `requestId` plus the
//!   `expectedRevision` it was written against. The server deduplicates both,
//!   so resending the same mutation after a lost reply cannot apply it twice,
//!   and a mutation written against an old revision ends in
//!   `PRIVATE_CONTEXT_REVISION_CONFLICT` instead of overwriting newer text.
//! * A mutation is saved before every request. After a lost reply it is
//!   `Unknown` and is resolved by readback. A readback reports a point in time
//!   only: `not-observed` is not proof that nothing happened, so the entry
//!   stays `Unknown` (resending the same request stays safe). An observed
//!   readback shows the record as of that mutation, which may be older than
//!   the current one; the record cache never lets an older revision replace a
//!   newer one, and callers fetch the current record before showing it.
//! * Conflicts and refusals keep the mutation (text included) so the user can
//!   recover it; nothing is re-keyed or rebased automatically.
//! * The store has no rollback anchor. A restored older copy can only resend
//!   mutations with their original ids and revisions, which the server answers
//!   with the earlier result or a revision conflict.
use std::collections::HashMap;
use std::path::Path;

use base64::Engine as _;
use ring::rand::{SecureRandom, SystemRandom};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use zeroize::Zeroizing;

use crate::context_keys::{owner_digest, Aad, KeySet, RecordKind};

pub const MAX_TEXT_CHARS: usize = 8192;
pub const MAX_TAGS: usize = 5;
pub const MAX_REVISION: u32 = 1_000_000;
/// Requests per mutation; every one carries the same identity.
pub const MAX_SENDS: u32 = 5;
const MAX_RESPONSE_BYTES: usize = 65_536;
const READBACK_MEANING: &str = "point-in-time-only-not-absence-proof";
const SCHEMA: u16 = 1;

fn identifier(value: &str) -> bool {
    (1..=128).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn project_identifier(value: &str) -> bool {
    value.len() <= 80 && identifier(value)
}

/// `owner/name`, as the server's `expectedRepositoryId` pattern.
fn repository(value: &str) -> bool {
    let part = |p: &str| {
        p.bytes().next().is_some_and(|b| b.is_ascii_alphanumeric())
            && p.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.' || b == b'-')
    };
    (3..=200).contains(&value.len())
        && value
            .split_once('/')
            .is_some_and(|(owner, name)| part(owner) && part(name))
}

/// Server record ids are lowercase UUID v4.
fn record_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(i, b)| match i {
            8 | 13 | 18 | 23 => *b == b'-',
            _ => b.is_ascii_digit() || (b'a'..=b'f').contains(b),
        })
        && bytes[14] == b'4'
        && matches!(bytes[19], b'8' | b'9' | b'a' | b'b')
}

/// `YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`, as the server writes times.
pub(crate) fn utc_timestamp(value: &str) -> bool {
    let (main, fraction) = match value.strip_suffix("+00:00") {
        Some(rest) => rest.split_once('.').map_or((rest, ""), |(m, f)| (m, f)),
        None => return false,
    };
    let b = main.as_bytes();
    b.len() == 19
        && b.iter().enumerate().all(|(i, c)| match i {
            4 | 7 => *c == b'-',
            10 => *c == b'T',
            13 | 16 => *c == b':',
            _ => c.is_ascii_digit(),
        })
        && (fraction.is_empty()
            || (fraction.len() <= 6 && fraction.bytes().all(|c| c.is_ascii_digit())))
        && !value.ends_with(".+00:00")
}

fn valid_text(text: &str) -> bool {
    let count = text.chars().count();
    (1..=MAX_TEXT_CHARS).contains(&count) && !text.contains('\0')
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Tag {
    Decision,
    Progress,
    Blocker,
    Question,
    Verification,
}

fn valid_tags(tags: &[Tag]) -> bool {
    let mut seen = std::collections::HashSet::new();
    tags.len() <= MAX_TAGS && tags.iter().all(|t| seen.insert(*t))
}

// ---------------------------------------------------------------- records

/// A private record as the server returned it. `Debug` omits the text.
#[derive(Clone, PartialEq, Eq)]
pub struct PrivateRecord {
    pub id: String,
    pub event_id: String,
    pub revision: u32,
    pub withdrawn: bool,
    pub created_at: String,
    pub revision_changed_at: String,
    /// `None` once withdrawn: the server no longer returns the body.
    pub text: Option<Zeroizing<String>>,
    pub tags: Vec<Tag>,
    pub project_id: Option<String>,
    pub repository_id: Option<String>,
}

impl std::fmt::Debug for PrivateRecord {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PrivateRecord")
            .field("id", &self.id)
            .field("event_id", &self.event_id)
            .field("revision", &self.revision)
            .field("withdrawn", &self.withdrawn)
            .field("has_text", &self.text.is_some())
            .finish_non_exhaustive()
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawRecord {
    version: u32,
    id: String,
    event_id: String,
    revision: u32,
    visibility: String,
    evidence_kind: String,
    withdrawn: bool,
    created_at: String,
    revision_changed_at: String,
    text: Option<String>,
    tags: Option<Vec<Tag>>,
    #[serde(default)]
    project_id: Option<String>,
    #[serde(default)]
    repository_id: Option<String>,
}

fn record(raw: RawRecord) -> Option<PrivateRecord> {
    let body_ok = match (&raw.text, &raw.tags, raw.withdrawn) {
        (Some(text), Some(tags), false) => valid_text(text) && valid_tags(tags),
        (None, None, true) => true,
        _ => false,
    };
    let ok = raw.version == 1
        && record_id(&raw.id)
        && identifier(&raw.event_id)
        && (1..=MAX_REVISION).contains(&raw.revision)
        && raw.visibility == "private"
        && raw.evidence_kind == "user-statement"
        && utc_timestamp(&raw.created_at)
        && utc_timestamp(&raw.revision_changed_at)
        && body_ok
        && raw.project_id.as_deref().is_none_or(project_identifier)
        && raw.repository_id.as_deref().is_none_or(repository);
    ok.then(|| PrivateRecord {
        id: raw.id,
        event_id: raw.event_id,
        revision: raw.revision,
        withdrawn: raw.withdrawn,
        created_at: raw.created_at,
        revision_changed_at: raw.revision_changed_at,
        text: raw.text.map(Zeroizing::new),
        tags: raw.tags.unwrap_or_default(),
        project_id: raw.project_id,
        repository_id: raw.repository_id,
    })
}

pub fn decode_record(bytes: &[u8]) -> Option<PrivateRecord> {
    if bytes.len() > MAX_RESPONSE_BYTES {
        return None;
    }
    record(serde_json::from_slice(bytes).ok()?)
}

/// What an event or request readback proved at that moment.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Readback {
    /// The mutation was applied; `record` is as of that mutation, which may be
    /// older than the current record.
    Observed {
        record: Box<PrivateRecord>,
        mutation_id: Option<String>,
    },
    /// Not seen at this moment. Not proof that it never happened.
    NotObserved,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawReadback {
    version: u32,
    meaning: String,
    status: String,
    source: Option<RawRecord>,
    mutation_id: Option<String>,
}

pub fn decode_readback(bytes: &[u8]) -> Option<Readback> {
    if bytes.len() > MAX_RESPONSE_BYTES {
        return None;
    }
    let raw: RawReadback = serde_json::from_slice(bytes).ok()?;
    if raw.version != 1 || raw.meaning != READBACK_MEANING {
        return None;
    }
    match (raw.status.as_str(), raw.source, raw.mutation_id) {
        ("observed", Some(source), mutation_id) => {
            if mutation_id.as_deref().is_some_and(|m| !identifier(m)) {
                return None;
            }
            Some(Readback::Observed {
                record: Box::new(record(source)?),
                mutation_id,
            })
        }
        ("not-observed", None, None) => Some(Readback::NotObserved),
        _ => None,
    }
}

/// The newest revision seen for each record. An older revision (a readback,
/// a slow page) never replaces a newer one.
#[derive(Debug, Default)]
pub struct RecordCache {
    records: HashMap<String, PrivateRecord>,
}

impl RecordCache {
    /// Returns whether `record` is now the cached one.
    pub fn observe(&mut self, record: PrivateRecord) -> bool {
        match self.records.get(&record.id) {
            Some(current) if current.revision >= record.revision => false,
            _ => {
                self.records.insert(record.id.clone(), record);
                true
            }
        }
    }

    pub fn get(&self, id: &str) -> Option<&PrivateRecord> {
        self.records.get(id)
    }

    /// Drop everything (sign-out, account switch, panel closed).
    pub fn clear(&mut self) {
        self.records.clear();
    }
}

// ---------------------------------------------------------------- mutations

/// One private mutation with its identity fixed. `Debug` omits the text.
#[derive(Clone, PartialEq, Eq)]
pub enum PrivateMutation {
    Create {
        event_id: String,
        text: Zeroizing<String>,
        tags: Vec<Tag>,
        project_id: Option<String>,
        repository_id: Option<String>,
    },
    Edit {
        record_id: String,
        event_id: String,
        request_id: String,
        expected_revision: u32,
        text: Zeroizing<String>,
        tags: Vec<Tag>,
    },
    Withdraw {
        record_id: String,
        event_id: String,
        request_id: String,
        expected_revision: u32,
    },
}

impl std::fmt::Debug for PrivateMutation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Create { event_id, .. } => f
                .debug_struct("Create")
                .field("event_id", event_id)
                .finish_non_exhaustive(),
            Self::Edit {
                record_id,
                request_id,
                expected_revision,
                ..
            } => f
                .debug_struct("Edit")
                .field("record_id", record_id)
                .field("request_id", request_id)
                .field("expected_revision", expected_revision)
                .finish_non_exhaustive(),
            Self::Withdraw {
                record_id,
                request_id,
                expected_revision,
                ..
            } => f
                .debug_struct("Withdraw")
                .field("record_id", record_id)
                .field("request_id", request_id)
                .field("expected_revision", expected_revision)
                .finish(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PrivateError {
    InvalidInput,
    StoreUnavailable,
    /// The same id is already reserved with a different payload.
    IdentityConflict,
    NotFound,
    /// The mutation's state does not allow this step.
    NotAllowed,
    RetryLimit,
}

/// A fresh id for an `eventId` or `requestId`.
pub fn new_identifier(prefix: &str) -> Result<String, PrivateError> {
    let mut bytes = [0u8; 18];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| PrivateError::StoreUnavailable)?;
    let id = format!(
        "{prefix}{}",
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    );
    if identifier(&id) {
        Ok(id)
    } else {
        Err(PrivateError::InvalidInput)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    Get,
    Post,
    Put,
}

/// One HTTP request, relative to the Context endpoint. `body` may hold text.
pub struct WireRequest {
    pub method: Method,
    pub path: String,
    pub body: Option<Zeroizing<Vec<u8>>>,
}

impl PrivateMutation {
    pub fn validate(&self) -> Result<(), PrivateError> {
        let ok = match self {
            Self::Create {
                event_id,
                text,
                tags,
                project_id,
                repository_id,
            } => {
                identifier(event_id)
                    && valid_text(text)
                    && valid_tags(tags)
                    && project_id.as_deref().is_none_or(project_identifier)
                    && repository_id.as_deref().is_none_or(repository)
                    && (repository_id.is_none() || project_id.is_some())
            }
            Self::Edit {
                record_id: r,
                event_id,
                request_id,
                expected_revision,
                text,
                tags,
            } => {
                record_id(r)
                    && identifier(event_id)
                    && identifier(request_id)
                    && (1..=MAX_REVISION).contains(expected_revision)
                    && valid_text(text)
                    && valid_tags(tags)
            }
            Self::Withdraw {
                record_id: r,
                event_id,
                request_id,
                expected_revision,
            } => {
                record_id(r)
                    && identifier(event_id)
                    && identifier(request_id)
                    && (1..=MAX_REVISION).contains(expected_revision)
            }
        };
        if ok {
            Ok(())
        } else {
            Err(PrivateError::InvalidInput)
        }
    }

    /// Unique per owner: the create's eventId or the edit/withdraw requestId,
    /// in separate namespaces.
    pub fn key(&self) -> String {
        match self {
            Self::Create { event_id, .. } => format!("create:{event_id}"),
            Self::Edit { request_id, .. } | Self::Withdraw { request_id, .. } => {
                format!("request:{request_id}")
            }
        }
    }

    fn event_id(&self) -> &str {
        match self {
            Self::Create { event_id, .. }
            | Self::Edit { event_id, .. }
            | Self::Withdraw { event_id, .. } => event_id,
        }
    }

    /// The request that applies this mutation. Always the same bytes.
    pub fn request(&self) -> WireRequest {
        let (method, path, body) = match self {
            Self::Create {
                event_id,
                text,
                tags,
                project_id,
                repository_id,
            } => {
                let mut body = serde_json::json!({"eventId": event_id, "text": text.as_str(), "tags": tags_wire(tags)});
                if let Some(project) = project_id {
                    body["projectId"] = project.as_str().into();
                }
                if let Some(repository) = repository_id {
                    body["expectedRepositoryId"] = repository.as_str().into();
                }
                (Method::Post, "v1/context-private".to_string(), body)
            }
            Self::Edit {
                record_id,
                request_id,
                expected_revision,
                text,
                tags,
                ..
            } => (
                Method::Put,
                format!("v1/context-private/{record_id}"),
                serde_json::json!({
                    "requestId": request_id,
                    "expectedRevision": expected_revision,
                    "text": text.as_str(),
                    "tags": tags_wire(tags),
                }),
            ),
            Self::Withdraw {
                record_id,
                request_id,
                expected_revision,
                ..
            } => (
                Method::Post,
                format!("v1/context-private/{record_id}/withdraw"),
                serde_json::json!({"requestId": request_id, "expectedRevision": expected_revision}),
            ),
        };
        WireRequest {
            method,
            path,
            body: Some(Zeroizing::new(serde_json::to_vec(&body).expect("json"))),
        }
    }

    /// The readback that tells whether this mutation was applied.
    pub fn readback(&self) -> WireRequest {
        let path = match self {
            Self::Create { event_id, .. } => format!("v1/context-private-events/{event_id}"),
            Self::Edit {
                event_id,
                request_id,
                ..
            }
            | Self::Withdraw {
                event_id,
                request_id,
                ..
            } => {
                format!("v1/context-private-events/{event_id}/requests/{request_id}")
            }
        };
        WireRequest {
            method: Method::Get,
            path,
            body: None,
        }
    }

    fn same_body(&self, record: &PrivateRecord) -> bool {
        let (text, tags) = match self {
            Self::Create { text, tags, .. } | Self::Edit { text, tags, .. } => (text, tags),
            Self::Withdraw { .. } => return record.withdrawn,
        };
        !record.withdrawn
            && record.text.as_deref().map(String::as_str) == Some(text.as_str())
            && record.tags == *tags
    }

    /// The revision this mutation produced, if a 200 reply to *this* request
    /// proves it. The server answers 200 only for the same eventId+payload or
    /// requestId+payload (anything else is a 409), so the record may already
    /// be later than the mutation (edited or withdrawn since). A repository is
    /// compared only when the create asked for one: otherwise the server fills
    /// in the project's actual repository.
    fn proven_by_reply(&self, record: &PrivateRecord) -> Option<u32> {
        match self {
            Self::Create {
                event_id,
                project_id,
                repository_id,
                ..
            } => {
                let first = record.revision == 1 && !record.withdrawn;
                (record.event_id == *event_id
                    && (!first
                        || (self.same_body(record)
                            && record.project_id == *project_id
                            && (repository_id.is_none()
                                || record.repository_id == *repository_id))))
                    .then_some(1)
            }
            Self::Edit {
                record_id,
                event_id,
                expected_revision,
                ..
            }
            | Self::Withdraw {
                record_id,
                event_id,
                expected_revision,
                ..
            } => {
                let produced = expected_revision + 1;
                let withdraw = matches!(self, Self::Withdraw { .. });
                let consistent = if record.revision == produced {
                    self.same_body(record)
                } else {
                    // Later than this mutation: only a withdrawal can follow it,
                    // and a withdrawal stays withdrawn.
                    record.revision > produced && (record.withdrawn || !withdraw)
                };
                (record.id == *record_id && record.event_id == *event_id && consistent)
                    .then_some(produced)
            }
        }
    }

    /// The revision this mutation produced, if a readback proves it. A
    /// readback shows the record, not the accepted payload, so it proves a
    /// mutation only when the record is exactly what this mutation produced:
    /// revision 1 with this project-less create's text and tags,
    /// or `expected+1` with this edit's body, bound to this requestId. A
    /// withdrawal is never proven by readback. Anything else is inconclusive.
    fn proven_by_readback(&self, record: &PrivateRecord, mutation_id: Option<&str>) -> Option<u32> {
        match self {
            Self::Create {
                event_id,
                project_id,
                repository_id,
                ..
            } => (mutation_id.is_none()
                && record.event_id == *event_id
                && record.revision == 1
                && self.same_body(record)
                // With a project, the server fills in the repository whether or
                // not one was asked for, so the record cannot tell two such
                // creates apart: only project-less creates are proven here.
                && project_id.is_none()
                && repository_id.is_none()
                && record.project_id.is_none()
                && record.repository_id.is_none())
            .then_some(1),
            // A tombstone does not say which request withdrew it (an edit's
            // requestId reused for a refused withdrawal would match too).
            Self::Withdraw { .. } => None,
            Self::Edit {
                record_id,
                event_id,
                request_id,
                expected_revision,
                ..
            } => (mutation_id == Some(request_id.as_str())
                && record.id == *record_id
                && record.event_id == *event_id
                && record.revision == expected_revision + 1
                && self.same_body(record))
            .then_some(expected_revision + 1),
        }
    }

    /// Whether a readback is about this mutation's identity at all.
    fn readback_concerns(&self, record: &PrivateRecord, mutation_id: Option<&str>) -> bool {
        match self {
            Self::Create { event_id, .. } => mutation_id.is_none() && record.event_id == *event_id,
            Self::Edit {
                record_id,
                request_id,
                ..
            }
            | Self::Withdraw {
                record_id,
                request_id,
                ..
            } => mutation_id == Some(request_id.as_str()) && record.id == *record_id,
        }
    }
}

fn tags_wire(tags: &[Tag]) -> Vec<&'static str> {
    tags.iter()
        .map(|t| match t {
            Tag::Decision => "decision",
            Tag::Progress => "progress",
            Tag::Blocker => "blocker",
            Tag::Question => "question",
            Tag::Verification => "verification",
        })
        .collect()
}

// ---------------------------------------------------------------- journal

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "phase", content = "detail")]
pub enum Phase {
    /// Never sent: send allowed.
    Reserved,
    /// A request may be in flight.
    Dispatching,
    /// The outcome is not known: read back; resending the same request is safe.
    Unknown(String),
    /// Applied as `revision` of `record_id` (as of the mutation; fetch the
    /// current record before showing it).
    Applied { record_id: String, revision: u32 },
    /// Revision or payload conflict: the user decides. Kept for recovery.
    Conflict(String),
    /// Refused for good (not found, invalid).
    Rejected(String),
    /// Sign-in or permission lost; the user may retry with the same identity.
    Blocked(String),
}

#[derive(Clone, PartialEq, Eq)]
pub struct JournalEntry {
    pub owner: String,
    pub mutation: PrivateMutation,
    pub phase: Phase,
    pub sends: u32,
    /// A request went out and nothing has settled its outcome yet. While set,
    /// the mutation can be neither dismissed nor re-keyed, whatever the phase
    /// (a sign-in refusal does not prove the earlier request did nothing).
    pub maybe_applied: bool,
}

impl std::fmt::Debug for JournalEntry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("JournalEntry")
            .field("mutation", &self.mutation)
            .field("phase", &self.phase)
            .field("sends", &self.sends)
            .finish_non_exhaustive()
    }
}

/// Durable storage for journal entries: `save` returns only once durable.
pub trait PrivateStore {
    fn save(&mut self, entry: &JournalEntry) -> Result<(), PrivateError>;
    fn remove(&mut self, owner: &str, key: &str) -> Result<(), PrivateError>;
    fn load(&self, owner: &str) -> Result<Vec<JournalEntry>, PrivateError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReserveOutcome {
    Reserved,
    /// The same mutation was already reserved; nothing changed.
    AlreadyReserved,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Applied {
    Updated,
    /// The entry is gone, belongs to someone else, or is not waiting for this.
    Ignored,
}

fn refusal(body: &[u8]) -> String {
    crate::context_login::refusal_code(body).unwrap_or_else(|| "UNSPECIFIED".into())
}

pub struct PrivateJournal<S: PrivateStore> {
    store: S,
    entries: Vec<JournalEntry>,
}

impl<S: PrivateStore> PrivateJournal<S> {
    /// Open for `owner`, loading that owner's saved entries. An entry saved as
    /// `Dispatching` may have reached the server: it becomes `Unknown`.
    pub fn open(store: S, owner: &str) -> Result<Self, PrivateError> {
        Self::try_open(store, owner).map_err(|(_, error)| error)
    }

    /// Like `open`, but gives the store back when loading fails, so a
    /// temporary failure does not lose it.
    pub fn try_open(store: S, owner: &str) -> Result<Self, (S, PrivateError)> {
        let mut entries = match store.load(owner) {
            Ok(entries) => entries,
            Err(error) => return Err((store, error)),
        };
        for entry in &mut entries {
            if entry.phase == Phase::Dispatching {
                entry.phase = Phase::Unknown("RESTARTED".into());
            }
        }
        Ok(Self { store, entries })
    }

    pub fn store(&self) -> &S {
        &self.store
    }

    /// Give the store back (owner switch: reopen it for the new owner).
    pub fn into_store(self) -> S {
        self.store
    }

    /// After this process reopened its own journal while a request it sent
    /// is still in flight, the reopen marked that entry `Unknown("RESTARTED")`.
    /// The caller, which holds that request, takes it back as `Dispatching`
    /// so the reply can be recorded. The saved phase is already `Dispatching`.
    pub fn reclaim_in_flight(&mut self, owner: &str, key: &str) -> bool {
        match self.position(owner, key) {
            Some(index) if self.entries[index].phase == Phase::Unknown("RESTARTED".into()) => {
                self.entries[index].phase = Phase::Dispatching;
                true
            }
            _ => false,
        }
    }

    /// The phase of a mutation, if this owner has it.
    pub fn phase(&self, owner: &str, key: &str) -> Option<&Phase> {
        self.entry(owner, key).map(|e| &e.phase)
    }

    pub fn store_mut(&mut self) -> &mut S {
        &mut self.store
    }

    fn position(&self, owner: &str, key: &str) -> Option<usize> {
        self.entries
            .iter()
            .position(|e| e.owner == owner && e.mutation.key() == key)
    }

    /// Save first; memory changes only after the store did.
    fn commit(&mut self, index: usize, next: JournalEntry) -> Result<(), PrivateError> {
        self.store.save(&next)?;
        self.entries[index] = next;
        Ok(())
    }

    /// Record what a reply proved; memory follows even if the save fails (the
    /// stored older phase only leads to readback or resending the same
    /// mutation). The save error is reported.
    fn record(&mut self, index: usize, next: JournalEntry) -> Result<(), PrivateError> {
        let saved = self.store.save(&next);
        self.entries[index] = next;
        saved
    }

    /// Reserve `mutation` durably before anything is sent.
    pub fn reserve(
        &mut self,
        owner: &str,
        mutation: PrivateMutation,
    ) -> Result<ReserveOutcome, PrivateError> {
        mutation.validate()?;
        if let Some(index) = self.position(owner, &mutation.key()) {
            return if self.entries[index].mutation == mutation {
                Ok(ReserveOutcome::AlreadyReserved)
            } else {
                Err(PrivateError::IdentityConflict)
            };
        }
        let entry = JournalEntry {
            owner: owner.to_string(),
            mutation,
            phase: Phase::Reserved,
            sends: 0,
            maybe_applied: false,
        };
        self.store.save(&entry)?;
        self.entries.push(entry);
        Ok(ReserveOutcome::Reserved)
    }

    /// Start sending. Allowed when never sent, when the outcome is unknown, or
    /// as a user retry after a block. Always the same request.
    pub fn begin_send(&mut self, owner: &str, key: &str) -> Result<WireRequest, PrivateError> {
        let index = self.position(owner, key).ok_or(PrivateError::NotFound)?;
        let entry = &self.entries[index];
        if !matches!(
            entry.phase,
            Phase::Reserved | Phase::Unknown(_) | Phase::Blocked(_)
        ) {
            return Err(PrivateError::NotAllowed);
        }
        if entry.sends >= MAX_SENDS {
            return Err(PrivateError::RetryLimit);
        }
        let request = entry.mutation.request();
        let mut next = entry.clone();
        next.phase = Phase::Dispatching;
        next.sends += 1;
        next.maybe_applied = true;
        self.commit(index, next)?;
        Ok(request)
    }

    /// Apply the reply to a send. `None` means no response was read.
    pub fn finish_send(
        &mut self,
        owner: &str,
        key: &str,
        reply: Option<(u16, &[u8])>,
        cache: &mut RecordCache,
    ) -> Result<Applied, PrivateError> {
        let Some(index) = self.position(owner, key) else {
            return Ok(Applied::Ignored);
        };
        let entry = &self.entries[index];
        if entry.phase != Phase::Dispatching {
            return Ok(Applied::Ignored);
        }
        let mut settled = true;
        let phase = match reply {
            Some((200, body)) => match decode_record(body) {
                Some(record) => match entry.mutation.proven_by_reply(&record) {
                    Some(revision) => {
                        let phase = Phase::Applied {
                            record_id: record.id.clone(),
                            revision,
                        };
                        cache.observe(record);
                        phase
                    }
                    None => {
                        settled = false;
                        Phase::Unknown("INVALID_RESPONSE".into())
                    }
                },
                None => {
                    settled = false;
                    Phase::Unknown("INVALID_RESPONSE".into())
                }
            },
            Some((409, body)) => Phase::Conflict(refusal(body)),
            // A refusal of the first request settles it. A refusal of a resend
            // does not prove the earlier request did nothing (permission may
            // have changed since): it stays unknown, so readback and the same
            // request remain available once access is back.
            Some((404 | 413 | 422, body)) if entry.sends <= 1 => Phase::Rejected(refusal(body)),
            Some((404 | 413 | 422, body)) => {
                settled = false;
                Phase::Unknown(refusal(body))
            }
            Some((401 | 403, body)) => {
                settled = false;
                Phase::Blocked(refusal(body))
            }
            None => {
                settled = false;
                Phase::Unknown("NO_RESPONSE".into())
            }
            Some((_, body)) => {
                settled = false;
                Phase::Unknown(refusal(body))
            }
        };
        let mut next = entry.clone();
        next.phase = phase;
        if settled {
            next.maybe_applied = false;
        }
        self.record(index, next)?;
        Ok(Applied::Updated)
    }

    /// The readback request for a mutation whose outcome is unsettled
    /// (`Unknown`, or `Blocked` after a request that may have been applied).
    pub fn readback_request(&self, owner: &str, key: &str) -> Option<WireRequest> {
        let entry = &self.entries[self.position(owner, key)?];
        Self::unsettled(entry).then(|| entry.mutation.readback())
    }

    fn unsettled(entry: &JournalEntry) -> bool {
        matches!(entry.phase, Phase::Unknown(_))
            || (entry.maybe_applied && matches!(entry.phase, Phase::Blocked(_)))
    }

    /// Apply a readback. Only an observed readback that matches this mutation
    /// resolves it; `not-observed` keeps it `Unknown`.
    pub fn finish_readback(
        &mut self,
        owner: &str,
        key: &str,
        reply: Option<(u16, &[u8])>,
        cache: &mut RecordCache,
    ) -> Result<Applied, PrivateError> {
        let Some(index) = self.position(owner, key) else {
            return Ok(Applied::Ignored);
        };
        let entry = &self.entries[index];
        if !Self::unsettled(entry) {
            return Ok(Applied::Ignored);
        }
        let mut settled = false;
        let phase = match reply {
            Some((200, body)) => match decode_readback(body) {
                Some(Readback::Observed {
                    record,
                    mutation_id,
                }) => match entry
                    .mutation
                    .proven_by_readback(&record, mutation_id.as_deref())
                {
                    Some(revision) => {
                        settled = true;
                        let phase = Phase::Applied {
                            record_id: record.id.clone(),
                            revision,
                        };
                        cache.observe(*record);
                        phase
                    }
                    // About this identity, but not proof of this payload:
                    // resending the same request decides (200 replay or 409).
                    None if entry
                        .mutation
                        .readback_concerns(&record, mutation_id.as_deref()) =>
                    {
                        Phase::Unknown("READBACK_INCONCLUSIVE".into())
                    }
                    None => return Ok(Applied::Ignored),
                },
                Some(Readback::NotObserved) => Phase::Unknown("NOT_OBSERVED".into()),
                None => return Ok(Applied::Ignored),
            },
            Some((401 | 403, body)) => Phase::Blocked(refusal(body)),
            _ => return Ok(Applied::Ignored),
        };
        let mut next = entry.clone();
        next.phase = phase;
        if settled {
            next.maybe_applied = false;
        }
        self.record(index, next)?;
        Ok(Applied::Updated)
    }

    /// Remove a finished or abandoned mutation the user dismissed. Never while
    /// a request may be in flight or may have been applied unconfirmed.
    pub fn dismiss(&mut self, owner: &str, key: &str) -> Result<(), PrivateError> {
        let index = self.position(owner, key).ok_or(PrivateError::NotFound)?;
        let entry = &self.entries[index];
        if entry.maybe_applied || matches!(entry.phase, Phase::Dispatching | Phase::Unknown(_)) {
            return Err(PrivateError::NotAllowed);
        }
        self.store.remove(owner, key)?;
        self.entries.remove(index);
        Ok(())
    }

    pub fn entry(&self, owner: &str, key: &str) -> Option<&JournalEntry> {
        self.entries.get(self.position(owner, key)?)
    }

    pub fn entries(&self, owner: &str) -> Vec<&JournalEntry> {
        self.entries.iter().filter(|e| e.owner == owner).collect()
    }

    /// Drop other owners' entries from memory (account switch). Their saved
    /// rows stay, sealed to their owner, for that owner's next session.
    pub fn retain_owner(&mut self, owner: &str) {
        self.entries.retain(|e| e.owner == owner);
    }

    /// The event id a mutation belongs to (for grouping in the UI).
    pub fn event_of(&self, owner: &str, key: &str) -> Option<&str> {
        Some(self.entries[self.position(owner, key)?].mutation.event_id())
    }
}

// ---------------------------------------------------------------- sealed store

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SealedEntry {
    v: u32,
    kind: String,
    event_id: String,
    record_id: Option<String>,
    request_id: Option<String>,
    expected_revision: Option<u32>,
    text: Option<String>,
    tags: Vec<Tag>,
    project_id: Option<String>,
    repository_id: Option<String>,
    phase: Phase,
    sends: u32,
    maybe_applied: bool,
}

impl Drop for SealedEntry {
    fn drop(&mut self) {
        use zeroize::Zeroize;
        if let Some(text) = &mut self.text {
            text.zeroize();
        }
    }
}

fn to_sealed(entry: &JournalEntry) -> SealedEntry {
    let mut sealed = SealedEntry {
        v: 1,
        kind: String::new(),
        event_id: String::new(),
        record_id: None,
        request_id: None,
        expected_revision: None,
        text: None,
        tags: Vec::new(),
        project_id: None,
        repository_id: None,
        phase: entry.phase.clone(),
        sends: entry.sends,
        maybe_applied: entry.maybe_applied,
    };
    match &entry.mutation {
        PrivateMutation::Create {
            event_id,
            text,
            tags,
            project_id,
            repository_id,
        } => {
            sealed.kind = "create".into();
            sealed.event_id = event_id.clone();
            sealed.text = Some(text.to_string());
            sealed.tags = tags.clone();
            sealed.project_id = project_id.clone();
            sealed.repository_id = repository_id.clone();
        }
        PrivateMutation::Edit {
            record_id,
            event_id,
            request_id,
            expected_revision,
            text,
            tags,
        } => {
            sealed.kind = "edit".into();
            sealed.event_id = event_id.clone();
            sealed.record_id = Some(record_id.clone());
            sealed.request_id = Some(request_id.clone());
            sealed.expected_revision = Some(*expected_revision);
            sealed.text = Some(text.to_string());
            sealed.tags = tags.clone();
        }
        PrivateMutation::Withdraw {
            record_id,
            event_id,
            request_id,
            expected_revision,
        } => {
            sealed.kind = "withdraw".into();
            sealed.event_id = event_id.clone();
            sealed.record_id = Some(record_id.clone());
            sealed.request_id = Some(request_id.clone());
            sealed.expected_revision = Some(*expected_revision);
        }
    }
    sealed
}

fn from_sealed(owner: &str, sealed: &mut SealedEntry) -> Option<JournalEntry> {
    let text = sealed.text.take().map(Zeroizing::new);
    let mutation = match (
        sealed.kind.as_str(),
        &sealed.record_id,
        &sealed.request_id,
        sealed.expected_revision,
    ) {
        ("create", None, None, None) => PrivateMutation::Create {
            event_id: sealed.event_id.clone(),
            text: text?,
            tags: sealed.tags.clone(),
            project_id: sealed.project_id.clone(),
            repository_id: sealed.repository_id.clone(),
        },
        ("edit", Some(record_id), Some(request_id), Some(expected_revision)) => {
            PrivateMutation::Edit {
                record_id: record_id.clone(),
                event_id: sealed.event_id.clone(),
                request_id: request_id.clone(),
                expected_revision,
                text: text?,
                tags: sealed.tags.clone(),
            }
        }
        ("withdraw", Some(record_id), Some(request_id), Some(expected_revision))
            if text.is_none() =>
        {
            PrivateMutation::Withdraw {
                record_id: record_id.clone(),
                event_id: sealed.event_id.clone(),
                request_id: request_id.clone(),
                expected_revision,
            }
        }
        _ => return None,
    };
    mutation.validate().ok()?;
    Some(JournalEntry {
        owner: owner.to_string(),
        mutation,
        phase: sealed.phase.clone(),
        sends: sealed.sends,
        maybe_applied: sealed.maybe_applied,
    })
}

/// The AAD id for a mutation key: a digest, so both key namespaces fit the
/// identifier alphabet and length.
fn aad_for(owner: &str, key: &str) -> Result<Aad, PrivateError> {
    let digest = Sha256::digest(format!("vox-private-key-v1\0{key}").as_bytes());
    let id: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    Aad::new(
        SCHEMA,
        owner_digest(owner),
        &id,
        RecordKind::PrivateMutation,
    )
    .map_err(|_| PrivateError::InvalidInput)
}

/// SQLite store in its own database (never the team journal's). The owner
/// column is a digest and the mutation key (an eventId or requestId, not a
/// secret) is clear; everything else is sealed with the data key. The AAD
/// binds owner, mutation key and record kind, so a row cannot move between
/// owners, mutations or journals. A row that fails to open is counted in
/// `quarantined` and never acted on.
pub struct SealedPrivateStore {
    db: rusqlite::Connection,
    keys: KeySet,
    quarantined: std::cell::Cell<usize>,
}

impl SealedPrivateStore {
    pub fn open(path: &Path, keys: KeySet) -> Result<Self, PrivateError> {
        let fail = |_| PrivateError::StoreUnavailable;
        let db = rusqlite::Connection::open(path).map_err(fail)?;
        db.execute_batch(
            "PRAGMA synchronous=FULL; PRAGMA secure_delete=ON; PRAGMA journal_mode=DELETE;
             CREATE TABLE IF NOT EXISTS private_mutations_v1 (
               owner BLOB NOT NULL, key TEXT NOT NULL, sealed BLOB NOT NULL,
               PRIMARY KEY(owner, key));",
        )
        .map_err(fail)?;
        Ok(Self {
            db,
            keys,
            quarantined: std::cell::Cell::new(0),
        })
    }

    /// Rows that failed authentication at the last `load`.
    pub fn quarantined(&self) -> usize {
        self.quarantined.get()
    }

    fn owner_column(owner: &str) -> Vec<u8> {
        owner_digest(owner).to_vec()
    }

    fn open_row(&self, owner: &str, key: &str, sealed: &[u8]) -> Option<JournalEntry> {
        let plain = self.keys.open(&aad_for(owner, key).ok()?, sealed).ok()?;
        let mut parsed: SealedEntry = serde_json::from_slice(&plain).ok()?;
        let entry = from_sealed(owner, &mut parsed)?;
        (entry.mutation.key() == key).then_some(entry)
    }
}

impl PrivateStore for SealedPrivateStore {
    fn save(&mut self, entry: &JournalEntry) -> Result<(), PrivateError> {
        let key = entry.mutation.key();
        let plain = Zeroizing::new(
            serde_json::to_vec(&to_sealed(entry)).map_err(|_| PrivateError::InvalidInput)?,
        );
        let sealed = self
            .keys
            .seal(&aad_for(&entry.owner, &key)?, &plain)
            .map_err(|_| PrivateError::StoreUnavailable)?;
        self.db
            .execute(
                "INSERT INTO private_mutations_v1(owner,key,sealed) VALUES(?1,?2,?3)
                 ON CONFLICT(owner,key) DO UPDATE SET sealed=excluded.sealed",
                rusqlite::params![Self::owner_column(&entry.owner), key, sealed],
            )
            .map_err(|_| PrivateError::StoreUnavailable)?;
        Ok(())
    }

    fn remove(&mut self, owner: &str, key: &str) -> Result<(), PrivateError> {
        self.db
            .execute(
                "DELETE FROM private_mutations_v1 WHERE owner=?1 AND key=?2",
                rusqlite::params![Self::owner_column(owner), key],
            )
            .map_err(|_| PrivateError::StoreUnavailable)?;
        Ok(())
    }

    fn load(&self, owner: &str) -> Result<Vec<JournalEntry>, PrivateError> {
        let fail = |_| PrivateError::StoreUnavailable;
        let mut statement = self
            .db
            .prepare("SELECT key, sealed FROM private_mutations_v1 WHERE owner=?1 ORDER BY rowid")
            .map_err(fail)?;
        let rows = statement
            .query_map([Self::owner_column(owner)], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, Vec<u8>>(1)?))
            })
            .map_err(fail)?;
        let (mut entries, mut quarantined) = (Vec::new(), 0);
        for row in rows {
            let (key, sealed) = row.map_err(fail)?;
            match self.open_row(owner, &key, &sealed) {
                Some(entry) => entries.push(entry),
                None => quarantined += 1,
            }
        }
        self.quarantined.set(quarantined);
        Ok(entries)
    }
}
