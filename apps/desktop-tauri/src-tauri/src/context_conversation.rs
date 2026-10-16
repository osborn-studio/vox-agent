//! Feishu conversation history and text outbox (T25).
//!
//! * History is `GET /v1/conversations/{id}/messages`. The server decides what
//!   the signed-in user may read and says so in `mode`: `personal` (the user's
//!   own Feishu CLI chat), `own_submissions` (a group without a shared-read
//!   policy: only this user's own submissions) or `shared_policy` (group
//!   history since the policy took effect). The client never widens that: an
//!   `own_submissions` page holding anything but the user's submissions is
//!   refused, and the timeline states its coverage instead of implying a
//!   complete group history.
//! * Cursors are opaque and single use. A traversal is bound to its first
//!   page's `policyRevision`; a changed revision, `INVALID_CURSOR` or
//!   `AUTHORIZATION_RESET` restarts it from the first page. Late pages of an
//!   earlier traversal are ignored.
//! * Text sends are idempotent per `eventId`: the server returns the existing
//!   submission for the same eventId, conversation and text, and refuses a
//!   different payload (`EVENT_PAYLOAD_CONFLICT`). The outbox fixes the eventId
//!   and text before the first POST and saves the entry before every POST, so a
//!   retry after a lost reply resends the same request and can never become a
//!   second message. A conflict is never "fixed" by a new eventId.
//! * Receipts are checked against the eventId and conversation they answer.
//!   `unknown` means the server cannot tell whether Feishu delivered it: it is
//!   shown as such and never resent automatically.
use std::collections::HashSet;

use ring::rand::{SecureRandom, SystemRandom};
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

use crate::context_transport::read_bounded;
use crate::context_wire::ContextEndpoint;

/// Same limit as the server (`feishu_conversation_store.MAX_BYTES`), in bytes.
pub const MAX_TEXT_BYTES: usize = 20_000;
pub const MAX_PAGE_SIZE: u8 = 50;
const MAX_PAGE_BYTES: usize = 2_097_152;
const MAX_RECEIPT_BYTES: usize = 4_096;
const MAX_ENTRIES_PER_OWNER: usize = 100;
/// POST attempts for one entry; every attempt carries the same eventId.
pub const MAX_POSTS: u32 = 5;

fn token_chars(value: &str) -> bool {
    value
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

fn valid_conversation(id: &str) -> bool {
    id.strip_prefix("fc_")
        .is_some_and(|rest| (16..=64).contains(&rest.len()) && token_chars(rest))
}

fn valid_event(id: &str) -> bool {
    (1..=128).contains(&id.len()) && token_chars(id)
}

fn valid_message(id: &str) -> bool {
    id.strip_prefix("om_")
        .is_some_and(|rest| (1..=253).contains(&rest.len()) && token_chars(rest))
}

fn valid_cursor(cursor: &str) -> bool {
    (32..=64).contains(&cursor.len()) && token_chars(cursor)
}

fn valid_reason(reason: &str) -> bool {
    (1..=64).contains(&reason.len())
        && reason
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
}

fn valid_text(text: &str) -> bool {
    !text.trim().is_empty() && text.len() <= MAX_TEXT_BYTES && !text.contains('\0')
}

/// Display names are server data: render controls inert instead of trusting them.
fn inert(name: &str) -> String {
    name.chars()
        .take(120)
        .map(|c| if c.is_control() { '\u{fffd}' } else { c })
        .collect()
}

// ---------------------------------------------------------------- history

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HistoryMode {
    Personal,
    OwnSubmissions,
    SharedPolicy,
}

impl HistoryMode {
    fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "personal" => Self::Personal,
            "own_submissions" => Self::OwnSubmissions,
            "shared_policy" => Self::SharedPolicy,
            _ => return None,
        })
    }

    /// What the timeline may claim about completeness. Only `shared_policy`
    /// shows other members' messages, and only since the policy took effect.
    pub fn coverage(self) -> &'static str {
        match self {
            Self::Personal => "personal_chat",
            Self::OwnSubmissions => "own_submissions_only",
            Self::SharedPolicy => "shared_since_policy",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SendStatus {
    Pending,
    Sending,
    Sent,
    Failed,
    Unknown,
    MembershipLost,
}

impl SendStatus {
    fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "pending" => Self::Pending,
            "sending" => Self::Sending,
            "sent" => Self::Sent,
            "failed" => Self::Failed,
            "unknown" => Self::Unknown,
            "membership_lost" => Self::MembershipLost,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ItemSource {
    Submission,
    Feishu,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AuthorKind {
    Vox,
    FeishuUser,
    FeishuApp,
    FeishuAnonymous,
    FeishuUnknown,
}

impl AuthorKind {
    fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "vox" => Self::Vox,
            "feishu_user" => Self::FeishuUser,
            "feishu_app" => Self::FeishuApp,
            "feishu_anonymous" => Self::FeishuAnonymous,
            "feishu_unknown" => Self::FeishuUnknown,
            _ => return None,
        })
    }
}

