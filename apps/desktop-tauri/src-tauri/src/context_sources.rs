//! Weekly report sources and their coverage (T21, plan C-23).
//!
//! `GET /v1/context-weekly-draft?week=YYYY-MM-DD[&cursor=]` returns the
//! signed-in user's own accepted team backups for one week, as quoted user
//! statements. The week runs Monday 00:00 to the next Monday in Asia/Shanghai,
//! the same fixed boundary as macOS; it is not localized.
//!
//! * Pages are bound to the requested week and owner. A source outside the
//!   week, a repeated source, or a changed draft contract refuses the page.
//! * Coverage is stated, never inflated: "all pages read" covers only the
//!   currently authorized accepted backups, omission flags are combined across
//!   pages, a page cap marks the result truncated, and private or local inputs
//!   are never part of it. Nothing here claims a complete week of work.
//! * Exclusions are the user's explicit choice per source.
//! * Before a draft is copied, the sources are fetched again and compared by
//!   `eventId` and text digest; any difference needs review again.
use std::collections::{BTreeMap, HashSet};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use zeroize::Zeroizing;

use crate::context_transport::read_bounded;
use crate::context_wire::ContextEndpoint;

pub const TIMEZONE: &str = "Asia/Shanghai";
/// Asia/Shanghai has been a fixed UTC+8 since daylight saving ended in
/// September 1991. The server uses the IANA zone, so earlier weeks (with DST
/// or local mean time) are refused instead of computed differently.
const OFFSET_SECONDS: i64 = 8 * 3600;
/// The first Monday after Asia/Shanghai's last offset change.
const FIRST_SUPPORTED_MONDAY: (i64, i64, i64) = (1992, 1, 6);
pub const MAX_PAGES: usize = 20;
const MAX_SOURCES_PER_PAGE: usize = 50;
const MAX_TEXT_CHARS: usize = 8192;
const MAX_PAGE_BYTES: usize = 4_000_000;
const MAX_ERROR_BYTES: usize = 4_096;

