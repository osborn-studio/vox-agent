//! T06: the Rust session authority owns cloud identity, generations and the token.
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use vox_agent_desktop_tauri_lib::context_session::{
    DestinationKey, PrincipalKey, SessionAuthority, SessionError, TokenStore, REFRESH_ACCOUNT,
};
use vox_agent_desktop_tauri_lib::context_wire::ContextEndpoint;
use vox_agent_desktop_tauri_lib::secrets::SecretError;

#[derive(Clone, Default)]
struct FakeStore {
    values: Rc<RefCell<HashMap<String, String>>>,
    fail_writes: Rc<RefCell<bool>>,
    fail_deletes: Rc<RefCell<bool>>,
}

impl TokenStore for FakeStore {
    fn read(&self, account: &str) -> Result<Option<String>, SecretError> {
        Ok(self.values.borrow().get(account).cloned())
    }
    fn write(&self, account: &str, value: &str) -> Result<(), SecretError> {
        if *self.fail_writes.borrow() {
            return Err(SecretError::Locked);
        }
        self.values
            .borrow_mut()
            .insert(account.into(), value.into());
        Ok(())
    }
    fn delete(&self, account: &str) -> Result<(), SecretError> {
        if *self.fail_deletes.borrow() {
            return Err(SecretError::Backend);
        }
        self.values.borrow_mut().remove(account);
        Ok(())
    }
}

fn principal(actor: &str) -> PrincipalKey {
    let endpoint = ContextEndpoint::parse("https://api.example.test").unwrap();
    PrincipalKey::new(&endpoint, "default-org", actor, 1).unwrap()
}

fn signed_in(store: &FakeStore) -> SessionAuthority<FakeStore> {
    let mut authority = SessionAuthority::new(store.clone());
    authority
        .sign_in(principal("alice"), "token-1".into())
        .unwrap();
    authority
}

#[test]
fn signed_in_lease_yields_the_token_and_the_token_never_prints() {
    let store = FakeStore::default();
    let authority = signed_in(&store);
    let lease = authority.lease().unwrap();
    let token = authority.token(&lease).unwrap();
    assert_eq!(token.expose_for_request(), "token-1");
    assert!(!format!("{token:?}").contains("token-1"));
    assert!(!format!("{lease:?}").contains("token-1"));
}

#[test]
fn sign_out_and_back_in_as_the_same_account_invalidates_old_leases() {
    let store = FakeStore::default();
    let mut authority = signed_in(&store);
    let old = authority.lease().unwrap();
    authority.sign_out().unwrap();
    assert!(matches!(
        authority.token(&old),
        Err(SessionError::StaleLease)
    ));
    authority
        .sign_in(principal("alice"), "token-2".into())
        .unwrap();
    assert!(matches!(
        authority.token(&old),
        Err(SessionError::StaleLease)
    ));
    let fresh = authority.lease().unwrap();
    assert_eq!(
        authority.token(&fresh).unwrap().expose_for_request(),
        "token-2"
    );
}

#[test]
fn refresh_for_the_same_principal_keeps_in_flight_leases_and_rotates_the_token() {
    let store = FakeStore::default();
    let mut authority = signed_in(&store);
    let lease = authority.lease().unwrap();
    authority
        .refresh(&lease, &principal("alice"), "token-2".into())
        .unwrap();
    assert_eq!(
        authority.token(&lease).unwrap().expose_for_request(),
        "token-2"
    );
    let after = authority.lease().unwrap();
    assert_eq!(after.generation(), lease.generation());
    assert_eq!(after.epoch(), lease.epoch() + 1);
}

#[test]
fn refresh_that_reveals_a_different_principal_signs_out_completely() {
    let store = FakeStore::default();
    let mut authority = signed_in(&store);
    let lease = authority.lease().unwrap();
    assert!(matches!(
        authority.refresh(&lease, &principal("mallory"), "token-2".into()),
        Err(SessionError::IdentityChanged)
    ));
    assert!(matches!(authority.lease(), Err(SessionError::NotSignedIn)));
    assert!(matches!(
        authority.token(&lease),
        Err(SessionError::StaleLease)
    ));
    assert!(
        store.values.borrow().is_empty(),
        "stored token and binding are removed"
    );
}

#[test]
fn restart_restores_an_unverified_session_that_cannot_dispatch_until_confirmed() {
    let store = FakeStore::default();
    drop(signed_in(&store));
    let mut restored = SessionAuthority::new(store.clone());
    let ticket = restored.restore().unwrap();
    assert!(matches!(restored.lease(), Err(SessionError::Unverified)));
    assert_eq!(restored.queue_scope(), Some(principal("alice").scope()));
    restored.confirm(ticket, &principal("alice")).unwrap();
    let lease = restored.lease().unwrap();
    assert_eq!(
        restored.token(&lease).unwrap().expose_for_request(),
        "token-1"
    );
}

