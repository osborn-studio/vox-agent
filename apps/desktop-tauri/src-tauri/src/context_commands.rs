//! Minimal Context record IPC (T10).
//!
//! Thin layer over `ContextService`: strict input DTOs, enum error codes, and
//! output DTOs without text, tokens, keys or paths. No command accepts a URL,
//! an identity, a token or a delivery state. The service is configured only
//! from Rust (sign-in, T24a; DPAPI key provider, T07b); until then every
//! command answers `not-configured`.
use crate::context_account::{
    apply_renewal, discover_for, AccountError, AccountSlot, ConversationKind, RenewalApplied,
};
use crate::context_delivery::{BackupPayload, DeliveryState, ProjectSnapshot};
use crate::context_journal::{DispatchPhase, Journal, JournalError, RecoveryAction};
use crate::context_policy::{Next, Stop, UserAction};
use crate::context_service::{ContextService, RecordStatus, ServiceError};
use crate::context_wire::ContextEndpoint;
use crate::secrets::NativeSecretStore;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::time::{Duration, SystemTime};
use tauri::{AppHandle, Emitter, Manager, State};
use zeroize::Zeroizing;

pub const RECORD_EVENT: &str = "vox-agent://context-record";
/// Emitted when Rust ends the account on its own (session renewal refused).
pub const ACCOUNT_EVENT: &str = "vox-agent://context-account";

/// Tauri state: empty until Rust configures a service for a verified session.
/// `drivers` holds the records that already have a polling task, so each
/// record has at most one, sleeps included.
pub struct ContextState {
    account: AccountSlot<NativeSecretStore>,
    drivers: Arc<Mutex<HashSet<String>>>,
    /// The Context server, set once by Rust at startup; never from IPC.
    endpoint: OnceLock<ContextEndpoint>,
    /// Why the last sign-in did or did not get an encrypted journal
    /// (`JournalOpenReport::key_store_label`), for diagnostics only.
    journal_label: Mutex<&'static str>,
    /// When the current access token was issued and for how long; `None`
    /// when nothing is scheduled (signed out, or no refresh token offered).
    renewal: Mutex<Option<RenewalSchedule>>,
    /// Fixed status line for diagnostics (`none`, `scheduled`, `renewed`,
    /// `not-offered`, `renewal-unavailable:<CODE>`, `signed-out:<CODE>`).
    renewal_label: Mutex<String>,
}

/// When the current access token was issued and for how long. Wall-clock
/// time, so a token that expired while the machine slept is due at once.
#[derive(Debug, Clone, Copy)]
pub struct RenewalTimer {
    expires_in: u64,
    issued_at: SystemTime,
}

/// The timer plus the session it belongs to: a result or sign-out for one
/// service can never clear or replace the schedule of a newer one.
#[derive(Clone)]
struct RenewalSchedule {
    timer: RenewalTimer,
    service: Weak<ContextService<NativeSecretStore>>,
}

impl RenewalTimer {
    pub fn new(expires_in: u64, issued_at: SystemTime) -> Self {
        Self {
            expires_in,
            issued_at,
        }
    }
    /// Whether renewal is due at `now` (a clock that went backwards counts
    /// as due: renewing early is harmless, renewing late is not).
    pub fn due_at(&self, now: SystemTime) -> bool {
        let elapsed = now
            .duration_since(self.issued_at)
            .map_or(u64::MAX, |d| d.as_secs());
        crate::context_login::renewal_due(self.expires_in, elapsed)
    }
}

/// Payload of `ACCOUNT_EVENT`: labels only.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccountEvent {
    pub signed_in: bool,
    /// The `AccountError` code the WebView already knows how to word.
    pub code: &'static str,
}

/// How often the maintenance task checks whether a renewal is due.
const MAINTENANCE_TICK: Duration = Duration::from_secs(60);
/// The server's access-token lifetime when it does not report one.
const DEFAULT_ACCESS_SECONDS: u64 = 3600;

impl Default for ContextState {
    fn default() -> Self {
        Self {
            account: AccountSlot::default(),
            drivers: Arc::default(),
            endpoint: OnceLock::new(),
            journal_label: Mutex::new(JournalOpenReport::NotSupported.key_store_label()),
            renewal: Mutex::new(None),
            renewal_label: Mutex::new("none".to_string()),
        }
    }
}

/// How long the loopback listener waits for the browser (plan C-12: 60 s).
const SIGN_IN_TIMEOUT: Duration = Duration::from_secs(60);
/// `Busy` on the journal at sign-in is retried this often, this many times
/// (a record step in flight holds it for at most one request timeout).
const JOURNAL_BUSY_ATTEMPTS: u32 = 6;
const JOURNAL_BUSY_RETRY: Duration = Duration::from_secs(5);

