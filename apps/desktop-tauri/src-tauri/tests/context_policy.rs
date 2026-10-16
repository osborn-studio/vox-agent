//! T09: what each submission or read-back result does to a record and what
//! happens next (plan §3.2, design §6.3, ADR backup-idempotency-retention-v1).
use std::time::Duration;

use vox_agent_desktop_tauri_lib::context_delivery::DeliveryState;
use vox_agent_desktop_tauri_lib::context_journal::DispatchPhase;
use vox_agent_desktop_tauri_lib::context_policy::{
    backoff, decide, Decision, Effect, Next, Observation, Operation, RecordView, Stop, UserAction,
    POLL_CAP, READBACK_404_LIMIT, RETRY_CAP, SERVER_RETRY_WINDOW, SLOW_UNKNOWN_POLL,
};
use vox_agent_desktop_tauri_lib::context_transport::{ServerCode, TransportError};

fn view(phase: DispatchPhase, state: Option<DeliveryState>) -> RecordView {
    RecordView {
        phase,
        server_state: state,
        since_first_dispatch: Duration::from_secs(10),
        attempt: 0,
        readback_not_found: 0,
    }
}

fn dispatching() -> RecordView {
    view(DispatchPhase::Dispatching, None)
}

fn refused(status: u16, code: ServerCode) -> Observation {
    Observation::Refused { status, code }
}

#[test]
fn an_accepted_submission_is_queued_not_delivered_and_is_polled() {
    let d = decide(
        Operation::Submit,
        Observation::Receipt(DeliveryState::Pending),
        &dispatching(),
    );
    assert_eq!(d.effect, Effect::RecordState(DeliveryState::Pending));
    assert!(matches!(d.next, Next::PollAfter(_)));
}

#[test]
fn submission_refusals_follow_the_contract_table() {
    let cases = [
        (
            refused(409, ServerCode::EventPayloadConflict),
            Effect::RecordState(DeliveryState::Conflict),
            Next::Stop(Stop::Conflict),
        ),
        (
            refused(403, ServerCode::TargetDenied),
            Effect::RecordState(DeliveryState::Blocked),
            Next::Stop(Stop::PermissionDenied),
        ),
        (
            refused(403, ServerCode::ProjectDenied),
            Effect::RecordState(DeliveryState::Blocked),
            Next::Stop(Stop::PermissionDenied),
        ),
        (
            refused(422, ServerCode::InvalidProjectAttribution),
            Effect::RecordState(DeliveryState::Rejected),
            Next::Stop(Stop::ProjectInvalid),
        ),
        (
            refused(422, ServerCode::InvalidBackupInput),
            Effect::RecordState(DeliveryState::Rejected),
            Next::Stop(Stop::InvalidInput),
        ),
        (
            refused(413, ServerCode::RenderedMessageTooLarge),
            Effect::RecordState(DeliveryState::Rejected),
            Next::Stop(Stop::TooLarge),
        ),
    ];
    for (observation, effect, next) in cases {
        let d = decide(Operation::Submit, observation, &dispatching());
        assert_eq!((d.effect, d.next), (effect, next));
    }
}

#[test]
fn service_paused_or_signed_out_means_not_accepted_and_no_retry_storm() {
    let mut v = dispatching();
    v.attempt = 20;
    let d = decide(
        Operation::Submit,
        refused(503, ServerCode::BackupDisabled),
        &v,
    );
    assert_eq!(
        d,
        Decision {
            effect: Effect::MarkNotSent,
            next: Next::ResendAfter(RETRY_CAP)
        }
    );
    let d = decide(
        Operation::Submit,
        refused(401, ServerCode::InvalidCredential),
        &v,
    );
    assert_eq!(
        d,
        Decision {
            effect: Effect::MarkNotSent,
            next: Next::WaitForUser(UserAction::SignIn)
        }
    );
}

#[test]
fn only_a_proven_pre_dispatch_failure_allows_a_resend() {
    let d = decide(
        Operation::Submit,
        Observation::Transport(TransportError::PreDispatch),
        &dispatching(),
    );
    assert_eq!(d.effect, Effect::MarkNotSent);
    assert!(matches!(d.next, Next::ResendAfter(_)));
    for error in [TransportError::PostDispatch, TransportError::TooLarge] {
        let d = decide(
            Operation::Submit,
            Observation::Transport(error),
            &dispatching(),
        );
        assert_eq!(d.effect, Effect::None, "{error:?}");
        assert!(
            matches!(d.next, Next::PollAfter(_)),
            "{error:?}: read back only"
        );
    }
    let d = decide(
        Operation::Submit,
        Observation::Transport(TransportError::UnexpectedStatus(307)),
        &dispatching(),
    );
    assert_eq!(d.next, Next::Stop(Stop::ServerAddressInvalid));
    assert_eq!(
        d.effect,
        Effect::None,
        "a redirect may follow an accepted request; never resend"
    );
}

#[test]
fn an_unknown_refusal_code_on_submit_is_read_back_not_resent() {
    let d = decide(
        Operation::Submit,
        refused(409, ServerCode::Unrecognized),
        &dispatching(),
    );
    assert_eq!(d.effect, Effect::None);
    assert!(matches!(d.next, Next::PollAfter(_)));
}

