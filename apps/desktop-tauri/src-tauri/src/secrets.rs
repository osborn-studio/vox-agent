use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

const KEYRING_SERVICE: &str = "com.osborn.voxagent.desktop";
const LEGACY_KEYRING_SERVICE: &str = "com.voxlog2.desktop";
/// Product limit on a stored secret, in UTF-8 bytes.
pub const MAX_SECRET_BYTES: usize = 2048;
/// Windows Credential Manager rejects blobs above 2,560 bytes
/// (CRED_MAX_CREDENTIAL_BLOB_SIZE) and keyring stores the value as UTF-16LE,
/// so the same value must also fit in 1,280 UTF-16 code units. Enforced on
/// every platform so a secret accepted on macOS is never refused on Windows.
pub const MAX_SECRET_UTF16_UNITS: usize = 2560 / 2;

// Release builds are only supported where a native OS credential store is
// compiled in (keyring `apple-native` / `windows-native`). Anything else would
// silently use keyring's in-process mock and lose credentials on restart.
#[cfg(all(
    not(debug_assertions),
    not(any(target_os = "macos", target_os = "windows"))
))]
compile_error!(
    "release builds require a native OS credential store; this target is not a supported SKU"
);

/// Error class of a credential-store operation. Never carries secret content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SecretError {
    /// No credential stored under this account (not an error for callers).
    NoEntry,
    /// The store exists but refused access (locked keychain, denied, session not ready).
    Locked,
    /// Platform/backend failure (API error, mock backend, corrupt entry).
    Backend,
    /// More than one credential matched the lookup.
    Ambiguous,
    /// Value exceeds [`MAX_SECRET_BYTES`] or [`MAX_SECRET_UTF16_UNITS`].
    /// `bytes` is the UTF-8 length when known locally; `None` when the
    /// platform rejected the value without reporting its size.
    TooLarge { bytes: Option<usize> },
    /// Invalid account/service name or non-UTF8 stored value.
    Invalid,
}

impl std::fmt::Display for SecretError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SecretError::NoEntry => write!(f, "credential not found"),
            SecretError::Locked => write!(f, "credential store locked or access denied"),
            SecretError::Backend => write!(f, "credential store backend failure"),
            SecretError::Ambiguous => write!(f, "credential lookup ambiguous"),
            SecretError::TooLarge { bytes: Some(bytes) } => write!(
                f,
                "credential too large ({bytes} UTF-8 bytes; limit {MAX_SECRET_BYTES} bytes and {MAX_SECRET_UTF16_UNITS} UTF-16 units)"
            ),
            SecretError::TooLarge { bytes: None } => {
                write!(f, "credential rejected by the platform store as too large")
            }
            SecretError::Invalid => write!(f, "credential name or value invalid"),
        }
    }
}

pub fn classify(error: keyring::Error) -> SecretError {
    match error {
        keyring::Error::NoEntry => SecretError::NoEntry,
        keyring::Error::NoStorageAccess(_) => SecretError::Locked,
        keyring::Error::Ambiguous(_) => SecretError::Ambiguous,
        // keyring 3.6.3 (windows.rs validate_attributes) names the value
        // attribute "password encoded as UTF-16" or "secret"; other attributes
        // ("user", "target", "target alias", "comment") are naming errors.
        keyring::Error::TooLong(attribute, _)
            if attribute == "secret" || attribute.starts_with("password") =>
        {
            SecretError::TooLarge { bytes: None }
        }
        keyring::Error::BadEncoding(_)
        | keyring::Error::TooLong(_, _)
        | keyring::Error::Invalid(_, _) => SecretError::Invalid,
        _ => SecretError::Backend,
    }
}

pub fn validate_secret_size(value: &str) -> Result<(), SecretError> {
    let bytes = value.len();
    if bytes > MAX_SECRET_BYTES || value.encode_utf16().count() > MAX_SECRET_UTF16_UNITS {
        return Err(SecretError::TooLarge { bytes: Some(bytes) });
    }
    Ok(())
}

/// Refresh a cached credential slot from the store. On success the slot
/// mirrors the store (including removal). On a store failure the last known
/// value is kept and the error class is returned so callers can surface
/// "credential store unavailable" instead of "not configured".
pub fn refresh_slot(
    read: &dyn Fn(&str) -> Result<Option<String>, SecretError>,
    slot: &mut Option<String>,
    name: &str,
) -> Option<SecretError> {
    match read(name) {
        Ok(value) => {
            *slot = value;
            None
        }
        Err(error) => Some(error),
    }
}

