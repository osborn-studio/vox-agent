//! Signed update manifest verification (T42a, plan C-28). Pure library: no
//! network, no installer. The updater (T42b) calls it before downloading and
//! again on the downloaded file; no WebView may install anything.
//!
//! * Envelope `vox-update-manifest-v1`: base64url payload bytes plus an
//!   Ed25519 signature over exactly those bytes, so no JSON
//!   canonicalization is involved. The signed payload repeats the key id.
//! * Keys rotate by id; a key can be retired (`not_after`, compared with
//!   `issuedAt`) or revoked. Private keys are never shipped.
//! * Expired, future-dated or replayed manifests are `CannotConfirm`: the
//!   app must say it cannot confirm the latest version, never "up to date",
//!   and keeps working offline.
//! * The normal channel never downgrades. Rollback needs a separate signed
//!   policy entry and an explicit user action (T42b).
//! * Counters: the highest observed, the installed and the pending release
//!   are kept apart. Re-installing the same counter and digest is idempotent;
//!   the same counter with another digest is equivocation.
use base64::Engine as _;
use ring::signature::{UnparsedPublicKey, ED25519};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::io::Read;

pub const FORMAT: &str = "vox-update-manifest-v1";
const MAX_ENVELOPE_BYTES: usize = 65_536;
const MAX_ARTIFACT_BYTES: u64 = 4 * 1024 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct TrustedKey {
    pub key_id: String,
    pub public_key: [u8; 32],
    /// Manifests issued after this time (unix seconds) are refused.
    pub not_after: Option<u64>,
    pub revoked: bool,
}