#[test]
fn readback_states_set_the_poll_schedule() {
    let v = view(DispatchPhase::Accepted, Some(DeliveryState::Pending));
    for state in [DeliveryState::Pending, DeliveryState::Sending] {
        let d = decide(Operation::Readback, Observation::Receipt(state), &v);
        assert_eq!(d.effect, Effect::RecordState(state));
        assert!(matches!(d.next, Next::PollAfter(t) if t <= POLL_CAP));
    }
    for state in [DeliveryState::Delivered, DeliveryState::Rejected] {
        assert_eq!(
            decide(Operation::Readback, Observation::Receipt(state), &v).next,
            Next::Stop(Stop::Final)
        );
    }
    let d = decide(
        Operation::Readback,
        Observation::Receipt(DeliveryState::Blocked),
        &v,
    );
    assert_eq!(d.next, Next::WaitForUser(UserAction::AdministratorRecovery));
}

#[test]
fn server_unknown_is_polled_normally_inside_the_retry_window_and_slowly_after() {
    let mut v = view(DispatchPhase::Accepted, Some(DeliveryState::ServerUnknown));
    v.since_first_dispatch = SERVER_RETRY_WINDOW - Duration::from_secs(1);
    let d = decide(
        Operation::Readback,
        Observation::Receipt(DeliveryState::ServerUnknown),
        &v,
    );
    assert!(matches!(d.next, Next::PollAfter(t) if t <= POLL_CAP));
    v.since_first_dispatch = SERVER_RETRY_WINDOW;
    let d = decide(
        Operation::Readback,
        Observation::Receipt(DeliveryState::ServerUnknown),
        &v,
    );
    assert_eq!(d.next, Next::PollAfter(SLOW_UNKNOWN_POLL));
}

#[test]
fn readback_404_never_deletes_and_never_posts_after_a_possible_send() {
    let mut v = dispatching();
    let d = decide(
        Operation::Readback,
        refused(404, ServerCode::BackupNotFound),
        &v,
    );
    assert_eq!(d.effect, Effect::None);
    assert!(matches!(d.next, Next::PollAfter(_)));
    v.readback_not_found = READBACK_404_LIMIT;
    let d = decide(
        Operation::Readback,
        refused(404, ServerCode::BackupNotFound),
        &v,
    );
    assert_eq!(
        d.next,
        Next::PollAndAsk(RETRY_CAP, UserAction::CheckDelivery)
    );
    for phase in [DispatchPhase::Restored, DispatchPhase::Accepted] {
        let d = decide(
            Operation::Readback,
            refused(404, ServerCode::BackupNotFound),
            &view(phase, None),
        );
        assert!(!matches!(d.next, Next::ResendAfter(_)), "{phase:?}");
    }
}

#[test]
fn readback_404_for_a_never_sent_record_allows_a_controlled_same_payload_resend() {
    let d = decide(
        Operation::Readback,
        refused(404, ServerCode::BackupNotFound),
        &view(DispatchPhase::Reserved, None),
    );
    assert_eq!(d.effect, Effect::None);
    assert!(matches!(d.next, Next::ResendAfter(_)));
}

#[test]
fn readback_failures_only_ever_read_back_again() {
    for observation in [
        Observation::Transport(TransportError::PreDispatch),
        Observation::Transport(TransportError::PostDispatch),
        refused(503, ServerCode::BackupDisabled),
    ] {
        let d = decide(Operation::Readback, observation, &dispatching());
        assert_eq!(d.effect, Effect::None);
        assert!(matches!(d.next, Next::PollAfter(t) if t <= RETRY_CAP));
    }
    let d = decide(
        Operation::Readback,
        refused(401, ServerCode::AuthRequired),
        &dispatching(),
    );
    assert_eq!(
        d,
        Decision {
            effect: Effect::None,
            next: Next::WaitForUser(UserAction::SignIn)
        }
    );
    let d = decide(
        Operation::Readback,
        refused(403, ServerCode::TargetDenied),
        &dispatching(),
    );
    assert_eq!(d.next, Next::Stop(Stop::PermissionDenied));
    assert_eq!(
        d.effect,
        Effect::None,
        "a read refusal says nothing about delivery"
    );
}

#[test]
fn a_final_record_is_never_acted_on_again() {
    for state in [
        DeliveryState::Delivered,
        DeliveryState::Rejected,
        DeliveryState::Conflict,
    ] {
        let v = view(DispatchPhase::Accepted, Some(state));
        for observation in [
            Observation::Receipt(DeliveryState::Pending),
            refused(404, ServerCode::BackupNotFound),
            Observation::Transport(TransportError::PreDispatch),
        ] {
            assert_eq!(
                decide(Operation::Readback, observation, &v),
                Decision {
                    effect: Effect::None,
                    next: Next::Stop(Stop::Final)
                }
            );
        }
    }
}

#[test]
fn backoff_doubles_from_its_base_and_is_capped() {
    let base = Duration::from_secs(2);
    assert_eq!(backoff(base, POLL_CAP, 0), base);
    assert_eq!(backoff(base, POLL_CAP, 3), Duration::from_secs(16));
    assert_eq!(backoff(base, POLL_CAP, 63), POLL_CAP);
    assert_eq!(backoff(base, POLL_CAP, u32::MAX), POLL_CAP);
}
