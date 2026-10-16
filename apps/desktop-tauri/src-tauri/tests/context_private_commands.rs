//! T18b/T19 IPC: strict inputs and DTOs that carry codes, never server text.
use vox_agent_desktop_tauri_lib::context_private::{decode_record, Phase, Tag};
use vox_agent_desktop_tauri_lib::context_private_commands::{
    parse_create, parse_list, phase_dto, record_dto, valid_key, PrivateCreateInput,
    PrivateListInput, PrivateStepInput,
};
use vox_agent_desktop_tauri_lib::context_private_service::PrivateServiceError;

#[test]
fn inputs_are_strict() {
    let create: PrivateCreateInput = serde_json::from_str(
        r#"{"text":"x","tags":["question","blocker"],"projectId":null,"repositoryId":null}"#,
    )
    .unwrap();
    let tags = parse_create(create).unwrap().tags;
    assert_eq!(tags, vec![Tag::Question, Tag::Blocker]);
    // Owner, visibility or revision can never come from the WebView.
    for extra in [
        r#""owner":"x""#,
        r#""visibility":"team""#,
        r#""eventId":"e""#,
    ] {
        let json =
            format!(r#"{{"text":"x","tags":[],"projectId":null,"repositoryId":null,{extra}}}"#);
        assert!(
            serde_json::from_str::<PrivateCreateInput>(&json).is_err(),
            "{extra}"
        );
    }
    let bad_tag: PrivateCreateInput = serde_json::from_str(
        r#"{"text":"x","tags":["urgent"],"projectId":null,"repositoryId":null}"#,
    )
    .unwrap();
    assert_eq!(
        parse_create(bad_tag).err(),
        Some(PrivateServiceError::InvalidInput)
    );
    let too_many: PrivateCreateInput = serde_json::from_str(
        r#"{"text":"x","tags":["decision","progress","blocker","question","verification","decision"],"projectId":null,"repositoryId":null}"#,
    )
    .unwrap();
    assert_eq!(
        parse_create(too_many).err(),
        Some(PrivateServiceError::InvalidInput)
    );

    assert!(serde_json::from_str::<PrivateStepInput>(
        r#"{"key":"create:pv_1","retry":false,"phase":"applied"}"#
    )
    .is_err());
    assert!(valid_key("create:pv_abc") && valid_key("request:r-1"));
    for bad in ["pv_abc", "create:", "create:a/b", "request:../x", "edit:pv"] {
        assert!(!valid_key(bad), "{bad}");
    }

    let list: PrivateListInput = serde_json::from_str(
        r#"{"start":"2026-10-01T00:00:00+00:00","end":"2026-10-15T00:00:00+00:00","limit":20,"cursor":null,"tag":"progress","projectId":null,"text":null}"#,
    )
    .unwrap();
    assert_eq!(parse_list(list).unwrap().tag, Some(Tag::Progress));
    assert!(serde_json::from_str::<PrivateListInput>(
        r#"{"start":"a","end":"b","limit":20,"cursor":null,"tag":null,"projectId":null,"text":null,"owner":"x"}"#
    )
    .is_err());
}

#[test]
fn dtos_carry_codes_and_the_record_only() {
    let dto = phase_dto(
        "create:pv_1",
        &Phase::Conflict("PRIVATE_CONTEXT_CONFLICT".into()),
    );
    assert_eq!(
        (dto.phase, dto.detail.as_deref()),
        ("conflict", Some("PRIVATE_CONTEXT_CONFLICT"))
    );
    let applied = phase_dto(
        "create:pv_1",
        &Phase::Applied {
            record_id: "r".into(),
            revision: 2,
        },
    );
    assert_eq!((applied.phase, applied.revision), ("applied", Some(2)));
    let record = decode_record(
        br#"{"version":1,"id":"0f8fad5b-d9cb-469f-a165-70867728950e","eventId":"pv_1","revision":3,"visibility":"private","evidenceKind":"user-statement","withdrawn":true,"createdAt":"2026-10-08T01:02:03+00:00","revisionChangedAt":"2026-10-08T01:02:04+00:00"}"#,
    )
    .unwrap();
    let dto = record_dto(&record);
    let edited = decode_record(
        br#"{"version":1,"id":"0f8fad5b-d9cb-469f-a165-70867728950e","eventId":"pv_1","revision":2,"visibility":"private","evidenceKind":"user-statement","withdrawn":false,"createdAt":"2026-10-08T01:02:03+00:00","revisionChangedAt":"2026-10-08T01:02:04+00:00","text":"synthetic secret","tags":[]}"#,
    )
    .unwrap();
    assert!(!format!("{:?}", record_dto(&edited)).contains("synthetic"));
    assert!(dto.withdrawn && dto.text.is_none() && dto.tags.is_empty());
    let json = serde_json::to_value(&dto).unwrap();
    assert!(json.get("revisionChangedAt").is_some() && json.get("revision_changed_at").is_none());
}

#[test]
fn a_withdrawal_needs_an_explicit_confirm() {
    use vox_agent_desktop_tauri_lib::context_private_commands::{
        withdraw_confirmation, PrivateEditInput, PrivateWithdrawInput,
    };
    let unconfirmed: PrivateWithdrawInput = serde_json::from_str(
        r#"{"recordId":"0f8fad5b-d9cb-469f-a165-70867728950e","seenRevision":2,"confirm":false}"#,
    )
    .unwrap();
    assert_eq!(
        withdraw_confirmation(&unconfirmed).err(),
        Some(PrivateServiceError::NotAllowed)
    );
    assert!(
        serde_json::from_str::<PrivateWithdrawInput>(r#"{"recordId":"r","seenRevision":2}"#)
            .is_err()
    );
    assert!(serde_json::from_str::<PrivateEditInput>(
        r#"{"recordId":"r","seenRevision":1,"text":"x","tags":[],"expectedRevision":5}"#
    )
    .is_err());
}
