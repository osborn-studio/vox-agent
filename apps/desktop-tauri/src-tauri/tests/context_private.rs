//! T18a: private mutation journal contract (scenario private_journal_core).
//! Server replies come from the committed real-service fixture
//! (scripts/context_extended_fixture.py, private section).
use serde_json::Value;
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::{owner_digest, Aad, KeyRing, RecordKind};
use vox_agent_desktop_tauri_lib::context_private::{
    decode_readback, decode_record, new_identifier, Applied, Method, Phase, PrivateError,
    PrivateJournal, PrivateMutation, PrivateStore, Readback, RecordCache, ReserveOutcome,
    SealedPrivateStore, Tag, MAX_SENDS, MAX_TEXT_CHARS,
};
use zeroize::Zeroizing;

const OWNER: &str = "https://context.example|org|feishu-0123456789abcdef0123456789abcdef|1";
const OTHER: &str = "https://context.example|org|feishu-fedcba9876543210fedcba9876543210|1";
const RECORD: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";
const STAMP: &str = "2026-10-08T01:02:03.456789+00:00";

fn cases() -> Vec<Value> {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../test/fixtures/context-extended-wire-v1.json"
    ))
    .unwrap()
    .replace("<id-1>", RECORD)
    .replace("<timestamp>", STAMP);
    let fixture: Value = serde_json::from_str(&text).unwrap();
    fixture["private"]["http_cases"].as_array().unwrap().clone()
}

fn case(name: &str) -> Value {
    cases()
        .into_iter()
        .find(|c| c["name"] == name)
        .unwrap_or_else(|| panic!("fixture case {name}"))
}

fn body(name: &str) -> Vec<u8> {
    serde_json::to_vec(&case(name)["response"]["body"]).unwrap()
}

fn status(name: &str) -> u16 {
    case(name)["response"]["status"].as_u64().unwrap() as u16
}

fn create() -> PrivateMutation {
    PrivateMutation::Create {
        event_id: "personal-1".into(),
        text: Zeroizing::new("synthetic note".into()),
        tags: vec![Tag::Question],
        project_id: None,
        repository_id: None,
    }
}

fn edit(request: &str, expected: u32, text: &str) -> PrivateMutation {
    PrivateMutation::Edit {
        record_id: RECORD.into(),
        event_id: "personal-1".into(),
        request_id: request.into(),
        expected_revision: expected,
        text: Zeroizing::new(text.into()),
        tags: vec![],
    }
}

fn withdraw() -> PrivateMutation {
    PrivateMutation::Withdraw {
        record_id: RECORD.into(),
        event_id: "personal-1".into(),
        request_id: "withdraw-1".into(),
        expected_revision: 2,
    }
}

#[derive(Default)]
struct MemoryStore {
    rows: Vec<(String, String, Vec<u8>)>,
    saves: usize,
    fail: bool,
}

impl PrivateStore for MemoryStore {
    fn save(
        &mut self,
        entry: &vox_agent_desktop_tauri_lib::context_private::JournalEntry,
    ) -> Result<(), PrivateError> {
        if self.fail {
            return Err(PrivateError::StoreUnavailable);
        }
        self.saves += 1;
        self.rows
            .retain(|r| !(r.0 == entry.owner && r.1 == entry.mutation.key()));
        self.rows
            .push((entry.owner.clone(), entry.mutation.key(), Vec::new()));
        Ok(())
    }
    fn remove(&mut self, owner: &str, key: &str) -> Result<(), PrivateError> {
        self.rows.retain(|r| !(r.0 == owner && r.1 == key));
        Ok(())
    }
    fn load(
        &self,
        _owner: &str,
    ) -> Result<Vec<vox_agent_desktop_tauri_lib::context_private::JournalEntry>, PrivateError> {
        Ok(Vec::new())
    }
}

