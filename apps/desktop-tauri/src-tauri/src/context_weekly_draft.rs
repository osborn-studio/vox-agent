//! Weekly report draft (T22, plan C-23).
//!
//! A draft quotes two kinds of source in two separate sections that are never
//! merged: the user's accepted team backups for the week (T21 collector) and
//! private notes the user explicitly picked. It states the week, the fixed
//! Asia/Shanghai boundary and the coverage, and that quoted statements are not
//! verified work. The user may edit the text freely.
//!
//! Copying is the only way out, and it is gated every time: the draft freezes
//! a snapshot of the team sources it quotes (owner, week, exclusions, omission
//! flag, eventId and text digest of each), and a copy compares a fresh complete
//! read against that snapshot; the picked private revisions must pass a
//! reference check (`/v1/context-private-reference-check`) whose result is a
//! single-use `PrivateCheck` consumed by that copy. Text lives in `Zeroizing`
//! buffers and is dropped with the draft (sign-out, panel closed).
use std::collections::{BTreeMap, HashSet};
use std::fmt::Write as _;

use serde::Serialize;
use zeroize::Zeroizing;

use crate::context_private::PrivateRecord;
use crate::context_sources::{CoverageStatement, SourceCollector, Week};

pub const MAX_PRIVATE_PICKS: usize = 100;
pub const MAX_DRAFT_CHARS: usize = 100_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    En,
    Zh,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DraftError {
    /// A picked private note is withdrawn, empty, outside the week, or repeated.
    InvalidPrivatePick,
    /// Not every page of the week was read: a draft quotes only a complete read.
    IncompleteSources,
    TooManyPicks,
    TooLong,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CopyBlocked {
    /// Team sources changed since review: review again.
    TeamChanged,
    /// The fresh team read is incomplete or for another owner or week.
    TeamCannotConfirm,
    /// A picked private note changed, was withdrawn or is no longer authorized.
    PrivateChanged,
    /// Private picks exist but their reference check has not run.
    PrivateNotChecked,
}

/// Civil date for days since 1970-01-01 (Hinnant's algorithm).
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(month <= 2), month, day)
}

/// The Asia/Shanghai calendar date of a UTC instant (fixed UTC+8 since 1992).
fn shanghai_date(micros: i64) -> String {
    let days = (micros.div_euclid(1_000_000) + 8 * 3600).div_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    format!("{y:04}-{m:02}-{d:02}")
}

fn coverage_line(statement: CoverageStatement, lang: Lang) -> &'static str {
    match (statement, lang) {
        (CoverageStatement::AllPagesAuthorizedBackupsOnly, Lang::En) => {
            "All pages read; only your accepted team backups you can still see. Not a complete record of the week."
        }
        (CoverageStatement::AllPagesAuthorizedBackupsOnly, Lang::Zh) => {
            "已读完全部分页；仅含你当前有权查看的已接受团队备份。不是本周完整工作记录。"
        }
        (CoverageStatement::AllPagesWithOmissions, Lang::En) => {
            "All pages read, but the server left some records out. Not a complete record of the week."
        }
        (CoverageStatement::AllPagesWithOmissions, Lang::Zh) => {
            "已读完全部分页，但服务器略去了部分记录。不是本周完整工作记录。"
        }
        (CoverageStatement::Partial, Lang::En) => "Not all pages were loaded; this draft is partial.",
        (CoverageStatement::Partial, Lang::Zh) => "尚未加载全部分页；本草稿不完整。",
    }
}

/// Append a statement on one line, quoted, so it cannot pose as draft
/// structure. Writes straight into the (zeroized) draft buffer.
fn push_quote(out: &mut String, text: &str) {
    out.push('“');
    for c in text.trim().chars() {
        out.push(if c.is_control() { ' ' } else { c });
    }
    out.push('”');
}

/// The result of one private reference check for exact revisions. Only the
/// service builds it, from a request it just made, and a copy consumes it.
#[derive(Debug, PartialEq, Eq)]
pub struct PrivateCheck {
    refs: Vec<(String, u32)>,
    valid: bool,
}

