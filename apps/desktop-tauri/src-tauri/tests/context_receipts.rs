//! T04: only a receipt verified against an outstanding lease can change delivery
//! state, and every receipt the real server produces (fixture v2) decodes.
use serde_json::Value;
use vox_agent_desktop_tauri_lib::context_delivery::{
    BackupPayload, ContextDeliveryOwner, DeliveryError, DeliveryState, ProjectSnapshot,
};
use vox_agent_desktop_tauri_lib::context_wire::{verify_readback, verify_submission, WireError};

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../test/fixtures/context-backup-wire-v2.json"
    ))
    .unwrap()
}

fn payload_from(request: &Value) -> BackupPayload {
    let project = request.get("projectId").and_then(Value::as_str).map(|id| {
        ProjectSnapshot::new(
            id.to_string(),
            request
                .get("repository")
                .and_then(Value::as_str)
                .unwrap_or("studio/repo")
                .to_string(),
        )
        .unwrap()
    });
    BackupPayload::new(
        request["eventId"].as_str().unwrap().to_string(),
        request["targetId"].as_str().unwrap().to_string(),
        request["text"].as_str().unwrap().to_string(),
        project,
    )
    .unwrap()
}

fn owner() -> ContextDeliveryOwner {
    let mut owner = ContextDeliveryOwner::new(64);
    owner
        .set_authenticated_scope(Some("principal-A".into()))
        .unwrap();
    owner
}

fn body(value: &Value) -> Vec<u8> {
    serde_json::to_vec(value).unwrap()
}

#[test]
fn every_accepted_submission_in_the_real_fixture_verifies() {
    let mut checked = 0;
    for case in fixture()["http_cases"].as_array().unwrap() {
        let request = &case["request"];
        if request["method"] != "POST"
            || case["response"]["status"] != 202
            || request["auth"] != "owner"
        {
            continue;
        }
        let mut owner = owner();
        let lease = owner
            .reserve_submission(payload_from(&request["json"]))
            .unwrap();
        let receipt = verify_submission(&lease, &body(&case["response"]["body"])).unwrap();
        owner.apply_verified(&receipt).unwrap();
        assert_eq!(
            owner.state(lease.payload().event_id()),
            Some(DeliveryState::Pending)
        );
        checked += 1;
    }
    assert!(checked >= 3, "fixture must contain accepted submissions");
}

#[test]
fn every_status_in_the_real_sequences_decodes_to_the_right_state() {
    let expected = |status: &str| match status {
        "pending" => DeliveryState::Pending,
        "sending" => DeliveryState::Sending,
        "unknown" => DeliveryState::ServerUnknown,
        "delivered" => DeliveryState::Delivered,
        "blocked" => DeliveryState::Blocked,
        "rejected" => DeliveryState::Rejected,
        other => panic!("unexpected status {other}"),
    };
    for sequence in fixture()["status_sequences"].as_array().unwrap() {
        let mut owner = owner();
        let event = sequence["steps"][0]["receipt"]["eventId"]
            .as_str()
            .unwrap()
            .to_string();
        let payload = BackupPayload::new(
            event.clone(),
            "team".into(),
            "synthetic context".into(),
            None,
        )
        .unwrap();
        owner.reserve_submission(payload).unwrap();
        for step in sequence["steps"].as_array().unwrap() {
            let lease = owner.reserve_readback(&event).unwrap();
            let receipt = verify_readback(&lease, &body(&step["receipt"])).unwrap();
            owner.apply_verified(&receipt).unwrap();
            let status = step["receipt"]["status"].as_str().unwrap();
            assert_eq!(
                owner.state(&event),
                Some(expected(status)),
                "{} {}",
                sequence["name"],
                step["action"]
            );
        }
    }
}

#[test]
fn conflict_is_never_a_receipt_status_and_unknown_fields_shapes_fail_closed() {
    let mut owner = owner();
    let payload = BackupPayload::new("event-1".into(), "team".into(), "x".into(), None).unwrap();
    owner.reserve_submission(payload).unwrap();
    let lease = owner.reserve_readback("event-1").unwrap();
    for bad in [
        r#"{"eventId":"event-1","status":"conflict","messageId":null}"#,
        r#"{"eventId":"event-1","status":"not_sent","messageId":null}"#,
        r#"{"eventId":"event-1","status":"pending","messageId":"om_unexpected"}"#,
        r#"{"eventId":"event-1","status":"delivered","messageId":null}"#,
        r#"{"eventId":"event-2","status":"pending","messageId":null}"#,
    ] {
        assert_eq!(
            verify_readback(&lease, bad.as_bytes()).err(),
            Some(WireError::InvalidReceipt),
            "{bad}"
        );
    }
}

#[test]
fn a_receipt_for_an_old_identity_generation_is_refused() {
    let mut owner = owner();
    owner
        .reserve_submission(
            BackupPayload::new("event-1".into(), "team".into(), "x".into(), None).unwrap(),
        )
        .unwrap();
    let lease = owner.reserve_readback("event-1").unwrap();
    let receipt = verify_readback(
        &lease,
        br#"{"eventId":"event-1","status":"delivered","messageId":"om_1"}"#,
    )
    .unwrap();
    owner.set_authenticated_scope(None).unwrap();
    owner
        .set_authenticated_scope(Some("principal-A".into()))
        .unwrap();
    assert_eq!(
        owner.apply_verified(&receipt),
        Err(DeliveryError::StaleLease)
    );
}

