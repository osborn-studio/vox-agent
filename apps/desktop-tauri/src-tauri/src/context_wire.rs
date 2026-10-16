//! Bounded cloud Context wire adapter. No credentials or network side effects.
use crate::context_delivery::{
    BackupPayload, DeliveryState, ProjectSnapshot, ReceiptLease, ReceiptSource, SubmissionLease,
};
use serde::{Deserialize, Serialize};

#[derive(Debug, PartialEq, Eq)]
pub enum WireError {
    InvalidEndpoint,
    InvalidReceipt,
    TooLarge,
    Encoding,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextEndpoint(reqwest::Url);
impl ContextEndpoint {
    pub fn parse(value: &str) -> Result<Self, WireError> {
        let mut url = reqwest::Url::parse(value).map_err(|_| WireError::InvalidEndpoint)?;
        let host = url.host_str().ok_or(WireError::InvalidEndpoint)?;
        let loopback = matches!(host, "127.0.0.1" | "[::1]" | "::1");
        if !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || url.path() != "/"
            || !(url.scheme() == "https" || (url.scheme() == "http" && loopback))
        {
            return Err(WireError::InvalidEndpoint);
        }
        url.set_path("/");
        Ok(Self(url))
    }
    /// Canonical origin (`scheme://host[:port]/`) used to bind identities to an endpoint.
    pub fn canonical(&self) -> &str {
        self.0.as_str()
    }
    pub fn submission_url(&self) -> reqwest::Url {
        self.0.join("v1/input-backups").expect("fixed relative URL")
    }
    /// `event_id` is a validated identifier ([A-Za-z0-9_-]), so it is a single safe path segment.
    pub fn readback_url(&self, payload: &BackupPayload) -> reqwest::Url {
        self.0
            .join(&format!("v1/input-backups/{}", payload.event_id()))
            .expect("validated event id")
    }
    pub fn projects_url(&self) -> reqwest::Url {
        self.0
            .join("v1/context-projects")
            .expect("fixed relative URL")
    }
    /// All requests made with this client reject redirects, including same-origin
    /// ones, and ignore proxy environment variables so a bearer token can never be
    /// handed to a proxy implicitly.
    pub fn transport_client(&self) -> Result<reqwest::Client, WireError> {
        reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(std::time::Duration::from_secs(5))
            .timeout(std::time::Duration::from_secs(20))
            .build()
            .map_err(|_| WireError::InvalidEndpoint)
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BackupRequest<'a> {
    event_id: &'a str,
    target_id: &'a str,
    text: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_id: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    repo_source: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    repository: Option<&'a str>,
}

pub fn encode_submission(payload: &BackupPayload) -> Result<Vec<u8>, WireError> {
    let project = payload.project();
    serde_json::to_vec(&BackupRequest {
        event_id: payload.event_id(),
        target_id: payload.target_id(),
        text: payload.text(),
        project_id: project.map(|p| p.id()),
        repo_source: project.map(|_| "manual"),
        repository: project.map(|p| p.repository()),
    })
    .map_err(|_| WireError::Encoding)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Receipt {
    event_id: String,
    status: String,
    message_id: Option<String>,
    attribution: Option<Attribution>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Attribution {
    version: u32,
    project_id: String,
    repository_id: String,
}

/// A server receipt checked against the lease it answers. Fields are private
/// and there is no public constructor: only `verify_submission` and
/// `verify_readback` in this module can create one, so state cannot be applied
/// from an arbitrary DTO, string or `DeliveryState`.
pub struct VerifiedReceipt {
    instance: u64,
    owner: String,
    generation: u64,
    payload: BackupPayload,
    state: DeliveryState,
    source: ReceiptSource,
    message_id: Option<String>,
}
impl VerifiedReceipt {
    pub fn state(&self) -> DeliveryState {
        self.state
    }
    pub fn message_id(&self) -> Option<&str> {
        self.message_id.as_deref()
    }
    pub fn source(&self) -> ReceiptSource {
        self.source
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
    pub(crate) fn payload(&self) -> &BackupPayload {
        &self.payload
    }
}

/// Maximum project catalog body accepted from the server.
pub const MAX_CATALOG_BYTES: usize = 65_536;
const MAX_PROJECTS: usize = 500;

/// One authorized project: the frozen snapshot used for attribution and a
/// display name. The catalog never grants anything by itself; the server
/// re-checks authorization on every submission.
#[derive(Clone, PartialEq, Eq)]
pub struct ProjectEntry {
    pub snapshot: ProjectSnapshot,
    pub name: String,
}

pub struct ProjectCatalog {
    pub projects: Vec<ProjectEntry>,
    /// Entries that failed validation and were left out.
    pub omitted: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawCatalog {
    version: u32,
    projects: Vec<serde_json::Value>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RawProject {
    id: String,
    name: String,
    repository_id: String,
}

fn valid_name(name: &str) -> bool {
    !name.trim().is_empty() && name.chars().count() <= 120 && !name.chars().any(char::is_control)
}

/// Decode `GET /v1/context-projects` (version 1).
pub fn decode_projects(bytes: &[u8]) -> Result<ProjectCatalog, WireError> {
    if bytes.len() > MAX_CATALOG_BYTES {
        return Err(WireError::TooLarge);
    }
    let raw: RawCatalog = serde_json::from_slice(bytes).map_err(|_| WireError::InvalidReceipt)?;
    if raw.version != 1 {
        return Err(WireError::InvalidReceipt);
    }
    if raw.projects.len() > MAX_PROJECTS {
        return Err(WireError::TooLarge);
    }
    let mut catalog = ProjectCatalog {
        projects: Vec::new(),
        omitted: 0,
    };
    for value in raw.projects {
        let entry = serde_json::from_value::<RawProject>(value)
            .ok()
            .filter(|p| valid_name(&p.name))
            .and_then(|p| {
                ProjectSnapshot::new(p.id, p.repository_id)
                    .ok()
                    .map(|snapshot| ProjectEntry {
                        snapshot,
                        name: p.name.trim().to_string(),
                    })
            });
        match entry {
            Some(entry) => catalog.projects.push(entry),
            None => catalog.omitted += 1,
        }
    }
    Ok(catalog)
}

/// Maximum receipt body accepted from the server.
pub const MAX_RECEIPT_BYTES: usize = 16_384;

/// Statuses the real service exposes (fixture v2 `visible_statuses`).
/// `conflict` is never a receipt status: the server reports it as HTTP 409.
fn decode_body(
    bytes: &[u8],
    payload: &BackupPayload,
) -> Result<(DeliveryState, Option<String>), WireError> {
    if bytes.len() > MAX_RECEIPT_BYTES {
        return Err(WireError::TooLarge);
    }
    let r: Receipt = serde_json::from_slice(bytes).map_err(|_| WireError::InvalidReceipt)?;
    if r.event_id != payload.event_id() {
        return Err(WireError::InvalidReceipt);
    }
    match (payload.project(), r.attribution) {
        (None, None) => (),
        (Some(project), Some(a))
            if a.version == 1
                && a.project_id == project.id()
                && a.repository_id == project.repository() => {}
        _ => return Err(WireError::InvalidReceipt),
    }
    let valid_message = |v: &String| !v.is_empty() && v.len() <= 256;
    let state = match (r.status.as_str(), r.message_id.as_ref()) {
        ("delivered", Some(id)) if valid_message(id) => DeliveryState::Delivered,
        ("pending", None) => DeliveryState::Pending,
        ("sending", None) => DeliveryState::Sending,
        ("unknown", None) => DeliveryState::ServerUnknown,
        ("blocked", None) => DeliveryState::Blocked,
        ("rejected", None) => DeliveryState::Rejected,
        _ => return Err(WireError::InvalidReceipt),
    };
    Ok((state, r.message_id))
}

/// Verify the body of a `202` response to the POST made under `lease`.
pub fn verify_submission(
    lease: &SubmissionLease,
    bytes: &[u8],
) -> Result<VerifiedReceipt, WireError> {
    let (state, message_id) = decode_body(bytes, lease.payload())?;
    Ok(VerifiedReceipt {
        instance: lease.instance(),
        owner: lease.owner().to_string(),
        generation: lease.generation(),
        payload: lease.payload().clone(),
        state,
        source: ReceiptSource::Submission,
        message_id,
    })
}

/// Verify the body of a `200` readback made under `lease`.
pub fn verify_readback(lease: &ReceiptLease, bytes: &[u8]) -> Result<VerifiedReceipt, WireError> {
    let (state, message_id) = decode_body(bytes, lease.payload())?;
    Ok(VerifiedReceipt {
        instance: lease.instance(),
        owner: lease.owner().to_string(),
        generation: lease.generation(),
        payload: lease.payload().clone(),
        state,
        source: ReceiptSource::Readback { seq: lease.seq() },
        message_id,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context_delivery::ProjectSnapshot;
    fn payload(project: bool) -> BackupPayload {
        BackupPayload::new(
            "event-1".into(),
            "team".into(),
            "synthetic context".into(),
            project.then(|| ProjectSnapshot::new("p1".into(), "studio/repo".into()).unwrap()),
        )
        .unwrap()
    }
    fn decode(bytes: &[u8], p: &BackupPayload) -> Result<DeliveryState, WireError> {
        decode_body(bytes, p).map(|(state, _)| state)
    }
    #[test]
    fn cloud_fixture_v1_requests_still_serialize_identically() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../test/fixtures/context-backup-wire-v1.json"
        ))
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let p = payload(case["request"].get("projectId").is_some());
            let actual: serde_json::Value =
                serde_json::from_slice(&encode_submission(&p).unwrap()).unwrap();
            assert_eq!(actual, case["request"]);
            let receipt = serde_json::to_vec(&case["receipt"]).unwrap();
            assert!(decode(&receipt, &p).is_ok());
        }
    }
    #[test]
    fn emits_only_server_fields_and_frozen_project_attribution() {
        let raw = encode_submission(&payload(true)).unwrap();
        let v: serde_json::Value = serde_json::from_slice(&raw).unwrap();
        assert_eq!(
            v,
            serde_json::json!({"eventId":"event-1","targetId":"team","text":"synthetic context","projectId":"p1","repoSource":"manual","repository":"studio/repo"})
        );
        let raw = encode_submission(&payload(false)).unwrap();
        let v: serde_json::Value = serde_json::from_slice(&raw).unwrap();
        assert_eq!(v.as_object().unwrap().len(), 3);
    }
    #[test]
    fn only_matching_project_and_delivered_message_can_confirm() {
        let valid = br#"{"eventId":"event-1","status":"delivered","messageId":"om_synthetic","attribution":{"version":1,"projectId":"p1","repositoryId":"studio/repo"}}"#;
        assert_eq!(
            decode(valid, &payload(true)).unwrap(),
            DeliveryState::Delivered
        );
        assert!(decode(valid, &payload(false)).is_err());
        assert!(decode(
            br#"{"eventId":"event-1","status":"delivered","messageId":null}"#,
            &payload(false)
        )
        .is_err());
    }
    #[test]
    fn malformed_cross_event_and_oversized_receipts_fail_closed() {
        assert!(decode(
            br#"{"eventId":"event-2","status":"pending"}"#,
            &payload(false)
        )
        .is_err());
        assert!(decode(br#"{"eventId":"event-1","status":"pending","attribution":{"version":true,"projectId":"p1","repositoryId":"studio/repo"}}"#, &payload(true)).is_err());
        assert!(matches!(
            decode(&vec![b' '; 16_385], &payload(false)),
            Err(WireError::TooLarge)
        ));
    }
    #[test]
    fn remote_plain_http_credentials_paths_and_queries_are_rejected() {
        for v in [
            "http://example.com",
            "https://u:p@example.com",
            "https://example.com/path",
            "https://example.com?secret=x",
            "https://example.com#fragment",
        ] {
            assert!(ContextEndpoint::parse(v).is_err());
        }
        assert_eq!(
            ContextEndpoint::parse("https://example.com")
                .unwrap()
                .submission_url()
                .as_str(),
            "https://example.com/v1/input-backups"
        );
        assert!(ContextEndpoint::parse("http://127.0.0.1:1234").is_ok());
        assert!(ContextEndpoint::parse("http://[::1]:1234").is_ok());
    }
}