impl PrivateCheck {
    /// From a `/v1/context-private-reference-check` reply for `refs`.
    pub(crate) fn from_reply(refs: Vec<(String, u32)>, body: &[u8]) -> Option<Self> {
        let valid = crate::context_private_service::decode_reference_check(body)?;
        Some(Self { refs, valid })
    }
}

/// A private note the user picked, frozen at the revision they saw.
struct Pick {
    id: String,
    revision: u32,
}

pub struct WeeklyDraft {
    owner: String,
    week: Week,
    picks: Vec<Pick>,
    /// The team sources the draft quotes: eventId -> text digest (after the
    /// user's exclusions), the exclusions, and the omission flag.
    team: BTreeMap<String, String>,
    excluded: Vec<String>,
    omitted: bool,
    generated: Zeroizing<String>,
    edited: Option<Zeroizing<String>>,
}

impl std::fmt::Debug for WeeklyDraft {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WeeklyDraft")
            .field("week", &self.week.monday)
            .field("picks", &self.picks.len())
            .field("edited", &self.edited.is_some())
            .finish_non_exhaustive()
    }
}

impl WeeklyDraft {
    /// Compose a draft from the collected team sources (exclusions applied)
    /// and the private notes the user picked.
    pub fn compose(
        collector: &SourceCollector,
        picked: &[PrivateRecord],
        lang: Lang,
    ) -> Result<Self, DraftError> {
        if picked.len() > MAX_PRIVATE_PICKS {
            return Err(DraftError::TooManyPicks);
        }
        if !collector.complete() {
            return Err(DraftError::IncompleteSources);
        }
        let week = collector.week().clone();
        let mut seen = HashSet::new();
        for record in picked {
            let created = crate::context_sources::utc_micros(&record.created_at);
            let ok = !record.withdrawn
                && record.text.is_some()
                && created.is_some_and(|c| (week.start_micros..week.end_micros).contains(&c))
                && seen.insert(record.id.clone());
            if !ok {
                return Err(DraftError::InvalidPrivatePick);
            }
        }
        let coverage = collector.coverage();
        let mut out = Zeroizing::new(String::new());
        let (title, boundary, caution, team, private, none) = match lang {
            Lang::En => (
                "Weekly draft",
                "Monday to Monday, Asia/Shanghai",
                "Review before sharing: these are your own statements, not verified work.",
                "Team backups",
                "Private notes you picked",
                "(none)",
            ),
            Lang::Zh => (
                "周报草稿",
                "周一至下周一，Asia/Shanghai",
                "分享前请核对：以下是你本人的陈述，不是已核实的工作成果。",
                "团队备份",
                "你选择的私人记录",
                "（无）",
            ),
        };
        let _ = writeln!(out, "{title} · {} ({boundary})", week.monday);
        out.push_str(coverage_line(coverage.statement, lang));
        out.push('\n');
        out.push_str(caution);
        out.push_str("\n\n");
        let included: Vec<_> = collector.included().collect();
        let _ = writeln!(out, "## {team} ({})", included.len());
        if included.is_empty() {
            out.push_str(none);
            out.push('\n');
        }
        for source in &included {
            let _ = write!(out, "- {} ", shanghai_date(source.submitted_micros));
            push_quote(&mut out, &source.text);
            out.push('\n');
        }
        let _ = writeln!(out, "\n## {private} ({})", picked.len());
        if picked.is_empty() {
            out.push_str(none);
            out.push('\n');
        }
        for record in picked {
            let created = crate::context_sources::utc_micros(&record.created_at).unwrap_or(0);
            let _ = write!(out, "- {} ", shanghai_date(created));
            push_quote(
                &mut out,
                record.text.as_deref().map(String::as_str).unwrap_or(""),
            );
            out.push('\n');
        }
        let team_snapshot = included
            .iter()
            .map(|s| (s.event_id.clone(), s.digest()))
            .collect();
        if out.chars().count() > MAX_DRAFT_CHARS {
            return Err(DraftError::TooLong);
        }
        Ok(Self {
            owner: collector.owner().to_string(),
            week,
            picks: picked
                .iter()
                .map(|r| Pick {
                    id: r.id.clone(),
                    revision: r.revision,
                })
                .collect(),
            team: team_snapshot,
            excluded: collector.excluded_ids(),
            omitted: collector.omitted(),
            generated: out,
            edited: None,
        })
    }

