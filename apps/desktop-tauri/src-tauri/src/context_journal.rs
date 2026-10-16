//! Encrypted local journal for team Context submissions (T08).
//!
//! Layout: `<dir>/journal.lock` (OS file lock, one process at a time, released
//! by the OS if the holder dies) and `<dir>/db/` holding the SQLite database
//! and its WAL/SHM sidecars, so the whole database moves as one directory.
//!
//! * SQLite in WAL mode, `synchronous=FULL`, `secure_delete=ON`. Record content
//!   is sealed with the data key (`context_keys`); clear columns are the owner
//!   digest, eventId, phase, server state, sequence and times. Each row carries
//!   a metadata-key HMAC over all of them plus the ciphertext digest, so an edit
//!   of a clear column is detected (the row is quarantined, never acted on).
//! * `reserve` is durable before any POST. Dispatch phases: `Reserved` (no bytes
//!   ever sent: POST allowed), `Dispatching` (a POST may be in flight: read back
//!   only), `Accepted`.
//! * Rollback: every mutation bumps a generation whose HMAC also covers a
//!   digest of every row (so an old row spliced into a current database is
//!   detected), and
//!   publishes it to an `AnchorStore` outside the journal directory (the OS
//!   credential store in production), so restoring the directory cannot bring
//!   the anchor back with it. A mutation reports success only after the anchor
//!   is published; `begin_dispatch` therefore never authorises a POST whose
//!   state the anchor does not cover. A journal behind its anchor, with a
//!   forged generation, or with records but no anchor is treated as restored:
//!   in one transaction every `Reserved` record moves to the `Restored` phase
//!   (read back only, and it can never return to `Reserved`), the generation
//!   continues above the highest one ever published (the anchor never goes
//!   down), and a verify-only flag is set. Clearing the flag cannot
//!   re-authorise anything that came from the restored copy.
//! * A lost key (`Unrecoverable`) moves `db/` into `isolated/` with one rename
//!   and starts an empty journal with a new key; nothing is overwritten. An
//!   `Unavailable` key, or an unreadable anchor store, changes nothing: both are
//!   checked through a read-only probe before any writable connection. (After
//!   a crash with WAL sidecars present, that probe may let SQLite rebuild the
//!   `-shm` index; database and WAL contents are never changed.)
//!
//! * Quota: only new records are gated by the disk budget; status updates of
//!   existing records, tombstones and recovery writes may use the emergency
//!   reserve (plan C-24).
//!
//! Limitations, by design:
//! * Restoring the journal directory together with the OS credential store (a
//!   whole-profile restore) is not detectable from this machine.
//! * The journal cannot remember a record that was deleted from its own files.
//!   If the same eventId is reserved again, the backstop is the server: its
//!   idempotency on (org, actor, eventId) is permanent (ADR
//!   2026-10-08-backup-idempotency-retention-v1), so a re-POST with the same
//!   payload returns the original receipt and a different payload gets 409.
//!   The same applies to a tombstone lost by restoring an older copy of the
//!   journal: tombstones prevent reuse within one journal history only.
use crate::context_delivery::{BackupPayload, DeliveryState, ProjectSnapshot};
use crate::context_keys::{
    owner_digest, sync_dir, Aad, KeyError, KeyId, KeyProvider, KeyRing, KeySet, RecordKind,
};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::File;
use std::path::{Path, PathBuf};
use zeroize::Zeroizing;

const SCHEMA_VERSION: u16 = 1;
const DB_DIR: &str = "db";
const DB_FILE: &str = "journal.sqlite";
const LOCK_FILE: &str = "journal.lock";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JournalError {
    /// Another process holds the journal.
    Busy,
    /// The key provider or anchor store cannot run now; nothing was changed.
    KeyUnavailable,
    /// The change was committed but its anchor could not be published. The
    /// journal is now verify-only; do not POST on the strength of this change.
    AnchorFailed,
    Conflict,
    InvalidInput,
    InvalidTransition,
    StaleSequence,
    /// A limit was reached. Nothing was evicted; the caller keeps the draft.
    QuotaExceeded(QuotaKind),
    /// The eventId was disposed of locally and can never be reused.
    Tombstoned,
    /// The confirmation does not match the current state of its preview.
    StalePreview,
    /// The preview includes records that may have been delivered; the user
    /// must acknowledge that the cloud copy is not withdrawn.
    ConfirmationRequired,
    /// Restored or unanchored journal: no dispatch until acknowledged.
    VerifyOnly,
    NotFound,
    /// Authentication of stored data failed; the record is quarantined.
    Corrupt,
    /// The journal root, `db/` or `isolated/` is a symlink or reparse point.
    UnsafePath,
    Io,
}

