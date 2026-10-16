//! Private Context IPC (T18b, T19). Main window only (capabilities/main.json);
//! the voice HUD is never granted these commands.
//!
//! The owner comes from the verified Context session in Rust; no command takes
//! an owner, endpoint, record state or revision from the WebView. Inputs are
//! strict (`deny_unknown_fields`, enums, lengths) before anything is saved or
//! sent. Until a key provider exists on this platform (T07b) no private
//! journal is configured and every command answers `journal_unavailable`.
use std::sync::{Arc, OnceLock};

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::context_commands::ContextState;
use crate::context_private::{Phase, PrivateRecord, SealedPrivateStore, Tag};
use crate::context_private_service::{
    ListPage, ListQuery, PrivateService, PrivateServiceError, StepResult, UserConfirmed,
};

#[derive(Default)]
pub struct PrivateState {
    service: OnceLock<Arc<PrivateService<SealedPrivateStore>>>,
}

impl PrivateState {
    /// Rust startup only, once a journal key is available. A second call is ignored.
    pub fn configure(&self, service: PrivateService<SealedPrivateStore>) {
        let _ = self.service.set(Arc::new(service));
    }
    /// Drop visible private plaintext (T23b); saved mutations stay.
    pub fn clear_view(&self) {
        if let Some(service) = self.service.get() {
            service.clear_view();
        }
    }
    /// Records this session has shown, for a weekly draft's private picks.
    pub(crate) fn shown_records<A: crate::context_private_service::PrivateSession>(
        &self,
        session: &A,
        ids: &[String],
    ) -> Result<Vec<PrivateRecord>, PrivateServiceError> {
        self.service()?.shown_records(session, ids)
    }

    /// Copy a weekly draft with private picks (fresh team read + reference check).
    pub(crate) async fn copy_draft<A: crate::context_private_service::PrivateSession>(
        &self,
        session: &A,
        draft: &crate::context_weekly_draft::WeeklyDraft,
    ) -> Result<
        Result<zeroize::Zeroizing<String>, crate::context_weekly_draft::CopyBlocked>,
        PrivateServiceError,
    > {
        self.service()?.copy_draft(session, draft).await
    }

    fn service(&self) -> Result<Arc<PrivateService<SealedPrivateStore>>, PrivateServiceError> {
        self.service
            .get()
            .cloned()
            .ok_or(PrivateServiceError::JournalUnavailable)
    }
}

/// No `Debug`: it holds private text.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateCreateInput {
    pub text: String,
    pub tags: Vec<String>,
    pub project_id: Option<String>,
    pub repository_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateStepInput {
    pub key: String,
    /// An explicit user retry of a blocked mutation.
    pub retry: bool,
}

/// No `Debug`: it holds private text.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateListInput {
    pub start: String,
    pub end: String,
    pub limit: u8,
    pub cursor: Option<String>,
    pub tag: Option<String>,
    pub project_id: Option<String>,
    pub text: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PhaseDto {
    pub key: String,
    pub phase: &'static str,
    /// The server's code or the reason it is unknown; never server text.
    pub detail: Option<String>,
    pub record_id: Option<String>,
    pub revision: Option<u32>,
}

/// `Debug` omits the text.
#[derive(Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordDto {
    pub id: String,
    pub event_id: String,
    pub revision: u32,
    pub withdrawn: bool,
    pub created_at: String,
    pub revision_changed_at: String,
    pub text: Option<String>,
    pub tags: Vec<&'static str>,
    pub project_id: Option<String>,
    pub repository_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase", tag = "kind")]
pub enum StepDto {
    Phase(PhaseDto),
    Current { key: String, record: RecordDto },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PageDto {
    pub items: Vec<RecordDto>,
    pub next_cursor: Option<String>,
    pub omitted_records: bool,
}

impl std::fmt::Debug for RecordDto {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RecordDto")
            .field("id", &self.id)
            .field("revision", &self.revision)
            .field("withdrawn", &self.withdrawn)
            .field("has_text", &self.text.is_some())
            .finish_non_exhaustive()
    }
}