    pub fn owner(&self) -> &str {
        &self.owner
    }

    pub fn week(&self) -> &Week {
        &self.week
    }

    /// The draft as the user will copy it: their edit, or the generated text.
    pub fn text(&self) -> &str {
        self.edited
            .as_deref()
            .map(String::as_str)
            .unwrap_or(&self.generated)
    }

    /// Replace the text with the user's edit.
    pub fn edit(&mut self, text: &str) -> Result<(), DraftError> {
        if text.chars().count() > MAX_DRAFT_CHARS {
            return Err(DraftError::TooLong);
        }
        self.edited = Some(Zeroizing::new(text.to_string()));
        Ok(())
    }

    /// The exact private revisions to reference-check before copying.
    pub fn private_refs(&self) -> Vec<(String, u32)> {
        self.picks
            .iter()
            .map(|p| (p.id.clone(), p.revision))
            .collect()
    }

    /// Whether a fresh, complete read of the same owner's week still holds
    /// exactly the team sources this draft quotes (the draft's exclusions
    /// applied to it).
    fn team_unchanged(&self, fresh: &SourceCollector) -> Result<(), CopyBlocked> {
        if fresh.owner() != self.owner || fresh.week() != &self.week || !fresh.complete() {
            return Err(CopyBlocked::TeamCannotConfirm);
        }
        let current: BTreeMap<String, String> = fresh
            .sources()
            .iter()
            .filter(|s| !self.excluded.contains(&s.event_id))
            .map(|s| (s.event_id.clone(), s.digest()))
            .collect();
        if current != self.team || fresh.omitted() != self.omitted {
            return Err(CopyBlocked::TeamChanged);
        }
        Ok(())
    }