#[test]
fn confirming_a_different_principal_after_restart_clears_the_session() {
    let store = FakeStore::default();
    drop(signed_in(&store));
    let mut restored = SessionAuthority::new(store.clone());
    let ticket = restored.restore().unwrap();
    assert!(matches!(
        restored.confirm(ticket, &principal("mallory")),
        Err(SessionError::IdentityChanged)
    ));
    assert_eq!(restored.queue_scope(), None);
    assert!(store.values.borrow().is_empty());
}

#[test]
fn a_store_failure_during_sign_in_leaves_nothing_signed_in_or_persisted() {
    let store = FakeStore::default();
    *store.fail_writes.borrow_mut() = true;
    let mut authority = SessionAuthority::new(store.clone());
    assert!(matches!(
        authority.sign_in(principal("alice"), "token-1".into()),
        Err(SessionError::Store(SecretError::Locked))
    ));
    assert!(matches!(authority.lease(), Err(SessionError::NotSignedIn)));
    assert!(store.values.borrow().is_empty());
}

#[test]
fn oversized_tokens_are_refused_before_touching_the_store() {
    let store = FakeStore::default();
    let mut authority = SessionAuthority::new(store.clone());
    assert!(matches!(
        authority.sign_in(principal("alice"), "t".repeat(3000)),
        Err(SessionError::TokenTooLarge)
    ));
    assert!(store.values.borrow().is_empty());
}

#[test]
fn sign_out_clears_memory_even_when_the_store_cannot_delete() {
    let store = FakeStore::default();
    let mut authority = signed_in(&store);
    let lease = authority.lease().unwrap();
    *store.fail_deletes.borrow_mut() = true;
    assert!(matches!(
        authority.sign_out(),
        Err(SessionError::Store(SecretError::Backend))
    ));
    assert!(matches!(authority.lease(), Err(SessionError::NotSignedIn)));
    assert!(matches!(
        authority.token(&lease),
        Err(SessionError::StaleLease)
    ));
}

#[test]
fn principal_scope_excludes_the_destination() {
    let alice = principal("alice");
    let team = DestinationKey::new("team").unwrap();
    let other = DestinationKey::new("other-team").unwrap();
    assert_ne!(team, other);
    assert_eq!(alice.scope(), principal("alice").scope());
    assert_ne!(alice.scope(), principal("bob").scope());
    assert!(DestinationKey::new("").is_err() && DestinationKey::new(&"t".repeat(81)).is_err());
}

#[test]
fn principals_are_validated() {
    let endpoint = ContextEndpoint::parse("https://api.example.test").unwrap();
    assert!(PrincipalKey::new(&endpoint, "", "alice", 1).is_err());
    assert!(PrincipalKey::new(&endpoint, "org", "", 1).is_err());
    assert!(PrincipalKey::new(&endpoint, "org", "al\nice", 1).is_err());
    assert!(PrincipalKey::new(&endpoint, &"o".repeat(129), "alice", 1).is_err());
    assert!(PrincipalKey::new(&endpoint, "org", "alice", 0).is_err());
    let other = ContextEndpoint::parse("https://other.example.test").unwrap();
    assert_ne!(
        PrincipalKey::new(&endpoint, "org", "alice", 1)
            .unwrap()
            .scope(),
        PrincipalKey::new(&other, "org", "alice", 1)
            .unwrap()
            .scope()
    );
}

#[test]
fn a_lease_from_one_authority_cannot_read_another_authoritys_token() {
    let first = signed_in(&FakeStore::default());
    let second = signed_in(&FakeStore::default());
    let lease = first.lease().unwrap();
    assert!(matches!(
        second.token(&lease),
        Err(SessionError::StaleLease)
    ));
}

#[test]
fn rebuilding_the_authority_never_revives_an_old_lease() {
    let store = FakeStore::default();
    let first = signed_in(&store);
    let old = first.lease().unwrap();
    drop(first);
    let mut rebuilt = SessionAuthority::new(store.clone());
    let ticket = rebuilt.restore().unwrap();
    rebuilt.confirm(ticket, &principal("alice")).unwrap();
    assert!(matches!(rebuilt.token(&old), Err(SessionError::StaleLease)));
}

#[test]
fn late_online_results_from_an_older_generation_or_epoch_are_ignored() {
    let store = FakeStore::default();
    let mut authority = signed_in(&store);
    let before_switch = authority.lease().unwrap();
    authority.sign_out().unwrap();
    authority
        .sign_in(principal("bob"), "token-b".into())
        .unwrap();
    // A refresh result for alice's old session must not sign bob out.
    assert!(matches!(
        authority.refresh(&before_switch, &principal("alice"), "token-a2".into()),
        Err(SessionError::StaleLease)
    ));
    assert!(authority.lease().is_ok());
    // Two refreshes for bob returning out of order: the older one is refused.
    let lease = authority.lease().unwrap();
    authority
        .refresh(&lease, &principal("bob"), "token-b2".into())
        .unwrap();
    assert!(matches!(
        authority.refresh(&lease, &principal("bob"), "token-b-old".into()),
        Err(SessionError::StaleLease)
    ));
    let current = authority.lease().unwrap();
    assert_eq!(
        authority.token(&current).unwrap().expose_for_request(),
        "token-b2"
    );
}

