//! Bounded HTTP transport for team Context backups (T05).
//!
//! The caller passes the bearer token for one request; it is never stored,
//! logged or returned. Responses are classified before any body is trusted:
//! the expected success status yields raw receipt bytes for
//! `context_wire::verify_*`; whitelisted refusals yield a typed server code
//! from either error envelope; everything else is an unexpected status.
//! Transport failures say whether the request may have reached the server,
//! which decides between a controlled resend and read-back only
//! (ADR 2026-10-08-backup-idempotency-retention-v1).
use crate::context_delivery::BackupPayload;
use crate::context_wire::{
    encode_submission, ContextEndpoint, MAX_CATALOG_BYTES, MAX_RECEIPT_BYTES,
};
use serde::Deserialize;

/// Maximum error body read to extract a server code.
pub const MAX_ERROR_BYTES: usize = 4096;
/// Statuses the backup routes and authentication use for refusals.
const REFUSAL_STATUSES: [u16; 7] = [401, 403, 404, 409, 413, 422, 503];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ServerCode {
    AuthRequired,
    InvalidCredential,
    TargetDenied,
    ProjectDenied,
    AccountDisabled,
    BackupNotFound,
    EventPayloadConflict,
    RenderedMessageTooLarge,
    InvalidProjectAttribution,
    InvalidBackupInput,
    BackupDisabled,
    /// A refusal status with a code this client does not know.
    Unrecognized,
}

impl ServerCode {
    fn parse(code: &str) -> Self {
        match code {
            "AUTH_REQUIRED" => Self::AuthRequired,
            "INVALID_CREDENTIAL" => Self::InvalidCredential,
            "TARGET_DENIED" => Self::TargetDenied,
            "PROJECT_DENIED" => Self::ProjectDenied,
            "ACCOUNT_DISABLED" => Self::AccountDisabled,
            "BACKUP_NOT_FOUND" => Self::BackupNotFound,
            "EVENT_PAYLOAD_CONFLICT" => Self::EventPayloadConflict,
            "RENDERED_MESSAGE_TOO_LARGE" => Self::RenderedMessageTooLarge,
            "INVALID_PROJECT_ATTRIBUTION" => Self::InvalidProjectAttribution,
            "INVALID_BACKUP_INPUT" => Self::InvalidBackupInput,
            "BACKUP_DISABLED" => Self::BackupDisabled,
            _ => Self::Unrecognized,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// Expected success status; bytes are unverified until `verify_*`.
    Receipt(Vec<u8>),
    /// A refusal the server explained.
    Rejected { status: u16, code: ServerCode },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransportError {
    /// The connection was never established: no request bytes left this process.
    PreDispatch,
    /// The request may have reached the server but no complete response arrived.
    PostDispatch,
    /// Redirect, server error or other status outside the contract.
    UnexpectedStatus(u16),
    /// Declared or streamed body exceeded the limit.
    TooLarge,
    /// The request could not be built (endpoint, encoding or client setup).
    Invalid,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Detail {
    Code(String),
    Object { code: String },
}
#[derive(Deserialize)]
struct ErrorEnvelope {
    detail: Detail,
}

fn classify_send_error(error: &reqwest::Error) -> TransportError {
    if error.is_builder() {
        TransportError::Invalid
    } else if error.is_connect() {
        TransportError::PreDispatch
    } else {
        TransportError::PostDispatch
    }
}

pub(crate) async fn read_bounded(
    mut response: reqwest::Response,
    limit: usize,
) -> Result<Vec<u8>, TransportError> {
    if response
        .content_length()
        .is_some_and(|length| length > limit as u64)
    {
        return Err(TransportError::TooLarge);
    }
    let mut body = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| TransportError::PostDispatch)?
    {
        if body.len() + chunk.len() > limit {
            return Err(TransportError::TooLarge);
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}

async fn classify(response: reqwest::Response, expected: u16) -> Result<Outcome, TransportError> {
    classify_limited(response, expected, MAX_RECEIPT_BYTES).await
}

async fn classify_limited(
    response: reqwest::Response,
    expected: u16,
    limit: usize,
) -> Result<Outcome, TransportError> {
    let status = response.status().as_u16();
    if status == expected {
        return read_bounded(response, limit).await.map(Outcome::Receipt);
    }
    if !REFUSAL_STATUSES.contains(&status) {
        return Err(TransportError::UnexpectedStatus(status));
    }
    let body = read_bounded(response, MAX_ERROR_BYTES).await?;
    let code = serde_json::from_slice::<ErrorEnvelope>(&body)
        .map(|envelope| match envelope.detail {
            Detail::Code(code) | Detail::Object { code } => ServerCode::parse(&code),
        })
        .unwrap_or(ServerCode::Unrecognized);
    Ok(Outcome::Rejected { status, code })
}

/// POST the frozen payload. Success is `202`.
pub async fn submit(
    endpoint: &ContextEndpoint,
    token: &str,
    payload: &BackupPayload,
) -> Result<Outcome, TransportError> {
    let client = endpoint
        .transport_client()
        .map_err(|_| TransportError::Invalid)?;
    let body = encode_submission(payload).map_err(|_| TransportError::Invalid)?;
    let response = client
        .post(endpoint.submission_url())
        .bearer_auth(token)
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(body)
        .send()
        .await
        .map_err(|error| classify_send_error(&error))?;
    classify(response, 202).await
}

/// GET the receipt for the payload's event. Success is `200`.
pub async fn readback(
    endpoint: &ContextEndpoint,
    token: &str,
    payload: &BackupPayload,
) -> Result<Outcome, TransportError> {
    let client = endpoint
        .transport_client()
        .map_err(|_| TransportError::Invalid)?;
    let response = client
        .get(endpoint.readback_url(payload))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|error| classify_send_error(&error))?;
    classify(response, 200).await
}

/// GET the authorized project catalog. Success is `200`; the body is
/// unverified until `context_wire::decode_projects`.
pub async fn fetch_projects(
    endpoint: &ContextEndpoint,
    token: &str,
) -> Result<Outcome, TransportError> {
    let client = endpoint
        .transport_client()
        .map_err(|_| TransportError::Invalid)?;
    let response = client
        .get(endpoint.projects_url())
        .bearer_auth(token)
        .send()
        .await
        .map_err(|error| classify_send_error(&error))?;
    classify_limited(response, 200, MAX_CATALOG_BYTES).await
}
