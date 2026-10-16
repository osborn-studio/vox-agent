//! Response policy for team Context records (T09).
//!
//! `decide` is a pure function from one submission or read-back result to the
//! journal change and the next action, following plan §3.2, design §6.3 and
//! ADR 2026-10-08-backup-idempotency-retention-v1:
//!
//! * A POST is resent (same eventId, same payload) only when it provably never
//!   reached the server: a connection that was never established, or a
//!   refusal (401/503) that proves the server did not accept it. After a
//!   possible send, only read-back is allowed.
//! * A read-back 404 never deletes a record and never triggers a POST, unless
//!   the record is `Reserved` (marked not sent), where the server's permanent
//!   idempotency makes a same-payload resend safe (owner decision D2).
//! * `blocked` is not final (an operator can requeue it) but is not polled
//!   automatically; `unknown` is polled slowly once the server's retry window
//!   has passed; final states are never acted on again.
//! * Late or out-of-order results cannot regress state: the journal's
//!   sequence check and terminal rule enforce that, not this function.
use crate::context_delivery::DeliveryState;
use crate::context_journal::DispatchPhase;
use crate::context_transport::{ServerCode, TransportError};
use std::time::Duration;

pub const POLL_BASE: Duration = Duration::from_secs(2);
pub const POLL_CAP: Duration = Duration::from_secs(60);
pub const RETRY_BASE: Duration = Duration::from_secs(5);
/// No retry storm: transient failures back off to at most 10 minutes.
pub const RETRY_CAP: Duration = Duration::from_secs(600);
/// The server worker stops retrying `unknown` after this (engine RETRY_WINDOW_SECONDS).
pub const SERVER_RETRY_WINDOW: Duration = Duration::from_secs(3_000);
pub const SLOW_UNKNOWN_POLL: Duration = Duration::from_secs(15 * 60);
/// Consecutive read-back 404s before asking the user to check delivery.
pub const READBACK_404_LIMIT: u32 = 5;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operation {
    Submit,
    Readback,
}

/// One result, already classified by `context_transport` and, for receipts,
/// verified by `context_wire` against the record's lease.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Observation {
    Receipt(DeliveryState),
    Refused { status: u16, code: ServerCode },
    Transport(TransportError),
}