impl From<rusqlite::Error> for JournalError {
    fn from(_: rusqlite::Error) -> Self {
        JournalError::Io
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QuotaKind {
    ActiveRecords,
    ActiveBytes,
    IsolatedBytes,
    /// Total journal files would cut into the emergency reserve.
    DiskBudget,
    Tombstones,
}

/// Storage policy (design §6.4, plan C-24). Adjustable product policy; never
/// enforced by evicting another owner's records.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QuotaLimits {
    pub max_active_records_per_owner: u64,
    /// Sealed payload bytes in the active queue.
    pub max_active_bytes_per_owner: u64,
    pub max_isolated_bytes_per_owner: u64,
    /// All journal files (database, WAL, isolated archives).
    pub max_total_bytes: u64,
    /// Part of `max_total_bytes` that only tombstone and recovery writes may use.
    pub emergency_reserve_bytes: u64,
    /// Tombstones are kept forever to prevent eventId reuse; at the limit,
    /// disposal is refused rather than forgetting old tombstones.
    pub max_tombstones_per_owner: u64,
}

impl Default for QuotaLimits {
    fn default() -> Self {
        const MIB: u64 = 1024 * 1024;
        Self {
            max_active_records_per_owner: 5_000,
            max_active_bytes_per_owner: 50 * MIB,
            max_isolated_bytes_per_owner: 50 * MIB,
            max_total_bytes: 200 * MIB,
            emergency_reserve_bytes: 20 * MIB,
            max_tombstones_per_owner: 100_000,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExportChoice {
    /// The user exported a backup of the records first.
    Exported,
    /// The user was warned that export is unavailable and disposes anyway.
    DeclinedAfterWarning,
}

impl ExportChoice {
    fn label(self) -> &'static str {
        match self {
            Self::Exported => "exported",
            Self::DeclinedAfterWarning => "declined-after-warning",
        }
    }
}

/// Explicit user confirmation of one preview.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Disposal {
    pub token: String,
    pub export: ExportChoice,
    pub acknowledge_possible_delivery: bool,
    pub reason: String,
    /// Local user label for the audit record (no secrets, no message text).
    pub actor_local: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DisposalPreview {
    pub token: String,
    pub events: Vec<String>,
    pub count: u64,
    pub sealed_bytes: u64,
    /// Records whose message may exist in the cloud; disposal never withdraws it.
    pub may_have_been_delivered: u64,
    pub already_disposed: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DisposalOutcome {
    pub disposed: u64,
    /// False when another reader kept the WAL from being folded; the old
    /// ciphertext may remain in the WAL until the next open completes cleanup.
    pub wal_cleanup_complete: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IsolatedArchive {
    pub name: String,
    pub bytes: u64,
}

const MAX_DISPOSAL_EVENTS: usize = 500;

fn valid_text(value: &str, max: usize) -> bool {
    !value.trim().is_empty() && value.chars().count() <= max && !value.chars().any(char::is_control)
}

fn valid_archive_name(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('.')
        && name.len() <= 200
        && name != "."
        && name != ".."
        && !name.contains(['/', '\\', ':'])
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QuotaReport {
    pub active_records: u64,
    pub active_bytes: u64,
    pub isolated_bytes: u64,
    pub total_disk_bytes: u64,
    pub limits: QuotaLimits,
}

fn tree_bytes(path: &Path) -> u64 {
    let Ok(meta) = std::fs::symlink_metadata(path) else {
        return 0;
    };
    if meta.is_file() {
        return meta.len();
    }
    if !meta.is_dir() {
        return 0; // symlinks and others are not followed
    }
    std::fs::read_dir(path)
        .map(|entries| entries.flatten().map(|e| tree_bytes(&e.path())).sum())
        .unwrap_or(0)
}

/// Digest of a directory tree's shape: relative names, kinds, sizes and
/// modification times, without following links. Binds a preview to the exact
/// tree it showed, so a swapped directory of the same size is detected.
fn tree_fingerprint(root: &Path) -> String {
    fn walk(base: &Path, path: &Path, out: &mut Vec<(String, char, u64, u128)>) {
        let Ok(entries) = std::fs::read_dir(path) else {
            return;
        };
        for entry in entries.flatten() {
            let child = entry.path();
            let Ok(meta) = std::fs::symlink_metadata(&child) else {
                continue;
            };
            let rel = child
                .strip_prefix(base)
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_default();
            let kind = if meta.file_type().is_symlink() {
                'l'
            } else if meta.is_dir() {
                'd'
            } else {
                'f'
            };
            let modified = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map_or(0, |d| d.as_nanos());
            out.push((rel, kind, meta.len(), modified));
            if kind == 'd' {
                walk(base, &child, out);
            }
        }
    }
    let mut entries = Vec::new();
    walk(root, root, &mut entries);
    entries.sort();
    let mut hash = Sha256::new();
    for (rel, kind, len, modified) in entries {
        hash.update((rel.len() as u64).to_be_bytes());
        hash.update(rel.as_bytes());
        hash.update([kind as u8]);
        hash.update(len.to_be_bytes());
        hash.update(modified.to_be_bytes());
    }
    hex(&hash.finalize())
}

/// A real directory: not a symlink, junction or other reparse point.
fn real_dir(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok_and(|m| m.is_dir() && !m.file_type().is_symlink())
}

/// Holds the latest published generation per journal, outside the journal directory.
pub trait AnchorStore: Send {
    fn read(&self, db_id: &str) -> Result<Option<String>, JournalError>;
    fn write(&self, db_id: &str, value: &str) -> Result<(), JournalError>;
}

impl AnchorStore for crate::secrets::NativeSecretStore {
    fn read(&self, db_id: &str) -> Result<Option<String>, JournalError> {
        crate::secrets::NativeSecretStore::read(self, &format!("context_journal_anchor_{db_id}"))
            .map_err(|_| JournalError::KeyUnavailable)
    }
    fn write(&self, db_id: &str, value: &str) -> Result<(), JournalError> {
        crate::secrets::NativeSecretStore::write(
            self,
            &format!("context_journal_anchor_{db_id}"),
            value,
        )
        .map_err(|_| JournalError::AnchorFailed)
    }
}

/// Test-only anchor store: one file per journal in a directory that callers
/// keep outside the journal directory. Not compiled into release builds.
#[cfg(any(test, debug_assertions))]
pub struct FileAnchorStore {
    dir: PathBuf,
}

#[cfg(any(test, debug_assertions))]
impl FileAnchorStore {
    pub fn new(dir: &Path) -> Self {
        Self {
            dir: dir.to_path_buf(),
        }
    }
}

#[cfg(any(test, debug_assertions))]
impl AnchorStore for FileAnchorStore {
    fn read(&self, db_id: &str) -> Result<Option<String>, JournalError> {
        match std::fs::read_to_string(self.dir.join(format!("{db_id}.anchor"))) {
            Ok(value) => Ok(Some(value)),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(_) => Err(JournalError::KeyUnavailable),
        }
    }
    fn write(&self, db_id: &str, value: &str) -> Result<(), JournalError> {
        use std::io::Write;
        let fail = |_| JournalError::AnchorFailed;
        std::fs::create_dir_all(&self.dir).map_err(fail)?;
        let mut temp = tempfile::NamedTempFile::new_in(&self.dir).map_err(fail)?;
        temp.write_all(value.as_bytes()).map_err(fail)?;
        temp.as_file().sync_all().map_err(fail)?;
        temp.persist(self.dir.join(format!("{db_id}.anchor")))
            .map_err(|_| JournalError::AnchorFailed)?;
        sync_dir(&self.dir).map_err(fail)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DispatchPhase {
    Reserved,
    Dispatching,
    Accepted,
    /// Demoted after a restore: read back only, never returns to `Reserved`.
    Restored,
}

impl DispatchPhase {
    fn label(self) -> &'static str {
        match self {
            Self::Reserved => "reserved",
            Self::Dispatching => "dispatching",
            Self::Accepted => "accepted",
            Self::Restored => "restored",
        }
    }
    fn parse(value: &str) -> Result<Self, JournalError> {
        match value {
            "reserved" => Ok(Self::Reserved),
            "dispatching" => Ok(Self::Dispatching),
            "accepted" => Ok(Self::Accepted),
            "restored" => Ok(Self::Restored),
            _ => Err(JournalError::Corrupt),
        }
    }
}

fn state_label(state: DeliveryState) -> Option<&'static str> {
    Some(match state {
        DeliveryState::Unknown => return None,
        DeliveryState::ServerUnknown => "unknown",
        DeliveryState::Pending => "pending",
        DeliveryState::Sending => "sending",
        DeliveryState::Delivered => "delivered",
        DeliveryState::Blocked => "blocked",
        DeliveryState::Rejected => "rejected",
        DeliveryState::Conflict => "conflict",
    })
}

fn parse_state(value: &str) -> Result<DeliveryState, JournalError> {
    Ok(match value {
        "unknown" => DeliveryState::ServerUnknown,
        "pending" => DeliveryState::Pending,
        "sending" => DeliveryState::Sending,
        "delivered" => DeliveryState::Delivered,
        "blocked" => DeliveryState::Blocked,
        "rejected" => DeliveryState::Rejected,
        "conflict" => DeliveryState::Conflict,
        _ => return Err(JournalError::Corrupt),
    })
}

fn is_terminal(state: DeliveryState) -> bool {
    matches!(
        state,
        DeliveryState::Delivered | DeliveryState::Rejected | DeliveryState::Conflict
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecoveryMode {
    Normal,
    VerifyOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecoveryAction {
    /// Never sent: the same eventId and payload may be POSTed.
    MayPost,
    /// A POST may have reached the server: read back, never POST again.
    ReadBackOnly,
    /// Accepted and not final: poll.
    Poll,
    Final,
    /// Stored data failed authentication: take no automatic action.
    Quarantine,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReserveOutcome {
    Reserved,
    AlreadyReserved,
}

/// `Debug` omits the payload, which holds user text.
#[derive(Clone, PartialEq, Eq)]
pub struct StoredRecord {
    pub payload: BackupPayload,
    pub phase: DispatchPhase,
    pub server_state: Option<DeliveryState>,
    pub poll_seq: u64,
}

impl std::fmt::Debug for StoredRecord {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("StoredRecord")
            .field("event_id", &self.payload.event_id())
            .field("phase", &self.phase)
            .field("server_state", &self.server_state)
            .field("poll_seq", &self.poll_seq)
            .finish_non_exhaustive()
    }
}

/// A retained original, returned only after every integrity check passed.
pub struct Original {
    pub payload: BackupPayload,
    pub created_at: i64,
}

const MAX_PAGE: usize = 200;

fn parse_cursor(cursor: &str) -> Option<(i64, String)> {
    let rest = cursor.strip_prefix("c1.")?;
    let (at, event) = rest.split_once('.')?;
    let at: i64 = at.parse().ok()?;
    let valid = !event.is_empty()
        && event.len() <= 128
        && event
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-');
    valid.then(|| (at, event.to_string()))
}

pub enum Opened {
    Ready(Journal),
    /// The previous journal's key was unrecoverable; it was moved to `archive`.
    IsolatedPrevious {
        journal: Journal,
        archive: PathBuf,
    },
}

impl Opened {
    pub fn into_journal(self) -> Journal {
        match self {
            Opened::Ready(journal) | Opened::IsolatedPrevious { journal, .. } => journal,
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SealPayload<'a> {
    v: u32,
    target_id: &'a str,
    text: &'a str,
    project_id: Option<&'a str>,
    repository: Option<&'a str>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct OpenedPayload {
    v: u32,
    target_id: String,
    text: String,
    project_id: Option<String>,
    repository: Option<String>,
}

/// One authenticated row.
struct Row {
    sealed: Vec<u8>,
    payload_tag: Vec<u8>,
    phase: DispatchPhase,
    state: Option<DeliveryState>,
    seq: u64,
    /// Ordering key for paging; authenticated so it cannot be edited to
    /// duplicate or hide records across pages.
    created_at: i64,
}

pub struct Journal {
    dir: PathBuf,
    limits: QuotaLimits,
    conn: Connection,
    keys: KeySet,
    anchors: Box<dyn AnchorStore>,
    db_id: String,
    mode: RecoveryMode,
    /// Generation of our last commit; the database must still match it.
    generation: u64,
    /// Last record-disposal preview: (token, owner digest, events).
    pending_disposal: Option<(String, [u8; 32], Vec<String>)>,
    /// Last archive-disposal preview: (token, archive name).
    pending_archive: Option<(String, String)>,
    _lock: File,
}

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn connect(path: &Path) -> Result<Connection, JournalError> {
    #[cfg(unix)]
    if !path.exists() {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)
            .map_err(|_| JournalError::Io)?;
    }
    let conn = Connection::open(path)?;
    let mode: String = conn.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
    if mode != "wal" {
        return Err(JournalError::Io);
    }
    conn.execute_batch(
        "PRAGMA synchronous=FULL; PRAGMA secure_delete=ON; PRAGMA foreign_keys=ON;
         CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
         CREATE TABLE IF NOT EXISTS records(
             owner BLOB NOT NULL,
             event_id TEXT NOT NULL,
             key_id TEXT NOT NULL,
             sealed BLOB NOT NULL,
             payload_tag BLOB NOT NULL,
             phase TEXT NOT NULL CHECK(phase IN ('reserved','dispatching','accepted','restored')),
             server_state TEXT CHECK(server_state IN
                 ('pending','sending','unknown','delivered','blocked','rejected','conflict')),
             poll_seq INTEGER NOT NULL DEFAULT 0,
             zone TEXT NOT NULL DEFAULT 'active' CHECK(zone IN ('active','isolated','tombstone')),
             row_tag BLOB NOT NULL,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL,
             PRIMARY KEY(owner, event_id)) WITHOUT ROWID;
         CREATE TABLE IF NOT EXISTS disposals(
             id INTEGER PRIMARY KEY,
             owner BLOB NOT NULL,
             event_id TEXT NOT NULL,
             key_id TEXT NOT NULL,
             export TEXT NOT NULL,
             reason TEXT NOT NULL,
             actor_local TEXT NOT NULL,
             at INTEGER NOT NULL,
             tag BLOB NOT NULL);
         CREATE TABLE IF NOT EXISTS archive_disposals(
             name TEXT PRIMARY KEY,
             export TEXT NOT NULL,
             reason TEXT NOT NULL,
             actor_local TEXT NOT NULL,
             status TEXT NOT NULL CHECK(status IN ('pending','done')),
             fingerprint TEXT NOT NULL,
             at INTEGER NOT NULL,
             tag BLOB NOT NULL);",
    )?;
    Ok(conn)
}

/// Absolute path as the path part of a `file://` URI (forward slashes):
/// `/unix/path`, `/C:/drive/path` and `//server/share/path` for UNC, which
/// gives `file:////server/share/...`. Windows verbatim prefixes are removed.
fn sqlite_uri_path(absolute: &str) -> String {
    let path = if let Some(unc) = absolute.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else if let Some(local) = absolute.strip_prefix(r"\\?\") {
        local.to_string()
    } else {
        absolute.to_string()
    };
    let mut text = path.replace('\\', "/");
    if !text.starts_with('/') {
        text.insert(0, '/');
    }
    text
}

/// SQLite URI for `path` (made absolute), opened read-only. With no WAL
/// sidecars present the file is also opened `immutable`, so SQLite creates no
/// `-wal`/`-shm` files. With sidecars present (after a crash) SQLite may rebuild
/// the `-shm` index, but never changes the database or WAL contents.
fn read_only_uri(path: &Path, immutable: bool) -> Result<String, JournalError> {
    let absolute = std::fs::canonicalize(path).map_err(|_| JournalError::Io)?;
    let text = sqlite_uri_path(&absolute.to_string_lossy());
    let mut encoded = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '%' => encoded.push_str("%25"),
            '?' => encoded.push_str("%3f"),
            '#' => encoded.push_str("%23"),
            c => encoded.push(c),
        }
    }
    Ok(format!(
        "file://{encoded}?mode=ro{}",
        if immutable { "&immutable=1" } else { "" }
    ))
}

/// Read `(key_id, db_id)` without migrating, checkpointing or writing the database.
fn read_identity(path: &Path) -> Result<Option<(String, String)>, JournalError> {
    let sidecar = |suffix: &str| {
        let mut name = path.as_os_str().to_os_string();
        name.push(suffix);
        PathBuf::from(name).exists()
    };
    let immutable = !sidecar("-wal") && !sidecar("-shm");
    let conn = Connection::open_with_flags(
        read_only_uri(path, immutable)?,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_URI
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    let Ok(key_id) = meta(&conn, "key_id") else {
        return Ok(None); // never initialised
    };
    Ok(match (key_id, meta(&conn, "db_id")?) {
        (Some(key_id), Some(db_id)) => Some((key_id, db_id)),
        _ => None,
    })
}

fn meta(conn: &Connection, key: &str) -> Result<Option<String>, JournalError> {
    Ok(conn
        .query_row("SELECT v FROM meta WHERE k=?1", [key], |r| r.get(0))
        .optional()?)
}

fn generation_tag(keys: &KeySet, db_id: &str, generation: u64, state: &[u8; 32]) -> String {
    hex(&keys.tag_parts(
        "journal-generation",
        &[db_id.as_bytes(), &generation.to_be_bytes(), state],
    ))
}

/// Authenticated digest of every row (all zones) in key order, so replacing,
/// removing, re-zoning or re-adding any row changes it.
fn state_digest(conn: &Connection, keys: &KeySet) -> Result<[u8; 32], JournalError> {
    let mut hash = Sha256::new();
    let mut statement = conn
        .prepare("SELECT owner, event_id, zone, row_tag FROM records ORDER BY owner, event_id")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        for part in [
            row.get::<_, Vec<u8>>(0)?,
            row.get::<_, String>(1)?.into_bytes(),
            row.get::<_, String>(2)?.into_bytes(),
            row.get::<_, Vec<u8>>(3)?,
        ] {
            hash.update((part.len() as u64).to_be_bytes());
            hash.update(&part);
        }
    }
    drop(rows);
    drop(statement);
    // Archive disposal decisions authorise file deletion; they are covered too.
    let mut statement =
        conn.prepare("SELECT name, status, tag FROM archive_disposals ORDER BY name")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        for part in [
            row.get::<_, String>(0)?.into_bytes(),
            row.get::<_, String>(1)?.into_bytes(),
            row.get::<_, Vec<u8>>(2)?,
        ] {
            hash.update((part.len() as u64).to_be_bytes());
            hash.update(&part);
        }
    }
    let digest: [u8; 32] = hash.finalize().into();
    Ok(keys.tag("journal-state", &digest))
}

fn stored_generation(conn: &Connection) -> Result<Option<u64>, JournalError> {
    Ok(meta(conn, "generation")?.and_then(|v| v.parse::<u64>().ok()))
}

fn anchor_value(keys: &KeySet, db_id: &str, generation: u64) -> String {
    let tag = keys.tag_parts(
        "journal-anchor",
        &[db_id.as_bytes(), &generation.to_be_bytes()],
    );
    format!("{generation}:{}", hex(&tag))
}

fn parse_anchor(keys: &KeySet, db_id: &str, value: &str) -> Option<u64> {
    let generation: u64 = value.split_once(':')?.0.parse().ok()?;
    (anchor_value(keys, db_id, generation) == value).then_some(generation)
}

/// Authenticated generation, or `None` when the stored tag does not match the
/// generation and the current row set.
fn authenticated_generation(
    conn: &Connection,
    keys: &KeySet,
    db_id: &str,
) -> Result<Option<u64>, JournalError> {
    let Some(generation) = stored_generation(conn)? else {
        return Ok(None);
    };
    let expected = generation_tag(keys, db_id, generation, &state_digest(conn, keys)?);
    Ok((meta(conn, "generation_tag")?.as_deref() == Some(expected.as_str())).then_some(generation))
}

/// Increment the stored generation and re-tag it over the current row set.
fn bump_generation(conn: &Connection, keys: &KeySet, db_id: &str) -> Result<u64, JournalError> {
    let next = stored_generation(conn)?
        .unwrap_or(0)
        .checked_add(1)
        .ok_or(JournalError::Corrupt)?;
    conn.execute(
        "INSERT OR REPLACE INTO meta(k, v) VALUES ('generation', ?1)",
        params![next.to_string()],
    )?;
    conn.execute(
        "INSERT OR REPLACE INTO meta(k, v) VALUES ('generation_tag', ?1)",
        params![generation_tag(
            keys,
            db_id,
            next,
            &state_digest(conn, keys)?
        )],
    )?;
    Ok(next)
}

#[allow(clippy::too_many_arguments)]
fn archive_tag(
    keys: &KeySet,
    name: &str,
    export: &str,
    reason: &str,
    actor: &str,
    status: &str,
    fingerprint: &str,
    at: i64,
) -> [u8; 32] {
    keys.tag_parts(
        "archive-disposal",
        &[
            name.as_bytes(),
            export.as_bytes(),
            reason.as_bytes(),
            actor.as_bytes(),
            status.as_bytes(),
            fingerprint.as_bytes(),
            &at.to_be_bytes(),
        ],
    )
}

fn row_tag(keys: &KeySet, owner: &[u8], event_id: &str, zone: &str, row: &Row) -> Vec<u8> {
    let sealed_digest: [u8; 32] = Sha256::digest(&row.sealed).into();
    let state = row.state.and_then(state_label).unwrap_or("");
    keys.tag_parts(
        "journal-row",
        &[
            owner,
            event_id.as_bytes(),
            keys.id().as_str().as_bytes(),
            &sealed_digest,
            &row.payload_tag,
            row.phase.label().as_bytes(),
            state.as_bytes(),
            &row.seq.to_be_bytes(),
            &row.created_at.to_be_bytes(),
            zone.as_bytes(),
        ],
    )
    .to_vec()
}

/// Read and authenticate one row in any zone.
fn read_row_any(
    conn: &Connection,
    keys: &KeySet,
    owner: &[u8],
    event_id: &str,
) -> Result<Option<(Row, String)>, JournalError> {
    type Raw = (
        String,
        Vec<u8>,
        Vec<u8>,
        String,
        Option<String>,
        i64,
        Vec<u8>,
        String,
        i64,
    );
    let raw: Option<Raw> = conn
        .query_row(
            "SELECT key_id, sealed, payload_tag, phase, server_state, poll_seq, row_tag, zone,
                    created_at
             FROM records WHERE owner=?1 AND event_id=?2",
            params![owner, event_id],
            |r| {
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    r.get(3)?,
                    r.get(4)?,
                    r.get(5)?,
                    r.get(6)?,
                    r.get(7)?,
                    r.get(8)?,
                ))
            },
        )
        .optional()?;
    let Some((key_id, sealed, payload_tag, phase, state, seq, stored_tag, zone, created_at)) = raw
    else {
        return Ok(None);
    };
    if key_id != keys.id().as_str() {
        return Err(JournalError::Corrupt);
    }
    let row = Row {
        sealed,
        payload_tag,
        phase: DispatchPhase::parse(&phase)?,
        state: state.as_deref().map(parse_state).transpose()?,
        seq: u64::try_from(seq).map_err(|_| JournalError::Corrupt)?,
        created_at,
    };
    if row_tag(keys, owner, event_id, &zone, &row) != stored_tag {
        return Err(JournalError::Corrupt);
    }
    Ok(Some((row, zone)))
}

/// Read and authenticate one active row; rows in other zones are hidden.
fn read_row(
    conn: &Connection,
    keys: &KeySet,
    owner: &[u8],
    event_id: &str,
) -> Result<Option<Row>, JournalError> {
    Ok(read_row_any(conn, keys, owner, event_id)?
        .and_then(|(row, zone)| (zone == "active").then_some(row)))
}

/// Persistently quarantine a row that failed authentication during a restore:
/// move it to the `isolated` zone in the `Restored` phase and re-sign that
/// state, so no later edit of its old fields can make it dispatchable again.
fn quarantine_row(
    conn: &Connection,
    keys: &KeySet,
    owner: &[u8],
    event_id: &str,
) -> Result<(), JournalError> {
    let (sealed, payload_tag, created_at): (Vec<u8>, Vec<u8>, i64) = conn.query_row(
        "SELECT sealed, payload_tag, created_at FROM records WHERE owner=?1 AND event_id=?2",
        params![owner, event_id],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
    )?;
    let row = Row {
        sealed,
        payload_tag,
        phase: DispatchPhase::Restored,
        state: None,
        seq: 0,
        created_at,
    };
    conn.execute(
        "UPDATE records SET key_id=?3, phase='restored', server_state=NULL, poll_seq=0,
                zone='isolated', row_tag=?4, updated_at=?5
         WHERE owner=?1 AND event_id=?2",
        params![
            owner,
            event_id,
            keys.id().as_str(),
            row_tag(keys, owner, event_id, "isolated", &row),
            now()
        ],
    )?;
    Ok(())
}

fn update_row(
    conn: &Connection,
    keys: &KeySet,
    owner: &[u8],
    event_id: &str,
    row: &Row,
) -> Result<(), JournalError> {
    let seq = i64::try_from(row.seq).map_err(|_| JournalError::InvalidInput)?;
    conn.execute(
        "UPDATE records SET phase=?3, server_state=?4, poll_seq=?5, row_tag=?6, updated_at=?7
         WHERE owner=?1 AND event_id=?2 AND zone='active'",
        params![
            owner,
            event_id,
            row.phase.label(),
            row.state.and_then(state_label),
            seq,
            row_tag(keys, owner, event_id, "active", row),
            now()
        ],
    )?;
    Ok(())
}

impl Journal {
    /// Open (or create) the journal in `dir`. Keys live in the ring's
    /// directory; the anchor store must not live inside `dir`.
    pub fn open<P: KeyProvider>(
        dir: &Path,
        ring: &KeyRing<P>,
        anchors: Box<dyn AnchorStore>,
    ) -> Result<Opened, JournalError> {
        // Refuse links at the paths we create, rename and delete in. (An
        // attacker who can swap directories as this user between the check
        // and the operation is outside this design.)
        if std::fs::symlink_metadata(dir).is_ok() && !real_dir(dir) {
            return Err(JournalError::UnsafePath);
        }
        std::fs::create_dir_all(dir).map_err(|_| JournalError::Io)?;
        for path in [dir.to_path_buf(), dir.join(DB_DIR), dir.join("isolated")] {
            if std::fs::symlink_metadata(&path).is_ok() && !real_dir(&path) {
                return Err(JournalError::UnsafePath);
            }
        }
        let lock = File::options()
            .create(true)
            .truncate(false)
            .write(true)
            .open(dir.join(LOCK_FILE))
            .map_err(|_| JournalError::Io)?;
        if lock.try_lock().is_err() {
            return Err(JournalError::Busy);
        }
        let db_dir = dir.join(DB_DIR);
        let db_path = db_dir.join(DB_FILE);
        let mut archive = None;
        if db_path.exists() {
            match read_identity(&db_path)? {
                Some((id, db_id)) => {
                    let id = KeyId::parse(&id).map_err(|_| JournalError::Corrupt)?;
                    match ring.open(&id) {
                        Ok(keys) => {
                            // Read everything that can be unavailable before any write.
                            let stored_anchor = anchors.read(&db_id)?;
                            let conn = connect(&db_path)?;
                            return Self::finish_open(
                                dir,
                                conn,
                                keys,
                                anchors,
                                stored_anchor,
                                lock,
                            )
                            .map(Opened::Ready);
                        }
                        Err(KeyError::Unavailable | KeyError::Io) => {
                            return Err(JournalError::KeyUnavailable)
                        }
                        Err(_) => archive = Some(Self::isolate(dir, id.as_str())?),
                    }
                }
                None => archive = Some(Self::isolate(dir, "uninitialized")?),
            }
        } else if db_dir.exists() {
            archive = Some(Self::isolate(dir, "orphaned")?);
        }

        std::fs::create_dir_all(&db_dir).map_err(|_| JournalError::Io)?;
        sync_dir(dir).map_err(|_| JournalError::Io)?;
        let keys = ring.create().map_err(|error| match error {
            KeyError::Unavailable => JournalError::KeyUnavailable,
            _ => JournalError::Io,
        })?;
        let conn = connect(&db_path)?;
        sync_dir(&db_dir).map_err(|_| JournalError::Io)?;
        let db_id = format!("db-{}", keys.id().as_str());
        conn.execute_batch("BEGIN IMMEDIATE")?;
        for (k, v) in [
            ("schema", SCHEMA_VERSION.to_string()),
            ("key_id", keys.id().as_str().to_string()),
            ("db_id", db_id.clone()),
            ("generation", "0".to_string()),
        ] {
            conn.execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES (?1, ?2)",
                params![k, v],
            )?;
        }
        conn.execute(
            "INSERT OR REPLACE INTO meta(k, v) VALUES ('generation_tag', ?1)",
            params![generation_tag(
                &keys,
                &db_id,
                0,
                &state_digest(&conn, &keys)?
            )],
        )?;
        conn.execute_batch("COMMIT")?;
        let mut journal = Self {
            dir: dir.to_path_buf(),
            limits: QuotaLimits::default(),
            conn,
            keys,
            anchors,
            db_id,
            mode: RecoveryMode::Normal,
            generation: 0,
            pending_disposal: None,
            pending_archive: None,
            _lock: lock,
        };
        journal.publish_anchor(0)?;
        Ok(match archive {
            Some(archive) => Opened::IsolatedPrevious { journal, archive },
            None => Opened::Ready(journal),
        })
    }

    fn finish_open(
        dir: &Path,
        conn: Connection,
        keys: KeySet,
        anchors: Box<dyn AnchorStore>,
        stored_anchor: Option<String>,
        lock: File,
    ) -> Result<Self, JournalError> {
        if meta(&conn, "schema")?.as_deref() != Some(&SCHEMA_VERSION.to_string()) {
            return Err(JournalError::Corrupt);
        }
        let db_id = meta(&conn, "db_id")?.ok_or(JournalError::Corrupt)?;
        if db_id != format!("db-{}", keys.id().as_str()) {
            return Err(JournalError::Corrupt);
        }
        let mut journal = Self {
            dir: dir.to_path_buf(),
            limits: QuotaLimits::default(),
            conn,
            keys,
            anchors,
            db_id,
            mode: RecoveryMode::Normal,
            generation: 0,
            pending_disposal: None,
            pending_archive: None,
            _lock: lock,
        };
        let generation = authenticated_generation(&journal.conn, &journal.keys, &journal.db_id)?;
        let anchor = stored_anchor.map(|value| parse_anchor(&journal.keys, &journal.db_id, &value));
        let flagged = meta(&journal.conn, "verify_only")?.is_some();
        let has_records: bool =
            journal
                .conn
                .query_row("SELECT EXISTS(SELECT 1 FROM records)", [], |r| r.get(0))?;
        let restored = flagged
            || match (generation, anchor) {
                // Forged generation, or rows that do not match it (splice).
                (None, _) => true,
                // Forged anchor.
                (Some(_), Some(None)) => true,
                // Older than the published generation.
                (Some(db), Some(Some(published))) => published > db,
                // Records but never anchored.
                (Some(_), None) => has_records,
            };
        if restored {
            // Continue above every generation ever published: the anchor never regresses.
            let floor = stored_generation(&journal.conn)?
                .unwrap_or(0)
                .max(anchor.flatten().unwrap_or(0));
            journal.enter_verify_only(floor)?;
        } else if let Some(db) = generation {
            journal.generation = db;
            // Covers a crash after a commit and before its anchor was published.
            if anchor != Some(Some(db)) {
                let _ = journal.publish_anchor(db);
            }
        }
        // Best effort, after the generation is known; retried on every open.
        let _ = journal.resume_archive_disposals();
        if meta(&journal.conn, "wal_cleanup_pending")?.is_some() {
            let _ = journal.finish_wal_cleanup();
        }
        Ok(journal)
    }

    /// Demote every `Reserved` record to read-back-only and set the verify-only
    /// flag, in one transaction. Rows that fail authentication are left as they
    /// are; recovery reports them as `Quarantine`.
    fn enter_verify_only(&mut self, floor: u64) -> Result<(), JournalError> {
        self.mode = RecoveryMode::VerifyOnly;
        let result = self.mutate_unchecked(|tx, keys| {
            let all: Vec<(Vec<u8>, String)> = tx
                .prepare("SELECT owner, event_id FROM records")?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<Result<_, _>>()?;
            for (owner, event_id) in all {
                match read_row_any(tx, keys, &owner, &event_id) {
                    Ok(Some((mut row, zone))) => {
                        if zone == "active" && row.phase == DispatchPhase::Reserved {
                            row.phase = DispatchPhase::Restored;
                            update_row(tx, keys, &owner, &event_id, &row)?;
                        }
                    }
                    Ok(None) => {}
                    // Never re-sign a row we could not authenticate as it is.
                    Err(_) => quarantine_row(tx, keys, &owner, &event_id)?,
                }
            }
            tx.execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES ('verify_only', '1')",
                [],
            )?;
            tx.execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES ('generation', ?1)",
                params![floor.to_string()],
            )?;
            Ok(())
        });
        match result {
            Ok(()) | Err(JournalError::AnchorFailed) => Ok(()),
            Err(error) => Err(error),
        }
    }

    /// Move `db/` into `isolated/<ms>-<label>/` with a single rename.
    fn isolate(dir: &Path, label: &str) -> Result<PathBuf, JournalError> {
        let isolated = dir.join("isolated");
        std::fs::create_dir_all(&isolated).map_err(|_| JournalError::Io)?;
        if !real_dir(&isolated) || !real_dir(&dir.join(DB_DIR)) {
            return Err(JournalError::UnsafePath);
        }
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0);
        let mut archive = isolated.join(format!("{stamp}-{label}"));
        let mut suffix = 0;
        while archive.exists() {
            suffix += 1;
            archive = isolated.join(format!("{stamp}-{label}-{suffix}"));
        }
        std::fs::rename(dir.join(DB_DIR), &archive).map_err(|_| JournalError::Io)?;
        sync_dir(&isolated).map_err(|_| JournalError::Io)?;
        sync_dir(dir).map_err(|_| JournalError::Io)?;
        Ok(archive)
    }