#[test]
fn real_service_records_and_readbacks_decode() {
    let record = decode_record(&body("create")).unwrap();
    assert_eq!(
        (record.id.as_str(), record.revision, record.withdrawn),
        (RECORD, 1, false)
    );
    assert_eq!(record.tags, vec![Tag::Question]);
    assert!(!format!("{record:?}").contains("synthetic"));
    let withdrawn = decode_record(&body("withdraw")).unwrap();
    assert!(withdrawn.withdrawn && withdrawn.text.is_none() && withdrawn.revision == 3);

    match decode_readback(&body("request_readback")).unwrap() {
        Readback::Observed {
            record,
            mutation_id,
        } => {
            assert_eq!(mutation_id.as_deref(), Some("edit-1"));
            assert_eq!(record.revision, 2);
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(
        decode_readback(&body("event_readback_not_observed")),
        Some(Readback::NotObserved)
    );

    // Records that are not private user statements, or are inconsistent, are refused.
    let mutate = |f: &dyn Fn(&mut Value)| {
        let mut v = case("create")["response"]["body"].clone();
        f(&mut v);
        decode_record(&serde_json::to_vec(&v).unwrap())
    };
    assert!(mutate(&|v| v["visibility"] = "team".into()).is_none());
    assert!(mutate(&|v| v["evidenceKind"] = "verified-work".into()).is_none());
    assert!(mutate(&|v| v["withdrawn"] = true.into()).is_none());
    assert!(mutate(&|v| v["tags"] = serde_json::json!(["question", "question"])).is_none());
    assert!(mutate(&|v| v["tags"] = serde_json::json!(["urgent"])).is_none());
    assert!(mutate(&|v| v["id"] = "not-a-uuid".into()).is_none());
    assert!(mutate(&|v| v["revision"] = 0.into()).is_none());
    assert!(mutate(&|v| v["createdAt"] = "2026-10-08T01:02:03".into()).is_none());
    assert!(mutate(&|v| v["repositoryId"] = "../etc".into()).is_none());
    let mut not_observed = case("event_readback_not_observed")["response"]["body"].clone();
    not_observed["meaning"] = "absence-proof".into();
    assert!(decode_readback(&serde_json::to_vec(&not_observed).unwrap()).is_none());
}

#[test]
fn requests_match_the_real_service_contract() {
    let request = create().request();
    assert_eq!(
        (request.method, request.path.as_str()),
        (Method::Post, "v1/context-private")
    );
    let sent: Value = serde_json::from_slice(request.body.as_ref().unwrap()).unwrap();
    assert_eq!(sent, case("create")["request"]["json"]);

    let request = edit("edit-1", 1, "synthetic edit").request();
    assert_eq!(request.method, Method::Put);
    assert_eq!(request.path, format!("v1/context-private/{RECORD}"));
    let sent: Value = serde_json::from_slice(request.body.as_ref().unwrap()).unwrap();
    assert_eq!(sent, case("edit")["request"]["json"]);

    let request = withdraw().request();
    assert_eq!(
        request.path,
        format!("v1/context-private/{RECORD}/withdraw")
    );
    let sent: Value = serde_json::from_slice(request.body.as_ref().unwrap()).unwrap();
    assert_eq!(sent, case("withdraw")["request"]["json"]);

    assert_eq!(
        create().readback().path,
        "v1/context-private-events/personal-1"
    );
    assert_eq!(
        edit("edit-1", 1, "x").readback().path,
        "v1/context-private-events/personal-1/requests/edit-1"
    );
    // Same bytes on every attempt.
    assert_eq!(
        *create().request().body.unwrap(),
        *create().request().body.unwrap()
    );
}

#[test]
fn invalid_mutations_are_refused_before_anything_is_saved() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let too_long = PrivateMutation::Create {
        event_id: "e".into(),
        text: Zeroizing::new("字".repeat(MAX_TEXT_CHARS + 1)),
        tags: vec![],
        project_id: None,
        repository_id: None,
    };
    let at_limit = PrivateMutation::Create {
        event_id: "e2".into(),
        text: Zeroizing::new("字".repeat(MAX_TEXT_CHARS)),
        tags: vec![],
        project_id: None,
        repository_id: None,
    };
    let snapshot_without_project = PrivateMutation::Create {
        event_id: "e3".into(),
        text: Zeroizing::new("x".into()),
        tags: vec![],
        project_id: None,
        repository_id: Some("studio/repo".into()),
    };
    let duplicate_tags = PrivateMutation::Create {
        event_id: "e4".into(),
        text: Zeroizing::new("x".into()),
        tags: vec![Tag::Blocker, Tag::Blocker],
        project_id: None,
        repository_id: None,
    };
    for bad in [
        too_long,
        snapshot_without_project,
        duplicate_tags,
        edit("bad id", 1, "x"),
        edit("r", 0, "x"),
    ] {
        assert_eq!(journal.reserve(OWNER, bad), Err(PrivateError::InvalidInput));
    }
    assert_eq!(journal.store().saves, 0);
    assert_eq!(
        journal.reserve(OWNER, at_limit),
        Ok(ReserveOutcome::Reserved)
    );
    let id = new_identifier("pv_").unwrap();
    assert!(id.starts_with("pv_") && id.len() <= 128);
}

#[test]
fn a_lost_reply_is_resolved_by_readback_never_by_guessing() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();
    let key = create().key();
    assert_eq!(
        journal.reserve(OWNER, create()),
        Ok(ReserveOutcome::Reserved)
    );
    assert_eq!(
        journal.reserve(OWNER, create()),
        Ok(ReserveOutcome::AlreadyReserved)
    );
    // Same id, other payload: never silently replaced.
    let mut other = create();
    if let PrivateMutation::Create { tags, .. } = &mut other {
        tags.clear();
    }
    assert_eq!(
        journal.reserve(OWNER, other),
        Err(PrivateError::IdentityConflict)
    );
    assert_eq!(journal.store().saves, 1, "saved before any request");

    journal.begin_send(OWNER, &key).unwrap();
    assert_eq!(
        journal.begin_send(OWNER, &key).err(),
        Some(PrivateError::NotAllowed)
    );
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    assert!(matches!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Unknown(_)
    ));

    // not-observed is not proof that nothing happened: still unknown.
    let readback = journal.readback_request(OWNER, &key).unwrap();
    assert_eq!(readback.path, "v1/context-private-events/personal-1");
    let reply = body("event_readback_not_observed");
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Unknown("NOT_OBSERVED".into())
    );

    // Resending is the same request; the server replays the first result.
    let resend = journal.begin_send(OWNER, &key).unwrap();
    assert_eq!(*resend.body.unwrap(), *create().request().body.unwrap());
    let reply = body("create_replay");
    journal
        .finish_send(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied {
            record_id: RECORD.into(),
            revision: 1
        }
    );
    assert_eq!(cache.get(RECORD).unwrap().revision, 1);
}