impl ContextState {
    pub fn configure(&self, service: ContextService<NativeSecretStore>) {
        self.account.configure(service);
    }
    /// Startup only. A second value is ignored.
    pub fn set_endpoint(&self, endpoint: ContextEndpoint) {
        let _ = self.endpoint.set(endpoint);
    }
    fn service(&self) -> Result<Arc<ContextService<NativeSecretStore>>, CommandError> {
        self.account.current().ok_or(CommandError::NotConfigured)
    }
    /// The verified session's service, for other Rust command modules.
    pub(crate) fn current_service(&self) -> Option<Arc<ContextService<NativeSecretStore>>> {
        self.account.current()
    }
    pub fn set_journal_label(&self, label: &'static str) {
        *self.journal_label.lock().unwrap_or_else(|p| p.into_inner()) = label;
    }
    /// Before a reservation or a record step: with a journal open, the
    /// Context directories must still be owner-only. A widened directory
    /// turns the label to `unsafe-acl` and blocks delivery from here on
    /// (the next sign-in refuses the journal outright).
    fn journal_still_private(&self) -> bool {
        const OPENED: &str = "dpapi-user";
        if self.journal_label() == OPENED
            && !journal_dirs_private(&crate::state::vox_agent_data_dir())
        {
            self.set_journal_label(JournalOpenReport::UnsafeAcl.key_store_label());
        }
        self.journal_label() != JournalOpenReport::UnsafeAcl.key_store_label()
    }
    pub fn journal_label(&self) -> &'static str {
        *self.journal_label.lock().unwrap_or_else(|p| p.into_inner())
    }
    fn set_renewal_label(&self, label: String) {
        *self.renewal_label.lock().unwrap_or_else(|p| p.into_inner()) = label;
    }
    pub fn renewal_label(&self) -> String {
        self.renewal_label
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone()
    }
    /// After a sign-in: schedule renewal of the access token just issued for
    /// `service`, if the server offered a refresh token (without a reported
    /// lifetime the server's documented hour applies).
    pub fn schedule_renewal(
        &self,
        service: &Arc<ContextService<NativeSecretStore>>,
        expires_in: Option<u64>,
        offered: bool,
    ) {
        let schedule = offered.then(|| RenewalSchedule {
            timer: RenewalTimer::new(
                expires_in.unwrap_or(DEFAULT_ACCESS_SECONDS),
                SystemTime::now(),
            ),
            service: Arc::downgrade(service),
        });
        let label = if schedule.is_some() {
            "scheduled"
        } else {
            "not-offered"
        };
        *self.renewal.lock().unwrap_or_else(|p| p.into_inner()) = schedule;
        self.set_renewal_label(label.to_string());
    }
    /// After a renewal: restart the timer, but only while the schedule still
    /// belongs to `service` (a newer sign-in's schedule is never replaced).
    fn reschedule_for(&self, service: &Arc<ContextService<NativeSecretStore>>, expires_in: u64) {
        let mut slot = self.renewal.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(schedule) = slot.as_mut() {
            if Weak::ptr_eq(&schedule.service, &Arc::downgrade(service)) {
                schedule.timer = RenewalTimer::new(expires_in, SystemTime::now());
            }
        }
    }
    fn renewal_belongs_to(&self, service: &Arc<ContextService<NativeSecretStore>>) -> bool {
        self.renewal
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .as_ref()
            .is_some_and(|s| Weak::ptr_eq(&s.service, &Arc::downgrade(service)))
    }
    /// Drop the schedule. With `only_for`, only a schedule belonging to that
    /// service is dropped, and the label changes only when it was.
    fn clear_renewal(
        &self,
        only_for: Option<&Arc<ContextService<NativeSecretStore>>>,
        label: &str,
    ) -> bool {
        let mut slot = self.renewal.lock().unwrap_or_else(|p| p.into_inner());
        let matches = match (&*slot, only_for) {
            (_, None) => true,
            (Some(schedule), Some(service)) => {
                Weak::ptr_eq(&schedule.service, &Arc::downgrade(service))
            }
            (None, Some(_)) => false,
        };
        if matches {
            *slot = None;
            drop(slot);
            self.set_renewal_label(label.to_string());
        }
        matches
    }
    /// What a due renewal needs, captured before any await: the scheduled
    /// service (which must still be the account) and its lease (so a later
    /// result applies only to this session). `None` when nothing is due. A
    /// schedule whose service is gone or no longer the account is dropped.
    fn due_renewal(
        &self,
    ) -> Option<(
        Arc<ContextService<NativeSecretStore>>,
        crate::context_session::SessionLease,
    )> {
        let schedule = self
            .renewal
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone()?;
        let scheduled = schedule.service.upgrade();
        let current = self.account.current();
        let service = match (scheduled, current) {
            (Some(scheduled), Some(current)) if Arc::ptr_eq(&scheduled, &current) => current,
            _ => {
                self.clear_renewal(None, "none");
                return None;
            }
        };
        if !schedule.timer.due_at(SystemTime::now()) {
            return None;
        }
        let lease = service.with_session(|s| s.lease()).ok()?;
        Some((service, lease))
    }
}

/// One maintenance step: renew the access token when it is due. The refresh
/// token is read from the credential store only now and dropped after the
/// request; it never reaches argv, logs or events.
pub async fn maintain_session(app: &AppHandle) {
    let state = app.state::<ContextState>();
    let Some((service, lease)) = state.due_renewal() else {
        return;
    };
    // Read under the lease: a sign-out and sign-in that landed since
    // `due_renewal` make this `StaleLease`, never another session's token.
    let refresh = match service.with_session(|s| s.refresh_token_for(&lease)) {
        Ok(Some(token)) => Zeroizing::new(token),
        Ok(None) => {
            state.clear_renewal(Some(&service), "not-offered");
            return;
        }
        Err(crate::context_session::SessionError::StaleLease) => return,
        Err(_) => {
            state.set_renewal_label("renewal-unavailable:STORE".to_string());
            return;
        }
    };
    let outcome = crate::context_login::renew(service.endpoint(), &refresh).await;
    drop(refresh);
    let applied = apply_renewal(&service, &lease, outcome);
    // Everything below is tied to `service`: the schedule is only touched
    // while it still belongs to this service, and the account is ended only
    // if it still is this service (decided under the slot lock in `end_if`).
    match applied {
        RenewalApplied::Renewed {
            expires_in,
            refresh_stored,
        } => {
            state.reschedule_for(&service, expires_in);
            if state.renewal_belongs_to(&service) {
                state.set_renewal_label(
                    if refresh_stored {
                        "renewed"
                    } else {
                        "renewal-unavailable:STORE"
                    }
                    .to_string(),
                );
            }
        }
        RenewalApplied::Stale => {}
        RenewalApplied::SignOut(code) => {
            if state.account.end_if(&service) {
                // Visible private plaintext goes with the account (T23b), as
                // on any other server-side sign-out; the WebView is told with
                // a code it already words (`AccountError`), never the body.
                app.state::<crate::context_private_commands::PrivateState>()
                    .clear_view();
                app.state::<crate::context_weekly_commands::WeeklyState>()
                    .clear();
                state.clear_renewal(Some(&service), &format!("signed-out:{code}"));
                let _ = app.emit_to(
                    "main",
                    ACCOUNT_EVENT,
                    AccountEvent {
                        signed_in: false,
                        code: account_event_code(code),
                    },
                );
            }
        }
        RenewalApplied::RetryLater(code) => {
            if state.renewal_belongs_to(&service) {
                state.set_renewal_label(format!("renewal-unavailable:{code}"));
            }
        }
    }
}

