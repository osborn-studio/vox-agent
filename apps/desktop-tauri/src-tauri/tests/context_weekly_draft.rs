//! T22: weekly draft composition and the copy gate.
use serde_json::Value;
use vox_agent_desktop_tauri_lib::context_private::decode_record;
use vox_agent_desktop_tauri_lib::context_sources::{decode_draft_page, week, SourceCollector};
use vox_agent_desktop_tauri_lib::context_weekly_draft::{DraftError, Lang, WeeklyDraft};

const OWNER: &str = "endpoint|org|feishu-0123456789abcdef0123456789abcdef|1";
const MONDAY: &str = "2026-10-05";
// 2026-10-06 23:30 UTC is 2026-10-07 in Asia/Shanghai.
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
    let w = week(MONDAY).unwrap();
    let mut collector = SourceCollector::new(OWNER, w.clone());
    let request = collector.next_request().unwrap();
    let page = decode_draft_page(
        case("inputs_weekly", "weekly_draft").to_string().as_bytes(),
        &w,
    );
    collector.apply(&request, page);
    collector
}

fn private_note() -> vox_agent_desktop_tauri_lib::context_private::PrivateRecord {
    decode_record(case("private", "edit").to_string().as_bytes()).unwrap()
}

#[test]
fn team_and_private_sources_stay_in_separate_sections() {
    let mut collector = collector();
    assert!(collector.set_excluded("team-1", true));
    let draft = WeeklyDraft::compose(&collector, &[private_note()], Lang::En).unwrap();
    let text = draft.text();
    let team = text.find("## Team backups (2)").unwrap();
    let private = text.find("## Private notes you picked (1)").unwrap();
    assert!(team < private);
    assert!(text[team..private].contains("“team note 2”") && !text.contains("team note 1"));
    assert!(
        text[private..].contains("“synthetic edit”")
            && !text[team..private].contains("synthetic edit")
    );
    assert!(
        text.contains("2026-10-07"),
        "dates are Asia/Shanghai calendar days"
    );
    assert!(
        text.contains("Not a complete record of the week") && text.contains("not verified work")
    );
    assert!(text.contains("2026-10-05 (Monday to Monday, Asia/Shanghai)"));
    assert!(!format!("{draft:?}").contains("team note"));

    let zh = WeeklyDraft::compose(&collector, &[], Lang::Zh).unwrap();
    assert!(zh.text().contains("不是本周完整工作记录") && zh.text().contains("（无）"));
}

#[test]
fn only_valid_explicit_private_picks_are_accepted() {
    let collector = collector();
    let withdrawn = decode_record(case("private", "withdraw").to_string().as_bytes()).unwrap();
    assert_eq!(
        WeeklyDraft::compose(&collector, &[withdrawn], Lang::En).err(),
        Some(DraftError::InvalidPrivatePick)
    );
    let note = private_note();
    assert_eq!(
        WeeklyDraft::compose(&collector, &[note.clone(), note.clone()], Lang::En).err(),
        Some(DraftError::InvalidPrivatePick)
    );
    let mut outside = case("private", "edit");
    outside["createdAt"] = "2026-09-30T00:00:00+00:00".into();
    let outside = decode_record(outside.to_string().as_bytes()).unwrap();
    assert_eq!(
        WeeklyDraft::compose(&collector, &[outside], Lang::En).err(),
        Some(DraftError::InvalidPrivatePick)
    );
    // A statement cannot pose as draft structure.
    let mut multiline = case("private", "edit");
    multiline["text"] = "line one\n## Fake section".into();
    let multiline = decode_record(multiline.to_string().as_bytes()).unwrap();
    let draft = WeeklyDraft::compose(&collector, &[multiline], Lang::En).unwrap();
    assert!(!draft.text().contains("\n## Fake section"));
}

#[test]
fn a_draft_quotes_only_a_complete_read() {
    let w = week(MONDAY).unwrap();
    let mut page = case("inputs_weekly", "weekly_draft");
    page["hasMore"] = true.into();
    page["nextCursor"] = "WzE3NjAwMDAwMDAuMCwgImlkIl0".into();
    let mut partial = SourceCollector::new(OWNER, w.clone());
    let request = partial.next_request().unwrap();
    partial.apply(&request, decode_draft_page(page.to_string().as_bytes(), &w));
    assert!(!partial.complete());
    assert_eq!(
        WeeklyDraft::compose(&partial, &[], Lang::En).err(),
        Some(DraftError::IncompleteSources)
    );
}
