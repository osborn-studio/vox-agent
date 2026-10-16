//! T42a: signed update manifest verification and attack fixtures.
//!
//! Every case signs a manifest with a test-only Ed25519 key (fixed seed; never
//! shipped) and runs it through the same verifier the updater will call.
use base64::Engine as _;
use ring::signature::{Ed25519KeyPair, KeyPair};
use serde_json::{json, Value};

use vox_agent_desktop_tauri_lib::updater_manifest::{
    verify_artifact, verify_manifest, CannotConfirm, CounterState, Outcome, Policy, Rejection,
    TrustedKey,
};

const NOW: u64 = 1_800_000_000;
const COMMIT: &str = "0123456789abcdef0123456789abcdef01234567";

fn key(seed: u8) -> Ed25519KeyPair {
    Ed25519KeyPair::from_seed_unchecked(&[seed; 32]).unwrap()
}

fn trusted(id: &str, seed: u8) -> TrustedKey {
    TrustedKey {
        key_id: id.into(),
        public_key: key(seed).public_key().as_ref().try_into().unwrap(),
        not_after: None,
        revoked: false,
    }
}

fn policy() -> Policy {
    Policy {
        product: "vox-agent".into(),
        platform: "windows".into(),
        arch: "x86_64".into(),
        channel: "preview".into(),
        allowed_hosts: vec!["github.com".into(), "objects.githubusercontent.com".into()],
        installed_version: semver::Version::parse("4.2.9").unwrap(),
        local_schema: 3,
        keys: vec![trusted("release-2026", 7)],
        max_clock_skew_secs: 300,
    }
}

fn payload() -> Value {
    json!({
        "product": "vox-agent",
        "version": "4.3.0",
        "buildId": "4.3.0+20261101.1",
        "sourceCommit": COMMIT,
        "platform": "windows",
        "arch": "x86_64",
        "channel": "preview",
        "artifactUrl": "https://github.com/osborn-studio/osvox/releases/download/v4.3.0/VoxAgent-v4.3.0-windows-x64-setup.exe",
        "artifactSha256": "a".repeat(64),
        "artifactSize": 123_456_789u64,
        "minSchema": 3,
        "keyId": "release-2026",
        "issuedAt": NOW - 60,
        "expiresAt": NOW + 7 * 86_400,
        "counter": 42u64,
    })
}

fn sign_with(payload: &Value, seed: u8, envelope_key: &str) -> Vec<u8> {
    let bytes = serde_json::to_vec(payload).unwrap();
    let signature = key(seed).sign(&bytes);
    let b64 = base64::engine::general_purpose::URL_SAFE_NO_PAD;
    serde_json::to_vec(&json!({
        "format": "vox-update-manifest-v1",
        "keyId": envelope_key,
        "payload": b64.encode(&bytes),
        "signature": b64.encode(signature.as_ref()),
    }))
    .unwrap()
}

fn sign(payload: &Value) -> Vec<u8> {
    sign_with(payload, 7, "release-2026")
}

fn edit(f: impl FnOnce(&mut Value)) -> Vec<u8> {
    let mut p = payload();
    f(&mut p);
    sign(&p)
}

fn fresh() -> CounterState {
    CounterState::new(41)
}

fn rejected(bytes: &[u8]) -> Rejection {
    match verify_manifest(bytes, &policy(), &fresh(), NOW) {
        Outcome::Rejected(reason) => reason,
        other => panic!("expected a rejection, got {other:?}"),
    }
}

