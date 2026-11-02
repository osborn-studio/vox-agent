//! T07b: the DPAPI provider wraps a key for the current user only and binds the
//! wrapping to the caller's context bytes. Windows only: elsewhere this file
//! compiles to an empty test binary.
#![cfg(windows)]

use vox_agent_desktop_tauri_lib::context_keys::dpapi::DpapiKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::{
    owner_digest, Aad, KeyError, KeyProvider, KeyRing, ProviderKind, RecordKind,
};

#[test]
fn wrap_unwrap_round_trip_and_context_binding() {
    let provider = DpapiKeyProvider::new();
    assert_eq!(provider.kind(), ProviderKind::DpapiUser);
    let key = [0x5a_u8; 32];
    let wrapped = provider.wrap(b"ctx-a", &key).unwrap();
    assert_ne!(&wrapped[..], &key[..]);
    assert!(
        !wrapped.windows(key.len()).any(|w| w == key),
        "the plaintext key never appears in the blob"
    );
    assert_eq!(&provider.unwrap(b"ctx-a", &wrapped).unwrap()[..], &key[..]);
    assert_eq!(
        provider.unwrap(b"ctx-b", &wrapped).unwrap_err(),
        KeyError::Unrecoverable
    );
}

#[test]
fn tampered_blob_is_unrecoverable_not_a_panic() {
    let provider = DpapiKeyProvider::new();
    let mut wrapped = provider.wrap(b"ctx", &[1_u8; 32]).unwrap();
    let last = wrapped.len() - 1;
    wrapped[last] ^= 0xff;
    assert_eq!(
        provider.unwrap(b"ctx", &wrapped).unwrap_err(),
        KeyError::Unrecoverable
    );
    assert_eq!(
        provider.unwrap(b"ctx", b"").unwrap_err(),
        KeyError::Unrecoverable
    );
    assert_eq!(
        provider.unwrap(b"ctx", &[0u8; 64]).unwrap_err(),
        KeyError::Unrecoverable
    );
}

#[test]
fn wrap_rejects_empty_and_oversized_input() {
    let provider = DpapiKeyProvider::new();
    assert_eq!(
        provider.wrap(b"ctx", b"").unwrap_err(),
        KeyError::InvalidInput
    );
    assert_eq!(
        provider.wrap(b"ctx", &vec![7u8; 4097]).unwrap_err(),
        KeyError::InvalidInput
    );
}

#[test]
fn key_ring_creates_and_reopens_a_dpapi_key_set() {
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), DpapiKeyProvider::new());
    let created = ring.create().unwrap();
    let reopened = ring.open(created.id()).unwrap();
    let aad = Aad::new(1, owner_digest("scope"), "evt_1", RecordKind::TeamBackup).unwrap();
    let sealed = created.seal(&aad, b"record").unwrap();
    assert_eq!(&reopened.open(&aad, &sealed).unwrap()[..], b"record");
    assert!(ring
        .key_path(created.id())
        .to_string_lossy()
        .ends_with(".dpapi"));
    let file = std::fs::read_to_string(ring.key_path(created.id())).unwrap();
    assert!(file.contains("\"provider\":\"dpapi-user\""), "{file}");
}