/// What this installation accepts.
#[derive(Debug, Clone)]
pub struct Policy {
    pub product: String,
    pub platform: String,
    pub arch: String,
    pub channel: String,
    pub allowed_hosts: Vec<String>,
    pub installed_version: semver::Version,
    /// The local data schema; the new release must be able to read it.
    pub local_schema: u32,
    pub keys: Vec<TrustedKey>,
    pub max_clock_skew_secs: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedManifest {
    pub version: semver::Version,
    pub build_id: String,
    pub source_commit: String,
    pub artifact_url: String,
    pub artifact_sha256: String,
    pub artifact_size: u64,
    pub counter: u64,
    /// SHA-256 of the signed payload bytes: identifies this exact manifest.
    pub digest: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CannotConfirm {
    Expired,
    ClockSkew,
    /// An older manifest than one already seen (freeze or replay).
    Replayed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rejection {
    Malformed,
    UnknownKey,
    KeyMismatch,
    KeyExpired,
    BadSignature,
    WrongTarget,
    BadArtifactUrl,
    IncompatibleSchema,
    Downgrade,
    /// Same counter as an observed manifest, different signed content.
    CounterConflict,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    UpdateAvailable(ValidatedManifest),
    UpToDate,
    CannotConfirm(CannotConfirm),
    Rejected(Rejection),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    format: String,
    #[serde(rename = "keyId")]
    key_id: String,
    payload: String,
    signature: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Payload {
    product: String,
    version: String,
    build_id: String,
    source_commit: String,
    platform: String,
    arch: String,
    channel: String,
    artifact_url: String,
    artifact_sha256: String,
    artifact_size: u64,
    min_schema: u32,
    key_id: String,
    issued_at: u64,
    expires_at: u64,
    counter: u64,
}

/// High-water marks kept by the updater across runs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CounterState {
    highest: Option<(u64, String)>,
    installed: u64,
    pending: Option<(u64, String)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CounterError {
    NotObserved,
    Conflict,
    NotPending,
}

impl CounterState {
    pub fn new(installed: u64) -> Self {
        Self {
            highest: None,
            installed,
            pending: None,
        }
    }

    pub fn highest_observed(&self) -> Option<(u64, &str)> {
        self.highest.as_ref().map(|(c, d)| (*c, d.as_str()))
    }

    pub fn installed(&self) -> u64 {
        self.installed
    }

    pub fn pending(&self) -> Option<(u64, &str)> {
        self.pending.as_ref().map(|(c, d)| (*c, d.as_str()))
    }

    /// Record a verified manifest. The high-water mark never goes down.
    pub fn observe(&mut self, counter: u64, digest: &str) {
        if self.highest.as_ref().is_none_or(|(c, _)| counter > *c) {
            self.highest = Some((counter, digest.to_string()));
        }
    }

    /// Start installing the observed manifest `counter`/`digest`. Repeating
    /// the same pair is allowed (a retry); anything else is refused.
    pub fn begin_install(&mut self, counter: u64, digest: &str) -> Result<(), CounterError> {
        match &self.highest {
            Some((c, d)) if *c == counter && d == digest => {}
            Some((c, _)) if *c == counter => return Err(CounterError::Conflict),
            _ => return Err(CounterError::NotObserved),
        }
        match &self.pending {
            Some((c, d)) if *c == counter && d != digest => Err(CounterError::Conflict),
            _ => {
                self.pending = Some((counter, digest.to_string()));
                Ok(())
            }
        }
    }

    /// The pending release started successfully.
    pub fn confirm_installed(&mut self, counter: u64) -> Result<(), CounterError> {
        match &self.pending {
            Some((c, _)) if *c == counter => {
                self.installed = counter;
                self.pending = None;
                Ok(())
            }
            _ => Err(CounterError::NotPending),
        }
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn lower_hex(value: &str, len: usize) -> bool {
    value.len() == len
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn label(value: &str, max: usize) -> bool {
    !value.is_empty() && value.len() <= max && !value.chars().any(char::is_control)
}

fn artifact_url_allowed(value: &str, policy: &Policy) -> bool {
    // The URL parser drops a default port (":443"), so check the authority as
    // written: it must be exactly an allowed host, with no port or userinfo.
    let Some(rest) = value.strip_prefix("https://") else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if !policy
        .allowed_hosts
        .iter()
        .any(|allowed| allowed == authority)
    {
        return false;
    }
    let Ok(url) = reqwest::Url::parse(value) else {
        return false;
    };
    url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none()
        && url.fragment().is_none()
        && url
            .host_str()
            .is_some_and(|host| policy.allowed_hosts.iter().any(|allowed| allowed == host))
}

/// Verify a downloaded manifest envelope for this installation at `now`
/// (unix seconds).
pub fn verify_manifest(bytes: &[u8], policy: &Policy, state: &CounterState, now: u64) -> Outcome {
    match verify(bytes, policy, state, now) {
        Ok(outcome) => outcome,
        Err(rejection) => Outcome::Rejected(rejection),
    }
}

fn verify(
    bytes: &[u8],
    policy: &Policy,
    state: &CounterState,
    now: u64,
) -> Result<Outcome, Rejection> {
    if bytes.len() > MAX_ENVELOPE_BYTES {
        return Err(Rejection::Malformed);
    }
    let envelope: Envelope = serde_json::from_slice(bytes).map_err(|_| Rejection::Malformed)?;
    if envelope.format != FORMAT {
        return Err(Rejection::Malformed);
    }
    let b64 = base64::engine::general_purpose::URL_SAFE_NO_PAD;
    let payload_bytes = b64
        .decode(&envelope.payload)
        .map_err(|_| Rejection::Malformed)?;
    let signature = b64
        .decode(&envelope.signature)
        .map_err(|_| Rejection::Malformed)?;
    let key = policy
        .keys
        .iter()
        .find(|k| k.key_id == envelope.key_id && !k.revoked)
        .ok_or(Rejection::UnknownKey)?;
    UnparsedPublicKey::new(&ED25519, key.public_key)
        .verify(&payload_bytes, &signature)
        .map_err(|_| Rejection::BadSignature)?;
    // Only signed bytes are interpreted from here on.
    let p: Payload = serde_json::from_slice(&payload_bytes).map_err(|_| Rejection::Malformed)?;
    if p.key_id != envelope.key_id {
        return Err(Rejection::KeyMismatch);
    }
    if key.not_after.is_some_and(|limit| p.issued_at > limit) {
        return Err(Rejection::KeyExpired);
    }
    let version = semver::Version::parse(&p.version).map_err(|_| Rejection::Malformed)?;
    if !label(&p.build_id, 128)
        || !lower_hex(&p.source_commit, 40)
        || !lower_hex(&p.artifact_sha256, 64)
        || p.artifact_size == 0
        || p.artifact_size > MAX_ARTIFACT_BYTES
        || p.expires_at <= p.issued_at
    {
        return Err(Rejection::Malformed);
    }
    if (
        p.product.as_str(),
        p.platform.as_str(),
        p.arch.as_str(),
        p.channel.as_str(),
    ) != (
        policy.product.as_str(),
        policy.platform.as_str(),
        policy.arch.as_str(),
        policy.channel.as_str(),
    ) {
        return Err(Rejection::WrongTarget);
    }
    if !artifact_url_allowed(&p.artifact_url, policy) {
        return Err(Rejection::BadArtifactUrl);
    }
    if p.min_schema > policy.local_schema {
        return Err(Rejection::IncompatibleSchema);
    }
    if now > p.expires_at {
        return Ok(Outcome::CannotConfirm(CannotConfirm::Expired));
    }
    if p.issued_at > now.saturating_add(policy.max_clock_skew_secs) {
        return Ok(Outcome::CannotConfirm(CannotConfirm::ClockSkew));
    }
    let digest = hex(&Sha256::digest(&payload_bytes));
    match state.highest_observed() {
        Some((c, _)) if p.counter < c => {
            return Ok(Outcome::CannotConfirm(CannotConfirm::Replayed))
        }
        Some((c, d)) if p.counter == c && d != digest => return Err(Rejection::CounterConflict),
        _ => {}
    }
    if p.counter <= state.installed() {
        return Ok(Outcome::CannotConfirm(CannotConfirm::Replayed));
    }
    if version < policy.installed_version {
        return Err(Rejection::Downgrade);
    }
    if version == policy.installed_version {
        return Ok(Outcome::UpToDate);
    }
    Ok(Outcome::UpdateAvailable(ValidatedManifest {
        version,
        build_id: p.build_id,
        source_commit: p.source_commit,
        artifact_url: p.artifact_url,
        artifact_sha256: p.artifact_sha256,
        artifact_size: p.artifact_size,
        counter: p.counter,
        digest,
    }))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArtifactError {
    Io,
    SizeMismatch,
    DigestMismatch,
}

/// Check the staged download against the manifest: exact size and SHA-256 of
/// the same bytes that will be installed. Reads at most `size + 1` bytes.
pub fn verify_artifact(
    manifest: &ValidatedManifest,
    reader: &mut impl Read,
) -> Result<(), ArtifactError> {
    let mut hasher = Sha256::new();
    let mut total: u64 = 0;
    let mut buffer = [0u8; 64 * 1024];
    let mut limited = reader.take(manifest.artifact_size + 1);
    loop {
        let n = limited.read(&mut buffer).map_err(|_| ArtifactError::Io)?;
        if n == 0 {
            break;
        }
        total += n as u64;
        hasher.update(&buffer[..n]);
    }
    if total != manifest.artifact_size {
        return Err(ArtifactError::SizeMismatch);
    }
    if hex(&hasher.finalize()) != manifest.artifact_sha256 {
        return Err(ArtifactError::DigestMismatch);
    }
    Ok(())
}
