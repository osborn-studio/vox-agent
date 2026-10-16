//! Context journal keys (T07a).
//!
//! Each key set has a random 256-bit data key (DEK, seals journal records with
//! XChaCha20-Poly1305) and an independent 256-bit metadata key (HMAC-SHA256
//! tags for indexes and tombstones, so they stay verifiable without the DEK).
//! Both are wrapped separately by a `KeyProvider` and stored in
//! `<dir>/<keyId>.<provider>`; plaintext keys never touch disk.
//!
//! * A key file is created with a fresh id and never overwritten.
//! * A provider only opens key files that name it, so a test-only fake can
//!   never be mistaken for DPAPI (and vice versa).
//! * `Unavailable` (provider cannot run now) and `Unrecoverable` (the key is
//!   gone or cannot be unwrapped) are distinct; callers isolate data on
//!   `Unrecoverable` and must never replace the key.
use chacha20poly1305::aead::{rand_core::RngCore, Aead, KeyInit, OsRng, Payload};
use chacha20poly1305::{XChaCha20Poly1305, XNonce};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Write;
use std::path::{Path, PathBuf};
use zeroize::Zeroizing;

#[cfg(any(test, debug_assertions))]
pub mod fake;

const KEY_FILE_VERSION: u32 = 1;
const NONCE_LEN: usize = 24;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderKind {
    /// Test-only fake. Never compiled into release builds.
    FakeTestOnly,
    /// Windows DPAPI, current-user scope (T07b).
    DpapiUser,
}