fn identifier(value: &str, max: usize) -> bool {
    (1..=max).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// Days since 1970-01-01 for a proleptic Gregorian date (Hinnant's algorithm).
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn date_parts(value: &str) -> Option<(i64, i64, i64)> {
    let b = value.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let number = |range: std::ops::Range<usize>| {
        let part = &value[range];
        part.bytes()
            .all(|c| c.is_ascii_digit())
            .then(|| part.parse::<i64>().ok())
            .flatten()
    };
    let (year, month, day) = (number(0..4)?, number(5..7)?, number(8..10)?);
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    ((1..=12).contains(&month) && day >= 1 && day <= days[(month - 1) as usize])
        .then_some((year, month, day))
}

/// Microseconds since the epoch for `YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`.
pub(crate) fn utc_micros(value: &str) -> Option<i64> {
    if !crate::context_private::utc_timestamp(value) {
        return None;
    }
    let (year, month, day) = date_parts(&value[..10])?;
    let number = |range: std::ops::Range<usize>| value[range].parse::<i64>().ok();
    let (hour, minute, second) = (number(11..13)?, number(14..16)?, number(17..19)?);
    if hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    let fraction = value[19..value.len() - 6].strip_prefix('.').unwrap_or("");
    let micros = if fraction.is_empty() {
        0
    } else {
        fraction.parse::<i64>().ok()? * 10_i64.pow(6 - fraction.len() as u32)
    };
    let seconds = days_from_civil(year, month, day) * 86_400 + hour * 3600 + minute * 60 + second;
    Some(seconds * 1_000_000 + micros)
}

/// One report week: Monday 00:00 Asia/Shanghai to the next Monday.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Week {
    pub monday: String,
    pub start_micros: i64,
    pub end_micros: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceError {
    NotMonday,
    SignInRequired,
    NotPermitted,
    InvalidWeek,
    InvalidCursor,
    RateLimited,
    Unavailable,
    /// The page does not belong to the requested week or breaks the contract.
    InvalidResponse,
    Network,
}

pub fn week(monday: &str) -> Result<Week, SourceError> {
    let (year, month, day) = date_parts(monday).ok_or(SourceError::InvalidWeek)?;
    let days = days_from_civil(year, month, day);
    let (fy, fm, fd) = FIRST_SUPPORTED_MONDAY;
    if days < days_from_civil(fy, fm, fd) {
        return Err(SourceError::InvalidWeek);
    }
    // 1970-01-01 was a Thursday.
    if (days + 3).rem_euclid(7) != 0 {
        return Err(SourceError::NotMonday);
    }
    let start = (days * 86_400 - OFFSET_SECONDS) * 1_000_000;
    Ok(Week {
        monday: monday.to_string(),
        start_micros: start,
        end_micros: start + 7 * 86_400 * 1_000_000,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryStatus {
    Pending,
    Sending,
    Unknown,
    Delivered,
    Blocked,
    Rejected,
}

/// One source: a quoted statement the user submitted. `Debug` omits the text.
#[derive(Clone, PartialEq, Eq)]
pub struct Source {
    pub event_id: String,
    pub text: Zeroizing<String>,
    pub submitted_at: String,
    pub submitted_micros: i64,
    pub target_id: String,
    pub delivery_status: DeliveryStatus,
    pub project_id: Option<String>,
    pub repository_id: Option<String>,
}

impl std::fmt::Debug for Source {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Source")
            .field("event_id", &self.event_id)
            .field("submitted_at", &self.submitted_at)
            .field("delivery_status", &self.delivery_status)
            .finish_non_exhaustive()
    }
}

impl Source {
    pub(crate) fn digest(&self) -> String {
        let mut hash = Sha256::new();
        hash.update(b"vox-weekly-source-v1\0");
        hash.update(self.event_id.as_bytes());
        hash.update([0]);
        hash.update(self.text.as_bytes());
        hash.finalize().iter().map(|b| format!("{b:02x}")).collect()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DraftPage {
    pub sources: Vec<Source>,
    pub omitted_records: bool,
    pub next_cursor: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawPage {
    version: u32,
    week: String,
    timezone: String,
    kind: String,
    requires_user_review: bool,
    coverage: String,
    has_more: bool,
    omitted_records: bool,
    next_cursor: Option<String>,
    notices: Vec<String>,
    sources: Vec<RawSource>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawSource {
    event_id: String,
    text: String,
    submitted_at: String,
    target_id: String,
    share_intent: String,
    delivery_status: DeliveryStatus,
    attribution: Option<RawAttribution>,
    evidence_kind: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RawAttribution {
    project_id: String,
    repository_id: String,
    source: String,
}

fn repository(value: &str) -> bool {
    let part = |p: &str| {
        p != "."
            && p != ".."
            && p.bytes().next().is_some_and(|b| b.is_ascii_alphanumeric())
            && p.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.' || b == b'-')
    };
    value.len() <= 200
        && value
            .split_once('/')
            .is_some_and(|(o, n)| part(o) && part(n))
}

fn source(raw: RawSource, week: &Week) -> Option<Source> {
    let submitted = utc_micros(&raw.submitted_at)?;
    let attribution_ok = raw.attribution.as_ref().is_none_or(|a| {
        identifier(&a.project_id, 80) && repository(&a.repository_id) && a.source == "manual"
    });
    let text_chars = raw.text.chars().count();
    let ok = identifier(&raw.event_id, 128)
        && (1..=MAX_TEXT_CHARS).contains(&text_chars)
        && !raw.text.contains('\0')
        && (week.start_micros..week.end_micros).contains(&submitted)
        && identifier(&raw.target_id, 80)
        && raw.share_intent == "backup-destination"
        && raw.evidence_kind == "user-statement"
        && attribution_ok;
    ok.then(|| {
        let (project_id, repository_id) = raw.attribution.map_or((None, None), |a| {
            (Some(a.project_id), Some(a.repository_id))
        });
        Source {
            event_id: raw.event_id,
            text: Zeroizing::new(raw.text),
            submitted_at: raw.submitted_at,
            submitted_micros: submitted,
            target_id: raw.target_id,
            delivery_status: raw.delivery_status,
            project_id,
            repository_id,
        }
    })
}

/// Decode one draft page of `week`. Any invalid source refuses the page: a
/// partial page could hide a source without saying so.
pub fn decode_draft_page(bytes: &[u8], week: &Week) -> Result<DraftPage, SourceError> {
    if bytes.len() > MAX_PAGE_BYTES {
        return Err(SourceError::InvalidResponse);
    }
    let raw: RawPage = serde_json::from_slice(bytes).map_err(|_| SourceError::InvalidResponse)?;
    let cursor_ok = match (&raw.next_cursor, raw.has_more) {
        (Some(cursor), true) => identifier(cursor, 256),
        (None, false) => true,
        _ => false,
    };
    let contract_ok = raw.version == 1
        && raw.week == week.monday
        && raw.timezone == TIMEZONE
        && raw.kind == "source-review-draft"
        && raw.requires_user_review
        && raw.coverage == "currently-authorized-accepted-backups"
        && raw.notices.len() <= 16
        && raw.sources.len() <= MAX_SOURCES_PER_PAGE;
    if !cursor_ok || !contract_ok {
        return Err(SourceError::InvalidResponse);
    }
    let mut seen = HashSet::new();
    let mut sources = Vec::with_capacity(raw.sources.len());
    for raw_source in raw.sources {
        let decoded = source(raw_source, week).ok_or(SourceError::InvalidResponse)?;
        if !seen.insert(decoded.event_id.clone()) {
            return Err(SourceError::InvalidResponse);
        }
        sources.push(decoded);
    }
    Ok(DraftPage {
        sources,
        omitted_records: raw.omitted_records,
        next_cursor: raw.next_cursor,
    })
}

// ---------------------------------------------------------------- collector

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageRequest {
    pub generation: u64,
    pub cursor: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CollectEvent {
    Appended(usize),
    /// The collection was discarded; read from the first page again.
    Restarted,
    Stale,
    Failed(SourceError),
}

/// What the collected sources cover. Never "the complete week".
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CoverageStatement {
    /// Every page was read; only currently authorized accepted backups.
    AllPagesAuthorizedBackupsOnly,
    /// Every page was read, but the server left records out.
    AllPagesWithOmissions,
    /// More pages remain or the page cap was reached.
    Partial,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Coverage {
    pub week: String,
    pub timezone: &'static str,
    pub pages: usize,
    pub sources: usize,
    pub included: usize,
    pub excluded: usize,
    pub omitted_records: bool,
    pub truncated: bool,
    pub statement: CoverageStatement,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Recheck {
    Unchanged,
    /// Sources differ since review: added, removed and changed eventIds.
    Changed {
        added: Vec<String>,
        removed: Vec<String>,
        modified: Vec<String>,
    },
    /// The fresh read is not complete, or is for another owner or week.
    CannotConfirm,
}

/// The sources of one owner's week, page by page.
#[derive(Debug)]
pub struct SourceCollector {
    owner: String,
    week: Week,
    generation: u64,
    pages: usize,
    sources: Vec<Source>,
    seen: HashSet<String>,
    excluded: HashSet<String>,
    omitted: bool,
    cursor: Option<String>,
    has_more: bool,
    in_flight: bool,
}

impl SourceCollector {
    pub fn new(owner: &str, week: Week) -> Self {
        Self {
            owner: owner.to_string(),
            week,
            generation: 0,
            pages: 0,
            sources: Vec::new(),
            seen: HashSet::new(),
            excluded: HashSet::new(),
            omitted: false,
            cursor: None,
            has_more: true,
            in_flight: false,
        }
    }

    pub fn owner(&self) -> &str {
        &self.owner
    }

    pub fn week(&self) -> &Week {
        &self.week
    }

    /// Discard everything read (exclusions too) and start over.
    pub fn restart(&mut self) {
        let generation = self.generation + 1;
        *self = Self::new(&self.owner, self.week.clone());
        self.generation = generation;
    }

    pub fn next_request(&mut self) -> Option<PageRequest> {
        if self.in_flight || !self.has_more || self.pages >= MAX_PAGES {
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
        result: Result<DraftPage, SourceError>,
    ) -> CollectEvent {
        if request.generation != self.generation || request.cursor != self.cursor || !self.in_flight
        {
            return CollectEvent::Stale;
        }
        self.in_flight = false;
        let page = match result {
            Ok(page) => page,
            Err(SourceError::InvalidCursor) => {
                self.restart();
                return CollectEvent::Restarted;
            }
            Err(error) => return CollectEvent::Failed(error),
        };
        if page.sources.iter().any(|s| self.seen.contains(&s.event_id)) {
            self.restart();
            return CollectEvent::Restarted;
        }
        let added = page.sources.len();
        self.pages += 1;
        self.omitted |= page.omitted_records;
        self.has_more = page.next_cursor.is_some();
        self.cursor = page.next_cursor;
        for source in page.sources {
            self.seen.insert(source.event_id.clone());
            self.sources.push(source);
        }
        CollectEvent::Appended(added)
    }

    /// Exclude or include a source; only sources that were read can be chosen.
    pub fn set_excluded(&mut self, event_id: &str, excluded: bool) -> bool {
        if !self.seen.contains(event_id) {
            return false;
        }
        if excluded {
            self.excluded.insert(event_id.to_string());
        } else {
            self.excluded.remove(event_id);
        }
        true
    }

    pub fn sources(&self) -> &[Source] {
        &self.sources
    }

    /// The sources the user excluded (eventIds).
    pub fn excluded_ids(&self) -> Vec<String> {
        let mut ids: Vec<String> = self.excluded.iter().cloned().collect();
        ids.sort();
        ids
    }

    /// Whether the server left records out on any page read.
    pub fn omitted(&self) -> bool {
        self.omitted
    }

    pub fn included(&self) -> impl Iterator<Item = &Source> {
        self.sources
            .iter()
            .filter(|s| !self.excluded.contains(&s.event_id))
    }

    pub fn complete(&self) -> bool {
        self.pages > 0 && !self.has_more
    }

    pub fn coverage(&self) -> Coverage {
        let truncated = self.has_more && self.pages >= MAX_PAGES;
        let statement = match (self.complete(), self.omitted) {
            (true, false) => CoverageStatement::AllPagesAuthorizedBackupsOnly,
            (true, true) => CoverageStatement::AllPagesWithOmissions,
            (false, _) => CoverageStatement::Partial,
        };
        Coverage {
            week: self.week.monday.clone(),
            timezone: TIMEZONE,
            pages: self.pages,
            sources: self.sources.len(),
            included: self.sources.len() - self.excluded.len(),
            excluded: self.excluded.len(),
            omitted_records: self.omitted,
            truncated,
            statement,
        }
    }

    fn fingerprint(&self) -> BTreeMap<String, String> {
        self.included()
            .map(|s| (s.event_id.clone(), s.digest()))
            .collect()
    }

    /// Compare the reviewed sources with a fresh, complete read of the same
    /// owner's week before copying. The user's exclusions apply to both.
    pub fn recheck(&self, fresh: &SourceCollector) -> Recheck {
        if fresh.owner != self.owner
            || fresh.week != self.week
            || !fresh.complete()
            || !self.complete()
        {
            return Recheck::CannotConfirm;
        }
        let reviewed = self.fingerprint();
        let current: BTreeMap<String, String> = fresh
            .sources
            .iter()
            .filter(|s| !self.excluded.contains(&s.event_id))
            .map(|s| (s.event_id.clone(), s.digest()))
            .collect();
        let added: Vec<String> = current
            .keys()
            .filter(|k| !reviewed.contains_key(*k))
            .cloned()
            .collect();
        let removed: Vec<String> = reviewed
            .keys()
            .filter(|k| !current.contains_key(*k))
            .cloned()
            .collect();
        let modified: Vec<String> = reviewed
            .iter()
            .filter(|(k, v)| current.get(*k).is_some_and(|c| c != *v))
            .map(|(k, _)| k.clone())
            .collect();
        if added.is_empty()
            && removed.is_empty()
            && modified.is_empty()
            && fresh.omitted == self.omitted
        {
            Recheck::Unchanged
        } else {
            Recheck::Changed {
                added,
                removed,
                modified,
            }
        }
    }
}

// ---------------------------------------------------------------- HTTP

fn refusal(status: u16, code: Option<&str>) -> SourceError {
    match (status, code) {
        (401, _) => SourceError::SignInRequired,
        (403, _) => SourceError::NotPermitted,
        (422, Some("INVALID_CONTEXT_CURSOR")) => SourceError::InvalidCursor,
        (422, _) => SourceError::InvalidWeek,
        (429, _) => SourceError::RateLimited,
        _ => SourceError::Unavailable,
    }
}

/// Fetch one draft page of `week`. Redirects are never followed.
pub async fn fetch_draft_page(
    endpoint: &ContextEndpoint,
    token: &str,
    week: &Week,
    cursor: Option<&str>,
) -> Result<DraftPage, SourceError> {
    if cursor.is_some_and(|c| !identifier(c, 256)) {
        return Err(SourceError::InvalidCursor);
    }
    let mut url = reqwest::Url::parse(endpoint.canonical())
        .ok()
        .and_then(|u| u.join("v1/context-weekly-draft").ok())
        .ok_or(SourceError::Network)?;
    {
        let mut query = url.query_pairs_mut();
        query.append_pair("week", &week.monday);
        if let Some(cursor) = cursor {
            query.append_pair("cursor", cursor);
        }
    }
    let client = endpoint
        .transport_client()
        .map_err(|_| SourceError::Network)?;
    let response = client
        .get(url)
        .bearer_auth(token)
        .send()
        .await
        .map_err(|_| SourceError::Network)?;
    let status = response.status().as_u16();
    if status != 200 {
        let code = read_bounded(response, MAX_ERROR_BYTES)
            .await
            .ok()
            .and_then(|body| crate::context_login::refusal_code(&body));
        return Err(refusal(status, code.as_deref()));
    }
    let body = read_bounded(response, MAX_PAGE_BYTES)
        .await
        .map_err(|_| SourceError::InvalidResponse)?;
    decode_draft_page(&body, week)
}