    /// The text to copy. Every copy needs a fresh complete team read for this
    /// owner and week, and (with private picks) a reference check of exactly
    /// these revisions, which this call consumes.
    /// Crate-internal: only `PrivateService::copy_draft` calls it, with a team
    /// read and a reference check it made for this copy.
    pub(crate) fn copy(
        &self,
        fresh: &SourceCollector,
        private: Option<PrivateCheck>,
    ) -> Result<Zeroizing<String>, CopyBlocked> {
        self.team_unchanged(fresh)?;
        if !self.picks.is_empty() {
            let check = private.ok_or(CopyBlocked::PrivateNotChecked)?;
            if check.refs != self.private_refs() {
                return Err(CopyBlocked::PrivateNotChecked);
            }
            if !check.valid {
                return Err(CopyBlocked::PrivateChanged);
            }
        }
        Ok(Zeroizing::new(self.text().to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context_private::decode_record;
    use crate::context_sources::{decode_draft_page, week};
    use serde_json::Value;

    const OWNER: &str = "endpoint|org|feishu-0123456789abcdef0123456789abcdef|1";
    const MONDAY: &str = "2026-10-05";
    const LATE: &str = "2026-10-06T23:30:00+00:00";

    fn fixture() -> Value {
        let text = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../test/fixtures/context-extended-wire-v1.json"
        ))
        .unwrap()
        .replace("<week-monday>", MONDAY)
        .replace("<timestamp>", LATE)
        .replace("<id-1>", "0f8fad5b-d9cb-469f-a165-70867728950e");
        serde_json::from_str(&text).unwrap()
    }

    fn case(section: &str, name: &str) -> Value {
        fixture()[section]["http_cases"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["name"] == name)
            .unwrap()["response"]["body"]
            .clone()
    }

    fn collector() -> SourceCollector {
        collector_from(OWNER, MONDAY, |_| {})
    }

    fn private_note() -> PrivateRecord {
        decode_record(case("private", "edit").to_string().as_bytes()).unwrap()
    }

    fn collector_from(
        owner: &str,
        monday: &str,
        mutate: impl FnOnce(&mut Value),
    ) -> SourceCollector {
        let w = week(monday).unwrap();
        let mut page = case("inputs_weekly", "weekly_draft");
        page["week"] = monday.into();
        mutate(&mut page);
        let mut collector = SourceCollector::new(owner, w.clone());
        let request = collector.next_request().unwrap();
        collector.apply(&request, decode_draft_page(page.to_string().as_bytes(), &w));
        collector
    }

    fn check(refs: Vec<(String, u32)>, valid: bool) -> PrivateCheck {
        let body = format!(
            r#"{{"version":1,"valid":{valid},"requiresUserReview":true,"meaning":"current-authorized-private-revisions-only","evidenceKind":"user-statement"}}"#
        );
        PrivateCheck::from_reply(refs, body.as_bytes()).unwrap()
    }

    #[test]
    fn copying_rechecks_the_drafts_own_team_sources_every_time() {
        let mut reviewed = collector();
        reviewed.set_excluded("team-1", true);
        let mut draft = WeeklyDraft::compose(&reviewed, &[], Lang::En).unwrap();
        draft.edit("My own words for the week.").unwrap();
        // Same sources (the excluded one may change freely): copy allowed.
        let same = collector_from(OWNER, MONDAY, |p| {
            p["sources"][1]["text"] = "edited, but excluded".into()
        });
        assert_eq!(
            draft.copy(&same, None).unwrap().as_str(),
            "My own words for the week."
        );
        // An included source changed, one disappeared, or omissions appeared: review again.
        let edited = collector_from(OWNER, MONDAY, |p| {
            p["sources"][0]["text"] = "changed".into()
        });
        assert_eq!(
            draft.copy(&edited, None).err(),
            Some(CopyBlocked::TeamChanged)
        );
        let fewer = collector_from(OWNER, MONDAY, |p| {
            p["sources"].as_array_mut().unwrap().pop();
        });
        assert_eq!(
            draft.copy(&fewer, None).err(),
            Some(CopyBlocked::TeamChanged)
        );
        let omitted = collector_from(OWNER, MONDAY, |p| p["omittedRecords"] = true.into());
        assert_eq!(
            draft.copy(&omitted, None).err(),
            Some(CopyBlocked::TeamChanged)
        );
        // Another owner, another week, or an incomplete read cannot confirm.
        let other = collector_from(
            "endpoint|org|feishu-fedcba9876543210fedcba9876543210|1",
            MONDAY,
            |_| {},
        );
        assert_eq!(
            draft.copy(&other, None).err(),
            Some(CopyBlocked::TeamCannotConfirm)
        );
        let incomplete = SourceCollector::new(OWNER, week(MONDAY).unwrap());
        assert_eq!(
            draft.copy(&incomplete, None).err(),
            Some(CopyBlocked::TeamCannotConfirm)
        );
    }

    #[test]
    fn private_picks_need_a_fresh_check_of_exactly_those_revisions_per_copy() {
        let fresh = collector();
        let draft = WeeklyDraft::compose(&fresh, &[private_note()], Lang::En).unwrap();
        let refs = draft.private_refs();
        assert_eq!(
            refs,
            vec![("0f8fad5b-d9cb-469f-a165-70867728950e".to_string(), 2)]
        );
        assert_eq!(
            draft.copy(&fresh, None).err(),
            Some(CopyBlocked::PrivateNotChecked)
        );
        let other = vec![("0f8fad5b-d9cb-469f-a165-70867728950e".to_string(), 1)];
        assert_eq!(
            draft.copy(&fresh, Some(check(other, true))).err(),
            Some(CopyBlocked::PrivateNotChecked)
        );
        assert_eq!(
            draft.copy(&fresh, Some(check(refs.clone(), false))).err(),
            Some(CopyBlocked::PrivateChanged)
        );
        assert!(draft.copy(&fresh, Some(check(refs.clone(), true))).is_ok());
        // The check was consumed: the next copy needs a new one.
        assert_eq!(
            draft.copy(&fresh, None).err(),
            Some(CopyBlocked::PrivateNotChecked)
        );
    }
}