/// The `AccountError` wire code for a renewal sign-out reason.
pub fn account_event_code(reason: &str) -> &'static str {
    match reason {
        "ACCOUNT_DISABLED" => "account-disabled",
        "IDENTITY_CHANGED" => "identity-changed",
        _ => "sign-in-required",
    }
}

/// Start the one maintenance task of this process (a second call is a no-op).
pub fn spawn_session_maintenance(app: AppHandle) {
    static STARTED: OnceLock<()> = OnceLock::new();
    if STARTED.set(()).is_err() {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let mut tick = tokio::time::interval(MAINTENANCE_TICK);
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            tick.tick().await;
            maintain_session(&app).await;
        }
    });
}

/// Why a build has (or lacks) an encrypted journal. Labels only: no paths.
pub enum JournalOpenReport {
    /// A DPAPI-backed journal in an owner-only directory.
    Opened(Journal),
    /// This platform has no key provider; delivery stays blocked.
    NotSupported,
    /// A Context directory exists with a DACL that is not owner-only. It is
    /// never repaired: the journal stays `None` and delivery stays blocked.
    UnsafeAcl,
    /// Another process holds the journal.
    Busy,
    /// The directories or the journal could not be created or opened.
    Failed,
}

// `Journal` has no `Debug` (it holds a live SQLite connection); the label only.
impl std::fmt::Debug for JournalOpenReport {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.key_store_label())
    }
}

impl JournalOpenReport {
    /// The `key_store` diagnostics label.
    pub fn key_store_label(&self) -> &'static str {
        match self {
            Self::Opened(_) => "dpapi-user",
            Self::NotSupported => "not-available-in-this-build",
            Self::UnsafeAcl => "unsafe-acl",
            Self::Busy | Self::Failed => "unavailable",
        }
    }
}

/// Open (or create) the encrypted journal under `<data_dir>/context`:
/// `keys/` (DPAPI-wrapped key files) and `journal/` (database and lock).
/// Anchors live in the credential store, outside the journal directory as
/// `Journal::open` requires. Every directory is created with, or verified to
/// have, an owner-only DACL before anything is written; a widened directory
/// is refused, not repaired.
pub fn open_journal(data_dir: &std::path::Path) -> JournalOpenReport {
    open_journal_with(data_dir, Box::new(NativeSecretStore::default()))
}

/// `open_journal` with the anchor store supplied (tests keep anchors in a
/// file store so they never touch the credential store).
pub fn open_journal_with(
    data_dir: &std::path::Path,
    anchors: Box<dyn crate::context_journal::AnchorStore>,
) -> JournalOpenReport {
    #[cfg(not(windows))]
    {
        let _ = (data_dir, anchors);
        JournalOpenReport::NotSupported
    }
    #[cfg(windows)]
    {
        use crate::context_acl::{
            ensure_private_dir, verify_private_child, verify_private_dir, AclError,
        };
        use crate::context_keys::dpapi::DpapiKeyProvider;
        use crate::context_keys::KeyRing;
        let root = data_dir.join("context");
        let keys = root.join("keys");
        let journal = root.join("journal");
        for dir in [&root, &keys, &journal] {
            match ensure_private_dir(dir).and_then(|_| verify_private_dir(dir)) {
                Ok(()) => {}
                Err(AclError::TooPermissive) => return JournalOpenReport::UnsafeAcl,
                Err(AclError::Io) => return JournalOpenReport::Failed,
            }
        }
        // `db/` and `isolated/` belong to `Journal::open` (an empty `db/`
        // created by anyone else is archived as orphaned), so they are not
        // pre-created; they inherit the protected owner-only DACL of
        // `journal/` and are verified as children before and after the open,
        // so a widened `db/` is refused like a widened `journal/`.
        let children = [journal.join("db"), journal.join("isolated")];
        let children_ok = || {
            children
                .iter()
                .filter(|dir| dir.exists())
                .try_for_each(|dir| verify_private_child(dir))
        };
        match children_ok() {
            Ok(()) => {}
            Err(AclError::TooPermissive) => return JournalOpenReport::UnsafeAcl,
            Err(AclError::Io) => return JournalOpenReport::Failed,
        }
        let ring = KeyRing::new(&keys, DpapiKeyProvider::new());
        let opened = match Journal::open(&journal, &ring, anchors) {
            Ok(opened) => opened.into_journal(),
            Err(JournalError::Busy) => return JournalOpenReport::Busy,
            Err(_) => return JournalOpenReport::Failed,
        };
        match children_ok() {
            Ok(()) => JournalOpenReport::Opened(opened),
            Err(AclError::TooPermissive) => JournalOpenReport::UnsafeAcl,
            Err(AclError::Io) => JournalOpenReport::Failed,
        }
    }
}

