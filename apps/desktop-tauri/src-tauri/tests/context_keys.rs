//! T07a: key lifecycle with the test-only fake provider.
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::{
    owner_digest, Aad, KeyError, KeyRing, KeyState, ProviderKind, RecordKind,
};

fn aad(event: &str) -> Aad {
    Aad::new(
        1,
        owner_digest("principal-A"),
        event,
        RecordKind::TeamBackup,
    )
    .unwrap()
}

#[test]
fn created_keys_round_trip_and_reopen_with_the_same_provider() {
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]));
    let keys = ring.create().unwrap();
    let sealed = keys.seal(&aad("event-1"), b"synthetic context").unwrap();
    assert!(
        !sealed.windows(9).any(|w| w == b"synthetic"),
        "plaintext must not appear"
    );

    let reopened = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]))
        .open(keys.id())
        .unwrap();
    assert_eq!(
        &*reopened.open(&aad("event-1"), &sealed).unwrap(),
        b"synthetic context"
    );
}

#[test]
fn a_record_opened_under_different_associated_data_fails() {
    let dir = tempfile::tempdir().unwrap();
    let keys = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]))
        .create()
        .unwrap();
    let sealed = keys.seal(&aad("event-1"), b"x").unwrap();
    for other in [
        aad("event-2"),
        Aad::new(
            1,
            owner_digest("principal-B"),
            "event-1",
            RecordKind::TeamBackup,
        )
        .unwrap(),
        Aad::new(
            2,
            owner_digest("principal-A"),
            "event-1",
            RecordKind::TeamBackup,
        )
        .unwrap(),
        Aad::new(
            1,
            owner_digest("principal-A"),
            "event-1",
            RecordKind::PrivateMutation,
        )
        .unwrap(),
    ] {
        assert!(keys.open(&other, &sealed).is_err());
    }
    let mut tampered = sealed.clone();
    *tampered.last_mut().unwrap() ^= 1;
    assert!(keys.open(&aad("event-1"), &tampered).is_err());
}

#[test]
fn lost_key_is_unrecoverable_and_an_unavailable_provider_is_distinct() {
    let dir = tempfile::tempdir().unwrap();
    let id = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]))
        .create()
        .unwrap()
        .id()
        .clone();

    let other_user = KeyRing::new(dir.path(), FakeKeyProvider::new([9; 32]));
    assert_eq!(other_user.open(&id).err(), Some(KeyError::Unrecoverable));
    assert_eq!(other_user.state(&id), KeyState::Unrecoverable);

    let locked = KeyRing::new(dir.path(), FakeKeyProvider::unavailable([7; 32]));
    assert_eq!(locked.open(&id).err(), Some(KeyError::Unavailable));
    assert_eq!(locked.state(&id), KeyState::Unavailable);

    let missing = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]));
    let bogus = missing.create().unwrap().id().clone();
    std::fs::remove_file(missing.key_path(&bogus)).unwrap();
    assert_eq!(missing.open(&bogus).err(), Some(KeyError::Unrecoverable));
}

#[test]
fn creating_a_key_never_overwrites_an_existing_one() {
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]));
    let first = ring.create().unwrap();
    let before = std::fs::read(ring.key_path(first.id())).unwrap();
    let second = ring.create().unwrap();
    assert_ne!(first.id(), second.id());
    assert_eq!(std::fs::read(ring.key_path(first.id())).unwrap(), before);
}

#[test]
fn the_fake_provider_never_passes_for_dpapi() {
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]));
    let keys = ring.create().unwrap();
    assert_eq!(ring.provider_kind(), ProviderKind::FakeTestOnly);
    let path = ring.key_path(keys.id());
    assert!(path.to_string_lossy().ends_with(".fake-test-only"));
    let file = std::fs::read_to_string(&path).unwrap();
    assert!(file.contains("\"provider\":\"fake-test-only\""));
    // A key file that claims DPAPI cannot be opened by the fake provider.
    let forged = ring.key_path(keys.id()).with_extension("dpapi");
    std::fs::write(&forged, file.replace("fake-test-only", "dpapi-user")).unwrap();
    assert_eq!(
        ring.open_path(&forged).err(),
        Some(KeyError::ProviderMismatch)
    );
}

#[test]
fn data_and_metadata_keys_are_independent_and_cannot_be_swapped() {
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]));
    let keys = ring.create().unwrap();
    // Metadata tags are stable, domain separated and differ from data sealing.
    let tag = keys.tag("tombstone", b"event-1");
    assert_eq!(tag, keys.tag("tombstone", b"event-1"));
    assert_ne!(tag, keys.tag("index", b"event-1"));
    // Swapping the wrapped blobs inside the key file makes it unopenable.
    let path = ring.key_path(keys.id());
    let mut file: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    let dek = file["dataKey"].clone();
    file["dataKey"] = file["metadataKey"].clone();
    file["metadataKey"] = dek;
    std::fs::write(&path, serde_json::to_vec(&file).unwrap()).unwrap();
    assert_eq!(ring.open(keys.id()).err(), Some(KeyError::Unrecoverable));
}

#[test]
fn key_ids_and_associated_data_are_validated_and_secrets_never_print() {
    assert!(Aad::new(1, owner_digest("A"), "", RecordKind::TeamBackup).is_err());
    assert!(Aad::new(
        1,
        owner_digest("A"),
        &"e".repeat(129),
        RecordKind::TeamBackup
    )
    .is_err());
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), FakeKeyProvider::new([7; 32]));
    let keys = ring.create().unwrap();
    let printed = format!("{keys:?}");
    assert!(printed.contains("redacted") && !printed.contains("dataKey"));
    assert!(ring.open_id_str("../escape").is_err());
}