/// What the policy needs to know about the record.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RecordView {
    pub phase: DispatchPhase,
    pub server_state: Option<DeliveryState>,
    pub since_first_dispatch: Duration,
    /// Consecutive attempts of the current operation (for backoff).
    pub attempt: u32,
    pub readback_not_found: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Effect {
    None,
    /// Store this state (journal `record_server_state`).
    RecordState(DeliveryState),
    /// The server provably did not get or accept the request (journal `dispatch_not_sent`).
    MarkNotSent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stop {
    Final,
    Conflict,
    PermissionDenied,
    ProjectInvalid,
    InvalidInput,
    TooLarge,
    ServerAddressInvalid,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UserAction {
    SignIn,
    /// `blocked`: only an operator can requeue (ADR backup-delivery-recovery-v1).
    AdministratorRecovery,
    /// Repeated 404 after a possible send: the user checks the group.
    CheckDelivery,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Next {
    PollAfter(Duration),
    /// Keep polling slowly and ask the user to check (e.g. repeated 404).
    PollAndAsk(Duration, UserAction),
    /// POST again with the same eventId and payload.
    ResendAfter(Duration),
    Stop(Stop),
    WaitForUser(UserAction),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Decision {
    pub effect: Effect,
    pub next: Next,
}

/// `base * 2^attempt`, capped.
pub fn backoff(base: Duration, cap: Duration, attempt: u32) -> Duration {
    if attempt >= 32 {
        return cap;
    }
    base.saturating_mul(1u32 << attempt).min(cap)
}

fn is_terminal(state: DeliveryState) -> bool {
    matches!(
        state,
        DeliveryState::Delivered | DeliveryState::Rejected | DeliveryState::Conflict
    )
}

fn after_state(state: DeliveryState, view: &RecordView) -> Next {
    match state {
        DeliveryState::Delivered | DeliveryState::Rejected | DeliveryState::Conflict => {
            Next::Stop(Stop::Final)
        }
        DeliveryState::Blocked => Next::WaitForUser(UserAction::AdministratorRecovery),
        DeliveryState::ServerUnknown if view.since_first_dispatch >= SERVER_RETRY_WINDOW => {
            Next::PollAfter(SLOW_UNKNOWN_POLL)
        }
        DeliveryState::Pending
        | DeliveryState::Sending
        | DeliveryState::ServerUnknown
        | DeliveryState::Unknown => Next::PollAfter(backoff(POLL_BASE, POLL_CAP, view.attempt)),
    }
}

fn decision(effect: Effect, next: Next) -> Decision {
    Decision { effect, next }
}

pub fn decide(operation: Operation, observation: Observation, view: &RecordView) -> Decision {
    if view.server_state.is_some_and(is_terminal) {
        return decision(Effect::None, Next::Stop(Stop::Final));
    }
    let poll = Next::PollAfter(backoff(POLL_BASE, POLL_CAP, view.attempt));
    let retry = backoff(RETRY_BASE, RETRY_CAP, view.attempt);
    match (operation, observation) {
        (_, Observation::Receipt(state)) => {
            decision(Effect::RecordState(state), after_state(state, view))
        }

        (Operation::Submit, Observation::Refused { status, code }) => match (status, code) {
            (409, ServerCode::EventPayloadConflict) => decision(
                Effect::RecordState(DeliveryState::Conflict),
                Next::Stop(Stop::Conflict),
            ),
            (
                403,
                ServerCode::TargetDenied | ServerCode::ProjectDenied | ServerCode::AccountDisabled,
            ) => decision(
                Effect::RecordState(DeliveryState::Blocked),
                Next::Stop(Stop::PermissionDenied),
            ),
            (422, ServerCode::InvalidProjectAttribution) => decision(
                Effect::RecordState(DeliveryState::Rejected),
                Next::Stop(Stop::ProjectInvalid),
            ),
            (422, ServerCode::InvalidBackupInput) => decision(
                Effect::RecordState(DeliveryState::Rejected),
                Next::Stop(Stop::InvalidInput),
            ),
            (413, ServerCode::RenderedMessageTooLarge) => decision(
                Effect::RecordState(DeliveryState::Rejected),
                Next::Stop(Stop::TooLarge),
            ),
            (503, ServerCode::BackupDisabled) => {
                decision(Effect::MarkNotSent, Next::ResendAfter(retry))
            }
            (401, _) => decision(Effect::MarkNotSent, Next::WaitForUser(UserAction::SignIn)),
            // A refusal we cannot interpret: the request may have been
            // accepted by something in between. Read back only.
            _ => decision(Effect::None, poll),
        },
        (Operation::Submit, Observation::Transport(error)) => match error {
            TransportError::PreDispatch => decision(Effect::MarkNotSent, Next::ResendAfter(retry)),
            TransportError::Invalid => {
                decision(Effect::MarkNotSent, Next::Stop(Stop::InvalidInput))
            }
            TransportError::UnexpectedStatus(300..=399) => {
                decision(Effect::None, Next::Stop(Stop::ServerAddressInvalid))
            }
            TransportError::UnexpectedStatus(_)
            | TransportError::PostDispatch
            | TransportError::TooLarge => decision(Effect::None, poll),
        },

        (
            Operation::Readback,
            Observation::Refused {
                status: 404,
                code: ServerCode::BackupNotFound,
            },
        ) => {
            if view.phase == DispatchPhase::Reserved {
                decision(Effect::None, Next::ResendAfter(retry))
            } else if view.readback_not_found >= READBACK_404_LIMIT {
                // A 404 after a possible send does not prove anything final.
                decision(
                    Effect::None,
                    Next::PollAndAsk(RETRY_CAP, UserAction::CheckDelivery),
                )
            } else {
                decision(
                    Effect::None,
                    Next::PollAfter(backoff(POLL_BASE, RETRY_CAP, view.readback_not_found)),
                )
            }
        }
        (Operation::Readback, Observation::Refused { status: 401, .. }) => {
            decision(Effect::None, Next::WaitForUser(UserAction::SignIn))
        }
        (Operation::Readback, Observation::Refused { status: 403, .. }) => {
            decision(Effect::None, Next::Stop(Stop::PermissionDenied))
        }
        (Operation::Readback, Observation::Refused { .. }) => {
            decision(Effect::None, Next::PollAfter(retry))
        }
        (
            Operation::Readback,
            Observation::Transport(TransportError::UnexpectedStatus(300..=399)),
        ) => decision(Effect::None, Next::Stop(Stop::ServerAddressInvalid)),
        (Operation::Readback, Observation::Transport(_)) => {
            decision(Effect::None, Next::PollAfter(retry))
        }
    }
}