#[test]
fn a_valid_signed_manifest_offers_the_update() {
    match verify_manifest(&sign(&payload()), &policy(), &fresh(), NOW) {
        Outcome::UpdateAvailable(m) => {
            assert_eq!(m.version.to_string(), "4.3.0");
            assert_eq!(m.counter, 42);
            assert_eq!(m.artifact_size, 123_456_789);
            assert_eq!(m.digest.len(), 64);
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn bad_signatures_and_tampering_are_rejected() {
    // Signed by an untrusted key, or a valid signature over other bytes.
    assert_eq!(
        rejected(&sign_with(&payload(), 9, "release-2026")),
        Rejection::BadSignature
    );
    let mut envelope: Value = serde_json::from_slice(&sign(&payload())).unwrap();
    let b64 = base64::engine::general_purpose::URL_SAFE_NO_PAD;
    let mut bytes = b64.decode(envelope["payload"].as_str().unwrap()).unwrap();
    let at = bytes.iter().position(|&b| b == b'3').unwrap();
    bytes[at] = b'9'; // "4.3.0" -> "4.9.0" under the old signature
    envelope["payload"] = json!(b64.encode(&bytes));
    assert_eq!(
        rejected(&serde_json::to_vec(&envelope).unwrap()),
        Rejection::BadSignature
    );
    // Envelope key id disagreeing with the signed payload, unknown or revoked keys.
    assert_eq!(
        rejected(&sign_with(&payload(), 7, "release-2027")),
        Rejection::UnknownKey
    );
    assert_eq!(
        rejected(&edit(|p| p["keyId"] = json!("release-2027"))),
        Rejection::KeyMismatch
    );
    let mut revoked = policy();
    revoked.keys[0].revoked = true;
    assert_eq!(
        verify_manifest(&sign(&payload()), &revoked, &fresh(), NOW),
        Outcome::Rejected(Rejection::UnknownKey)
    );
}

#[test]
fn key_rotation_accepts_the_new_key_and_retires_the_old_one() {
    let mut rotated = policy();
    rotated.keys[0].not_after = Some(NOW - 3_600);
    rotated.keys.push(trusted("release-2027", 8));
    // Old key, manifest issued after it was retired: refused.
    assert_eq!(
        verify_manifest(&sign(&payload()), &rotated, &fresh(), NOW),
        Outcome::Rejected(Rejection::KeyExpired)
    );
    let mut p = payload();
    p["keyId"] = json!("release-2027");
    assert!(matches!(
        verify_manifest(&sign_with(&p, 8, "release-2027"), &rotated, &fresh(), NOW),
        Outcome::UpdateAvailable(_)
    ));
}

#[test]
fn another_platform_arch_channel_or_product_is_rejected() {
    assert_eq!(
        rejected(&edit(|p| p["arch"] = json!("aarch64"))),
        Rejection::WrongTarget
    );
    assert_eq!(
        rejected(&edit(|p| p["platform"] = json!("macos"))),
        Rejection::WrongTarget
    );
    assert_eq!(
        rejected(&edit(|p| p["channel"] = json!("stable"))),
        Rejection::WrongTarget
    );
    assert_eq!(
        rejected(&edit(|p| p["product"] = json!("voxlog"))),
        Rejection::WrongTarget
    );
}

#[test]
fn downgrades_and_same_version_are_never_offered() {
    assert_eq!(
        rejected(&edit(|p| p["version"] = json!("4.2.8"))),
        Rejection::Downgrade
    );
    assert_eq!(
        verify_manifest(
            &edit(|p| p["version"] = json!("4.2.9")),
            &policy(),
            &fresh(),
            NOW
        ),
        Outcome::UpToDate
    );
}

#[test]
fn expired_future_and_replayed_manifests_cannot_confirm_the_latest_version() {
    let check =
        |bytes: Vec<u8>, state: &CounterState| verify_manifest(&bytes, &policy(), state, NOW);
    assert_eq!(
        check(edit(|p| p["expiresAt"] = json!(NOW - 1)), &fresh()),
        Outcome::CannotConfirm(CannotConfirm::Expired)
    );
    assert_eq!(
        check(edit(|p| p["issuedAt"] = json!(NOW + 3_600)), &fresh()),
        Outcome::CannotConfirm(CannotConfirm::ClockSkew)
    );
    // A freeze/replay: an older, validly signed manifest after a newer one was seen.
    let mut seen = fresh();
    seen.observe(50, &"b".repeat(64));
    assert_eq!(
        check(sign(&payload()), &seen),
        Outcome::CannotConfirm(CannotConfirm::Replayed)
    );
    // An old counter at or below the installed release is stale, not "up to date".
    assert_eq!(
        check(edit(|p| p["counter"] = json!(41)), &fresh()),
        Outcome::CannotConfirm(CannotConfirm::Replayed)
    );
}

#[test]
fn the_same_counter_with_different_content_is_equivocation() {
    let bytes = sign(&payload());
    let first = match verify_manifest(&bytes, &policy(), &fresh(), NOW) {
        Outcome::UpdateAvailable(m) => m,
        other => panic!("{other:?}"),
    };
    let mut state = fresh();
    state.observe(first.counter, &first.digest);
    // Same bytes again: an idempotent retry.
    assert!(matches!(
        verify_manifest(&bytes, &policy(), &state, NOW),
        Outcome::UpdateAvailable(_)
    ));
    // Same counter, different signed content.
    assert_eq!(
        verify_manifest(
            &edit(|p| p["buildId"] = json!("4.3.0+other")),
            &policy(),
            &state,
            NOW
        ),
        Outcome::Rejected(Rejection::CounterConflict)
    );
}

#[test]
fn counter_states_stay_separate_and_retries_are_idempotent() {
    let mut state = CounterState::new(41);
    let digest = "c".repeat(64);
    state.observe(42, &digest);
    assert_eq!(state.highest_observed(), Some((42, digest.as_str())));
    assert_eq!(state.installed(), 41, "observing is not installing");
    assert!(state.begin_install(42, &digest).is_ok());
    assert!(
        state.begin_install(42, &digest).is_ok(),
        "same counter + digest may retry"
    );
    assert!(state.begin_install(42, &"d".repeat(64)).is_err());
    assert!(
        state.begin_install(43, &digest).is_err(),
        "only an observed manifest may install"
    );
    assert_eq!(state.pending(), Some((42, digest.as_str())));
    assert!(
        state.confirm_installed(41).is_err(),
        "confirm only the pending counter"
    );
    assert!(state.confirm_installed(42).is_ok());
    assert_eq!((state.installed(), state.pending()), (42, None));
    // A lower observation never lowers the high-water mark.
    state.observe(40, &digest);
    assert_eq!(state.highest_observed().unwrap().0, 42);
}

#[test]
fn artifact_urls_must_be_https_on_the_allowlist() {
    for url in [
        "http://github.com/osborn-studio/osvox/releases/download/v4.3.0/x.exe",
        "https://evil.example/x.exe",
        "https://github.com.evil.example/x.exe",
        "https://user:pw@github.com/x.exe",
        "https://github.com:8443/x.exe",
        "https://github.com:443/x.exe",
        "https://GitHub.com/x.exe",
        "https://github.com./x.exe",
        "https://github.com/x.exe#frag",
        "file:///C:/x.exe",
    ] {
        assert_eq!(
            rejected(&edit(|p| p["artifactUrl"] = json!(url))),
            Rejection::BadArtifactUrl,
            "{url}"
        );
    }
}

#[test]
fn malformed_or_incompatible_payloads_are_rejected() {
    for (field, value) in [
        ("sourceCommit", json!("not-a-commit")),
        ("artifactSha256", json!("ABC")),
        ("artifactSize", json!(0)),
        ("artifactSize", json!(5_000_000_000u64)),
        ("version", json!("v4.3")),
        ("buildId", json!("")),
    ] {
        assert_eq!(
            rejected(&edit(|p| p[field] = value.clone())),
            Rejection::Malformed,
            "{field}"
        );
    }
    assert_eq!(
        rejected(&edit(|p| p["extra"] = json!("x"))),
        Rejection::Malformed,
        "unknown field"
    );
    assert_eq!(
        rejected(&edit(|p| p["minSchema"] = json!(4))),
        Rejection::IncompatibleSchema
    );
    assert_eq!(rejected(b"not json"), Rejection::Malformed);
    assert_eq!(
        rejected(&vec![b' '; 70_000]),
        Rejection::Malformed,
        "oversized"
    );
    let mut envelope: Value = serde_json::from_slice(&sign(&payload())).unwrap();
    envelope["format"] = json!("vox-update-manifest-v0");
    assert_eq!(
        rejected(&serde_json::to_vec(&envelope).unwrap()),
        Rejection::Malformed
    );
}

#[test]
fn the_downloaded_artifact_must_match_the_manifest() {
    let body = b"installer bytes".to_vec();
    let digest = {
        use sha2::Digest;
        sha2::Sha256::digest(&body)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    };
    let mut p = payload();
    p["artifactSha256"] = json!(digest);
    p["artifactSize"] = json!(body.len());
    let manifest = match verify_manifest(&sign(&p), &policy(), &fresh(), NOW) {
        Outcome::UpdateAvailable(m) => m,
        other => panic!("{other:?}"),
    };
    assert!(verify_artifact(&manifest, &mut body.as_slice()).is_ok());
    let mut tampered = body.clone();
    tampered[0] ^= 1;
    assert!(verify_artifact(&manifest, &mut tampered.as_slice()).is_err());
    let mut longer = body.clone();
    longer.push(0);
    assert!(verify_artifact(&manifest, &mut longer.as_slice()).is_err());
}

#[test]
fn no_window_may_install_updates() {
    for capability in ["capabilities/main.json", "capabilities/voice-hud.json"] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(capability);
        if let Ok(text) = std::fs::read_to_string(path) {
            assert!(
                !text.contains("updater:"),
                "{capability} must not grant updater permissions"
            );
        }
    }
}