    /// Publish `generation`. On failure the journal becomes verify-only.
    fn publish_anchor(&mut self, generation: u64) -> Result<(), JournalError> {
        let value = anchor_value(&self.keys, &self.db_id, generation);
        if self.anchors.write(&self.db_id, &value).is_ok() {
            return Ok(());
        }
        self.mode = RecoveryMode::VerifyOnly;
        let _ = self.conn.execute(
            "INSERT OR REPLACE INTO meta(k, v) VALUES ('verify_only', '1')",
            [],
        );
        Err(JournalError::AnchorFailed)
    }

    pub fn mode(&self) -> RecoveryMode {
        self.mode
    }

    /// Replace the storage policy (product configuration and tests).
    pub fn set_limits(&mut self, limits: QuotaLimits) {
        self.limits = limits;
    }

    fn disk_bytes(&self) -> u64 {
        tree_bytes(&self.dir.join(DB_DIR)) + tree_bytes(&self.dir.join("isolated"))
    }

    pub fn quota(&self, owner_scope: &str) -> Result<QuotaReport, JournalError> {
        let owner = owner_digest(owner_scope);
        let (active_records, active_bytes, isolated_bytes): (i64, i64, i64) = self.conn.query_row(
            "SELECT
                 COALESCE(SUM(zone='active'), 0),
                 COALESCE(SUM(CASE WHEN zone='active' THEN length(sealed) END), 0),
                 COALESCE(SUM(CASE WHEN zone='isolated' THEN length(sealed) END), 0)
             FROM records WHERE owner=?1",
            params![&owner[..]],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        Ok(QuotaReport {
            active_records: active_records as u64,
            active_bytes: active_bytes as u64,
            isolated_bytes: isolated_bytes as u64,
            total_disk_bytes: self.disk_bytes(),
            limits: self.limits,
        })
    }

    /// Acknowledge verify-only mode (e.g. after the user saw the restore
    /// notice). Records from the restored copy stay read-back-only.
    pub fn finish_verification(&mut self) -> Result<(), JournalError> {
        self.mutate(|tx, _| {
            tx.execute("DELETE FROM meta WHERE k='verify_only'", [])?;
            Ok(())
        })?;
        self.mode = RecoveryMode::Normal;
        Ok(())
    }

    /// Fold the WAL into the main database file.
    /// Returns false when a reader kept it from completing.
    pub fn checkpoint(&self) -> Result<bool, JournalError> {
        let busy: i64 = self
            .conn
            .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |r| r.get(0))?;
        Ok(busy == 0)
    }

