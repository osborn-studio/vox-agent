//! Weekly report IPC (T21/T22). Main window only (capabilities/main.json).
//!
//! The owner is the verified Context session; the week is the only input the
//! WebView chooses. Rust holds the collected sources and the draft; the
//! WebView sees the sources it lists and the draft text it edits. Copying
//! reads the week again now (and checks picked private revisions) before
//! anything reaches the clipboard. `context_weekly_clear` (panel collapsed,
//! window hidden or locked) and sign-out drop the sources and the draft (T23b).
use std::sync::{Arc, Mutex, MutexGuard};

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::context_commands::ContextState;
use crate::context_private_commands::PrivateState;
use crate::context_private_service::{
    copy_team_draft, read_week_fresh, PrivateServiceError, PrivateSession,
};
use crate::context_sources::{week, Coverage, DeliveryStatus, SourceCollector, SourceError};
use crate::context_weekly_draft::{CopyBlocked, DraftError, Lang, WeeklyDraft};

#[derive(Default)]
struct Inner {
    owner: Option<String>,
    collector: Option<SourceCollector>,
    draft: Option<WeeklyDraft>,
    /// Advanced by every clear: a load or copy started before it is dropped.
    epoch: u64,
    /// Advanced by every change of sources or draft (load, exclude, compose,
    /// edit, clear): a copy started before it neither restores nor copies.
    generation: u64,
}

#[derive(Default)]
pub struct WeeklyState {
    inner: Mutex<Inner>,
}

impl WeeklyState {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Drop the sources and the draft (T23b).
    pub fn clear(&self) {
        let mut inner = self.lock();
        inner.owner = None;
        inner.collector = None;
        inner.draft = None;
        inner.epoch += 1;
        inner.generation += 1;
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case", tag = "error", content = "detail")]
pub enum WeeklyError {
    NotSignedIn,
    /// The week is not a supported Monday.
    InvalidWeek,
    /// Sources were not loaded (or were cleared) for this account and week.
    NotLoaded,
    /// The view was cleared or the account changed while the request ran.
    Stale,
    Sources(SourceError),
    Draft(DraftError),
    Private(PrivateServiceError),
    Clipboard,
}

impl From<PrivateServiceError> for WeeklyError {
    fn from(error: PrivateServiceError) -> Self {
        match error {
            PrivateServiceError::NotSignedIn => Self::NotSignedIn,
            PrivateServiceError::StaleIdentity | PrivateServiceError::ViewCleared => Self::Stale,
            other => Self::Private(other),
        }
    }
}

/// One listed source. `Debug` omits the text.
#[derive(Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceDto {
    pub event_id: String,
    /// Asia/Shanghai calendar date.
    pub date: String,
    pub text: String,
    pub delivery_status: DeliveryStatus,
    pub excluded: bool,
    pub repository_id: Option<String>,
}

impl std::fmt::Debug for SourceDto {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SourceDto")
            .field("event_id", &self.event_id)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WeeklyDto {
    pub coverage: Coverage,
    pub sources: Vec<SourceDto>,
}

/// `Debug` omits the text.
#[derive(Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DraftDto {
    pub text: String,
    pub private_picks: usize,
    /// The draft version this text is; a copy must name it.
    pub version: u64,
}

impl std::fmt::Debug for DraftDto {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DraftDto")
            .field("private_picks", &self.private_picks)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CopyDto {
    pub copied: bool,
    pub blocked: Option<CopyBlocked>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WeeklyComposeInput {
    pub lang: String,
    /// Private record ids the user ticked; each must be shown in this session.
    pub private_record_ids: Vec<String>,
}

fn shanghai_date(micros: i64) -> String {
    // Same calendar arithmetic as the draft (fixed UTC+8 since 1992).
    let days = (micros.div_euclid(1_000_000) + 8 * 3600).div_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}")
}

pub fn weekly_dto(collector: &SourceCollector) -> WeeklyDto {
    let excluded = collector.excluded_ids();
    WeeklyDto {
        coverage: collector.coverage(),
        sources: collector
            .sources()
            .iter()
            .map(|s| SourceDto {
                event_id: s.event_id.clone(),
                date: shanghai_date(s.submitted_micros),
                text: s.text.to_string(),
                delivery_status: s.delivery_status,
                excluded: excluded.contains(&s.event_id),
                repository_id: s.repository_id.clone(),
            })
            .collect(),
    }
}

pub fn parse_lang(value: &str) -> Result<Lang, WeeklyError> {
    match value {
        "en" => Ok(Lang::En),
        "zh" => Ok(Lang::Zh),
        _ => Err(WeeklyError::Draft(DraftError::InvalidPrivatePick)),
    }
}

type Session = Arc<crate::context_service::ContextService<crate::secrets::NativeSecretStore>>;

fn session(context: &ContextState) -> Result<Session, WeeklyError> {
    context.current_service().ok_or(WeeklyError::NotSignedIn)
}

/// Read every page of a week for the signed-in owner; replaces any loaded
/// sources and draft.
#[tauri::command]
pub async fn context_weekly_load(
    monday: String,
    state: State<'_, WeeklyState>,
    context: State<'_, ContextState>,
) -> Result<WeeklyDto, WeeklyError> {
    let w = week(&monday).map_err(|_| WeeklyError::InvalidWeek)?;
    let session = session(&context)?;
    let (lease, owner, token) = session.current()?;
    let epoch = state.lock().epoch;
    let collector = read_week_fresh(session.endpoint(), &token, &*session, &lease, &owner, &w)
        .await?
        .map_err(WeeklyError::Sources)?;
    let mut inner = state.lock();
    if inner.epoch != epoch || !session.still_current(&lease) {
        return Err(WeeklyError::Stale);
    }
    let dto = weekly_dto(&collector);
    inner.owner = Some(owner);
    inner.collector = Some(collector);
    inner.draft = None;
    inner.generation += 1;
    Ok(dto)
}

fn loaded<'a>(inner: &'a mut Inner, owner: &str) -> Result<&'a mut SourceCollector, WeeklyError> {
    if inner.owner.as_deref() != Some(owner) {
        return Err(WeeklyError::NotLoaded);
    }
    inner.collector.as_mut().ok_or(WeeklyError::NotLoaded)
}