/// Collect provider keys for engine hydration. Absent keys become JSON null
/// (meaning "not configured"); any unreadable key fails the whole collection
/// so a locked store can never be sent to the engine as "no key".
pub fn collect_provider_keys(
    read: &dyn Fn(&str) -> Result<Option<String>, SecretError>,
    names: &[&str],
) -> Result<serde_json::Map<String, serde_json::Value>, SecretError> {
    let mut keys = serde_json::Map::new();
    for name in names {
        let value = read(name)?.map_or(serde_json::Value::Null, serde_json::Value::String);
        keys.insert((*name).to_string(), value);
    }
    Ok(keys)
}

/// Native OS credential store (Keychain / Credential Manager) bound to one service name.
#[derive(Clone, Copy)]
pub struct NativeSecretStore {
    service: &'static str,
}

impl NativeSecretStore {
    pub const fn with_service(service: &'static str) -> Self {
        Self { service }
    }

    fn entry(&self, name: &str) -> Result<keyring::Entry, SecretError> {
        keyring::Entry::new(self.service, name).map_err(classify)
    }

    pub fn read(&self, name: &str) -> Result<Option<String>, SecretError> {
        match self.entry(name)?.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(classify(error)),
        }
    }

    pub fn write(&self, name: &str, value: &str) -> Result<(), SecretError> {
        validate_secret_size(value)?;
        self.entry(name)?.set_password(value).map_err(classify)
    }

    pub fn delete(&self, name: &str) -> Result<(), SecretError> {
        match self.entry(name)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(classify(error)),
        }
    }
}

impl Default for NativeSecretStore {
    fn default() -> Self {
        Self::with_service(KEYRING_SERVICE)
    }
}

impl SecretStore for NativeSecretStore {
    fn get(&self, name: &str) -> Result<Option<String>, String> {
        self.read(name).map_err(|error| error.to_string())
    }

    fn set(&self, name: &str, value: &str) -> Result<(), String> {
        self.write(name, value).map_err(|error| error.to_string())
    }
}
const LEGACY_SECRET_KEYS: [(&str, &str); 8] = [
    ("DASHSCOPE_API_KEY", "dashscope_key_us"),
    ("DASHSCOPE_API_KEY_CN", "dashscope_key_cn"),
    ("OPENAI_API_KEY", "openai_key"),
    ("SILICONFLOW_API_KEY", "siliconflow_key"),
    ("DOUBAO_API_KEY", "doubao_key"),
    ("ARK_API_KEY", "doubao_key"),
    ("ANTHROPIC_API_KEY", "anthropic_key"),
    ("VOXAGENT_LLM_API_KEY", "custom_llm_key"),
];