    /// Fold the WAL after a disposal and clear the pending flag once done.
    fn finish_wal_cleanup(&self) -> Result<bool, JournalError> {
        if !self.checkpoint()? {
            return Ok(false);
        }
        self.conn
            .execute("DELETE FROM meta WHERE k='wal_cleanup_pending'", [])?;
        // Fold the flag change too, so the WAL holds nothing from before.
        self.checkpoint()
    }

    /// Run `change` in an IMMEDIATE transaction that also bumps the
    /// authenticated generation, then publish the anchor. `AnchorFailed` means
    /// the change is committed but must not be relied on for dispatch.
    ///
    /// The transaction first checks that the database still carries the
    /// generation we last committed, over the current row set. Anything else
    /// (another connection edited or spliced rows while we held the journal)
    /// is handled as a restore and the change is refused with `VerifyOnly`.
    fn mutate<T>(
        &mut self,
        change: impl FnOnce(&Connection, &KeySet) -> Result<T, JournalError>,
    ) -> Result<T, JournalError> {
        // `change` closures never return `VerifyOnly`; only the check does.
        match self.transact(true, change) {
            Err(JournalError::VerifyOnly) => {
                let floor = stored_generation(&self.conn)?
                    .unwrap_or(0)
                    .max(self.generation);
                self.enter_verify_only(floor)?;
                Err(JournalError::VerifyOnly)
            }
            other => other,
        }
    }