/// Exclude or include one listed source. A draft must be composed again.
#[tauri::command]
pub fn context_weekly_exclude(
    event_id: String,
    excluded: bool,
    state: State<'_, WeeklyState>,
    context: State<'_, ContextState>,
) -> Result<WeeklyDto, WeeklyError> {
    let session = session(&context)?;
    let (_, owner, _) = session.current()?;
    let mut inner = state.lock();
    let collector = loaded(&mut inner, &owner)?;
    if !collector.set_excluded(&event_id, excluded) {
        return Err(WeeklyError::NotLoaded);
    }
    let dto = weekly_dto(collector);
    inner.draft = None;
    inner.generation += 1;
    Ok(dto)
}

/// Compose a draft from the loaded sources and the private notes the user
/// ticked (only records this session has shown).
#[tauri::command]
pub fn context_weekly_compose(
    input: WeeklyComposeInput,
    state: State<'_, WeeklyState>,
    private: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<DraftDto, WeeklyError> {
    let lang = parse_lang(&input.lang)?;
    if input.private_record_ids.len() > crate::context_weekly_draft::MAX_PRIVATE_PICKS {
        return Err(WeeklyError::Draft(DraftError::TooManyPicks));
    }
    let session = session(&context)?;
    let picked = if input.private_record_ids.is_empty() {
        Vec::new()
    } else {
        private.shown_records(&*session, &input.private_record_ids)?
    };
    let (_, owner, _) = session.current()?;
    let mut inner = state.lock();
    let collector = loaded(&mut inner, &owner)?;
    let draft = WeeklyDraft::compose(collector, &picked, lang).map_err(WeeklyError::Draft)?;
    inner.draft = Some(draft);
    inner.generation += 1;
    Ok(DraftDto {
        text: inner.draft.as_ref().expect("set above").text().to_string(),
        private_picks: picked.len(),
        version: inner.generation,
    })
}

/// Replace draft `version`'s text with the user's edit. Returns the new draft
/// version.
#[tauri::command]
pub fn context_weekly_edit(
    text: String,
    version: u64,
    state: State<'_, WeeklyState>,
    context: State<'_, ContextState>,
) -> Result<u64, WeeklyError> {
    let session = session(&context)?;
    let (_, owner, _) = session.current()?;
    let mut inner = state.lock();
    if inner.owner.as_deref() != Some(owner.as_str()) {
        return Err(WeeklyError::NotLoaded);
    }
    // The edit belongs to the draft the page showed; a draft replaced since
    // (re-compose, other edit) is never overwritten with it.
    if inner.generation != version {
        return Err(WeeklyError::Stale);
    }
    inner
        .draft
        .as_mut()
        .ok_or(WeeklyError::NotLoaded)?
        .edit(&text)
        .map_err(WeeklyError::Draft)?;
    inner.generation += 1;
    Ok(inner.generation)
}

/// Copy draft `version` to the clipboard after reading the week again now
/// (and checking picked private revisions). Nothing is copied if the draft is
/// no longer that version or anything changed.
#[tauri::command]
pub async fn context_weekly_copy(
    version: u64,
    state: State<'_, WeeklyState>,
    private: State<'_, PrivateState>,
    context: State<'_, ContextState>,
) -> Result<CopyDto, WeeklyError> {
    let session = session(&context)?;
    let (lease, owner, _) = session.current()?;
    // Take the draft out for the duration of the copy: an edit meanwhile is
    // refused, and any change of sources or draft (generation) or a clear
    // (epoch) makes this copy restore nothing and copy nothing.
    let (draft, epoch, generation) = {
        let mut inner = state.lock();
        if inner.owner.as_deref() != Some(owner.as_str()) {
            return Err(WeeklyError::NotLoaded);
        }
        // Only the exact draft the page showed and the user asked to copy.
        if inner.generation != version {
            return Err(WeeklyError::Stale);
        }
        (
            inner.draft.take().ok_or(WeeklyError::NotLoaded)?,
            inner.epoch,
            inner.generation,
        )
    };
    let result = if draft.private_refs().is_empty() {
        copy_team_draft(session.endpoint(), &*session, &draft).await
    } else {
        private.copy_draft(&*session, &draft).await
    };
    // Checked and committed under the weekly lock. Sign-out clears this state
    // before it ends the account, so a copy either completes first or fails.
    let mut inner = state.lock();
    if inner.epoch != epoch
        || inner.generation != generation
        || inner.owner.as_deref() != Some(owner.as_str())
        || !session.still_current(&lease)
    {
        return Err(WeeklyError::Stale);
    }
    inner.draft = Some(draft);
    let text = match result? {
        Err(blocked) => {
            return Ok(CopyDto {
                copied: false,
                blocked: Some(blocked),
            })
        }
        Ok(text) => text,
    };
    crate::text_input::copy_text(&text).map_err(|_| WeeklyError::Clipboard)?;
    drop(inner);
    Ok(CopyDto {
        copied: true,
        blocked: None,
    })
}

/// Drop the loaded sources and the draft (panel collapsed, window hidden or
/// locked).
#[tauri::command]
pub fn context_weekly_clear(state: State<'_, WeeklyState>) {
    state.clear();
}