#[test]
fn an_observed_readback_never_replaces_a_newer_revision() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();
    // The current record is already at revision 3 (withdrawn).
    assert!(cache.observe(decode_record(&body("get_after_withdraw")).unwrap()));
    let mutation = edit("edit-1", 1, "synthetic edit");
    let key = mutation.key();
    journal.reserve(OWNER, mutation).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let reply = body("request_readback");
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied {
            record_id: RECORD.into(),
            revision: 2
        }
    );
    let current = cache.get(RECORD).unwrap();
    assert!(
        current.withdrawn && current.revision == 3,
        "readback is historical"
    );

    // A readback for another request proves nothing about this one.
    let other = edit("edit-9", 1, "synthetic edit");
    let other_key = other.key();
    journal.reserve(OWNER, other).unwrap();
    journal.begin_send(OWNER, &other_key).unwrap();
    journal
        .finish_send(OWNER, &other_key, None, &mut cache)
        .unwrap();
    assert_eq!(
        journal
            .finish_readback(OWNER, &other_key, Some((200, &reply)), &mut cache)
            .unwrap(),
        Applied::Ignored
    );
}

#[test]
fn conflicts_and_refusals_keep_the_mutation_for_recovery() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();

    let stale = edit("edit-2", 1, "another edit");
    let key = stale.key();
    journal.reserve(OWNER, stale).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    let reply = body("edit_stale_revision");
    journal
        .finish_send(
            OWNER,
            &key,
            Some((status("edit_stale_revision"), &reply)),
            &mut cache,
        )
        .unwrap();
    let entry = journal.entry(OWNER, &key).unwrap();
    assert_eq!(
        entry.phase,
        Phase::Conflict("PRIVATE_CONTEXT_REVISION_CONFLICT".into())
    );
    assert!(
        matches!(&entry.mutation, PrivateMutation::Edit { text, .. } if text.as_str() == "another edit")
    );
    assert_eq!(
        journal.begin_send(OWNER, &key).err(),
        Some(PrivateError::NotAllowed)
    );

    let mut changed = create();
    if let PrivateMutation::Create { tags, .. } = &mut changed {
        tags.clear();
    }
    let key = changed.key();
    journal.reserve(OWNER, changed).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    let reply = body("create_conflict");
    journal
        .finish_send(OWNER, &key, Some((409, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Conflict("PRIVATE_CONTEXT_CONFLICT".into())
    );

    let gone = withdraw();
    let key = gone.key();
    journal.reserve(OWNER, gone).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    let reply = body("get_other_owner");
    journal
        .finish_send(OWNER, &key, Some((404, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Rejected("PRIVATE_CONTEXT_NOT_FOUND".into())
    );

    // A 200 that is not this mutation's result is not trusted.
    let other = edit("edit-3", 2, "different text");
    let key = other.key();
    journal.reserve(OWNER, other).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    let reply = body("edit");
    journal
        .finish_send(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Unknown("INVALID_RESPONSE".into())
    );

    // Sign-in lost: a user retry keeps the identity; unknown entries cannot be dismissed.
    let blocked = edit("edit-4", 2, "x");
    let key = blocked.key();
    journal.reserve(OWNER, blocked).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    let reply = body("no_auth");
    journal
        .finish_send(OWNER, &key, Some((401, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Blocked("AUTH_REQUIRED".into())
    );
    assert!(journal.begin_send(OWNER, &key).is_ok());
    assert_eq!(
        journal.dismiss(OWNER, &key).err(),
        Some(PrivateError::NotAllowed)
    );

    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    while journal.begin_send(OWNER, &key).is_ok() {
        journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    }
    assert_eq!(
        journal.begin_send(OWNER, &key).err(),
        Some(PrivateError::RetryLimit)
    );
    assert_eq!(journal.entry(OWNER, &key).unwrap().sends, MAX_SENDS);
}

#[test]
fn nothing_is_sent_without_a_save_and_late_replies_are_ignored() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();
    journal.store_mut().fail = true;
    assert_eq!(
        journal.reserve(OWNER, create()),
        Err(PrivateError::StoreUnavailable)
    );
    journal.store_mut().fail = false;
    journal.reserve(OWNER, create()).unwrap();
    journal.store_mut().fail = true;
    assert_eq!(
        journal.begin_send(OWNER, &create().key()).err(),
        Some(PrivateError::StoreUnavailable)
    );
    assert_eq!(
        journal.entry(OWNER, &create().key()).unwrap().phase,
        Phase::Reserved
    );

    // Another owner's reply never touches this owner's entry.
    journal.store_mut().fail = false;
    journal.begin_send(OWNER, &create().key()).unwrap();
    let reply = body("create");
    assert_eq!(
        journal
            .finish_send(OWNER_B(), &create().key(), Some((200, &reply)), &mut cache)
            .unwrap(),
        Applied::Ignored
    );
    // A failed save after the reply still records what the reply proved.
    journal.store_mut().fail = true;
    assert_eq!(
        journal
            .finish_send(OWNER, &create().key(), Some((200, &reply)), &mut cache)
            .err(),
        Some(PrivateError::StoreUnavailable)
    );
    assert!(matches!(
        journal.entry(OWNER, &create().key()).unwrap().phase,
        Phase::Applied { .. }
    ));
}

#[allow(non_snake_case)]
fn OWNER_B() -> &'static str {
    OTHER
}

#[test]
fn the_sealed_store_survives_restart_and_binds_rows_to_owner_and_kind() {
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), FakeKeyProvider::new([9; 32]));
    let keys = ring.create().unwrap();
    let id = keys.id().clone();
    let path = dir.path().join("private.db");
    let mut cache = RecordCache::default();
    {
        let mut journal =
            PrivateJournal::open(SealedPrivateStore::open(&path, keys).unwrap(), OWNER).unwrap();
        journal.reserve(OWNER, create()).unwrap();
        journal.reserve(OWNER, withdraw()).unwrap();
        journal.begin_send(OWNER, &create().key()).unwrap(); // crash while dispatching
    }
    let bytes = std::fs::read(&path).unwrap();
    assert!(
        !String::from_utf8_lossy(&bytes).contains("synthetic note"),
        "text is sealed"
    );

    let store = SealedPrivateStore::open(&path, ring.open(&id).unwrap()).unwrap();
    let mut journal = PrivateJournal::open(store, OWNER).unwrap();
    // A request that may have been sent comes back as unknown, never as unsent.
    assert_eq!(
        journal.entry(OWNER, &create().key()).unwrap().phase,
        Phase::Unknown("RESTARTED".into())
    );
    assert_eq!(
        journal.entry(OWNER, &withdraw().key()).unwrap().phase,
        Phase::Reserved
    );
    assert!(journal.readback_request(OWNER, &create().key()).is_some());
    let reply = body("event_readback");
    journal
        .finish_readback(OWNER, &create().key(), Some((200, &reply)), &mut cache)
        .unwrap();
    assert!(matches!(
        journal.entry(OWNER, &create().key()).unwrap().phase,
        Phase::Applied { .. }
    ));

    // Another owner sees nothing; rows moved between owners or keys fail to open.
    let store = SealedPrivateStore::open(&path, ring.open(&id).unwrap()).unwrap();
    assert!(store.load(OTHER).unwrap().is_empty());
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute(
        "UPDATE private_mutations_v1 SET owner=?1",
        [owner_digest(OTHER).to_vec()],
    )
    .unwrap();
    let store = SealedPrivateStore::open(&path, ring.open(&id).unwrap()).unwrap();
    assert!(store.load(OTHER).unwrap().is_empty());
    assert_eq!(store.quarantined(), 2);

    // A team-journal AAD never opens a private row (separate record kind).
    let keys = ring.open(&id).unwrap();
    let team = Aad::new(1, owner_digest(OWNER), "personal-1", RecordKind::TeamBackup).unwrap();
    let private = Aad::new(
        1,
        owner_digest(OWNER),
        "personal-1",
        RecordKind::PrivateMutation,
    )
    .unwrap();
    let sealed = keys.seal(&private, b"x").unwrap();
    assert!(keys.open(&team, &sealed).is_err());
}

#[test]
fn the_record_cache_keeps_the_newest_revision() {
    let mut cache = RecordCache::default();
    assert!(cache.observe(decode_record(&body("edit")).unwrap()));
    assert!(!cache.observe(decode_record(&body("create")).unwrap()));
    assert_eq!(cache.get(RECORD).unwrap().revision, 2);
    assert!(cache.observe(decode_record(&body("withdraw")).unwrap()));
    cache.clear();
    assert!(cache.get(RECORD).is_none());
}

#[test]
fn a_sign_in_refusal_never_releases_a_mutation_that_may_have_been_applied() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();
    let key = create().key();
    journal.reserve(OWNER, create()).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let refused = body("no_auth");
    journal
        .finish_readback(OWNER, &key, Some((401, &refused)), &mut cache)
        .unwrap();
    let entry = journal.entry(OWNER, &key).unwrap();
    assert!(matches!(entry.phase, Phase::Blocked(_)) && entry.maybe_applied);
    assert_eq!(
        journal.dismiss(OWNER, &key).err(),
        Some(PrivateError::NotAllowed)
    );
    // After signing in again, the same readback settles it.
    assert!(journal.readback_request(OWNER, &key).is_some());
    let observed = body("event_readback");
    journal
        .finish_readback(OWNER, &key, Some((200, &observed)), &mut cache)
        .unwrap();
    let entry = journal.entry(OWNER, &key).unwrap();
    assert!(matches!(entry.phase, Phase::Applied { revision: 1, .. }) && !entry.maybe_applied);
    journal.dismiss(OWNER, &key).unwrap();

    // A refused resend after a lost reply is just as unsettled.
    let mutation = edit("edit-5", 1, "x");
    let key = mutation.key();
    journal.reserve(OWNER, mutation).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal
        .finish_send(OWNER, &key, Some((403, b"{}")), &mut cache)
        .unwrap();
    assert_eq!(
        journal.dismiss(OWNER, &key).err(),
        Some(PrivateError::NotAllowed)
    );
}