pub trait SecretStore {
    fn get(&self, name: &str) -> Result<Option<String>, String>;
    fn set(&self, name: &str, value: &str) -> Result<(), String>;
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct MigrationReport {
    pub files_scrubbed: usize,
    pub secrets_migrated: usize,
}

pub fn migrate_legacy_env_files(
    store: &dyn SecretStore,
    paths: &[PathBuf],
) -> Result<MigrationReport, String> {
    let mut report = MigrationReport::default();
    for path in paths {
        if !path.is_file() {
            continue;
        }
        let original = fs::read_to_string(path)
            .map_err(|error| format!("failed to read {}: {error}", path.display()))?;
        let mut kept_lines = Vec::new();
        let mut secrets = Vec::new();
        for line in original.lines() {
            if let Some((account, value)) = legacy_secret(line) {
                secrets.push((account, value));
            } else {
                kept_lines.push(line);
            }
        }
        if secrets.is_empty() {
            continue;
        }

        // Do not modify the source file until every credential-store write has
        // succeeded. A partially populated keyring is safe to retry because
        // existing values always win over legacy file values.
        for (account, value) in &secrets {
            if value.is_empty() || is_placeholder(value) || store.get(account)?.is_some() {
                continue;
            }
            store.set(account, value)?;
            report.secrets_migrated += 1;
        }

        let mut scrubbed = kept_lines.join("\n");
        if scrubbed.trim().is_empty() {
            scrubbed =
                "# Provider secrets migrated to the operating-system credential store.".to_string();
        }
        scrubbed.push('\n');
        atomic_replace(path, scrubbed.as_bytes())?;
        report.files_scrubbed += 1;
    }
    Ok(report)
}

/// Product credential store: current service with one-time read-through
/// migration from the legacy VoxLog service name.
pub struct SystemSecretStore;

impl SystemSecretStore {
    fn read_with_legacy_fallback(name: &str) -> Result<Option<String>, SecretError> {
        read_through(
            &NativeSecretStore::default(),
            &NativeSecretStore::with_service(LEGACY_KEYRING_SERVICE),
            name,
        )
    }
}

/// Classified store operations used by the legacy read-through path.
pub(crate) trait ClassifiedStore {
    fn read(&self, name: &str) -> Result<Option<String>, SecretError>;
    fn write(&self, name: &str, value: &str) -> Result<(), SecretError>;
}

impl ClassifiedStore for NativeSecretStore {
    fn read(&self, name: &str) -> Result<Option<String>, SecretError> {
        NativeSecretStore::read(self, name)
    }
    fn write(&self, name: &str, value: &str) -> Result<(), SecretError> {
        NativeSecretStore::write(self, name, value)
    }
}

/// Read `name` from `current`, falling back to `legacy`. A legacy value is
/// copied forward best-effort: if the copy fails (oversized, locked, backend)
/// the legacy value is still returned and remains in place for a later retry,
/// so migration can never make an existing credential unreadable.
pub(crate) fn read_through(
    current: &dyn ClassifiedStore,
    legacy: &dyn ClassifiedStore,
    name: &str,
) -> Result<Option<String>, SecretError> {
    if let Some(value) = current.read(name)? {
        return Ok(Some(value));
    }
    let Some(value) = legacy.read(name)? else {
        return Ok(None);
    };
    if let Err(error) = current.write(name, &value) {
        eprintln!("credential copy-forward skipped for account {name}: {error}");
    }
    Ok(Some(value))
}

impl SecretStore for SystemSecretStore {
    fn get(&self, name: &str) -> Result<Option<String>, String> {
        Self::read_with_legacy_fallback(name).map_err(|error| error.to_string())
    }

    fn set(&self, name: &str, value: &str) -> Result<(), String> {
        NativeSecretStore::default()
            .write(name, value)
            .map_err(|error| error.to_string())
    }
}

/// Classified read: callers that must distinguish "not configured" from a
/// locked or failing store use this instead of [`get_secret`].
pub fn read_secret(name: &str) -> Result<Option<String>, SecretError> {
    SystemSecretStore::read_with_legacy_fallback(name)
}

/// Compatibility read. Logs the error class (never the account value) so a
/// failing store is not mistaken for an unset credential.
pub fn get_secret(name: &str) -> Option<String> {
    match read_secret(name) {
        Ok(value) => value,
        Err(error) => {
            eprintln!("credential store read failed for account {name}: {error}");
            None
        }
    }
}

pub fn set_secret(name: &str, value: Option<String>) -> Result<(), String> {
    let store = NativeSecretStore::default();
    match value {
        Some(secret) if !secret.trim().is_empty() => store
            .write(name, secret.trim())
            .map_err(|error| error.to_string()),
        Some(_) => store.delete(name).map_err(|error| error.to_string()),
        None => Ok(()),
    }
}

pub fn migrate_default_legacy_env_files() -> Result<MigrationReport, String> {
    let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) else {
        return Ok(MigrationReport::default());
    };
    let home = PathBuf::from(home);
    migrate_legacy_env_files(
        &SystemSecretStore,
        &[
            home.join(".voxlog2").join(".env"),
            home.join(".voxlog").join(".env"),
        ],
    )
}

fn legacy_secret(line: &str) -> Option<(&'static str, String)> {
    let assignment = line
        .trim_start()
        .strip_prefix("export ")
        .unwrap_or_else(|| line.trim_start());
    let (key, raw_value) = assignment.split_once('=')?;
    let account = LEGACY_SECRET_KEYS
        .iter()
        .find_map(|(legacy_key, account)| (key.trim() == *legacy_key).then_some(*account))?;
    Some((account, unquote(raw_value.trim()).to_string()))
}