    fn mutate_unchecked<T>(
        &mut self,
        change: impl FnOnce(&Connection, &KeySet) -> Result<T, JournalError>,
    ) -> Result<T, JournalError> {
        self.transact(false, change)
    }

    /// One IMMEDIATE transaction. With `check`, the authenticated generation is
    /// compared with ours inside the write lock, before `change` runs; a
    /// mismatch rolls back and returns `VerifyOnly`.
    fn transact<T>(
        &mut self,
        check: bool,
        change: impl FnOnce(&Connection, &KeySet) -> Result<T, JournalError>,
    ) -> Result<T, JournalError> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        if check && authenticated_generation(&tx, &self.keys, &self.db_id)? != Some(self.generation)
        {
            drop(tx);
            return Err(JournalError::VerifyOnly);
        }
        let out = change(&tx, &self.keys)?;
        let next = bump_generation(&tx, &self.keys, &self.db_id)?;
        tx.commit()?;
        self.generation = next;
        self.publish_anchor(next)?;
        Ok(out)
    }

    fn aad(owner: &[u8; 32], event_id: &str) -> Result<Aad, JournalError> {
        Aad::new(SCHEMA_VERSION, *owner, event_id, RecordKind::TeamBackup)
            .map_err(|_| JournalError::InvalidInput)
    }