#[test]
fn an_event_readback_proves_a_create_only_while_it_is_still_that_create() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();
    // A different create for an eventId whose record was later withdrawn.
    let mut other = create();
    if let PrivateMutation::Create { text, .. } = &mut other {
        *text = Zeroizing::new("different text".into());
    }
    let key = other.key();
    journal.reserve(OWNER, other).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let mut withdrawn = case("event_readback")["response"]["body"].clone();
    withdrawn["source"] = case("get_after_withdraw")["response"]["body"].clone();
    let reply = serde_json::to_vec(&withdrawn).unwrap();
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Unknown("READBACK_INCONCLUSIVE".into())
    );
    // Resending tells: the server refuses another payload for that eventId.
    journal.begin_send(OWNER, &key).unwrap();
    let conflict = body("create_conflict");
    journal
        .finish_send(OWNER, &key, Some((409, &conflict)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Conflict("PRIVATE_CONTEXT_CONFLICT".into())
    );

    // The first page of an event readback with another payload is not proof either.
    let mut changed = create();
    if let PrivateMutation::Create { tags, .. } = &mut changed {
        tags.push(Tag::Blocker);
    }
    let key = changed.key();
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    journal.reserve(OWNER, changed).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let reply = body("event_readback");
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert!(!matches!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied { .. }
    ));
}

