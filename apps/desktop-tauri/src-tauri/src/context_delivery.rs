//! Context submission ownership core. No HTTP, credential, persistence or UI adapter yet.
//! A future adapter must persist the reservation before performing a POST.
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};

/// Process-unique id per `ContextDeliveryOwner`, never reused, so leases and
/// receipts from one owner instance can never be applied to another.
static NEXT_OWNER_INSTANCE: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, PartialEq, Eq)]
pub struct ProjectSnapshot {
    id: String,
    repository: String,
}

fn identifier(value: &str, max: usize) -> bool {
    !value.is_empty()
        && value.len() <= max
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
}

impl ProjectSnapshot {
    pub fn new(id: String, repository: String) -> Result<Self, DeliveryError> {
        let parts: Vec<_> = repository.split('/').collect();
        let valid_segment = |s: &str| {
            s.as_bytes().first().is_some_and(u8::is_ascii_alphanumeric)
                && s.bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
        };
        if !identifier(&id, 80)
            || repository.len() > 200
            || parts.len() != 2
            || !parts.iter().all(|s| valid_segment(s))
        {
            return Err(DeliveryError::InvalidInput);
        }
        Ok(Self { id, repository })
    }
    pub fn id(&self) -> &str {
        &self.id
    }
    pub fn repository(&self) -> &str {
        &self.repository
    }
}

// Do not derive Debug/Serialize: raw text and owner identifiers stay out of logs/WebView.
#[derive(Clone, PartialEq, Eq)]
pub struct BackupPayload {
    event_id: String,
    target_id: String,
    text: String,
    project: Option<ProjectSnapshot>,
}