/// `Debug` omits the text, which is user content.
#[derive(Clone, PartialEq, Eq)]
pub struct HistoryItem {
    pub message_id: Option<String>,
    pub event_id: Option<String>,
    pub source: ItemSource,
    /// `None` for deleted messages and placeholders (non-text, threads, ...).
    pub text: Option<String>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    pub deleted: bool,
    pub updated: bool,
    pub placeholder: bool,
    pub author_kind: AuthorKind,
    pub author_id: String,
    pub author_name: Option<String>,
    pub status: Option<SendStatus>,
    pub model_translation_allowed: bool,
}

impl std::fmt::Debug for HistoryItem {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HistoryItem")
            .field("message_id", &self.message_id)
            .field("event_id", &self.event_id)
            .field("source", &self.source)
            .field("has_text", &self.text.is_some())
            .field("status", &self.status)
            .finish_non_exhaustive()
    }
}

impl HistoryItem {
    /// Identity within one traversal: the Feishu message, or the submission
    /// that has not been delivered yet.
    pub fn key(&self) -> String {
        match (&self.message_id, &self.event_id) {
            (Some(message), _) => format!("m:{message}"),
            (None, Some(event)) => format!("e:{event}"),
            (None, None) => unreachable!("decoder requires one identifier"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HistoryPage {
    pub conversation_id: String,
    pub mode: HistoryMode,
    pub policy_revision: String,
    pub items: Vec<HistoryItem>,
    /// Present exactly when the server has more pages.
    pub next_cursor: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HistoryError {
    SignInRequired,
    NotPermitted,
    /// The cursor or authorization changed: read again from the first page.
    Restart,
    ReadLimit,
    RateLimited,
    Unavailable,
    ConversationChanged,
    InvalidResponse,
    Network,
}

/// Additive fields are allowed and ignored; required ones must be present.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawPage {
    conversation_id: String,
    mode: String,
    policy_revision: String,
    items: Vec<RawItem>,
    next_cursor: Option<String>,
    has_more: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawItem {
    message_id: Option<String>,
    event_id: Option<String>,
    source: String,
    text: Option<String>,
    created_at: String,
    updated_at: String,
    deleted: bool,
    updated: bool,
    placeholder: bool,
    author_kind: String,
    author_id: String,
    author_name: Option<String>,
    status: Option<String>,
    model_translation_allowed: bool,
}

fn millis(value: &str) -> Option<u64> {
    ((1..=16).contains(&value.len()) && value.bytes().all(|b| b.is_ascii_digit()))
        .then(|| value.parse().ok())
        .flatten()
}

fn item(raw: RawItem) -> Option<HistoryItem> {
    let source = match raw.source.as_str() {
        "submission" => ItemSource::Submission,
        "feishu" => ItemSource::Feishu,
        _ => return None,
    };
    let author_kind = AuthorKind::parse(&raw.author_kind)?;
    let status = match raw.status.as_deref() {
        Some(value) => Some(SendStatus::parse(value)?),
        None => None,
    };
    if raw.message_id.as_deref().is_some_and(|m| !valid_message(m))
        || raw.event_id.as_deref().is_some_and(|e| !valid_event(e))
    {
        return None;
    }
    match source {
        // A submission is this user's own text, with its delivery status.
        ItemSource::Submission => {
            if raw.event_id.is_none() || status.is_none() || author_kind != AuthorKind::Vox {
                return None;
            }
        }
        ItemSource::Feishu => {
            if raw.message_id.is_none() || status.is_some() {
                return None;
            }
        }
    }
    let created = millis(&raw.created_at)?;
    let updated_at = millis(&raw.updated_at)?;
    if updated_at < created {
        return None;
    }
    // Deleted messages and placeholders never carry a body.
    let hidden = raw.deleted || raw.placeholder;
    match &raw.text {
        Some(text) if hidden || text.len() > MAX_TEXT_BYTES || text.contains('\0') => return None,
        None if !hidden => return None,
        _ => {}
    }
    if raw.model_translation_allowed && raw.text.is_none() {
        return None;
    }
    if raw.author_id.len() > 256 || raw.author_id.chars().any(char::is_control) {
        return None;
    }
    Some(HistoryItem {
        message_id: raw.message_id,
        event_id: raw.event_id,
        source,
        text: raw.text,
        created_at_ms: created,
        updated_at_ms: updated_at,
        deleted: raw.deleted,
        updated: raw.updated,
        placeholder: raw.placeholder,
        author_kind,
        author_id: raw.author_id,
        author_name: raw.author_name.as_deref().map(inert),
        status,
        model_translation_allowed: raw.model_translation_allowed,
    })
}

/// Decode one history page of `conversation`. Any invalid item refuses the
/// whole page: a partial page could hide a message without saying so.
pub fn decode_history_page(bytes: &[u8], conversation: &str) -> Result<HistoryPage, HistoryError> {
    if bytes.len() > MAX_PAGE_BYTES {
        return Err(HistoryError::InvalidResponse);
    }
    let raw: RawPage = serde_json::from_slice(bytes).map_err(|_| HistoryError::InvalidResponse)?;
    if raw.conversation_id != conversation {
        return Err(HistoryError::ConversationChanged);
    }
    let mode = HistoryMode::parse(&raw.mode).ok_or(HistoryError::InvalidResponse)?;
    let revision_ok =
        (16..=128).contains(&raw.policy_revision.len()) && token_chars(&raw.policy_revision);
    let cursor_ok = match (&raw.next_cursor, raw.has_more) {
        (Some(cursor), true) => valid_cursor(cursor),
        (None, false) => true,
        _ => false,
    };
    if !revision_ok || !cursor_ok || raw.items.len() > usize::from(MAX_PAGE_SIZE) {
        return Err(HistoryError::InvalidResponse);
    }
    let mut keys = HashSet::new();
    let mut items = Vec::with_capacity(raw.items.len());
    for raw_item in raw.items {
        let decoded = item(raw_item).ok_or(HistoryError::InvalidResponse)?;
        // Without a shared-read policy the server may show only this user's
        // submissions; anything else would be guessed group history.
        if mode == HistoryMode::OwnSubmissions && decoded.source != ItemSource::Submission {
            return Err(HistoryError::InvalidResponse);
        }
        if !keys.insert(decoded.key()) {
            return Err(HistoryError::InvalidResponse);
        }
        items.push(decoded);
    }
    Ok(HistoryPage {
        conversation_id: raw.conversation_id,
        mode,
        policy_revision: raw.policy_revision,
        items,
        next_cursor: raw.next_cursor,
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageRequest {
    pub generation: u64,
    pub cursor: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PagerEvent {
    Appended(usize),
    /// The traversal was discarded; load the first page again.
    Restarted,
    /// A response for an earlier traversal or request; nothing changed.
    Stale,
    Failed(HistoryError),
}

/// One history traversal of one conversation, newest first.
#[derive(Debug)]
pub struct HistoryPager {
    conversation: String,
    generation: u64,
    revision: Option<String>,
    mode: Option<HistoryMode>,
    cursor: Option<String>,
    has_more: bool,
    items: Vec<HistoryItem>,
    keys: HashSet<String>,
    in_flight: bool,
}

impl HistoryPager {
    pub fn new(conversation: &str) -> Self {
        Self {
            conversation: conversation.to_string(),
            generation: 0,
            revision: None,
            mode: None,
            cursor: None,
            has_more: true,
            items: Vec::new(),
            keys: HashSet::new(),
            in_flight: false,
        }
    }

    pub fn conversation(&self) -> &str {
        &self.conversation
    }

    /// Discard everything loaded and start from the first page (refresh).
    pub fn restart(&mut self) {
        let generation = self.generation + 1;
        *self = Self::new(&self.conversation);
        self.generation = generation;
    }

    /// The next page to request, or `None` while one is in flight or when the
    /// traversal is complete.
    pub fn next_request(&mut self) -> Option<PageRequest> {
        if self.in_flight || !self.has_more {
            return None;
        }
        self.in_flight = true;
        Some(PageRequest {
            generation: self.generation,
            cursor: self.cursor.clone(),
        })
    }

    pub fn apply(
        &mut self,
        request: &PageRequest,
        result: Result<HistoryPage, HistoryError>,
    ) -> PagerEvent {
        if request.generation != self.generation || request.cursor != self.cursor || !self.in_flight
        {
            return PagerEvent::Stale;
        }
        self.in_flight = false;
        let page = match result {
            Ok(page) => page,
            Err(HistoryError::Restart) => {
                self.restart();
                return PagerEvent::Restarted;
            }
            Err(error) => return PagerEvent::Failed(error),
        };
        if page.conversation_id != self.conversation {
            return PagerEvent::Failed(HistoryError::ConversationChanged);
        }
        let same_traversal = self
            .revision
            .as_ref()
            .is_none_or(|r| *r == page.policy_revision)
            && self.mode.is_none_or(|m| m == page.mode);
        if !same_traversal || page.items.iter().any(|i| self.keys.contains(&i.key())) {
            self.restart();
            return PagerEvent::Restarted;
        }
        let added = page.items.len();
        self.revision = Some(page.policy_revision);
        self.mode = Some(page.mode);
        self.has_more = page.next_cursor.is_some();
        self.cursor = page.next_cursor;
        for item in page.items {
            self.keys.insert(item.key());
            self.items.push(item);
        }
        PagerEvent::Appended(added)
    }

    pub fn items(&self) -> &[HistoryItem] {
        &self.items
    }

    pub fn mode(&self) -> Option<HistoryMode> {
        self.mode
    }

    /// Every page of this traversal is loaded.
    pub fn complete(&self) -> bool {
        self.mode.is_some() && !self.has_more
    }
}

// ---------------------------------------------------------------- receipts

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Receipt {
    pub event_id: String,
    pub conversation_id: String,
    pub status: SendStatus,
    pub reason: Option<String>,
    pub is_final: bool,
    /// Present exactly when `status` is `sent`.
    pub message_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawReceipt {
    event_id: String,
    conversation_id: String,
    status: String,
    reason: Option<String>,
    #[serde(rename = "final")]
    is_final: bool,
    message_id: Option<String>,
}

/// Decode a receipt and check that it answers `event` in `conversation`.
pub fn decode_receipt(bytes: &[u8], event: &str, conversation: &str) -> Option<Receipt> {
    if bytes.len() > MAX_RECEIPT_BYTES {
        return None;
    }
    let raw: RawReceipt = serde_json::from_slice(bytes).ok()?;
    let status = SendStatus::parse(&raw.status)?;
    let final_ok = match status {
        SendStatus::Sent | SendStatus::Failed | SendStatus::MembershipLost => raw.is_final,
        SendStatus::Pending | SendStatus::Sending => !raw.is_final,
        SendStatus::Unknown => true,
    };
    let message_ok = match (&raw.message_id, status) {
        (Some(id), SendStatus::Sent) => valid_message(id),
        (None, SendStatus::Sent) => false,
        (Some(_), _) => false,
        (None, _) => true,
    };
    (raw.event_id == event
        && raw.conversation_id == conversation
        && final_ok
        && message_ok
        && raw.reason.as_deref().is_none_or(valid_reason))
    .then_some(Receipt {
        event_id: raw.event_id,
        conversation_id: raw.conversation_id,
        status,
        reason: raw.reason,
        is_final: raw.is_final,
        message_id: raw.message_id,
    })
}

// ---------------------------------------------------------------- outbox

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EntryState {
    /// Never posted: POST allowed.
    Queued,
    /// A POST may be in flight: wait for its outcome.
    Posting,
    /// The POST outcome is not known (lost reply, overload, receipt missing).
    /// Re-POSTing the same eventId and text is safe: the server deduplicates.
    NotConfirmed(String),
    /// Accepted and not final: poll the receipt.
    Accepted(Receipt),
    /// Final receipt. `unknown` is final too: check Feishu before writing again.
    Final(Receipt),
    /// The server holds a different payload for this eventId. Never re-keyed.
    Conflict,
    /// Refused for good (too large, invalid text).
    Rejected(String),
    /// Sign-in or permission lost. The user may retry after fixing it; the
    /// retry keeps the eventId.
    Blocked(String),
}

/// `Debug` omits the text.
#[derive(Clone, PartialEq, Eq)]
pub struct OutboxEntry {
    pub event_id: String,
    pub owner: String,
    pub conversation_id: String,
    pub text: Zeroizing<String>,
    pub state: EntryState,
    pub posts: u32,
}

impl std::fmt::Debug for OutboxEntry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OutboxEntry")
            .field("event_id", &self.event_id)
            .field("conversation_id", &self.conversation_id)
            .field("state", &self.state)
            .field("posts", &self.posts)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OutboxError {
    InvalidConversation,
    InvalidText,
    Full,
    StoreUnavailable,
    NotFound,
    /// The entry's state does not allow this step (e.g. a POST is in flight).
    NotAllowed,
    RetryLimit,
}

/// Where outbox entries are saved. `save` must be durable before it returns:
/// it runs before every POST.
pub trait OutboxStore {
    fn save(&mut self, entry: &OutboxEntry) -> Result<(), OutboxError>;
    fn remove(&mut self, event_id: &str) -> Result<(), OutboxError>;
}

/// In-process store: entries last while the app runs (see the 4.2.9 GAP note).
#[derive(Debug, Default)]
pub struct MemoryOutboxStore {
    pub saved: Vec<String>,
    pub fail: bool,
}

impl OutboxStore for MemoryOutboxStore {
    fn save(&mut self, entry: &OutboxEntry) -> Result<(), OutboxError> {
        if self.fail {
            return Err(OutboxError::StoreUnavailable);
        }
        self.saved.push(entry.event_id.clone());
        Ok(())
    }
    fn remove(&mut self, _event_id: &str) -> Result<(), OutboxError> {
        if self.fail {
            return Err(OutboxError::StoreUnavailable);
        }
        Ok(())
    }
}

/// The request for one POST. `body` holds the user's text.
pub struct PostRequest {
    pub event_id: String,
    pub conversation_id: String,
    pub body: Zeroizing<Vec<u8>>,
}

/// What came back from a POST or receipt GET. `Debug` shows the status and
/// body length only: a refusal may echo user text.
#[derive(Clone, PartialEq, Eq)]
pub enum HttpOutcome {
    Response {
        status: u16,
        body: Vec<u8>,
    },
    /// No response was read (connection, timeout, oversize body).
    Network,
}

impl std::fmt::Debug for HttpOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Response { status, body } => f
                .debug_struct("Response")
                .field("status", status)
                .field("body_bytes", &body.len())
                .finish(),
            Self::Network => f.write_str("Network"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Applied {
    Updated,
    /// The entry is gone or belongs to someone else: the late reply is dropped.
    Ignored,
}

pub fn new_event_id() -> Result<String, OutboxError> {
    use base64::Engine;
    let mut bytes = [0u8; 18];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| OutboxError::StoreUnavailable)?;
    Ok(format!(
        "ob_{}",
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    ))
}

fn refusal(body: &[u8]) -> String {
    crate::context_login::refusal_code(body).unwrap_or_else(|| "UNSPECIFIED".to_string())
}

pub struct Outbox<S: OutboxStore> {
    store: S,
    entries: Vec<OutboxEntry>,
}

impl<S: OutboxStore> Outbox<S> {
    pub fn new(store: S) -> Self {
        Self {
            store,
            entries: Vec::new(),
        }
    }

    pub fn store(&self) -> &S {
        &self.store
    }

    pub fn store_mut(&mut self) -> &mut S {
        &mut self.store
    }

    fn position(&self, owner: &str, event: &str) -> Option<usize> {
        self.entries
            .iter()
            .position(|e| e.owner == owner && e.event_id == event)
    }

    /// Save `next` for entry `index`; memory changes only after the store did.
    fn commit(&mut self, index: usize, next: OutboxEntry) -> Result<(), OutboxError> {
        self.store.save(&next)?;
        self.entries[index] = next;
        Ok(())
    }

    /// Record what a reply proved. Memory follows the reply even when the
    /// save fails: the stored state is then the older `Posting`/`Accepted`,
    /// which only ever leads to re-POSTing the same eventId (deduplicated by
    /// the server), so the entry is never stuck. The save error is reported.
    fn record(&mut self, index: usize, next: OutboxEntry) -> Result<(), OutboxError> {
        let saved = self.store.save(&next);
        self.entries[index] = next;
        saved
    }

    /// Queue `text` for `conversation`. The eventId is fixed here, and the
    /// entry is saved before this returns.
    pub fn enqueue(
        &mut self,
        owner: &str,
        conversation: &str,
        text: &str,
    ) -> Result<String, OutboxError> {
        if !valid_conversation(conversation) {
            return Err(OutboxError::InvalidConversation);
        }
        if !valid_text(text) {
            return Err(OutboxError::InvalidText);
        }
        if self.entries.iter().filter(|e| e.owner == owner).count() >= MAX_ENTRIES_PER_OWNER {
            return Err(OutboxError::Full);
        }
        let entry = OutboxEntry {
            event_id: new_event_id()?,
            owner: owner.to_string(),
            conversation_id: conversation.to_string(),
            text: Zeroizing::new(text.to_string()),
            state: EntryState::Queued,
            posts: 0,
        };
        self.store.save(&entry)?;
        let event = entry.event_id.clone();
        self.entries.push(entry);
        Ok(event)
    }

    /// Start a POST. Allowed when never posted, when the last outcome is not
    /// known, or as a user retry after a block. Always the same eventId/text.
    pub fn begin_post(&mut self, owner: &str, event: &str) -> Result<PostRequest, OutboxError> {
        let index = self.position(owner, event).ok_or(OutboxError::NotFound)?;
        let entry = &self.entries[index];
        if !matches!(
            entry.state,
            EntryState::Queued | EntryState::NotConfirmed(_) | EntryState::Blocked(_)
        ) {
            return Err(OutboxError::NotAllowed);
        }
        if entry.posts >= MAX_POSTS {
            return Err(OutboxError::RetryLimit);
        }
        let body = Zeroizing::new(
            serde_json::to_vec(&serde_json::json!({
                "eventId": entry.event_id,
                "text": entry.text.as_str(),
            }))
            .map_err(|_| OutboxError::InvalidText)?,
        );
        let request = PostRequest {
            event_id: entry.event_id.clone(),
            conversation_id: entry.conversation_id.clone(),
            body,
        };
        let mut next = entry.clone();
        next.state = EntryState::Posting;
        next.posts += 1;
        self.commit(index, next)?;
        Ok(request)
    }

    pub fn finish_post(
        &mut self,
        owner: &str,
        event: &str,
        outcome: HttpOutcome,
    ) -> Result<Applied, OutboxError> {
        let Some(index) = self.position(owner, event) else {
            return Ok(Applied::Ignored);
        };
        let entry = &self.entries[index];
        if entry.state != EntryState::Posting {
            return Ok(Applied::Ignored);
        }
        let state = match outcome {
            HttpOutcome::Network => EntryState::NotConfirmed("NO_RESPONSE".into()),
            HttpOutcome::Response { status: 202, body } => {
                match decode_receipt(&body, &entry.event_id, &entry.conversation_id) {
                    Some(receipt) if receipt.is_final => EntryState::Final(receipt),
                    Some(receipt) => EntryState::Accepted(receipt),
                    None => EntryState::NotConfirmed("INVALID_RECEIPT".into()),
                }
            }
            HttpOutcome::Response { status: 409, body }
                if refusal(&body) == "EVENT_PAYLOAD_CONFLICT" =>
            {
                EntryState::Conflict
            }
            HttpOutcome::Response {
                status: 413 | 422,
                body,
            } => EntryState::Rejected(refusal(&body)),
            HttpOutcome::Response {
                status: 401 | 403,
                body,
            } => EntryState::Blocked(refusal(&body)),
            HttpOutcome::Response { body, .. } => EntryState::NotConfirmed(refusal(&body)),
        };
        let mut next = entry.clone();
        next.state = state;
        self.record(index, next)?;
        Ok(Applied::Updated)
    }

    /// The conversation whose receipt to fetch, while accepted and not final.
    pub fn poll_target(&self, owner: &str, event: &str) -> Option<String> {
        let entry = &self.entries[self.position(owner, event)?];
        matches!(entry.state, EntryState::Accepted(_)).then(|| entry.conversation_id.clone())
    }

    pub fn finish_poll(
        &mut self,
        owner: &str,
        event: &str,
        outcome: HttpOutcome,
    ) -> Result<Applied, OutboxError> {
        let Some(index) = self.position(owner, event) else {
            return Ok(Applied::Ignored);
        };
        let entry = &self.entries[index];
        if !matches!(entry.state, EntryState::Accepted(_)) {
            return Ok(Applied::Ignored);
        }
        let state = match outcome {
            HttpOutcome::Response { status: 200, body } => {
                match decode_receipt(&body, &entry.event_id, &entry.conversation_id) {
                    Some(receipt) if receipt.is_final => EntryState::Final(receipt),
                    Some(receipt) => EntryState::Accepted(receipt),
                    // A receipt for something else proves nothing: keep polling.
                    None => return Ok(Applied::Ignored),
                }
            }
            // The server no longer knows it: re-POSTing the same request is safe.
            HttpOutcome::Response { status: 404, .. } => {
                EntryState::NotConfirmed("RECEIPT_NOT_FOUND".into())
            }
            HttpOutcome::Response {
                status: 401 | 403,
                body,
            } => EntryState::Blocked(refusal(&body)),
            _ => return Ok(Applied::Ignored),
        };
        let mut next = entry.clone();
        next.state = state;
        self.record(index, next)?;
        Ok(Applied::Updated)
    }

    /// Remove an entry the user dismissed. Not while a POST may be in flight.
    pub fn dismiss(&mut self, owner: &str, event: &str) -> Result<(), OutboxError> {
        let index = self.position(owner, event).ok_or(OutboxError::NotFound)?;
        if self.entries[index].state == EntryState::Posting {
            return Err(OutboxError::NotAllowed);
        }
        self.store.remove(event)?;
        self.entries.remove(index);
        Ok(())
    }

    /// Keep only `owner`'s entries (sign-out or account switch). Returns how
    /// many were dropped; their late replies are ignored.
    pub fn retain_owner(&mut self, owner: Option<&str>) -> usize {
        let before = self.entries.len();
        let dropped: Vec<String> = self
            .entries
            .iter()
            .filter(|e| Some(e.owner.as_str()) != owner)
            .map(|e| e.event_id.clone())
            .collect();
        for event in &dropped {
            let _ = self.store.remove(event);
        }
        self.entries.retain(|e| Some(e.owner.as_str()) == owner);
        before - self.entries.len()
    }

    pub fn entries(&self, owner: &str, conversation: &str) -> Vec<&OutboxEntry> {
        self.entries
            .iter()
            .filter(|e| e.owner == owner && e.conversation_id == conversation)
            .collect()
    }

    pub fn entry(&self, owner: &str, event: &str) -> Option<&OutboxEntry> {
        self.entries.get(self.position(owner, event)?)
    }
}

// ---------------------------------------------------------------- HTTP

fn conversation_url(
    endpoint: &ContextEndpoint,
    conversation: &str,
    tail: &str,
) -> Option<reqwest::Url> {
    if !valid_conversation(conversation) {
        return None;
    }
    reqwest::Url::parse(endpoint.canonical())
        .ok()?
        .join(&format!("v1/conversations/{conversation}/{tail}"))
        .ok()
}

fn history_refusal(status: u16, code: Option<&str>) -> HistoryError {
    match (status, code) {
        (401, _) => HistoryError::SignInRequired,
        (403, _) => HistoryError::NotPermitted,
        (409, Some("INVALID_CURSOR" | "AUTHORIZATION_RESET")) => HistoryError::Restart,
        (429, Some("READ_LIMIT")) => HistoryError::ReadLimit,
        (429, _) => HistoryError::RateLimited,
        _ => HistoryError::Unavailable,
    }
}

/// Fetch one history page. Redirects are never followed.
pub async fn fetch_history(
    endpoint: &ContextEndpoint,
    token: &str,
    conversation: &str,
    cursor: Option<&str>,
    page_size: u8,
) -> Result<HistoryPage, HistoryError> {
    if !(1..=MAX_PAGE_SIZE).contains(&page_size) || cursor.is_some_and(|c| !valid_cursor(c)) {
        return Err(HistoryError::InvalidResponse);
    }
    let mut url =
        conversation_url(endpoint, conversation, "messages").ok_or(HistoryError::Network)?;
    {
        let mut query = url.query_pairs_mut();
        query.append_pair("pageSize", &page_size.to_string());
        if let Some(cursor) = cursor {
            query.append_pair("cursor", cursor);
        }
    }
    let client = endpoint
        .transport_client()
        .map_err(|_| HistoryError::Network)?;
    let response = client
        .get(url)
        .bearer_auth(token)
        .send()
        .await
        .map_err(|_| HistoryError::Network)?;
    let status = response.status().as_u16();
    if status != 200 {
        let code = read_bounded(response, MAX_RECEIPT_BYTES)
            .await
            .ok()
            .and_then(|body| crate::context_login::refusal_code(&body));
        return Err(history_refusal(status, code.as_deref()));
    }
    let body = read_bounded(response, MAX_PAGE_BYTES)
        .await
        .map_err(|_| HistoryError::InvalidResponse)?;
    decode_history_page(&body, conversation)
}

async fn outcome(response: Result<reqwest::Response, reqwest::Error>) -> HttpOutcome {
    let Ok(response) = response else {
        return HttpOutcome::Network;
    };
    let status = response.status().as_u16();
    match read_bounded(response, MAX_RECEIPT_BYTES).await {
        Ok(body) => HttpOutcome::Response { status, body },
        Err(_) => HttpOutcome::Network,
    }
}

/// POST one outbox request.
pub async fn post_text(
    endpoint: &ContextEndpoint,
    token: &str,
    request: &PostRequest,
) -> HttpOutcome {
    let (Ok(client), Some(url)) = (
        endpoint.transport_client(),
        conversation_url(endpoint, &request.conversation_id, "messages"),
    ) else {
        return HttpOutcome::Network;
    };
    outcome(
        client
            .post(url)
            .bearer_auth(token)
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(request.body.to_vec())
            .send()
            .await,
    )
    .await
}

/// GET the receipt of `event` in `conversation`.
pub async fn get_receipt(
    endpoint: &ContextEndpoint,
    token: &str,
    conversation: &str,
    event: &str,
) -> HttpOutcome {
    if !valid_event(event) {
        return HttpOutcome::Network;
    }
    let (Ok(client), Some(url)) = (
        endpoint.transport_client(),
        conversation_url(endpoint, conversation, &format!("receipts/{event}")),
    ) else {
        return HttpOutcome::Network;
    };
    outcome(client.get(url).bearer_auth(token).send().await).await
}