#[test]
fn an_accepted_edit_settles_even_after_the_record_was_withdrawn() {
    let mut cache = RecordCache::default();
    let withdrawn = body("get_after_withdraw");
    // Resend answered with the current (withdrawn) record.
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mutation = edit("edit-1", 1, "synthetic edit");
    let key = mutation.key();
    journal.reserve(OWNER, mutation).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal
        .finish_send(OWNER, &key, Some((200, &withdrawn)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied {
            record_id: RECORD.into(),
            revision: 2
        }
    );
    // A request readback showing the later, withdrawn record is not proof of
    // this payload: inconclusive, and resending the same request decides.
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    journal
        .reserve(OWNER, edit("edit-1", 1, "synthetic edit"))
        .unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let mut readback = case("request_readback")["response"]["body"].clone();
    readback["source"] = case("get_after_withdraw")["response"]["body"].clone();
    let reply = serde_json::to_vec(&readback).unwrap();
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Unknown("READBACK_INCONCLUSIVE".into())
    );
    journal.begin_send(OWNER, &key).unwrap();
    journal
        .finish_send(OWNER, &key, Some((200, &withdrawn)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied {
            record_id: RECORD.into(),
            revision: 2
        }
    );
    // A same-revision readback with this exact body is proof.
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    journal
        .reserve(OWNER, edit("edit-1", 1, "synthetic edit"))
        .unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let reply = body("request_readback");
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert!(matches!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied { revision: 2, .. }
    ));
    // A withdrawal is never "proven" by a record that is not withdrawn.
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    journal.reserve(OWNER, withdraw()).unwrap();
    journal.begin_send(OWNER, &withdraw().key()).unwrap();
    let edited = body("edit");
    journal
        .finish_send(OWNER, &withdraw().key(), Some((200, &edited)), &mut cache)
        .unwrap();
    assert!(matches!(
        journal.entry(OWNER, &withdraw().key()).unwrap().phase,
        Phase::Unknown(_)
    ));
}

