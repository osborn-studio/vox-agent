//! T21/T22 weekly IPC helpers: DTOs carry Asia/Shanghai dates and the user's
//! exclusions; inputs are strict.
use vox_agent_desktop_tauri_lib::context_sources::{decode_draft_page, week, SourceCollector};
use vox_agent_desktop_tauri_lib::context_weekly_commands::{
    parse_lang, weekly_dto, WeeklyComposeInput,
};

#[test]
fn sources_carry_shanghai_dates_and_exclusions() {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../test/fixtures/context-extended-wire-v1.json"
    ))
    .unwrap()
    .replace("<week-monday>", "2026-10-05")
    // 23:30 UTC on the 6th is the 7th in Shanghai.
    .replace("<timestamp>", "2026-10-06T23:30:00+00:00");
    let fixture: serde_json::Value = serde_json::from_str(&text).unwrap();
    let page = fixture["inputs_weekly"]["http_cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["name"] == "weekly_draft")
        .unwrap()["response"]["body"]
        .clone();
    let w = week("2026-10-05").unwrap();
    let mut collector = SourceCollector::new("owner", w.clone());
    let request = collector.next_request().unwrap();
    collector.apply(&request, decode_draft_page(page.to_string().as_bytes(), &w));
    collector.set_excluded("team-1", true);
    let dto = weekly_dto(&collector);
    assert_eq!(dto.sources.len(), 3);
    assert!(dto.sources.iter().all(|s| s.date == "2026-10-07"));
    assert!(
        dto.sources
            .iter()
            .find(|s| s.event_id == "team-1")
            .unwrap()
            .excluded
    );
    assert_eq!(dto.coverage.excluded, 1);
    assert!(!format!("{:?}", dto.sources[0]).contains("team note"));
    let json = serde_json::to_value(&dto).unwrap();
    assert!(json["sources"][0].get("deliveryStatus").is_some());
}

#[test]
fn inputs_are_strict() {
    assert!(parse_lang("en").is_ok() && parse_lang("zh").is_ok() && parse_lang("fr").is_err());
    assert!(
        serde_json::from_str::<WeeklyComposeInput>(r#"{"lang":"en","privateRecordIds":[]}"#)
            .is_ok()
    );
    assert!(serde_json::from_str::<WeeklyComposeInput>(
        r#"{"lang":"en","privateRecordIds":[],"owner":"x"}"#
    )
    .is_err());
}