fn unquote(value: &str) -> &str {
    if value.len() >= 2 {
        let bytes = value.as_bytes();
        if (bytes[0] == b'"' && bytes[value.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[value.len() - 1] == b'\'')
        {
            return &value[1..value.len() - 1];
        }
    }
    value
}

fn is_placeholder(value: &str) -> bool {
    matches!(
        value,
        "sk-xxx" | "change-me" | "change-me-to-a-random-string"
    ) || value.starts_with("your-")
}

fn atomic_replace(path: &Path, contents: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("legacy environment path has no parent: {}", path.display()))?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)
        .map_err(|error| format!("failed to create migration file: {error}"))?;
    temporary
        .write_all(contents)
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|error| format!("failed to write migration file: {error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("failed to secure migration file: {error}"))?;
    }
    temporary
        .persist(path)
        .map_err(|error| format!("failed to replace {}: {}", path.display(), error.error))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::collections::HashMap;
    use std::fs;
    use tempfile::tempdir;

    #[derive(Default)]
    struct MemoryStore {
        values: RefCell<HashMap<String, String>>,
        fail_writes: Cell<bool>,
    }

    impl SecretStore for MemoryStore {
        fn get(&self, name: &str) -> Result<Option<String>, String> {
            Ok(self.values.borrow().get(name).cloned())
        }

        fn set(&self, name: &str, value: &str) -> Result<(), String> {
            if self.fail_writes.get() {
                return Err("credential store unavailable".to_string());
            }
            self.values
                .borrow_mut()
                .insert(name.to_string(), value.to_string());
            Ok(())
        }
    }

    #[test]
    fn migrates_allowlisted_secrets_and_preserves_non_secret_settings() {
        let temp = tempdir().unwrap();
        let path = temp.path().join(".env");
        fs::write(
            &path,
            "# VoxLog\nDASHSCOPE_API_KEY=\"dash-secret\"\nOPENAI_API_KEY=openai-secret\nDOUBAO_API_KEY=doubao-secret\nANTHROPIC_API_KEY=anthropic-secret\nVOXLOG_ENV=home\n",
        )
        .unwrap();
        let store = MemoryStore::default();

        let report = migrate_legacy_env_files(&store, std::slice::from_ref(&path)).unwrap();
        let scrubbed = fs::read_to_string(path).unwrap();

        assert_eq!(report.files_scrubbed, 1);
        assert_eq!(report.secrets_migrated, 4);
        assert_eq!(
            store.get("dashscope_key_us").unwrap().as_deref(),
            Some("dash-secret")
        );
        assert_eq!(
            store.get("openai_key").unwrap().as_deref(),
            Some("openai-secret")
        );
        assert_eq!(
            store.get("doubao_key").unwrap().as_deref(),
            Some("doubao-secret")
        );
        assert_eq!(
            store.get("anthropic_key").unwrap().as_deref(),
            Some("anthropic-secret")
        );
        assert!(scrubbed.contains("VOXLOG_ENV=home"));
        assert!(!scrubbed.contains("API_KEY"));
        assert!(!scrubbed.contains("secret"));
    }

    #[test]
    fn never_overwrites_an_existing_credential_but_still_scrubs_the_file() {
        let temp = tempdir().unwrap();
        let path = temp.path().join(".env");
        fs::write(&path, "OPENAI_API_KEY=legacy-value\n").unwrap();
        let store = MemoryStore::default();
        store
            .values
            .borrow_mut()
            .insert("openai_key".to_string(), "current-value".to_string());

        let report = migrate_legacy_env_files(&store, std::slice::from_ref(&path)).unwrap();

        assert_eq!(report.secrets_migrated, 0);
        assert_eq!(
            store.get("openai_key").unwrap().as_deref(),
            Some("current-value")
        );
        assert!(!fs::read_to_string(path).unwrap().contains("legacy-value"));
    }

    #[test]
    fn scrubs_example_placeholders_without_saving_them_as_credentials() {
        let temp = tempdir().unwrap();
        let path = temp.path().join(".env");
        fs::write(&path, "OPENAI_API_KEY=sk-xxx\nVOXLOG_ENV=home\n").unwrap();
        let store = MemoryStore::default();

        let report = migrate_legacy_env_files(&store, std::slice::from_ref(&path)).unwrap();

        assert_eq!(report.files_scrubbed, 1);
        assert_eq!(report.secrets_migrated, 0);
        assert_eq!(store.get("openai_key").unwrap(), None);
        assert_eq!(fs::read_to_string(path).unwrap(), "VOXLOG_ENV=home\n");
    }

    #[test]
    fn leaves_the_env_file_unchanged_when_secure_storage_fails() {
        let temp = tempdir().unwrap();
        let path = temp.path().join(".env");
        let original = "OPENAI_API_KEY=must-not-be-lost\nVOXLOG_ENV=office\n";
        fs::write(&path, original).unwrap();
        let store = MemoryStore::default();
        store.fail_writes.set(true);

        let error = migrate_legacy_env_files(&store, std::slice::from_ref(&path)).unwrap_err();

        assert!(error.contains("credential store unavailable"));
        assert_eq!(fs::read_to_string(path).unwrap(), original);
    }
}