#[test]
fn a_refused_resend_keeps_an_earlier_request_protected() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();
    let key = create().key();
    journal.reserve(OWNER, create()).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    let not_found = body("get_other_owner");
    journal
        .finish_send(OWNER, &key, Some((404, &not_found)), &mut cache)
        .unwrap();
    let entry = journal.entry(OWNER, &key).unwrap();
    assert_eq!(
        entry.phase,
        Phase::Unknown("PRIVATE_CONTEXT_NOT_FOUND".into())
    );
    assert!(entry.maybe_applied);
    assert_eq!(
        journal.dismiss(OWNER, &key).err(),
        Some(PrivateError::NotAllowed)
    );
    // Once access is back, the same readback settles it.
    let observed = body("event_readback");
    journal
        .finish_readback(OWNER, &key, Some((200, &observed)), &mut cache)
        .unwrap();
    assert!(matches!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied { .. }
    ));
    // A first request refused outright is settled.
    let mutation = edit("edit-6", 1, "x");
    let key = mutation.key();
    journal.reserve(OWNER, mutation).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal
        .finish_send(OWNER, &key, Some((404, &not_found)), &mut cache)
        .unwrap();
    journal.dismiss(OWNER, &key).unwrap();
}

#[test]
fn a_project_create_without_a_repository_expectation_is_confirmed_by_its_reply() {
    let mut cache = RecordCache::default();
    let mutation = PrivateMutation::Create {
        event_id: "personal-1".into(),
        text: Zeroizing::new("synthetic note".into()),
        tags: vec![Tag::Question],
        project_id: Some("vox".into()),
        repository_id: None,
    };
    let key = mutation.key();
    let mut accepted = case("create")["response"]["body"].clone();
    accepted["projectId"] = "vox".into();
    accepted["repositoryId"] = "studio/vox".into();
    let reply = serde_json::to_vec(&accepted).unwrap();
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    journal.reserve(OWNER, mutation.clone()).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal
        .finish_send(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert!(matches!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied { revision: 1, .. }
    ));
    // Its readback cannot prove which repository was asked for: inconclusive.
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    journal.reserve(OWNER, mutation).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let mut readback = case("event_readback")["response"]["body"].clone();
    readback["source"] = accepted;
    let reply = serde_json::to_vec(&readback).unwrap();
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Unknown("READBACK_INCONCLUSIVE".into())
    );
}