    /// Durably record the payload before any POST. Same eventId and payload is
    /// idempotent; a different payload for the same owner and eventId conflicts.
    pub fn reserve(
        &mut self,
        owner_scope: &str,
        payload: &BackupPayload,
    ) -> Result<ReserveOutcome, JournalError> {
        let owner = owner_digest(owner_scope);
        let plain = Zeroizing::new(
            serde_json::to_vec(&SealPayload {
                v: 1,
                target_id: payload.target_id(),
                text: payload.text(),
                project_id: payload.project().map(|p| p.id()),
                repository: payload.project().map(|p| p.repository()),
            })
            .map_err(|_| JournalError::InvalidInput)?,
        );
        let event_id = payload.event_id().to_string();
        let payload_tag = self
            .keys
            .tag_parts("team-payload", &[&owner, event_id.as_bytes(), &plain])
            .to_vec();
        let sealed = self
            .keys
            .seal(&Self::aad(&owner, &event_id)?, &plain)
            .map_err(|_| JournalError::InvalidInput)?;
        // An idempotent or refused reserve writes nothing (no generation bump).
        match read_row_any(&self.conn, &self.keys, &owner, &event_id)? {
            Some((_, zone)) if zone == "tombstone" => return Err(JournalError::Tombstoned),
            Some((row, zone)) if zone == "active" && row.payload_tag == payload_tag => {
                return Ok(ReserveOutcome::AlreadyReserved)
            }
            Some(_) => return Err(JournalError::Conflict),
            None => {}
        }
        let limits = self.limits;
        // Upper bound on growth: payload pages, B-tree splits and the meta
        // updates, each written to the WAL (page + 24-byte frame header) and
        // later to the database file.
        let page: u64 = self.conn.query_row("PRAGMA page_size", [], |r| r.get(0))?;
        let pages = (sealed.len() as u64).div_ceil(page.saturating_sub(128).max(512)) + 6;
        let disk_after = self.disk_bytes() + pages * (page + 24) * 2;
        let disk_ok = disk_after
            <= limits
                .max_total_bytes
                .saturating_sub(limits.emergency_reserve_bytes);
        self.mutate(|tx, keys| {
            let present: bool = tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM records WHERE owner=?1 AND event_id=?2)",
                params![&owner[..], &event_id],
                |r| r.get(0),
            )?;
            let existing = if present {
                match read_row_any(tx, keys, &owner, &event_id)? {
                    Some((_, zone)) if zone == "tombstone" => return Err(JournalError::Tombstoned),
                    Some((row, zone)) if zone == "active" => Some(row.payload_tag),
                    _ => Some(Vec::new()), // isolated: never reuse
                }
            } else {
                None
            };
            match existing {
                Some(stored) if stored == payload_tag => Ok(ReserveOutcome::AlreadyReserved),
                Some(_) => Err(JournalError::Conflict),
                None => {
                    let (count, bytes): (i64, i64) = tx.query_row(
                        "SELECT COUNT(*), COALESCE(SUM(length(sealed)), 0) FROM records
                         WHERE owner=?1 AND zone='active'",
                        params![&owner[..]],
                        |r| Ok((r.get(0)?, r.get(1)?)),
                    )?;
                    if count as u64 >= limits.max_active_records_per_owner {
                        return Err(JournalError::QuotaExceeded(QuotaKind::ActiveRecords));
                    }
                    if bytes as u64 + sealed.len() as u64 > limits.max_active_bytes_per_owner {
                        return Err(JournalError::QuotaExceeded(QuotaKind::ActiveBytes));
                    }
                    if !disk_ok {
                        return Err(JournalError::QuotaExceeded(QuotaKind::DiskBudget));
                    }
                    let at = now();
                    let row = Row {
                        sealed,
                        payload_tag,
                        phase: DispatchPhase::Reserved,
                        state: None,
                        seq: 0,
                        created_at: at,
                    };
                    tx.execute(
                        "INSERT INTO records(owner, event_id, key_id, sealed, payload_tag, phase,
                                             row_tag, created_at, updated_at)
                         VALUES (?1, ?2, ?3, ?4, ?5, 'reserved', ?6, ?7, ?7)",
                        params![
                            &owner[..],
                            &event_id,
                            keys.id().as_str(),
                            &row.sealed,
                            &row.payload_tag,
                            row_tag(keys, &owner, &event_id, "active", &row),
                            at
                        ],
                    )?;
                    Ok(ReserveOutcome::Reserved)
                }
            }
        })
    }

    fn set_phase(
        &mut self,
        owner_scope: &str,
        event_id: &str,
        from: DispatchPhase,
        to: DispatchPhase,
    ) -> Result<(), JournalError> {
        let owner = owner_digest(owner_scope);
        self.mutate(|tx, keys| {
            let mut row = read_row(tx, keys, &owner, event_id)?.ok_or(JournalError::NotFound)?;
            if row.phase != from {
                return Err(JournalError::InvalidTransition);
            }
            row.phase = to;
            update_row(tx, keys, &owner, event_id, &row)
        })
    }

    /// Durably mark that a POST is about to be attempted. POST only after this
    /// returns `Ok`: any error (including `AnchorFailed`) means do not send.
    pub fn begin_dispatch(
        &mut self,
        owner_scope: &str,
        event_id: &str,
    ) -> Result<(), JournalError> {
        if self.mode == RecoveryMode::VerifyOnly {
            return Err(JournalError::VerifyOnly);
        }
        self.set_phase(
            owner_scope,
            event_id,
            DispatchPhase::Reserved,
            DispatchPhase::Dispatching,
        )
    }

    /// The transport proved no request bytes left the process (`PreDispatch`).
    pub fn dispatch_not_sent(
        &mut self,
        owner_scope: &str,
        event_id: &str,
    ) -> Result<(), JournalError> {
        self.set_phase(
            owner_scope,
            event_id,
            DispatchPhase::Dispatching,
            DispatchPhase::Reserved,
        )
    }

    /// Store a verified server state from a submission or read-back with a
    /// strictly increasing sequence. Terminal states never change.
    pub fn record_server_state(
        &mut self,
        owner_scope: &str,
        event_id: &str,
        state: DeliveryState,
        seq: u64,
    ) -> Result<(), JournalError> {
        state_label(state).ok_or(JournalError::InvalidInput)?;
        i64::try_from(seq).map_err(|_| JournalError::InvalidInput)?;
        let owner = owner_digest(owner_scope);
        self.mutate(|tx, keys| {
            let mut row = read_row(tx, keys, &owner, event_id)?.ok_or(JournalError::NotFound)?;
            if seq <= row.seq {
                return Err(JournalError::StaleSequence);
            }
            if row
                .state
                .is_some_and(|current| is_terminal(current) && current != state)
            {
                return Err(JournalError::InvalidTransition);
            }
            row.phase = DispatchPhase::Accepted;
            row.state = Some(state);
            row.seq = seq;
            update_row(tx, keys, &owner, event_id, &row)
        })
    }

    pub fn load(
        &self,
        owner_scope: &str,
        event_id: &str,
    ) -> Result<Option<StoredRecord>, JournalError> {
        self.load_on(&self.conn, owner_scope, event_id)
    }

    /// `load` on a given connection or transaction, so callers can read and
    /// verify within one snapshot.
    fn load_on(
        &self,
        conn: &Connection,
        owner_scope: &str,
        event_id: &str,
    ) -> Result<Option<StoredRecord>, JournalError> {
        let owner = owner_digest(owner_scope);
        let Some(row) = read_row(conn, &self.keys, &owner, event_id)? else {
            return Ok(None);
        };
        let plain = self
            .keys
            .open(&Self::aad(&owner, event_id)?, &row.sealed)
            .map_err(|_| JournalError::Corrupt)?;
        let expected = self
            .keys
            .tag_parts("team-payload", &[&owner, event_id.as_bytes(), &plain]);
        if expected[..] != row.payload_tag[..] {
            return Err(JournalError::Corrupt);
        }
        let body: OpenedPayload =
            serde_json::from_slice(&plain).map_err(|_| JournalError::Corrupt)?;
        if body.v != 1 {
            return Err(JournalError::Corrupt);
        }
        let project = match (body.project_id, body.repository) {
            (Some(id), Some(repository)) => {
                Some(ProjectSnapshot::new(id, repository).map_err(|_| JournalError::Corrupt)?)
            }
            (None, None) => None,
            _ => return Err(JournalError::Corrupt),
        };
        let payload = BackupPayload::new(event_id.to_string(), body.target_id, body.text, project)
            .map_err(|_| JournalError::Corrupt)?;
        Ok(Some(StoredRecord {
            payload,
            phase: row.phase,
            server_state: row.state,
            poll_seq: row.seq,
        }))
    }

    /// Read-only preview of disposing `events` of `owner`. The returned token is
    /// bound to the exact current rows; any change makes it stale.
    pub fn preview_disposal(
        &mut self,
        owner_scope: &str,
        events: &[&str],
    ) -> Result<DisposalPreview, JournalError> {
        if events.is_empty() || events.len() > MAX_DISPOSAL_EVENTS {
            return Err(JournalError::InvalidInput);
        }
        let mut events: Vec<String> = events.iter().map(|e| e.to_string()).collect();
        events.sort();
        events.dedup();
        let owner = owner_digest(owner_scope);
        for event in &events {
            Self::aad(&owner, event)?; // validates the identifier
        }
        let preview = self.compute_preview(&owner, &events)?;
        self.pending_disposal = Some((preview.token.clone(), owner, preview.events.clone()));
        Ok(preview)
    }

    fn compute_preview(
        &self,
        owner: &[u8; 32],
        events: &[String],
    ) -> Result<DisposalPreview, JournalError> {
        let snapshot = self.conn.unchecked_transaction()?;
        if authenticated_generation(&snapshot, &self.keys, &self.db_id)? != Some(self.generation) {
            return Err(JournalError::VerifyOnly);
        }
        let mut included = Vec::new();
        let mut already_disposed = Vec::new();
        let (mut bytes, mut risky) = (0u64, 0u64);
        let mut binding: Vec<Vec<u8>> =
            vec![owner.to_vec(), self.generation.to_be_bytes().to_vec()];
        for event in events {
            let (row, zone) =
                read_row_any(&snapshot, &self.keys, owner, event)?.ok_or(JournalError::NotFound)?;
            if zone == "tombstone" {
                already_disposed.push(event.clone());
                continue;
            }
            bytes += row.sealed.len() as u64;
            let never_sent = zone == "active" && row.phase == DispatchPhase::Reserved;
            let settled_undelivered = matches!(
                row.state,
                Some(DeliveryState::Rejected | DeliveryState::Conflict)
            );
            if !never_sent && !settled_undelivered {
                risky += 1;
            }
            binding.push(event.as_bytes().to_vec());
            binding.push(row_tag(&self.keys, owner, event, &zone, &row));
            included.push(event.clone());
        }
        let parts: Vec<&[u8]> = binding.iter().map(Vec::as_slice).collect();
        Ok(DisposalPreview {
            token: hex(&self.keys.tag_parts("disposal-preview", &parts)),
            count: included.len() as u64,
            events: included,
            sealed_bytes: bytes,
            may_have_been_delivered: risky,
            already_disposed,
        })
    }

    fn check_confirmation(confirmation: &Disposal) -> Result<(), JournalError> {
        if valid_text(&confirmation.reason, 500) && valid_text(&confirmation.actor_local, 128) {
            Ok(())
        } else {
            Err(JournalError::InvalidInput)
        }
    }

    /// Dispose of the previewed records: in one transaction, write an audit row
    /// and turn each record into an integrity-protected tombstone without
    /// ciphertext; then fold the WAL so no copy of the ciphertext remains. The
    /// cloud copy is never touched. Tombstoned eventIds can never be reused.
    pub fn dispose(
        &mut self,
        owner_scope: &str,
        confirmation: Disposal,
    ) -> Result<DisposalOutcome, JournalError> {
        Self::check_confirmation(&confirmation)?;
        let owner = owner_digest(owner_scope);
        let Some((token, pending_owner, events)) = self.pending_disposal.clone() else {
            return Err(JournalError::StalePreview);
        };
        if token != confirmation.token || pending_owner != owner {
            return Err(JournalError::StalePreview);
        }
        let current = self.compute_preview(&owner, &events)?;
        if current.token != token || current.events != events {
            return Err(JournalError::StalePreview);
        }
        if current.may_have_been_delivered > 0 && !confirmation.acknowledge_possible_delivery {
            return Err(JournalError::ConfirmationRequired);
        }
        let limit = self.limits.max_tombstones_per_owner;
        let export = confirmation.export.label();
        let at = now();
        let disposed = self.mutate(|tx, keys| {
            let tombstones: i64 = tx.query_row(
                "SELECT COUNT(*) FROM records WHERE owner=?1 AND zone='tombstone'",
                params![&owner[..]],
                |r| r.get(0),
            )?;
            if tombstones as u64 + events.len() as u64 > limit {
                return Err(JournalError::QuotaExceeded(QuotaKind::Tombstones));
            }
            for event in &events {
                let (mut row, _) = read_row_any(tx, keys, &owner, event)?.ok_or(JournalError::NotFound)?;
                let tag = keys.tag_parts(
                    "disposal-audit",
                    &[
                        &owner,
                        event.as_bytes(),
                        keys.id().as_str().as_bytes(),
                        export.as_bytes(),
                        confirmation.reason.as_bytes(),
                        confirmation.actor_local.as_bytes(),
                        &at.to_be_bytes(),
                    ],
                );
                tx.execute(
                    "INSERT INTO disposals(owner, event_id, key_id, export, reason, actor_local, at, tag)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    params![&owner[..], event, keys.id().as_str(), export, confirmation.reason, confirmation.actor_local, at, &tag[..]],
                )?;
                row.sealed = Vec::new();
                tx.execute(
                    "UPDATE records SET sealed=x'', zone='tombstone', row_tag=?3, updated_at=?4
                     WHERE owner=?1 AND event_id=?2",
                    params![&owner[..], event, row_tag(keys, &owner, event, "tombstone", &row), at],
                )?;
            }
            // Old ciphertext stays in the WAL until it is folded; finish on open if needed.
            tx.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('wal_cleanup_pending', '1')", [])?;
            Ok(events.len() as u64)
        })?;
        self.pending_disposal = None;
        let wal_cleanup_complete = self.finish_wal_cleanup().unwrap_or(false);
        Ok(DisposalOutcome {
            disposed,
            wal_cleanup_complete,
        })
    }

    /// Disposed eventIds of `owner`, from the audit table.
    pub fn disposal_audit(&self, owner_scope: &str) -> Result<Vec<String>, JournalError> {
        let owner = owner_digest(owner_scope);
        Ok(self
            .conn
            .prepare("SELECT event_id FROM disposals WHERE owner=?1 ORDER BY id")?
            .query_map(params![&owner[..]], |r| r.get(0))?
            .collect::<Result<_, _>>()?)
    }

    fn archive_path(&self, name: &str) -> Result<PathBuf, JournalError> {
        if !valid_archive_name(name) {
            return Err(JournalError::InvalidInput);
        }
        let isolated = self.dir.join("isolated");
        if !real_dir(&self.dir) || !real_dir(&isolated) {
            return Err(JournalError::InvalidInput);
        }
        let path = isolated.join(name);
        match std::fs::symlink_metadata(&path) {
            Ok(meta) if meta.is_dir() && !meta.file_type().is_symlink() => Ok(path),
            Ok(_) => Err(JournalError::InvalidInput),
            Err(_) => Err(JournalError::NotFound),
        }
    }

    /// Archives created when a key was unrecoverable. Their contents cannot be
    /// authenticated, so they are only ever handled whole.
    pub fn isolated_archives(&self) -> Result<Vec<IsolatedArchive>, JournalError> {
        if !real_dir(&self.dir.join("isolated")) {
            return Ok(Vec::new());
        }
        let Ok(entries) = std::fs::read_dir(self.dir.join("isolated")) else {
            return Ok(Vec::new());
        };
        let mut out: Vec<IsolatedArchive> = entries
            .flatten()
            .filter(|e| e.file_type().is_ok_and(|t| t.is_dir() && !t.is_symlink()))
            .filter_map(|e| e.file_name().into_string().ok())
            .filter(|name| !name.starts_with('.'))
            .map(|name| IsolatedArchive {
                bytes: tree_bytes(&self.dir.join("isolated").join(&name)),
                name,
            })
            .collect();
        out.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(out)
    }

    pub fn preview_archive_disposal(
        &mut self,
        name: &str,
    ) -> Result<DisposalPreview, JournalError> {
        let path = self.archive_path(name)?;
        let bytes = tree_bytes(&path);
        let token = self.archive_token(name, &tree_fingerprint(&path));
        self.pending_archive = Some((token.clone(), name.to_string()));
        Ok(DisposalPreview {
            token,
            events: Vec::new(),
            count: 1,
            sealed_bytes: bytes,
            // Unverifiable contents: assume some records may have been delivered.
            may_have_been_delivered: 1,
            already_disposed: Vec::new(),
        })
    }

    /// Dispose of a whole isolated archive: record the decision first, then
    /// delete; an interrupted deletion is finished on the next open.
    pub fn dispose_archive(
        &mut self,
        name: &str,
        confirmation: Disposal,
    ) -> Result<DisposalOutcome, JournalError> {
        Self::check_confirmation(&confirmation)?;
        let Some((token, pending_name)) = self.pending_archive.clone() else {
            return Err(JournalError::StalePreview);
        };
        if token != confirmation.token || pending_name != name {
            return Err(JournalError::StalePreview);
        }
        let path = self.archive_path(name)?;
        let fingerprint = tree_fingerprint(&path);
        if self.archive_token(name, &fingerprint) != token {
            return Err(JournalError::StalePreview);
        }
        if !confirmation.acknowledge_possible_delivery {
            return Err(JournalError::ConfirmationRequired);
        }
        let at = now();
        let export = confirmation.export.label();
        self.mutate(|tx, keys| {
            let tag = archive_tag(keys, name, export, &confirmation.reason, &confirmation.actor_local, "pending", &fingerprint, at);
            tx.execute(
                "INSERT OR REPLACE INTO archive_disposals(name, export, reason, actor_local, status, fingerprint, at, tag)
                 VALUES (?1, ?2, ?3, ?4, 'pending', ?5, ?6, ?7)",
                params![name, export, confirmation.reason, confirmation.actor_local, fingerprint, at, &tag[..]],
            )?;
            Ok(())
        })?;
        self.pending_archive = None;
        self.finish_archive_disposal(name)?;
        Ok(DisposalOutcome {
            disposed: 1,
            wal_cleanup_complete: true,
        })
    }

    fn archive_token(&self, name: &str, fingerprint: &str) -> String {
        hex(&self.keys.tag_parts(
            "archive-disposal-preview",
            &[
                name.as_bytes(),
                fingerprint.as_bytes(),
                &self.generation.to_be_bytes(),
            ],
        ))
    }

    /// Delete an archive only under an authenticated pending decision whose
    /// recorded fingerprint still matches the directory, then mark it done.
    fn finish_archive_disposal(&mut self, name: &str) -> Result<(), JournalError> {
        type Decision = (String, String, String, String, String, i64, Vec<u8>);
        let decision: Option<Decision> = self
            .conn
            .query_row(
                "SELECT export, reason, actor_local, status, fingerprint, at, tag
                 FROM archive_disposals WHERE name=?1",
                params![name],
                |r| {
                    Ok((
                        r.get(0)?,
                        r.get(1)?,
                        r.get(2)?,
                        r.get(3)?,
                        r.get(4)?,
                        r.get(5)?,
                        r.get(6)?,
                    ))
                },
            )
            .optional()?;
        let Some((export, reason, actor, status, fingerprint, at, tag)) = decision else {
            return Err(JournalError::NotFound);
        };
        let expected = archive_tag(
            &self.keys,
            name,
            &export,
            &reason,
            &actor,
            &status,
            &fingerprint,
            at,
        );
        if status != "pending" || expected[..] != tag[..] {
            return Err(JournalError::Corrupt);
        }
        // Move the archive aside with one rename, then delete it. A deletion
        // interrupted half way resumes on the moved directory, whose partial
        // contents would no longer match the fingerprint.
        let deleting = self.dir.join("isolated").join(format!(".deleting-{name}"));
        match self.archive_path(name) {
            Ok(path) => {
                if tree_fingerprint(&path) != fingerprint {
                    return Err(JournalError::StalePreview);
                }
                std::fs::rename(&path, &deleting).map_err(|_| JournalError::Io)?;
                sync_dir(&self.dir.join("isolated")).map_err(|_| JournalError::Io)?;
            }
            Err(JournalError::NotFound) => {}
            Err(error) => return Err(error),
        }
        if real_dir(&deleting) {
            std::fs::remove_dir_all(&deleting).map_err(|_| JournalError::Io)?;
        }
        sync_dir(&self.dir.join("isolated")).map_err(|_| JournalError::Io)?;
        self.mutate(|tx, keys| {
            let done = archive_tag(
                keys,
                name,
                &export,
                &reason,
                &actor,
                "done",
                &fingerprint,
                at,
            );
            tx.execute(
                "UPDATE archive_disposals SET status='done', tag=?2 WHERE name=?1",
                params![name, &done[..]],
            )?;
            Ok(())
        })
    }

    fn resume_archive_disposals(&mut self) -> Result<(), JournalError> {
        let pending: Vec<String> = self
            .conn
            .prepare("SELECT name FROM archive_disposals WHERE status='pending'")?
            .query_map([], |r| r.get(0))?
            .collect::<Result<_, _>>()?;
        for name in pending {
            // Unauthenticated or changed decisions are never acted on.
            let _ = self.finish_archive_disposal(&name);
        }
        Ok(())
    }

    /// `(archive name, status)` of every archive disposal decision.
    pub fn archive_audit(&self) -> Result<Vec<(String, String)>, JournalError> {
        Ok(self
            .conn
            .prepare("SELECT name, status FROM archive_disposals ORDER BY at, name")?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<Result<_, _>>()?)
    }

    /// Record ids of `owner`, newest first. The cursor only marks a position;
    /// it grants nothing and is validated like any other input.
    pub fn page(
        &self,
        owner_scope: &str,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<(Vec<String>, Option<String>), JournalError> {
        if limit == 0 || limit > MAX_PAGE {
            return Err(JournalError::InvalidInput);
        }
        let after = match cursor {
            Some(value) => Some(parse_cursor(value).ok_or(JournalError::InvalidInput)?),
            None => None,
        };
        let owner = owner_digest(owner_scope);
        let (at, event) = after.unwrap_or((i64::MAX, "\u{10FFFF}".to_string()));
        let rows: Vec<(String, i64)> = self
            .conn
            .prepare(
                "SELECT event_id, created_at FROM records
                 WHERE owner=?1 AND zone IN ('active','isolated')
                   AND (created_at < ?2 OR (created_at = ?2 AND event_id < ?3))
                 ORDER BY created_at DESC, event_id DESC LIMIT ?4",
            )?
            .query_map(params![&owner[..], at, event, (limit + 1) as i64], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })?
            .collect::<Result<_, _>>()?;
        let more = rows.len() > limit;
        let page: Vec<(String, i64)> = rows.into_iter().take(limit).collect();
        let next = if more {
            page.last().map(|(event, at)| format!("c1.{at}.{event}"))
        } else {
            None
        };
        Ok((page.into_iter().map(|(event, _)| event).collect(), next))
    }

    /// The retained original of one record, for an explicit user action. It
    /// is returned only if the database still matches our generation, the row
    /// authenticates for this owner and key, the ciphertext opens under its
    /// owner/event associated data, and the payload digest matches. Anything
    /// else returns an error and no text.
    pub fn read_original(
        &self,
        owner_scope: &str,
        event_id: &str,
    ) -> Result<Original, JournalError> {
        // One read snapshot for the generation check, the row, its
        // authentication and decryption, and its timestamp.
        let snapshot = self.conn.unchecked_transaction()?;
        if authenticated_generation(&snapshot, &self.keys, &self.db_id)? != Some(self.generation) {
            return Err(JournalError::VerifyOnly);
        }
        let record = self
            .load_on(&snapshot, owner_scope, event_id)?
            .ok_or(JournalError::NotFound)?;
        let owner = owner_digest(owner_scope);
        let created_at: i64 = snapshot.query_row(
            "SELECT created_at FROM records WHERE owner=?1 AND event_id=?2",
            params![&owner[..], event_id],
            |r| r.get(0),
        )?;
        drop(snapshot);
        Ok(Original {
            payload: record.payload,
            created_at,
        })
    }

    /// What the caller may do with each active record of `owner` after a restart.
    pub fn recovery(
        &self,
        owner_scope: &str,
    ) -> Result<Vec<(String, RecoveryAction)>, JournalError> {
        // One read snapshot for the check and every row read below.
        let snapshot = self.conn.unchecked_transaction()?;
        if authenticated_generation(&snapshot, &self.keys, &self.db_id)? != Some(self.generation) {
            // Changed underneath us: the next mutation runs the restore path.
            return Err(JournalError::VerifyOnly);
        }
        let owner = owner_digest(owner_scope);
        let events: Vec<String> = snapshot
            .prepare("SELECT event_id FROM records WHERE owner=?1 ORDER BY event_id")?
            .query_map(params![&owner[..]], |r| r.get(0))?
            .collect::<Result<_, _>>()?;
        let mut out = Vec::with_capacity(events.len());
        for event in events {
            let action = match read_row_any(&snapshot, &self.keys, &owner, &event) {
                Ok(Some((_, zone))) if zone == "isolated" => RecoveryAction::Quarantine,
                Ok(Some((_, zone))) if zone != "active" => continue,
                Ok(Some((row, _))) => match row.phase {
                    DispatchPhase::Reserved if self.mode == RecoveryMode::Normal => {
                        RecoveryAction::MayPost
                    }
                    DispatchPhase::Reserved
                    | DispatchPhase::Dispatching
                    | DispatchPhase::Restored => RecoveryAction::ReadBackOnly,
                    DispatchPhase::Accepted => match row.state {
                        Some(state) if is_terminal(state) => RecoveryAction::Final,
                        _ => RecoveryAction::Poll,
                    },
                },
                Ok(None) => continue,
                Err(_) => RecoveryAction::Quarantine,
            };
            out.push((event, action));
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::sqlite_uri_path;

    #[test]
    fn uri_paths_cover_unix_drive_and_unc_forms() {
        assert_eq!(
            sqlite_uri_path("/home/u/journal.sqlite"),
            "/home/u/journal.sqlite"
        );
        assert_eq!(
            sqlite_uri_path(r"\\?\C:\Data\u\j.sqlite"),
            "/C:/Data/u/j.sqlite"
        );
        assert_eq!(
            sqlite_uri_path(r"C:\Data\u\j.sqlite"),
            "/C:/Data/u/j.sqlite"
        );
        assert_eq!(
            sqlite_uri_path(r"\\?\UNC\server\share\j.sqlite"),
            "//server/share/j.sqlite"
        );
        assert_eq!(
            sqlite_uri_path(r"\\server\share\j.sqlite"),
            "//server/share/j.sqlite"
        );
    }
}
