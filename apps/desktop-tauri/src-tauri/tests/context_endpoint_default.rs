//! Spec §3.6 / T49p: installed builds know the production Context endpoint;
//! a development override wins; an invalid override disables sign-in.
use std::env::VarError;
use vox_agent_desktop_tauri_lib::{
    context_endpoint_from_env, context_endpoint_from_var, PRODUCTION_CONTEXT_ENDPOINT,
};

#[test]
fn env_override_wins_and_default_is_production() {
    // `canonical()` always ends with a slash.
    let dev = context_endpoint_from_env(Some("http://127.0.0.1:18900")).unwrap();
    assert_eq!(dev.canonical(), "http://127.0.0.1:18900/");
    let prod = context_endpoint_from_env(None).unwrap();
    assert_eq!(prod.canonical(), "https://api.osbornvox.com/");
    assert_eq!(PRODUCTION_CONTEXT_ENDPOINT, "https://api.osbornvox.com");
}

#[test]
fn an_invalid_override_disables_sign_in_instead_of_falling_back() {
    for bad in [
        "ftp://nope",
        "http://api.osbornvox.com",
        "https://api.osbornvox.com/v1",
        "https://user:pw@api.osbornvox.com",
        "",
        "   ",
        "not a url",
    ] {
        assert!(context_endpoint_from_env(Some(bad)).is_none(), "{bad:?}");
    }
}

#[test]
fn a_non_unicode_override_is_invalid_not_absent() {
    assert_eq!(
        context_endpoint_from_var(Err(VarError::NotPresent))
            .unwrap()
            .canonical(),
        "https://api.osbornvox.com/"
    );
    assert_eq!(
        context_endpoint_from_var(Ok("http://127.0.0.1:18900".into()))
            .unwrap()
            .canonical(),
        "http://127.0.0.1:18900/"
    );
    assert!(context_endpoint_from_var(Err(VarError::NotUnicode("\u{fffd}".into()))).is_none());
    assert!(context_endpoint_from_var(Ok("ftp://nope".into())).is_none());
}