#[cfg(test)]
mod classification_tests {
    use super::*;

    fn boxed(message: &str) -> Box<dyn std::error::Error + Send + Sync> {
        Box::new(std::io::Error::other(message.to_string()))
    }

    #[test]
    fn missing_entries_are_not_errors() {
        assert_eq!(classify(keyring::Error::NoEntry), SecretError::NoEntry);
    }

    #[test]
    fn locked_or_denied_storage_is_reported_as_locked_not_absent() {
        assert_eq!(
            classify(keyring::Error::NoStorageAccess(boxed("denied"))),
            SecretError::Locked
        );
    }

    #[test]
    fn platform_failures_are_backend_errors() {
        assert_eq!(
            classify(keyring::Error::PlatformFailure(boxed("boom"))),
            SecretError::Backend
        );
    }

    #[test]
    fn ambiguous_matches_are_their_own_class() {
        assert_eq!(
            classify(keyring::Error::Ambiguous(Vec::new())),
            SecretError::Ambiguous
        );
    }

    #[test]
    fn oversized_values_are_rejected_before_touching_the_store() {
        let value = "x".repeat(MAX_SECRET_BYTES + 1);
        assert_eq!(
            validate_secret_size(&value),
            Err(SecretError::TooLarge {
                bytes: Some(MAX_SECRET_BYTES + 1)
            })
        );
        assert_eq!(validate_secret_size("ok"), Ok(()));
    }

    #[test]
    fn windows_utf16_storage_limit_is_enforced_for_ascii() {
        // 1,280 UTF-16 code units = 2,560 bytes, the Credential Manager maximum.
        assert_eq!(validate_secret_size(&"a".repeat(1280)), Ok(()));
        assert_eq!(
            validate_secret_size(&"a".repeat(1281)),
            Err(SecretError::TooLarge { bytes: Some(1281) })
        );
    }

    #[test]
    fn multibyte_text_is_bounded_by_both_encodings() {
        // 682 CJK chars = 2,046 UTF-8 bytes and 682 UTF-16 units (1,364 bytes).
        assert_eq!(validate_secret_size(&"密".repeat(682)), Ok(()));
        // 683 CJK chars = 2,049 UTF-8 bytes: over the product limit.
        assert!(matches!(
            validate_secret_size(&"密".repeat(683)),
            Err(SecretError::TooLarge { .. })
        ));
    }

    #[test]
    fn platform_value_length_rejection_is_a_size_error_not_a_name_error() {
        assert_eq!(
            classify(keyring::Error::TooLong(
                "password encoded as UTF-16".to_string(),
                2560
            )),
            SecretError::TooLarge { bytes: None }
        );
        assert_eq!(
            classify(keyring::Error::TooLong("secret".to_string(), 2560)),
            SecretError::TooLarge { bytes: None }
        );
        assert_eq!(
            classify(keyring::Error::TooLong("target".to_string(), 32767)),
            SecretError::Invalid
        );
    }
}