#[test]
fn a_failed_sign_in_never_leaves_the_previous_identity_restorable() {
    let store = FakeStore::default();
    drop(signed_in(&store));
    let mut authority = SessionAuthority::new(store.clone());
    *store.fail_writes.borrow_mut() = true;
    assert!(authority
        .sign_in(principal("bob"), "token-b".into())
        .is_err());
    *store.fail_writes.borrow_mut() = false;
    let mut later = SessionAuthority::new(store.clone());
    assert!(matches!(
        later.restore(),
        Ok(_) | Err(SessionError::CorruptBinding)
    ));
    assert_eq!(
        later.queue_scope(),
        None,
        "alice must not be restored after a failed switch"
    );
}

#[test]
fn a_restore_ticket_is_spent_by_the_first_confirmation() {
    let store = FakeStore::default();
    drop(signed_in(&store));
    let mut restored = SessionAuthority::new(store.clone());
    let ticket = restored.restore().unwrap();
    restored.confirm(ticket, &principal("alice")).unwrap();
    // A late, different confirmation for the same ticket must not sign alice out.
    assert!(matches!(
        restored.confirm(ticket, &principal("mallory")),
        Err(SessionError::StaleLease)
    ));
    assert!(restored.lease().is_ok());
}

#[test]
fn when_the_store_cannot_delete_the_old_binding_sign_in_fails_and_memory_is_signed_out() {
    let store = FakeStore::default();
    drop(signed_in(&store));
    let mut authority = SessionAuthority::new(store.clone());
    *store.fail_deletes.borrow_mut() = true;
    assert!(matches!(
        authority.sign_in(principal("bob"), "token-b".into()),
        Err(SessionError::Store(SecretError::Backend))
    ));
    assert!(matches!(authority.lease(), Err(SessionError::NotSignedIn)));
    // The old binding could not be removed; it can only come back as
    // Unverified and must still be confirmed online before any dispatch.
    *store.fail_deletes.borrow_mut() = false;
    let mut later = SessionAuthority::new(store.clone());
    let ticket = later.restore().unwrap();
    assert!(matches!(later.lease(), Err(SessionError::Unverified)));
    assert!(matches!(
        later.confirm(ticket, &principal("bob")),
        Err(SessionError::IdentityChanged)
    ));
}

#[test]
fn the_refresh_token_is_stored_beside_the_session_and_cleared_with_it() {
    let store = FakeStore::default();
    let mut authority = signed_in(&store);
    assert_eq!(authority.refresh_token().unwrap(), None);
    authority.set_refresh_token(Some("vox_refresh_1")).unwrap();
    assert_eq!(
        store
            .values
            .borrow()
            .get(REFRESH_ACCOUNT)
            .map(String::as_str),
        Some("vox_refresh_1")
    );
    assert_eq!(
        authority.refresh_token().unwrap().as_deref(),
        Some("vox_refresh_1")
    );
    authority.set_refresh_token(None).unwrap();
    assert!(!store.values.borrow().contains_key(REFRESH_ACCOUNT));
    authority.set_refresh_token(Some("vox_refresh_2")).unwrap();
    authority.sign_out().unwrap();
    assert!(
        store.values.borrow().is_empty(),
        "sign-out clears the refresh token too"
    );
}

#[test]
fn a_new_sign_in_removes_a_stale_refresh_token_first() {
    let store = FakeStore::default();
    store
        .values
        .borrow_mut()
        .insert(REFRESH_ACCOUNT.into(), "vox_refresh_old".into());
    let authority = signed_in(&store);
    assert_eq!(authority.refresh_token().unwrap(), None);

    // When the stale entry cannot be removed, nothing is signed in.
    let store = FakeStore::default();
    store
        .values
        .borrow_mut()
        .insert(REFRESH_ACCOUNT.into(), "vox_refresh_old".into());
    *store.fail_deletes.borrow_mut() = true;
    let mut authority = SessionAuthority::new(store.clone());
    assert!(matches!(
        authority.sign_in(principal("alice"), "token-1".into()),
        Err(SessionError::Store(_))
    ));
    assert!(matches!(authority.lease(), Err(SessionError::NotSignedIn)));
}

#[test]
fn an_oversized_refresh_token_is_refused() {
    let store = FakeStore::default();
    let mut authority = signed_in(&store);
    let huge = "r".repeat(100_000);
    assert!(matches!(
        authority.set_refresh_token(Some(&huge)),
        Err(SessionError::TokenTooLarge)
    ));
    assert!(!store.values.borrow().contains_key(REFRESH_ACCOUNT));
}