/// Whether the Context directories under `data_dir` still pass the checks
/// `open_journal_with` made. Called before every reservation and record step
/// while a journal is open, so widening a directory during a session stops
/// delivery instead of being noticed only at the next sign-in. Always true
/// where no journal can exist.
pub fn journal_dirs_private(data_dir: &std::path::Path) -> bool {
    #[cfg(not(windows))]
    {
        let _ = data_dir;
        true
    }
    #[cfg(windows)]
    {
        use crate::context_acl::{verify_private_child, verify_private_dir};
        let root = data_dir.join("context");
        let journal = root.join("journal");
        [root.clone(), root.join("keys"), journal.clone()]
            .iter()
            .all(|dir| verify_private_dir(dir).is_ok())
            && [journal.join("db"), journal.join("isolated")]
                .iter()
                .filter(|dir| dir.exists())
                .all(|dir| verify_private_child(dir).is_ok())
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProjectInput {
    pub id: String,
    pub repository: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SubmitRecordInput {
    /// Client draft id; reused when the user retries the same draft.
    pub event_id: String,
    pub target_id: String,
    pub text: String,
    pub project: Option<ProjectInput>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum CommandError {
    NotConfigured,
    NotSignedIn,
    Unverified,
    InvalidInput,
    JournalUnavailable,
    Busy,
    NotFound,
    QuotaExceeded,
    Conflict,
    Tombstoned,
    VerifyOnly,
    CatalogUnavailable,
    RepositoryUnavailable,
    NotARepository,
    /// A stored original failed its integrity check; nothing is shown or copied.
    IntegrityFailed,
    Internal,
}

impl From<ServiceError> for CommandError {
    fn from(error: ServiceError) -> Self {
        match error {
            ServiceError::NotSignedIn | ServiceError::WrongEndpoint => CommandError::NotSignedIn,
            ServiceError::DestinationNotAllowed => CommandError::InvalidInput,
            ServiceError::CatalogUnavailable => CommandError::CatalogUnavailable,
            ServiceError::Unverified => CommandError::Unverified,
            ServiceError::JournalUnavailable => CommandError::JournalUnavailable,
            ServiceError::Busy => CommandError::Busy,
            ServiceError::NotFound => CommandError::NotFound,
            ServiceError::Journal(JournalError::QuotaExceeded(_)) | ServiceError::Capacity => {
                CommandError::QuotaExceeded
            }
            ServiceError::Journal(JournalError::Conflict) => CommandError::Conflict,
            ServiceError::Journal(JournalError::Tombstoned) => CommandError::Tombstoned,
            ServiceError::Journal(JournalError::VerifyOnly) => CommandError::VerifyOnly,
            ServiceError::Journal(JournalError::InvalidInput) => CommandError::InvalidInput,
            ServiceError::Journal(JournalError::KeyUnavailable) => CommandError::JournalUnavailable,
            ServiceError::Journal(JournalError::Corrupt) => CommandError::IntegrityFailed,
            ServiceError::Journal(JournalError::NotFound) => CommandError::NotFound,
            _ => CommandError::Internal,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordDto {
    pub event_id: String,
    pub phase: &'static str,
    pub server_state: Option<&'static str>,
    pub action: &'static str,
    /// What happens next, as a label (`poll`, `user-sign-in`, `stop-final`, ...).
    pub next: Option<&'static str>,
}

pub fn next_label(next: &Next) -> &'static str {
    match next {
        Next::PollAfter(_) => "poll",
        Next::ResendAfter(_) => "resend",
        Next::PollAndAsk(_, action) => match action {
            UserAction::SignIn => "poll-ask-sign-in",
            UserAction::AdministratorRecovery => "poll-ask-administrator-recovery",
            UserAction::CheckDelivery => "poll-ask-check-delivery",
        },
        Next::WaitForUser(action) => match action {
            UserAction::SignIn => "user-sign-in",
            UserAction::AdministratorRecovery => "user-administrator-recovery",
            UserAction::CheckDelivery => "user-check-delivery",
        },
        Next::Stop(stop) => match stop {
            Stop::Final => "stop-final",
            Stop::Conflict => "stop-conflict",
            Stop::PermissionDenied => "stop-permission-denied",
            Stop::ProjectInvalid => "stop-project-invalid",
            Stop::InvalidInput => "stop-invalid-input",
            Stop::TooLarge => "stop-too-large",
            Stop::ServerAddressInvalid => "stop-server-address-invalid",
        },
    }
}

fn phase_label(phase: DispatchPhase) -> &'static str {
    match phase {
        DispatchPhase::Reserved => "reserved",
        DispatchPhase::Dispatching => "dispatching",
        DispatchPhase::Accepted => "accepted",
        DispatchPhase::Restored => "restored",
    }
}

fn state_label(state: DeliveryState) -> &'static str {
    match state {
        DeliveryState::Unknown => "local-unknown",
        DeliveryState::ServerUnknown => "unknown",
        DeliveryState::Pending => "pending",
        DeliveryState::Sending => "sending",
        DeliveryState::Delivered => "delivered",
        DeliveryState::Blocked => "blocked",
        DeliveryState::Rejected => "rejected",
        DeliveryState::Conflict => "conflict",
    }
}

fn action_label(action: RecoveryAction) -> &'static str {
    match action {
        RecoveryAction::MayPost => "may-post",
        RecoveryAction::ReadBackOnly => "read-back-only",
        RecoveryAction::Poll => "poll",
        RecoveryAction::Final => "final",
        RecoveryAction::Quarantine => "quarantine",
    }
}

pub fn record_dto(event_id: &str, status: &RecordStatus, next: Option<&Next>) -> RecordDto {
    RecordDto {
        event_id: event_id.to_string(),
        phase: phase_label(status.phase),
        server_state: status.server_state.map(state_label),
        action: action_label(status.action),
        next: next.map(next_label),
    }
}

/// Validate an IPC submission into a frozen payload.
pub fn parse_submission(input: SubmitRecordInput) -> Result<BackupPayload, CommandError> {
    let project = input
        .project
        .map(|p| ProjectSnapshot::new(p.id, p.repository))
        .transpose()
        .map_err(|_| CommandError::InvalidInput)?;
    BackupPayload::new(input.event_id, input.target_id, input.text, project)
        .map_err(|_| CommandError::InvalidInput)
}

/// Step a record until it stops, waits for the user, or fails; the caller
/// registered it in `drivers` and it is removed here when the task ends.
/// The service is held only while a step runs: across a sleep (up to
/// minutes) the task keeps a `Weak`, so a sign-out releases the service, and
/// with it the journal lock, as soon as the step in flight finishes.
async fn drive(
    app: AppHandle,
    service: Weak<ContextService<NativeSecretStore>>,
    drivers: Arc<Mutex<HashSet<String>>>,
    event_id: String,
) {
    loop {
        let Some(current) = service.upgrade() else {
            break; // signed out meanwhile
        };
        if !app.state::<ContextState>().journal_still_private() {
            break; // a Context directory was widened; delivery stops here
        }
        let next = current.step(&event_id).await;
        if let Ok(status) = current.status(&event_id) {
            let dto = record_dto(&event_id, &status, next.as_ref().ok());
            let _ = app.emit_to("main", RECORD_EVENT, dto);
        }
        drop(current);
        match next {
            Ok(Next::PollAfter(after) | Next::ResendAfter(after) | Next::PollAndAsk(after, _)) => {
                tokio::time::sleep(after).await;
            }
            _ => break,
        }
    }
    drivers
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .remove(&event_id);
}

#[tauri::command]
pub async fn context_submit_record(
    app: AppHandle,
    state: State<'_, ContextState>,
    input: SubmitRecordInput,
) -> Result<RecordDto, CommandError> {
    let payload = parse_submission(input)?;
    let service = state.service()?;
    if !state.journal_still_private() {
        return Err(CommandError::JournalUnavailable);
    }
    service.reserve(&payload)?;
    let event_id = payload.event_id().to_string();
    let status = service.status(&event_id)?;
    let fresh = state
        .drivers
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .insert(event_id.clone());
    if fresh {
        let drivers = state.drivers.clone();
        tauri::async_runtime::spawn(drive(
            app,
            Arc::downgrade(&service),
            drivers,
            event_id.clone(),
        ));
    }
    Ok(record_dto(&event_id, &status, None))
}

#[tauri::command]
pub fn context_record_status(
    state: State<'_, ContextState>,
    event_id: String,
) -> Result<RecordDto, CommandError> {
    if event_id.is_empty() || event_id.len() > 128 {
        return Err(CommandError::InvalidInput);
    }
    let status = state.service()?.status(&event_id)?;
    Ok(record_dto(&event_id, &status, None))
}

#[tauri::command]
pub fn context_list_records(
    state: State<'_, ContextState>,
) -> Result<Vec<RecordDto>, CommandError> {
    let service = state.service()?;
    service
        .list()?
        .into_iter()
        .map(|(event_id, _)| Ok(record_dto(&event_id, &service.status(&event_id)?, None)))
        .collect()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionSummaryDto {
    /// False until Rust configures the service (sign-in and key provider).
    pub configured: bool,
    pub signed_in: bool,
    pub destinations: Vec<String>,
    /// Whether this build has a Context server to sign in to.
    pub sign_in_available: bool,
    /// The Feishu display name, for "Signed in as …"; never an id.
    pub display_name: Option<String>,
}

/// What the Context panel may show about the session: no identity details,
/// no token, only whether records can be made and to which destinations.
#[tauri::command]
pub fn context_session_summary(state: State<'_, ContextState>) -> SessionSummaryDto {
    let sign_in_available = state.endpoint.get().is_some();
    let display_name = state.account.display_name();
    match state.service() {
        Ok(service) => SessionSummaryDto {
            configured: true,
            signed_in: service.signed_in(),
            destinations: service
                .destinations()
                .iter()
                .map(|d| d.as_str().to_string())
                .collect(),
            sign_in_available,
            display_name,
        },
        Err(_) => SessionSummaryDto {
            configured: false,
            signed_in: false,
            destinations: Vec::new(),
            sign_in_available,
            display_name: None,
        },
    }
}

/// Read-only re-check of one record; never sends it.
#[tauri::command]
pub async fn context_check_record(
    state: State<'_, ContextState>,
    event_id: String,
) -> Result<RecordDto, CommandError> {
    if event_id.is_empty() || event_id.len() > 128 {
        return Err(CommandError::InvalidInput);
    }
    let service = state.service()?;
    let next = service.check(&event_id).await?;
    let status = service.status(&event_id)?;
    Ok(record_dto(&event_id, &status, next.as_ref()))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectDto {
    pub id: String,
    pub name: String,
    pub repository: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectCatalogDto {
    pub projects: Vec<ProjectDto>,
    pub omitted: usize,
}

/// Projects the signed-in user may attribute records to.
#[tauri::command]
pub async fn context_list_projects(
    state: State<'_, ContextState>,
) -> Result<ProjectCatalogDto, CommandError> {
    let catalog = state.service()?.projects().await?;
    Ok(ProjectCatalogDto {
        omitted: catalog.omitted,
        projects: catalog
            .projects
            .into_iter()
            .map(|p| ProjectDto {
                id: p.snapshot.id().to_string(),
                repository: p.snapshot.repository().to_string(),
                name: p.name,
            })
            .collect(),
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RepositoryInspectionDto {
    /// True when the user closed the folder dialog.
    pub cancelled: bool,
    pub repositories: Vec<String>,
    pub branch: Option<String>,
}

/// Read-only local repository check. The folder is chosen in a native
/// dialog opened here, so the WebView never supplies a path; only remote
/// slugs and the branch name are returned, never the path itself.
#[tauri::command]
pub async fn context_inspect_repository(
    app: AppHandle,
) -> Result<RepositoryInspectionDto, CommandError> {
    use tauri_plugin_dialog::DialogExt;
    let picked = app.dialog().file().blocking_pick_folder();
    let Some(path) = picked.and_then(|p| p.into_path().ok()) else {
        return Ok(RepositoryInspectionDto {
            cancelled: true,
            repositories: Vec::new(),
            branch: None,
        });
    };
    let inspection =
        tauri::async_runtime::spawn_blocking(move || crate::context_repository::inspect(&path))
            .await
            .map_err(|_| CommandError::Internal)?
            .map_err(|error| match error {
                crate::context_repository::RepositoryError::NotARepository => {
                    CommandError::NotARepository
                }
                _ => CommandError::RepositoryUnavailable,
            })?;
    Ok(RepositoryInspectionDto {
        cancelled: false,
        repositories: inspection.repositories,
        branch: inspection.branch,
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordPageDto {
    pub records: Vec<RecordDto>,
    pub next_cursor: Option<String>,
}

fn valid_event_id(event_id: &str) -> bool {
    !event_id.is_empty()
        && event_id.len() <= 128
        && event_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// A page of records, newest first.
#[tauri::command]
pub fn context_page_records(
    state: State<'_, ContextState>,
    cursor: Option<String>,
    limit: Option<u32>,
) -> Result<RecordPageDto, CommandError> {
    if cursor.as_ref().is_some_and(|c| c.len() > 200) {
        return Err(CommandError::InvalidInput);
    }
    let service = state.service()?;
    let limit = limit.unwrap_or(20).clamp(1, 100) as usize;
    let (events, next_cursor) = service.page(cursor.as_deref(), limit)?;
    let records = events
        .iter()
        .filter_map(|event_id| {
            service
                .status(event_id)
                .ok()
                .map(|s| record_dto(event_id, &s, None))
        })
        .collect();
    Ok(RecordPageDto {
        records,
        next_cursor,
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OriginalDto {
    pub event_id: String,
    pub text: String,
    pub project: Option<ProjectInputDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectInputDto {
    pub id: String,
    pub repository: String,
}

/// The retained original of one record, only after the journal verified it.
/// Called on an explicit "View original"; the main window only (ACL).
#[tauri::command]
pub fn context_read_original(
    state: State<'_, ContextState>,
    event_id: String,
) -> Result<OriginalDto, CommandError> {
    if !valid_event_id(&event_id) {
        return Err(CommandError::InvalidInput);
    }
    let original = state.service()?.original(&event_id)?;
    Ok(OriginalDto {
        event_id,
        text: original.payload.text().to_string(),
        project: original.payload.project().map(|p| ProjectInputDto {
            id: p.id().to_string(),
            repository: p.repository().to_string(),
        }),
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DiagnosticsDto {
    pub app_version: &'static str,
    pub os: &'static str,
    pub arch: &'static str,
    pub configured: bool,
    pub signed_in: bool,
    /// Windows key store (DPAPI) availability for the encrypted journal:
    /// `dpapi-user`, `unsafe-acl`, `unavailable` or `not-available-in-this-build`.
    pub key_store: &'static str,
    /// "unavailable", "normal" or "verify-only".
    pub journal: &'static str,
    /// Session renewal status line (spec §3.6): `none`, `scheduled`,
    /// `renewed`, `not-offered`, `renewal-unavailable:<CODE>` or
    /// `signed-out:<CODE>`. Codes are fixed server refusal codes.
    pub renewal: String,
    pub active_records: Option<u64>,
}

/// Labels only: no paths, tokens, record text or server bodies.
pub fn diagnostics(state: &ContextState) -> DiagnosticsDto {
    use crate::context_journal::RecoveryMode;
    let service = state.service().ok();
    let service = service.as_deref();
    let journal = service.and_then(|s| s.journal_mode());
    DiagnosticsDto {
        app_version: env!("CARGO_PKG_VERSION"),
        os: std::env::consts::OS,
        arch: std::env::consts::ARCH,
        configured: service.is_some(),
        signed_in: service.is_some_and(|s| s.signed_in()),
        key_store: state.journal_label(),
        journal: match journal {
            None => "unavailable",
            Some(RecoveryMode::Normal) => "normal",
            Some(RecoveryMode::VerifyOnly) => "verify-only",
        },
        renewal: state.renewal_label(),
        active_records: service
            .and_then(|s| s.quota().ok())
            .map(|q| q.active_records),
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccountDto {
    pub signed_in: bool,
    pub display_name: Option<String>,
}

/// Feishu sign-in through the loopback callback (T24a/T24). The browser URL
/// is validated in Rust and opened by Rust; the WebView passes nothing.
/// On Windows the session gets the DPAPI-backed journal (T07b); elsewhere,
/// or when the Context directory is not owner-only, records stay unavailable
/// while the session and conversation discovery still work.
#[tauri::command]
pub async fn context_sign_in(
    app: AppHandle,
    state: State<'_, ContextState>,
) -> Result<AccountDto, AccountError> {
    use tauri_plugin_opener::OpenerExt;
    let endpoint = state
        .endpoint
        .get()
        .cloned()
        .ok_or(AccountError::NotAvailable)?;
    let ticket = state.account.begin_sign_in()?;
    let opener = |url: &reqwest::Url| {
        app.opener()
            .open_url(url.as_str(), None::<&str>)
            .map_err(|_| ())
    };
    let signed = crate::context_login::sign_in(&endpoint, opener, SIGN_IN_TIMEOUT).await?;
    // A record step of the previous session may still hold the journal for a
    // moment after sign-out (`drive` releases it when the step finishes);
    // `Busy` is retried briefly before the session is published without one.
    let mut report = JournalOpenReport::Busy;
    for attempt in 0..JOURNAL_BUSY_ATTEMPTS {
        if attempt > 0 {
            tokio::time::sleep(JOURNAL_BUSY_RETRY).await;
        }
        report = tauri::async_runtime::spawn_blocking(|| {
            open_journal(&crate::state::vox_agent_data_dir())
        })
        .await
        .unwrap_or(JournalOpenReport::Failed);
        if !matches!(report, JournalOpenReport::Busy) {
            break;
        }
    }
    state.set_journal_label(report.key_store_label());
    let journal = match report {
        JournalOpenReport::Opened(journal) => Some(journal),
        _ => None,
    };
    let expires_in = signed.expires_in;
    let renewal_offered = signed.refresh_token.is_some();
    let display_name = state.account.publish(
        &ticket,
        endpoint,
        signed,
        NativeSecretStore::default(),
        journal,
    )?;
    if let Some(service) = state.account.current() {
        state.schedule_renewal(&service, expires_in, renewal_offered);
    }
    Ok(AccountDto {
        signed_in: true,
        display_name,
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SignOutDto {
    /// Whether the server confirmed it ended the session. The local sign-out
    /// (memory and credential store) happens either way.
    pub server_confirmed: bool,
    /// False if the credential store could not delete the saved session.
    pub store_cleared: bool,
}

#[tauri::command]
pub async fn context_sign_out(
    state: State<'_, ContextState>,
    private: State<'_, crate::context_private_commands::PrivateState>,
    weekly: State<'_, crate::context_weekly_commands::WeeklyState>,
) -> Result<SignOutDto, AccountError> {
    // Local sign-out (memory and credential store) completes before the
    // server is contacted; the network call cannot delay or undo it. Visible
    // private plaintext and the weekly draft go with it (T23b); saved private
    // mutations stay.
    // Private and weekly state go first: a copy in flight then either
    // completed before this point or is refused.
    private.clear_view();
    weekly.clear();
    state.clear_renewal(None, "none");
    let ended = state.account.end(NativeSecretStore::default);
    let server_confirmed = match &ended.logout {
        Some((endpoint, token)) => crate::context_account::logout(endpoint, token).await,
        None => false,
    };
    Ok(SignOutDto {
        server_confirmed,
        store_cleared: ended.store_cleared,
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationDto {
    pub id: String,
    pub kind: ConversationKind,
    pub name: String,
    pub can_send: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationsDto {
    pub conversations: Vec<ConversationDto>,
    pub omitted: usize,
}

/// Feishu conversations the signed-in user may see. A session the server no
/// longer accepts, or a listing for another user, ends the account here.
#[tauri::command]
pub async fn context_conversations(
    state: State<'_, ContextState>,
    private: State<'_, crate::context_private_commands::PrivateState>,
    weekly: State<'_, crate::context_weekly_commands::WeeklyState>,
) -> Result<ConversationsDto, AccountError> {
    let service = state.service().map_err(|_| AccountError::NotSignedIn)?;
    match discover_for(&service).await {
        Ok(found) => Ok(ConversationsDto {
            conversations: found
                .conversations
                .into_iter()
                .map(|c| ConversationDto {
                    id: c.id,
                    kind: c.kind,
                    name: c.name,
                    can_send: c.can_send,
                })
                .collect(),
            omitted: found.omitted,
        }),
        Err(error) => {
            if matches!(
                error,
                AccountError::SignInRequired
                    | AccountError::IdentityChanged
                    | AccountError::AccountDisabled
            ) {
                // Only if this service is still the account (a late refusal never
                // ends a newer sign-in); its private plaintext goes with it.
                if state
                    .account
                    .current()
                    .is_some_and(|current| Arc::ptr_eq(&current, &service))
                {
                    private.clear_view();
                    weekly.clear();
                }
                state.account.end_if(&service);
            }
            Err(error)
        }
    }
}

#[tauri::command]
pub fn context_diagnostics(state: State<'_, ContextState>) -> DiagnosticsDto {
    diagnostics(&state)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QuotaDto {
    pub active_records: u64,
    pub active_bytes: u64,
    pub max_active_records: u64,
    pub max_active_bytes: u64,
}

#[tauri::command]
pub fn context_quota(state: State<'_, ContextState>) -> Result<QuotaDto, CommandError> {
    let report = state.service()?.quota()?;
    Ok(QuotaDto {
        active_records: report.active_records,
        active_bytes: report.active_bytes,
        max_active_records: report.limits.max_active_records_per_owner,
        max_active_bytes: report.limits.max_active_bytes_per_owner,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(json: &str) -> Result<SubmitRecordInput, serde_json::Error> {
        serde_json::from_str(json)
    }

    #[test]
    fn submissions_reject_unknown_fields_including_urls_identities_and_states() {
        for extra in [
            r#""endpoint":"https://evil.example""#,
            r#""actor":"bob""#,
            r#""token":"x""#,
            r#""status":"delivered""#,
        ] {
            let json = format!(r#"{{"eventId":"e1","targetId":"team","text":"x",{extra}}}"#);
            assert!(input(&json).is_err(), "{extra}");
        }
    }

    #[test]
    fn submissions_are_validated_into_frozen_payloads() {
        let ok = input(r#"{"eventId":"e1","targetId":"team","text":"x","project":{"id":"p1","repository":"studio/repo"}}"#).unwrap();
        assert_eq!(
            parse_submission(ok)
                .unwrap()
                .project()
                .unwrap()
                .repository(),
            "studio/repo"
        );
        for bad in [
            r#"{"eventId":"../e","targetId":"team","text":"x"}"#,
            r#"{"eventId":"e1","targetId":"team","text":""}"#,
            r#"{"eventId":"e1","targetId":"team","text":"x","project":{"id":"p1","repository":"../repo"}}"#,
        ] {
            assert_eq!(
                parse_submission(input(bad).unwrap()).err(),
                Some(CommandError::InvalidInput),
                "{bad}"
            );
        }
    }

    #[test]
    fn dtos_carry_only_labels_and_errors_only_codes() {
        let dto = record_dto(
            "e1",
            &RecordStatus {
                phase: DispatchPhase::Accepted,
                server_state: Some(DeliveryState::Delivered),
                action: RecoveryAction::Final,
            },
            Some(&Next::PollAndAsk(
                std::time::Duration::from_secs(600),
                UserAction::CheckDelivery,
            )),
        );
        let json = serde_json::to_string(&dto).unwrap();
        assert_eq!(
            json,
            r#"{"eventId":"e1","phase":"accepted","serverState":"delivered","action":"final","next":"poll-ask-check-delivery"}"#
        );
        assert_eq!(
            serde_json::to_string(&CommandError::NotConfigured).unwrap(),
            r#""not-configured""#
        );
        assert_eq!(
            CommandError::from(ServiceError::Journal(JournalError::Corrupt)),
            CommandError::IntegrityFailed
        );
        assert_eq!(
            CommandError::from(ServiceError::Journal(JournalError::Io)),
            CommandError::Internal
        );
    }

    #[test]
    fn diagnostics_report_the_real_version_and_no_secrets() {
        let state = ContextState::default();
        let dto = diagnostics(&state);
        assert_eq!(dto.app_version, env!("CARGO_PKG_VERSION"));
        assert_eq!(
            (dto.configured, dto.signed_in, dto.journal, dto.key_store),
            (false, false, "unavailable", "not-available-in-this-build")
        );
        state.set_journal_label(JournalOpenReport::UnsafeAcl.key_store_label());
        assert_eq!(diagnostics(&state).key_store, "unsafe-acl");
        assert_eq!(dto.renewal, "none");
        let service = idle_service();
        state.schedule_renewal(&service, Some(3600), true);
        assert_eq!(diagnostics(&state).renewal, "scheduled");
        state.schedule_renewal(&service, Some(3600), false);
        assert_eq!(diagnostics(&state).renewal, "not-offered");
        state.set_renewal_label("renewal-unavailable:RENEWAL_RATE_LIMIT".into());
        let json = serde_json::to_string(&diagnostics(&state)).unwrap();
        assert!(json.contains("\"renewal\":\"renewal-unavailable:RENEWAL_RATE_LIMIT\""));
        for forbidden in ["token", "vox_", "path", "\\"] {
            assert!(!json.contains(forbidden), "{forbidden}: {json}");
        }
        let json = serde_json::to_string(&dto).unwrap();
        for forbidden in ["token", "path", "/Users", "\\"] {
            assert!(!json.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn renewal_schedule_uses_wall_clock_time_and_treats_clock_jumps_as_due() {
        let issued = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000);
        let schedule = RenewalTimer::new(3600, issued);
        assert!(!schedule.due_at(issued));
        assert!(!schedule.due_at(issued + Duration::from_secs(3600 - 301)));
        assert!(schedule.due_at(issued + Duration::from_secs(3600 - 300)));
        assert!(
            schedule.due_at(issued + Duration::from_secs(86_400)),
            "slept past expiry"
        );
        assert!(
            schedule.due_at(issued - Duration::from_secs(1)),
            "clock went backwards"
        );
    }

    fn idle_service() -> Arc<ContextService<NativeSecretStore>> {
        // Never signed in, so the credential store is never touched.
        Arc::new(ContextService::new(
            ContextEndpoint::parse("https://api.example.test").unwrap(),
            Vec::new(),
            crate::context_session::SessionAuthority::new(NativeSecretStore::default()),
            None,
        ))
    }

    fn scheduled_expiry(state: &ContextState) -> Option<u64> {
        state
            .renewal
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.timer.expires_in)
    }

    #[test]
    fn the_renewal_schedule_belongs_to_one_service() {
        let state = ContextState::default();
        let first = idle_service();
        let second = idle_service();
        state.schedule_renewal(&first, None, true);
        assert_eq!(state.renewal_label(), "scheduled");
        assert_eq!(scheduled_expiry(&state), Some(DEFAULT_ACCESS_SECONDS));
        assert!(state.renewal_belongs_to(&first) && !state.renewal_belongs_to(&second));
        // A late result for another service neither restarts nor clears it.
        state.reschedule_for(&second, 10);
        assert_eq!(scheduled_expiry(&state), Some(DEFAULT_ACCESS_SECONDS));
        assert!(!state.clear_renewal(Some(&second), "signed-out:X"));
        assert_eq!(state.renewal_label(), "scheduled");
        state.reschedule_for(&first, 10);
        assert_eq!(scheduled_expiry(&state), Some(10));
        assert!(state.clear_renewal(Some(&first), "signed-out:SESSION_EXPIRED"));
        assert_eq!(state.renewal_label(), "signed-out:SESSION_EXPIRED");
        assert!(scheduled_expiry(&state).is_none());
        // Not offered: nothing scheduled.
        state.schedule_renewal(&first, Some(10), false);
        assert!(scheduled_expiry(&state).is_none());
        assert_eq!(state.renewal_label(), "not-offered");
        assert!(
            state.due_renewal().is_none(),
            "nothing scheduled, nothing due"
        );
        // Scheduled for a service that is not the account: dropped, not renewed.
        state.schedule_renewal(&first, Some(0), true);
        assert!(state.due_renewal().is_none());
        assert!(scheduled_expiry(&state).is_none());
        assert_eq!(state.renewal_label(), "none");
        // Scheduled for the account itself, but it is not signed in: no lease,
        // and the schedule is kept for when it is.
        state.account.configure(ContextService::new(
            ContextEndpoint::parse("https://api.example.test").unwrap(),
            Vec::new(),
            crate::context_session::SessionAuthority::new(NativeSecretStore::default()),
            None,
        ));
        let account = state.account.current().unwrap();
        state.schedule_renewal(&account, Some(0), true);
        assert!(state.due_renewal().is_none());
        assert!(state.renewal_belongs_to(&account));
    }

    #[test]
    fn a_widened_directory_blocks_delivery_only_once_a_journal_was_opened() {
        let state = ContextState::default();
        // Not supported here: nothing to check, nothing blocked.
        assert!(state.journal_still_private());
        state.set_journal_label(JournalOpenReport::UnsafeAcl.key_store_label());
        assert!(!state.journal_still_private(), "unsafe stays unsafe");
        assert_eq!(state.journal_label(), "unsafe-acl");
        let dir = tempfile::tempdir().unwrap();
        // Where no journal can exist the directories always verify; on
        // Windows an empty data directory has nothing to verify yet.
        assert_eq!(journal_dirs_private(dir.path()), !cfg!(windows));
    }

    #[test]
    fn renewal_sign_out_codes_map_to_known_account_error_codes() {
        assert_eq!(account_event_code("SESSION_EXPIRED"), "sign-in-required");
        assert_eq!(account_event_code("ACCOUNT_DISABLED"), "account-disabled");
        assert_eq!(account_event_code("IDENTITY_CHANGED"), "identity-changed");
        assert_eq!(account_event_code("anything"), "sign-in-required");
        let json = serde_json::to_string(&AccountEvent {
            signed_in: false,
            code: "sign-in-required",
        })
        .unwrap();
        assert_eq!(json, r#"{"signedIn":false,"code":"sign-in-required"}"#);
    }

    #[test]
    fn an_unconfigured_state_answers_not_configured() {
        assert_eq!(
            ContextState::default().service().err(),
            Some(CommandError::NotConfigured)
        );
    }
}