impl ProviderKind {
    fn label(self) -> &'static str {
        match self {
            Self::FakeTestOnly => "fake-test-only",
            Self::DpapiUser => "dpapi-user",
        }
    }
    fn extension(self) -> &'static str {
        match self {
            Self::FakeTestOnly => "fake-test-only",
            Self::DpapiUser => "dpapi",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyState {
    Ready,
    Unavailable,
    Unrecoverable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyError {
    /// The provider cannot unwrap right now (e.g. locked profile); retry later.
    Unavailable,
    /// The key file is missing, corrupt or cannot be unwrapped by this user.
    Unrecoverable,
    /// The key file belongs to a different provider.
    ProviderMismatch,
    InvalidId,
    InvalidInput,
    /// A sealed record failed authentication or could not be produced.
    RecordRejected,
    Io,
}

/// Wraps and unwraps key material. `context` is bound into the wrapping
/// (AEAD associated data / DPAPI entropy) so blobs cannot be swapped.
pub trait KeyProvider {
    fn kind(&self) -> ProviderKind;
    fn wrap(&self, context: &[u8], key: &[u8]) -> Result<Vec<u8>, KeyError>;
    fn unwrap(&self, context: &[u8], wrapped: &[u8]) -> Result<Zeroizing<Vec<u8>>, KeyError>;
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct KeyId(String);

impl KeyId {
    pub fn parse(value: &str) -> Result<Self, KeyError> {
        if value.len() == 32
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            Ok(Self(value.to_string()))
        } else {
            Err(KeyError::InvalidId)
        }
    }
    fn random() -> Self {
        let mut bytes = [0u8; 16];
        OsRng.fill_bytes(&mut bytes);
        Self(bytes.iter().map(|b| format!("{b:02x}")).collect())
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Owner namespace digest for a `PrincipalKey::scope()` string.
pub fn owner_digest(scope: &str) -> [u8; 32] {
    let mut hash = Sha256::new();
    hash.update(b"vox-context-owner-v1\0");
    hash.update(scope.as_bytes());
    hash.finalize().into()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecordKind {
    TeamBackup = 1,
    PrivateMutation = 2,
    Tombstone = 3,
}

/// Associated data bound into every sealed record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Aad(Vec<u8>);

impl Aad {
    pub fn new(
        schema: u16,
        owner: [u8; 32],
        event_id: &str,
        kind: RecordKind,
    ) -> Result<Self, KeyError> {
        if event_id.is_empty()
            || event_id.len() > 128
            || !event_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err(KeyError::InvalidInput);
        }
        let mut bytes = b"vox-context-aad-v1\0".to_vec();
        bytes.extend_from_slice(&schema.to_be_bytes());
        bytes.extend_from_slice(&owner);
        bytes.push(kind as u8);
        bytes.extend_from_slice(&(event_id.len() as u16).to_be_bytes());
        bytes.extend_from_slice(event_id.as_bytes());
        Ok(Self(bytes))
    }
}

/// Unwrapped keys for one key id. Zeroized on drop; `Debug` is redacted.
pub struct KeySet {
    id: KeyId,
    data: Zeroizing<[u8; 32]>,
    metadata: Zeroizing<[u8; 32]>,
}

impl std::fmt::Debug for KeySet {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("KeySet")
            .field("id", &self.id)
            .field("keys", &"<redacted>")
            .finish()
    }
}

impl KeySet {
    pub fn id(&self) -> &KeyId {
        &self.id
    }
    /// `nonce (24 bytes) || ciphertext+tag`, with a fresh random nonce.
    pub fn seal(&self, aad: &Aad, plaintext: &[u8]) -> Result<Vec<u8>, KeyError> {
        let cipher = XChaCha20Poly1305::new((&*self.data).into());
        let mut nonce = [0u8; NONCE_LEN];
        OsRng.fill_bytes(&mut nonce);
        let sealed = cipher
            .encrypt(
                XNonce::from_slice(&nonce),
                Payload {
                    msg: plaintext,
                    aad: &aad.0,
                },
            )
            .map_err(|_| KeyError::RecordRejected)?;
        let mut out = nonce.to_vec();
        out.extend_from_slice(&sealed);
        Ok(out)
    }
    pub fn open(&self, aad: &Aad, sealed: &[u8]) -> Result<Zeroizing<Vec<u8>>, KeyError> {
        if sealed.len() < NONCE_LEN + 16 {
            return Err(KeyError::RecordRejected);
        }
        let (nonce, body) = sealed.split_at(NONCE_LEN);
        XChaCha20Poly1305::new((&*self.data).into())
            .decrypt(
                XNonce::from_slice(nonce),
                Payload {
                    msg: body,
                    aad: &aad.0,
                },
            )
            .map(Zeroizing::new)
            .map_err(|_| KeyError::RecordRejected)
    }
    /// HMAC-SHA256 under the metadata key, domain separated by `label`.
    pub fn tag(&self, label: &str, data: &[u8]) -> [u8; 32] {
        self.tag_parts(label, &[data])
    }
    /// Like `tag`, over length-prefixed parts, so callers never concatenate
    /// sensitive buffers. (`tag` is the single-part case of the same encoding.)
    pub fn tag_parts(&self, label: &str, parts: &[&[u8]]) -> [u8; 32] {
        let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(&*self.metadata).expect("32-byte key");
        mac.update(b"vox-context-tag-v1\0");
        mac.update(&(label.len() as u32).to_be_bytes());
        mac.update(label.as_bytes());
        for part in parts {
            mac.update(&(part.len() as u64).to_be_bytes());
            mac.update(part);
        }
        mac.finalize().into_bytes().into()
    }
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct KeyFile {
    v: u32,
    provider: String,
    key_id: String,
    data_key: String,
    metadata_key: String,
}

fn wrap_context(kind: ProviderKind, id: &KeyId, role: &str) -> Vec<u8> {
    format!("vox-context-key-v1\0{}\0{}\0{role}", kind.label(), id.0).into_bytes()
}

pub struct KeyRing<P: KeyProvider> {
    dir: PathBuf,
    provider: P,
}

impl<P: KeyProvider> KeyRing<P> {
    pub fn new(dir: &Path, provider: P) -> Self {
        Self {
            dir: dir.to_path_buf(),
            provider,
        }
    }
    pub fn provider_kind(&self) -> ProviderKind {
        self.provider.kind()
    }
    pub fn key_path(&self, id: &KeyId) -> PathBuf {
        self.dir
            .join(format!("{}.{}", id.0, self.provider.kind().extension()))
    }

    /// Generate, wrap and durably store a new key set under a fresh id.
    pub fn create(&self) -> Result<KeySet, KeyError> {
        let kind = self.provider.kind();
        let id = KeyId::random();
        let mut data = Zeroizing::new([0u8; 32]);
        let mut metadata = Zeroizing::new([0u8; 32]);
        OsRng.fill_bytes(&mut *data);
        OsRng.fill_bytes(&mut *metadata);
        use base64::Engine;
        let b64 = base64::engine::general_purpose::STANDARD;
        let file = KeyFile {
            v: KEY_FILE_VERSION,
            provider: kind.label().to_string(),
            key_id: id.0.clone(),
            data_key: b64.encode(
                self.provider
                    .wrap(&wrap_context(kind, &id, "data"), &*data)?,
            ),
            metadata_key: b64.encode(
                self.provider
                    .wrap(&wrap_context(kind, &id, "metadata"), &*metadata)?,
            ),
        };
        let bytes = serde_json::to_vec(&file).map_err(|_| KeyError::Io)?;
        write_new_durably(&self.dir, &self.key_path(&id), &bytes)?;
        Ok(KeySet { id, data, metadata })
    }

    pub fn open(&self, id: &KeyId) -> Result<KeySet, KeyError> {
        self.open_path(&self.key_path(id))
    }

    pub fn open_id_str(&self, id: &str) -> Result<KeySet, KeyError> {
        self.open(&KeyId::parse(id)?)
    }

    pub fn open_path(&self, path: &Path) -> Result<KeySet, KeyError> {
        let kind = self.provider.kind();
        if path.extension().and_then(|e| e.to_str()) != Some(kind.extension()) {
            return Err(KeyError::ProviderMismatch);
        }
        let bytes = match std::fs::read(path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(KeyError::Unrecoverable)
            }
            Err(_) => return Err(KeyError::Io),
        };
        let file: KeyFile = serde_json::from_slice(&bytes).map_err(|_| KeyError::Unrecoverable)?;
        if file.provider != kind.label() {
            return Err(KeyError::ProviderMismatch);
        }
        let id = KeyId::parse(&file.key_id).map_err(|_| KeyError::Unrecoverable)?;
        let stem = path.file_stem().and_then(|s| s.to_str());
        if file.v != KEY_FILE_VERSION || stem != Some(id.as_str()) {
            return Err(KeyError::Unrecoverable);
        }
        use base64::Engine;
        let b64 = base64::engine::general_purpose::STANDARD;
        let unwrap = |role: &str, value: &str| -> Result<Zeroizing<[u8; 32]>, KeyError> {
            let wrapped = b64.decode(value).map_err(|_| KeyError::Unrecoverable)?;
            let key = self
                .provider
                .unwrap(&wrap_context(kind, &id, role), &wrapped)?;
            if key.len() != 32 {
                return Err(KeyError::Unrecoverable);
            }
            let mut array = Zeroizing::new([0u8; 32]);
            array.copy_from_slice(&key);
            Ok(array)
        };
        let data = unwrap("data", &file.data_key)?;
        let metadata = unwrap("metadata", &file.metadata_key)?;
        Ok(KeySet { id, data, metadata })
    }

    pub fn state(&self, id: &KeyId) -> KeyState {
        match self.open(id) {
            Ok(_) => KeyState::Ready,
            Err(KeyError::Unavailable | KeyError::Io) => KeyState::Unavailable,
            Err(_) => KeyState::Unrecoverable,
        }
    }
}

/// Write `bytes` to a temporary file, flush it to disk, then link it into
/// place only if `path` does not exist yet, and sync the directory.
fn write_new_durably(dir: &Path, path: &Path, bytes: &[u8]) -> Result<(), KeyError> {
    std::fs::create_dir_all(dir).map_err(|_| KeyError::Io)?;
    let mut temp = tempfile::NamedTempFile::new_in(dir).map_err(|_| KeyError::Io)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temp.as_file()
            .set_permissions(std::fs::Permissions::from_mode(0o600))
            .map_err(|_| KeyError::Io)?;
    }
    temp.write_all(bytes).map_err(|_| KeyError::Io)?;
    temp.as_file().sync_all().map_err(|_| KeyError::Io)?;
    std::fs::hard_link(temp.path(), path).map_err(|_| KeyError::Io)?;
    drop(temp);
    sync_dir(dir).map_err(|_| KeyError::Io)
}

/// Flush directory entries (new names) to disk. On Windows, NTFS metadata is
/// journaled and std offers no directory-handle sync; durability there is
/// established by Windows evidence (T07b), not assumed from this code.
pub(crate) fn sync_dir(dir: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    std::fs::File::open(dir)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = dir;
    Ok(())
}