#[cfg(test)]
mod legacy_fallback_tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    #[derive(Default)]
    struct FakeStore {
        values: RefCell<HashMap<String, String>>,
        write_error: Option<SecretError>,
    }

    impl ClassifiedStore for FakeStore {
        fn read(&self, name: &str) -> Result<Option<String>, SecretError> {
            Ok(self.values.borrow().get(name).cloned())
        }
        fn write(&self, name: &str, value: &str) -> Result<(), SecretError> {
            if let Some(error) = &self.write_error {
                return Err(error.clone());
            }
            validate_secret_size(value)?;
            self.values
                .borrow_mut()
                .insert(name.to_string(), value.to_string());
            Ok(())
        }
    }

    #[test]
    fn current_value_wins_over_legacy() {
        let current = FakeStore::default();
        current.values.borrow_mut().insert("k".into(), "new".into());
        let legacy = FakeStore::default();
        legacy.values.borrow_mut().insert("k".into(), "old".into());
        assert_eq!(read_through(&current, &legacy, "k"), Ok(Some("new".into())));
    }

    #[test]
    fn legacy_value_is_copied_forward_once() {
        let current = FakeStore::default();
        let legacy = FakeStore::default();
        legacy.values.borrow_mut().insert("k".into(), "old".into());
        assert_eq!(read_through(&current, &legacy, "k"), Ok(Some("old".into())));
        assert_eq!(
            current.values.borrow().get("k").cloned(),
            Some("old".into())
        );
    }

    #[test]
    fn oversized_legacy_value_is_still_readable_and_not_copied() {
        let current = FakeStore::default();
        let legacy = FakeStore::default();
        let long = "t".repeat(MAX_SECRET_BYTES + 100);
        legacy.values.borrow_mut().insert("k".into(), long.clone());
        assert_eq!(read_through(&current, &legacy, "k"), Ok(Some(long)));
        assert!(current.values.borrow().get("k").is_none());
    }

    #[test]
    fn failed_copy_forward_does_not_hide_the_legacy_value() {
        let current = FakeStore {
            write_error: Some(SecretError::Locked),
            ..Default::default()
        };
        let legacy = FakeStore::default();
        legacy.values.borrow_mut().insert("k".into(), "old".into());
        assert_eq!(read_through(&current, &legacy, "k"), Ok(Some("old".into())));
    }
}

#[cfg(test)]
mod propagation_tests {
    use super::*;

    fn reader(
        map: &'static [(&'static str, Result<Option<&'static str>, SecretError>)],
    ) -> impl Fn(&str) -> Result<Option<String>, SecretError> {
        move |name| {
            map.iter()
                .find(|(key, _)| *key == name)
                .map(|(_, value)| value.clone().map(|v| v.map(str::to_string)))
                .unwrap_or(Ok(None))
        }
    }

    #[test]
    fn refresh_replaces_the_slot_when_the_store_answers() {
        let read = reader(&[("token", Ok(Some("fresh")))]);
        let mut slot = Some("stale".to_string());
        assert_eq!(refresh_slot(&read, &mut slot, "token"), None);
        assert_eq!(slot.as_deref(), Some("fresh"));
    }

    #[test]
    fn refresh_clears_the_slot_when_the_credential_was_removed() {
        let read = reader(&[("token", Ok(None))]);
        let mut slot = Some("stale".to_string());
        assert_eq!(refresh_slot(&read, &mut slot, "token"), None);
        assert_eq!(slot, None);
    }

    #[test]
    fn refresh_keeps_the_last_value_and_reports_a_locked_store() {
        let read = reader(&[("token", Err(SecretError::Locked))]);
        let mut slot = Some("known".to_string());
        assert_eq!(
            refresh_slot(&read, &mut slot, "token"),
            Some(SecretError::Locked)
        );
        assert_eq!(slot.as_deref(), Some("known"));
    }

    #[test]
    fn provider_keys_are_collected_with_absent_keys_as_null() {
        let read = reader(&[("openai_key", Ok(Some("sk-test")))]);
        let keys = collect_provider_keys(&read, &["openai_key", "doubao_key"]).unwrap();
        assert_eq!(keys["openai_key"], serde_json::json!("sk-test"));
        assert_eq!(keys["doubao_key"], serde_json::Value::Null);
    }

    #[test]
    fn provider_keys_fail_closed_instead_of_sending_null_for_an_unreadable_key() {
        let read = reader(&[
            ("openai_key", Ok(Some("sk-test"))),
            ("doubao_key", Err(SecretError::Backend)),
        ]);
        assert_eq!(
            collect_provider_keys(&read, &["openai_key", "doubao_key"]),
            Err(SecretError::Backend)
        );
    }
}