#[test]
fn an_older_poll_cannot_overwrite_a_newer_intermediate_state() {
    let mut owner = owner();
    owner
        .reserve_submission(
            BackupPayload::new("event-1".into(), "team".into(), "x".into(), None).unwrap(),
        )
        .unwrap();
    let older = owner.reserve_readback("event-1").unwrap();
    let newer = owner.reserve_readback("event-1").unwrap();
    let sending = verify_readback(
        &newer,
        br#"{"eventId":"event-1","status":"sending","messageId":null}"#,
    )
    .unwrap();
    let pending = verify_readback(
        &older,
        br#"{"eventId":"event-1","status":"pending","messageId":null}"#,
    )
    .unwrap();
    owner.apply_verified(&sending).unwrap();
    assert_eq!(
        owner.apply_verified(&pending),
        Err(DeliveryError::StaleLease)
    );
    assert_eq!(owner.state("event-1"), Some(DeliveryState::Sending));
}

#[test]
fn blocked_can_recover_but_delivered_and_rejected_are_final() {
    let mut owner = owner();
    owner
        .reserve_submission(
            BackupPayload::new("event-1".into(), "team".into(), "x".into(), None).unwrap(),
        )
        .unwrap();
    for (status, message) in [
        ("blocked", "null"),
        ("pending", "null"),
        ("delivered", "\"om_1\""),
        ("pending", "null"),
    ] {
        let lease = owner.reserve_readback("event-1").unwrap();
        let raw = format!(r#"{{"eventId":"event-1","status":"{status}","messageId":{message}}}"#);
        let receipt = verify_readback(&lease, raw.as_bytes()).unwrap();
        let result = owner.apply_verified(&receipt);
        if status == "pending" && owner.state("event-1") == Some(DeliveryState::Delivered) {
            assert_eq!(result, Err(DeliveryError::InvalidReceipt));
        } else {
            result.unwrap();
        }
    }
    assert_eq!(owner.state("event-1"), Some(DeliveryState::Delivered));
}

#[test]
fn readback_lease_owns_the_frozen_payload_and_checks_project_attribution() {
    let mut owner = owner();
    let project = ProjectSnapshot::new("p1".into(), "studio/repo".into()).unwrap();
    owner
        .reserve_submission(
            BackupPayload::new("event-1".into(), "team".into(), "x".into(), Some(project)).unwrap(),
        )
        .unwrap();
    let lease = owner.reserve_readback("event-1").unwrap();
    assert_eq!(lease.payload().project().unwrap().id(), "p1");
    let wrong = br#"{"eventId":"event-1","status":"pending","messageId":null,"attribution":{"version":1,"projectId":"p2","repositoryId":"studio/repo"}}"#;
    assert_eq!(
        verify_readback(&lease, wrong).err(),
        Some(WireError::InvalidReceipt)
    );
    let missing = br#"{"eventId":"event-1","status":"pending","messageId":null}"#;
    assert_eq!(
        verify_readback(&lease, missing).err(),
        Some(WireError::InvalidReceipt)
    );
}

#[test]
fn a_receipt_verified_for_one_owner_instance_cannot_be_applied_to_another() {
    let mut first = owner();
    let mut second = owner();
    let payload = BackupPayload::new("event-1".into(), "team".into(), "x".into(), None).unwrap();
    first.reserve_submission(payload.clone()).unwrap();
    second.reserve_submission(payload).unwrap();
    let lease = first.reserve_readback("event-1").unwrap();
    let receipt = verify_readback(
        &lease,
        br#"{"eventId":"event-1","status":"delivered","messageId":"om_1"}"#,
    )
    .unwrap();
    assert_eq!(
        second.apply_verified(&receipt),
        Err(DeliveryError::StaleLease)
    );
    assert_eq!(second.state("event-1"), Some(DeliveryState::Unknown));
    first.apply_verified(&receipt).unwrap();
}

mod catalog {
    use vox_agent_desktop_tauri_lib::context_wire::{
        decode_projects, WireError, MAX_CATALOG_BYTES,
    };

    #[test]
    fn a_valid_catalog_decodes_and_invalid_entries_are_skipped_and_counted() {
        let body = br#"{"version":1,"projects":[
            {"id":"p1","name":"Studio","repositoryId":"studio/repo"},
            {"id":"../p2","name":"Bad id","repositoryId":"studio/repo"},
            {"id":"p3","name":"Bad repo","repositoryId":"no-slash"},
            {"id":"p4","name":"","repositoryId":"studio/other"},
            {"id":"p5","name":"Line\nbreak","repositoryId":"studio/other"}
        ]}"#;
        let catalog = decode_projects(body).unwrap();
        assert_eq!(catalog.projects.len(), 1);
        assert_eq!(catalog.projects[0].snapshot.id(), "p1");
        assert_eq!(catalog.projects[0].snapshot.repository(), "studio/repo");
        assert_eq!(catalog.projects[0].name, "Studio");
        assert_eq!(catalog.omitted, 4);
    }

    #[test]
    fn wrong_versions_shapes_and_sizes_are_refused() {
        for body in [
            &br#"{"version":2,"projects":[]}"#[..],
            br#"{"projects":[]}"#,
            br#"{"version":1,"projects":{}}"#,
            br#"[]"#,
        ] {
            assert_eq!(decode_projects(body).err(), Some(WireError::InvalidReceipt));
        }
        let big = vec![b' '; MAX_CATALOG_BYTES + 1];
        assert_eq!(decode_projects(&big).err(), Some(WireError::TooLarge));
        let many = format!(
            r#"{{"version":1,"projects":[{}]}}"#,
            vec![r#"{"id":"p","name":"n","repositoryId":"a/b"}"#; 501].join(",")
        );
        assert_eq!(
            decode_projects(many.as_bytes()).err(),
            Some(WireError::TooLarge)
        );
    }
}