#[test]
fn a_tombstone_never_proves_a_withdrawal_by_readback() {
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    let mut cache = RecordCache::default();
    let key = withdraw().key();
    journal.reserve(OWNER, withdraw()).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    let mut readback = case("request_readback")["response"]["body"].clone();
    readback["mutationId"] = "withdraw-1".into();
    readback["source"] = case("get_after_withdraw")["response"]["body"].clone();
    let reply = serde_json::to_vec(&readback).unwrap();
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert_eq!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Unknown("READBACK_INCONCLUSIVE".into())
    );
    // The same request answers it: the server replays the withdrawal.
    journal.begin_send(OWNER, &key).unwrap();
    let reply = body("withdraw");
    journal
        .finish_send(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    assert!(matches!(
        journal.entry(OWNER, &key).unwrap().phase,
        Phase::Applied { revision: 3, .. }
    ));
}

#[test]
fn a_project_create_is_never_proven_by_readback() {
    let mut cache = RecordCache::default();
    let mutation = PrivateMutation::Create {
        event_id: "personal-1".into(),
        text: Zeroizing::new("synthetic note".into()),
        tags: vec![Tag::Question],
        project_id: Some("vox".into()),
        repository_id: Some("studio/vox".into()),
    };
    let key = mutation.key();
    let mut journal = PrivateJournal::open(MemoryStore::default(), OWNER).unwrap();
    journal.reserve(OWNER, mutation).unwrap();
    journal.begin_send(OWNER, &key).unwrap();
    journal.finish_send(OWNER, &key, None, &mut cache).unwrap();
    // The stored record of a create that did not ask for a repository looks the same.
    let mut source = case("create")["response"]["body"].clone();
    source["projectId"] = "vox".into();
    source["repositoryId"] = "studio/vox".into();
    let mut readback = case("event_readback")["response"]["body"].clone();
    readback["source"] = source;
    let reply = serde_json::to_vec(&readback).unwrap();
    journal
        .finish_readback(OWNER, &key, Some((200, &reply)), &mut cache)
        .unwrap();
    let entry = journal.entry(OWNER, &key).unwrap();
    assert_eq!(entry.phase, Phase::Unknown("READBACK_INCONCLUSIVE".into()));
    assert!(entry.maybe_applied);
}