impl BackupPayload {
    pub fn new(
        event_id: String,
        target_id: String,
        text: String,
        project: Option<ProjectSnapshot>,
    ) -> Result<Self, DeliveryError> {
        if !identifier(&event_id, 128)
            || target_id.is_empty()
            || target_id.len() > 80
            || text.trim().is_empty()
            || text.len() > 8192
        {
            return Err(DeliveryError::InvalidInput);
        }
        Ok(Self {
            event_id,
            target_id,
            text,
            project,
        })
    }
    pub fn event_id(&self) -> &str {
        &self.event_id
    }
    pub fn target_id(&self) -> &str {
        &self.target_id
    }
    pub fn text(&self) -> &str {
        &self.text
    }
    pub fn project(&self) -> Option<&ProjectSnapshot> {
        self.project.as_ref()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryState {
    /// Reserved locally; the client does not know whether the server accepted it.
    Unknown,
    /// The server reports its worker does not know whether Feishu received it.
    ServerUnknown,
    Pending,
    Sending,
    Delivered,
    Blocked,
    Rejected,
    Conflict,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryError {
    InvalidInput,
    NoOwner,
    AlreadyReserved,
    Conflict,
    Capacity,
    StaleLease,
    InvalidReceipt,
}

// No actor ID from a mutation body: this key must come from a trusted Rust auth owner,
// binding canonical endpoint, authenticated account, target and credential scope.
struct OwnedRecord {
    payload: BackupPayload,
    state: DeliveryState,
    /// Highest readback sequence applied; older polls cannot overwrite newer ones.
    last_seq: u64,
}

/// Which request produced a verified receipt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReceiptSource {
    Submission,
    Readback { seq: u64 },
}

fn is_terminal(state: DeliveryState) -> bool {
    // blocked is not terminal: an operator can requeue it (ADR backup-delivery-recovery-v1).
    matches!(
        state,
        DeliveryState::Delivered | DeliveryState::Rejected | DeliveryState::Conflict
    )
}

pub struct SubmissionLease {
    instance: u64,
    generation: u64,
    owner: String,
    payload: BackupPayload,
}
impl SubmissionLease {
    pub fn payload(&self) -> &BackupPayload {
        &self.payload
    }
    pub(crate) fn owner(&self) -> &str {
        &self.owner
    }
    pub(crate) fn generation(&self) -> u64 {
        self.generation
    }
    pub(crate) fn instance(&self) -> u64 {
        self.instance
    }
}
/// Readback capability. Owns the frozen payload it checks receipts against,
/// so no parallel map can be consulted (or raced) during verification.
pub struct ReceiptLease {
    instance: u64,
    generation: u64,
    owner: String,
    payload: BackupPayload,
    seq: u64,
}
impl ReceiptLease {
    pub fn event_id(&self) -> &str {
        self.payload.event_id()
    }
    pub fn payload(&self) -> &BackupPayload {
        &self.payload
    }
    pub(crate) fn owner(&self) -> &str {
        &self.owner
    }
    pub(crate) fn generation(&self) -> u64 {
        self.generation
    }
    pub(crate) fn seq(&self) -> u64 {
        self.seq
    }
    pub(crate) fn instance(&self) -> u64 {
        self.instance
    }
}

pub struct ContextDeliveryOwner {
    instance: u64,
    owner: Option<String>,
    generation: u64,
    records: BTreeMap<(String, String), OwnedRecord>,
    capacity: usize,
    next_seq: u64,
}
impl ContextDeliveryOwner {
    pub fn new(capacity: usize) -> Self {
        Self {
            instance: NEXT_OWNER_INSTANCE.fetch_add(1, Ordering::Relaxed),
            owner: None,
            generation: 0,
            records: BTreeMap::new(),
            capacity: capacity.min(5000),
            next_seq: 0,
        }
    }
    pub fn set_authenticated_scope(&mut self, scope: Option<String>) -> Result<(), DeliveryError> {
        if scope
            .as_ref()
            .is_some_and(|s| s.is_empty() || s.len() > 1024)
        {
            return Err(DeliveryError::InvalidInput);
        }
        // Even A->B->A invalidates leases issued before logout/refresh.
        self.generation = self
            .generation
            .checked_add(1)
            .ok_or(DeliveryError::Capacity)?;
        self.owner = scope;
        Ok(())
    }
    pub fn reserve_submission(
        &mut self,
        payload: BackupPayload,
    ) -> Result<SubmissionLease, DeliveryError> {
        let owner = self.owner.clone().ok_or(DeliveryError::NoOwner)?;
        let key = (owner.clone(), payload.event_id.clone());
        if let Some(record) = self.records.get(&key) {
            return Err(if record.payload == payload {
                DeliveryError::AlreadyReserved
            } else {
                DeliveryError::Conflict
            });
        }
        if self.records.len() >= self.capacity {
            return Err(DeliveryError::Capacity);
        }
        // Unknown BEFORE dispatch. Timeout, dropped response or process exit must not cause a second POST.
        self.records.insert(
            key,
            OwnedRecord {
                payload: payload.clone(),
                state: DeliveryState::Unknown,
                last_seq: 0,
            },
        );
        Ok(SubmissionLease {
            instance: self.instance,
            generation: self.generation,
            owner,
            payload,
        })
    }
    fn check_owner(
        &self,
        instance: u64,
        owner: &str,
        generation: u64,
    ) -> Result<(), DeliveryError> {
        if instance != self.instance
            || self.owner.as_deref() != Some(owner)
            || self.generation != generation
        {
            Err(DeliveryError::StaleLease)
        } else {
            Ok(())
        }
    }
    /// Readback capability is distinct from submission; it never authorizes another POST.
    /// Each lease carries a fresh sequence number so out-of-order responses are detectable.
    pub fn reserve_readback(&mut self, event_id: &str) -> Result<ReceiptLease, DeliveryError> {
        let owner = self.owner.clone().ok_or(DeliveryError::NoOwner)?;
        let record = self
            .records
            .get(&(owner.clone(), event_id.to_string()))
            .ok_or(DeliveryError::InvalidReceipt)?;
        let payload = record.payload.clone();
        self.next_seq = self
            .next_seq
            .checked_add(1)
            .ok_or(DeliveryError::Capacity)?;
        Ok(ReceiptLease {
            instance: self.instance,
            generation: self.generation,
            owner,
            payload,
            seq: self.next_seq,
        })
    }
    /// The only way to change delivery state. `receipt` can only be built by
    /// `context_wire::verify_submission`/`verify_readback` from a lease this
    /// owner issued, so it carries the owner, generation, frozen payload and
    /// sequence it was verified against.
    pub fn apply_verified(
        &mut self,
        receipt: &crate::context_wire::VerifiedReceipt,
    ) -> Result<(), DeliveryError> {
        self.check_owner(receipt.instance(), receipt.owner(), receipt.generation())?;
        let record = self
            .records
            .get_mut(&(
                receipt.owner().to_string(),
                receipt.payload().event_id().to_string(),
            ))
            .ok_or(DeliveryError::InvalidReceipt)?;
        if record.payload != *receipt.payload() {
            return Err(DeliveryError::Conflict);
        }
        let state = receipt.state();
        match receipt.source() {
            // 202 means accepted by the backup service, not delivered to Feishu.
            // Once any readback has been applied, a late acceptance is ignored.
            ReceiptSource::Submission => {
                if record.last_seq == 0 && !is_terminal(record.state) {
                    record.state = state;
                }
                Ok(())
            }
            ReceiptSource::Readback { seq } => {
                if seq <= record.last_seq {
                    return Err(DeliveryError::StaleLease);
                }
                if is_terminal(record.state) && record.state != state {
                    return Err(DeliveryError::InvalidReceipt);
                }
                record.state = state;
                record.last_seq = seq;
                Ok(())
            }
        }
    }
    /// Forget a record of the current owner (e.g. once it is final), freeing
    /// capacity. Outstanding leases for it can no longer be applied.
    pub fn release(&mut self, event_id: &str) {
        if let Some(owner) = self.owner.clone() {
            self.records.remove(&(owner, event_id.to_string()));
        }
    }
    pub fn state(&self, event_id: &str) -> Option<DeliveryState> {
        self.owner
            .as_ref()
            .and_then(|owner| self.records.get(&(owner.clone(), event_id.to_string())))
            .map(|r| r.state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn payload(id: &str, text: &str) -> BackupPayload {
        BackupPayload::new(id.into(), "team".into(), text.into(), None).unwrap()
    }
    fn owner() -> ContextDeliveryOwner {
        let mut o = ContextDeliveryOwner::new(4);
        o.set_authenticated_scope(Some("account-A".into())).unwrap();
        o
    }
    #[test]
    fn ambiguous_submission_cannot_be_reposted() {
        let mut o = owner();
        let _lease = o.reserve_submission(payload("event-1", "hello")).unwrap();
        assert_eq!(o.state("event-1"), Some(DeliveryState::Unknown));
        assert!(matches!(
            o.reserve_submission(payload("event-1", "hello")),
            Err(DeliveryError::AlreadyReserved)
        ));
    }
    #[test]
    fn event_id_cannot_change_content() {
        let mut o = owner();
        o.reserve_submission(payload("event-1", "hello")).unwrap();
        assert!(matches!(
            o.reserve_submission(payload("event-1", "different")),
            Err(DeliveryError::Conflict)
        ));
    }
    fn accept(o: &mut ContextDeliveryOwner, lease: &SubmissionLease) -> Result<(), DeliveryError> {
        let body = format!(
            r#"{{"eventId":"{}","status":"pending","messageId":null}}"#,
            lease.payload().event_id()
        );
        let receipt = crate::context_wire::verify_submission(lease, body.as_bytes()).unwrap();
        o.apply_verified(&receipt)
    }
    fn poll(
        o: &mut ContextDeliveryOwner,
        lease: &ReceiptLease,
        status: &str,
    ) -> Result<(), DeliveryError> {
        let message = if status == "delivered" {
            "\"om_1\""
        } else {
            "null"
        };
        let body = format!(
            r#"{{"eventId":"{}","status":"{status}","messageId":{message}}}"#,
            lease.event_id()
        );
        let receipt = crate::context_wire::verify_readback(lease, body.as_bytes()).unwrap();
        o.apply_verified(&receipt)
    }
    #[test]
    fn accepted_is_not_delivered() {
        let mut o = owner();
        let l = o.reserve_submission(payload("event-1", "hello")).unwrap();
        accept(&mut o, &l).unwrap();
        assert_eq!(o.state("event-1"), Some(DeliveryState::Pending));
    }
    #[test]
    fn late_acceptance_cannot_regress_delivered_readback() {
        let mut o = owner();
        let post = o.reserve_submission(payload("event-1", "hello")).unwrap();
        let readback = o.reserve_readback("event-1").unwrap();
        poll(&mut o, &readback, "delivered").unwrap();
        accept(&mut o, &post).unwrap();
        assert_eq!(o.state("event-1"), Some(DeliveryState::Delivered));
    }
    #[test]
    fn account_switch_rejects_old_post_response() {
        let mut o = owner();
        let l = o.reserve_submission(payload("event-1", "hello")).unwrap();
        o.set_authenticated_scope(Some("account-B".into())).unwrap();
        assert_eq!(accept(&mut o, &l), Err(DeliveryError::StaleLease));
        assert_eq!(o.state("event-1"), None);
    }
    #[test]
    fn returning_to_same_account_still_rejects_old_poll() {
        let mut o = owner();
        o.reserve_submission(payload("event-1", "hello")).unwrap();
        let l = o.reserve_readback("event-1").unwrap();
        o.set_authenticated_scope(None).unwrap();
        o.set_authenticated_scope(Some("account-A".into())).unwrap();
        assert_eq!(
            poll(&mut o, &l, "delivered"),
            Err(DeliveryError::StaleLease)
        );
    }
    #[test]
    fn delivered_never_regresses_on_delayed_pending_poll() {
        let mut o = owner();
        o.reserve_submission(payload("event-1", "hello")).unwrap();
        let first = o.reserve_readback("event-1").unwrap();
        let later = o.reserve_readback("event-1").unwrap();
        poll(&mut o, &later, "delivered").unwrap();
        assert_eq!(
            poll(&mut o, &first, "pending"),
            Err(DeliveryError::StaleLease)
        );
        let newest = o.reserve_readback("event-1").unwrap();
        assert_eq!(
            poll(&mut o, &newest, "pending"),
            Err(DeliveryError::InvalidReceipt)
        );
        assert_eq!(o.state("event-1"), Some(DeliveryState::Delivered));
    }
    #[test]
    fn readback_for_another_event_cannot_be_verified() {
        let mut o = owner();
        o.reserve_submission(payload("event-1", "hello")).unwrap();
        let l = o.reserve_readback("event-1").unwrap();
        let body = br#"{"eventId":"event-2","status":"delivered","messageId":"om_1"}"#;
        assert!(crate::context_wire::verify_readback(&l, body).is_err());
        assert_eq!(o.state("event-1"), Some(DeliveryState::Unknown));
    }
    #[test]
    fn snapshot_frozen_in_lease_and_payload_change_conflicts() {
        let mut o = owner();
        let original = ProjectSnapshot::new("project-1".into(), "studio/repo".into()).unwrap();
        let p = BackupPayload::new(
            "event-1".into(),
            "team".into(),
            "hello".into(),
            Some(original.clone()),
        )
        .unwrap();
        let l = o.reserve_submission(p).unwrap();
        assert!(l.payload().project() == Some(&original));
        let changed =
            BackupPayload::new("event-1".into(), "team".into(), "hello".into(), None).unwrap();
        assert!(matches!(
            o.reserve_submission(changed),
            Err(DeliveryError::Conflict)
        ));
    }
    #[test]
    fn bounded_queue_and_utf8_text_reject_overflow() {
        let mut o = ContextDeliveryOwner::new(0);
        o.set_authenticated_scope(Some("A".into())).unwrap();
        assert!(matches!(
            o.reserve_submission(payload("e", "hello")),
            Err(DeliveryError::Capacity)
        ));
        assert!(BackupPayload::new("e".into(), "team".into(), "中".repeat(3000), None).is_err());
    }
    #[test]
    fn absent_account_invalid_id_and_repository_fail_closed() {
        assert!(matches!(
            ContextDeliveryOwner::new(4).reserve_submission(payload("e", "hello")),
            Err(DeliveryError::NoOwner)
        ));
        assert!(
            BackupPayload::new("../event".into(), "team".into(), "hello".into(), None).is_err()
        );
        assert!(ProjectSnapshot::new("p".into(), "../repo".into()).is_err());
    }
}