pub fn parse_tag(value: &str) -> Result<Tag, PrivateServiceError> {
    Ok(match value {
        "decision" => Tag::Decision,
        "progress" => Tag::Progress,
        "blocker" => Tag::Blocker,
        "question" => Tag::Question,
        "verification" => Tag::Verification,
        _ => return Err(PrivateServiceError::InvalidInput),
    })
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

/// `create:<eventId>` or `request:<requestId>`, as the journal names keys.
pub fn valid_key(key: &str) -> bool {
    let id = key
        .strip_prefix("create:")
        .or_else(|| key.strip_prefix("request:"));
    id.is_some_and(|id| {
        (1..=128).contains(&id.len())
            && id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    })
}

/// A create request after validation of the WebView input.
/// No `Debug`: it holds private text.
#[derive(Clone, PartialEq, Eq)]
pub struct ParsedCreate {
    pub text: String,
    pub tags: Vec<Tag>,
    pub project_id: Option<String>,
    pub repository_id: Option<String>,
}

pub fn parse_create(input: PrivateCreateInput) -> Result<ParsedCreate, PrivateServiceError> {
    if input.tags.len() > crate::context_private::MAX_TAGS {
        return Err(PrivateServiceError::InvalidInput);
    }
    let tags = input
        .tags
        .iter()
        .map(|t| parse_tag(t))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(ParsedCreate {
        text: input.text,
        tags,
        project_id: input.project_id,
        repository_id: input.repository_id,
    })
}

pub fn parse_list(input: PrivateListInput) -> Result<ListQuery, PrivateServiceError> {
    Ok(ListQuery {
        start: input.start,
        end: input.end,
        limit: input.limit,
        cursor: input.cursor,
        tag: input.tag.as_deref().map(parse_tag).transpose()?,
        project_id: input.project_id,
        text: input.text,
    })
}

pub fn phase_dto(key: &str, phase: &Phase) -> PhaseDto {
    let (name, detail, record_id, revision) = match phase {
        Phase::Reserved => ("reserved", None, None, None),
        Phase::Dispatching => ("dispatching", None, None, None),
        Phase::Unknown(reason) => ("unknown", Some(reason.clone()), None, None),
        Phase::Applied {
            record_id,
            revision,
        } => ("applied", None, Some(record_id.clone()), Some(*revision)),
        Phase::Conflict(code) => ("conflict", Some(code.clone()), None, None),
        Phase::Rejected(code) => ("rejected", Some(code.clone()), None, None),
        Phase::Blocked(code) => ("blocked", Some(code.clone()), None, None),
    };
    PhaseDto {
        key: key.to_string(),
        phase: name,
        detail,
        record_id,
        revision,
    }
}

pub fn record_dto(record: &PrivateRecord) -> RecordDto {
    RecordDto {
        id: record.id.clone(),
        event_id: record.event_id.clone(),
        revision: record.revision,
        withdrawn: record.withdrawn,
        created_at: record.created_at.clone(),
        revision_changed_at: record.revision_changed_at.clone(),
        text: record.text.as_ref().map(|t| t.to_string()),
        tags: record.tags.iter().map(|t| tag_wire(*t)).collect(),
        project_id: record.project_id.clone(),
        repository_id: record.repository_id.clone(),
    }
}

pub fn page_dto(page: &ListPage) -> PageDto {
    PageDto {
        items: page.items.iter().map(record_dto).collect(),
        next_cursor: page.next_cursor.clone(),
        omitted_records: page.omitted_records,
    }
}

fn session(
    context: &ContextState,
) -> Result<
    Arc<crate::context_service::ContextService<crate::secrets::NativeSecretStore>>,
    PrivateServiceError,
> {
    context
        .current_service()
        .ok_or(PrivateServiceError::NotSignedIn)
}

/// Save a new private record on this device; it is sent by `context_private_step`.
#[tauri::command]
pub fn context_private_create(
    input: PrivateCreateInput,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<PhaseDto, PrivateServiceError> {
    let service = state.service()?;
    let session = session(&context)?;
    let parsed = parse_create(input)?;
    let key = service.create(
        &*session,
        &parsed.text,
        parsed.tags,
        parsed.project_id,
        parsed.repository_id,
    )?;
    Ok(phase_dto(&key, &Phase::Reserved))
}

/// Take the next action for one private mutation.
#[tauri::command]
pub async fn context_private_step(
    input: PrivateStepInput,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<StepDto, PrivateServiceError> {
    if !valid_key(&input.key) {
        return Err(PrivateServiceError::InvalidInput);
    }
    let service = state.service()?;
    let session = session(&context)?;
    Ok(
        match service.step(&*session, &input.key, input.retry).await? {
            StepResult::Phase(phase) => StepDto::Phase(phase_dto(&input.key, &phase)),
            StepResult::Current(record) => StepDto::Current {
                key: input.key,
                record: record_dto(&record),
            },
        },
    )
}

/// One page of the user's own private records; `text` searches.
#[tauri::command]
pub async fn context_private_list(
    input: PrivateListInput,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<PageDto, PrivateServiceError> {
    let service = state.service()?;
    let session = session(&context)?;
    let query = parse_list(input)?;
    Ok(page_dto(&service.list(&*session, &query).await?))
}

/// The current version of one private record.
#[tauri::command]
pub async fn context_private_get(
    record_id: String,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<RecordDto, PrivateServiceError> {
    let service = state.service()?;
    let session = session(&context)?;
    Ok(record_dto(&service.get(&*session, &record_id).await?))
}

/// No `Debug`: it holds private text.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateEditInput {
    pub record_id: String,
    /// The revision the user was looking at when editing.
    pub seen_revision: u32,
    pub text: String,
    pub tags: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateWithdrawInput {
    pub record_id: String,
    pub seen_revision: u32,
    /// Must be `true`: set only by the explicit confirm control.
    pub confirm: bool,
}

/// No `Debug`: it holds private text.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KeptDto {
    pub key: String,
    pub text: String,
    pub tags: Vec<&'static str>,
}

pub fn parse_tags(tags: &[String]) -> Result<Vec<Tag>, PrivateServiceError> {
    if tags.len() > crate::context_private::MAX_TAGS {
        return Err(PrivateServiceError::InvalidInput);
    }
    tags.iter().map(|t| parse_tag(t)).collect()
}

/// The explicit confirmation a withdrawal needs, from the WebView's confirm
/// control; anything but `confirm: true` is refused.
pub fn withdraw_confirmation(
    input: &PrivateWithdrawInput,
) -> Result<UserConfirmed, PrivateServiceError> {
    if input.confirm {
        Ok(UserConfirmed::withdrawal_of(&input.record_id))
    } else {
        Err(PrivateServiceError::NotAllowed)
    }
}

/// Queue an edit of a record at the revision the user saw.
#[tauri::command]
pub fn context_private_edit(
    input: PrivateEditInput,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<PhaseDto, PrivateServiceError> {
    let service = state.service()?;
    let session = session(&context)?;
    let tags = parse_tags(&input.tags)?;
    let key = service.edit(
        &*session,
        &input.record_id,
        input.seen_revision,
        &input.text,
        tags,
    )?;
    Ok(phase_dto(&key, &Phase::Reserved))
}

/// Queue a withdrawal the user explicitly confirmed.
#[tauri::command]
pub fn context_private_withdraw(
    input: PrivateWithdrawInput,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<PhaseDto, PrivateServiceError> {
    let confirmed = withdraw_confirmation(&input)?;
    let service = state.service()?;
    let session = session(&context)?;
    let key = service.withdraw(&*session, &input.record_id, input.seen_revision, confirmed)?;
    Ok(phase_dto(&key, &Phase::Reserved))
}

/// The text a conflicted or refused mutation kept, to apply it again.
#[tauri::command]
pub fn context_private_kept(
    key: String,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<KeptDto, PrivateServiceError> {
    if !valid_key(&key) {
        return Err(PrivateServiceError::InvalidInput);
    }
    let service = state.service()?;
    let session = session(&context)?;
    let (text, tags) = service.kept(&*session, &key)?;
    Ok(KeptDto {
        key,
        text: text.to_string(),
        tags: tags.into_iter().map(tag_wire).collect(),
    })
}

/// Remove a settled mutation the user dismissed.
#[tauri::command]
pub fn context_private_dismiss(
    key: String,
    state: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<(), PrivateServiceError> {
    if !valid_key(&key) {
        return Err(PrivateServiceError::InvalidInput);
    }
    let service = state.service()?;
    let session = session(&context)?;
    service.dismiss(&*session, &key)
}

/// Drop the visible private plaintext: the panel collapsed, the window was
/// hidden or locked, or the user signed out. Unsettled mutations stay saved.
#[tauri::command]
pub fn context_private_clear_view(state: State<'_, PrivateState>) {
    state.clear_view();
}
