//! T06a: the OS credential store must be a real cross-process store, not the
//! keyring crate's in-process mock. A child process (this same test binary,
//! filtered to `child_probe`) must read back a value the parent wrote.
#![cfg(any(target_os = "macos", target_os = "windows"))]

use std::process::Command;
use vox_agent_desktop_tauri_lib::secrets::{NativeSecretStore, SecretStore};

const TEST_SERVICE: &str = "com.osborn.voxagent.desktop.test";
const CHILD_ENV: &str = "VOX_CREDENTIAL_CHILD_ACCOUNT";

/// Removes the synthetic credential even if the test panics after writing it.
struct Cleanup<'a> {
    store: &'a NativeSecretStore,
    account: &'a str,
}

impl Drop for Cleanup<'_> {
    fn drop(&mut self) {
        let _ = self.store.delete(self.account);
    }
}

#[test]
fn child_probe() {
    // Acts only when spawned by the parent test with `--exact child_probe`.
    let Ok(account) = std::env::var(CHILD_ENV) else {
        return;
    };
    let store = NativeSecretStore::with_service(TEST_SERVICE);
    // Leading newline: libtest prints "test child_probe ... " without a line
    // break, and the parent requires the marker to be a complete line.
    match store.get(&account) {
        Ok(Some(value)) => println!("\nCHILD_VALUE={value}"),
        Ok(None) => println!("\nCHILD_VALUE=<none>"),
        Err(error) => println!("\nCHILD_ERROR={error}"),
    }
}

#[test]
fn cross_process_readback_uses_a_real_os_credential_store() {
    // Never short-circuit: an inherited CHILD_ENV must not turn this into a vacuous pass.
    assert!(
        std::env::var_os(CHILD_ENV).is_none(),
        "{CHILD_ENV} is set in the parent environment; refusing to run"
    );
    assert!(
        std::env::var_os("VOX_SKIP_NATIVE_CREDENTIAL_TEST").is_none(),
        "VOX_SKIP_NATIVE_CREDENTIAL_TEST set: refusing to report a native credential PASS"
    );
    let account = format!(
        "crossprocess-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let synthetic = format!("synthetic-{account}");
    let store = NativeSecretStore::with_service(TEST_SERVICE);
    store.write(&account, &synthetic).expect("parent write");
    let cleanup = Cleanup {
        store: &store,
        account: &account,
    };

    let output = Command::new(std::env::current_exe().expect("test binary path"))
        .args(["--exact", "child_probe", "--nocapture", "--test-threads=1"])
        .env(CHILD_ENV, &account)
        .output()
        .expect("spawn child");
    let stdout = String::from_utf8_lossy(&output.stdout);

    assert!(
        output.status.success(),
        "child process failed: {:?}; stdout: {stdout}",
        output.status
    );
    let expected = format!("CHILD_VALUE={synthetic}");
    assert!(
        stdout.lines().any(|line| line.trim() == expected),
        "child did not read the parent's credential; stdout: {stdout}"
    );

    drop(cleanup);
    assert_eq!(
        store.read(&account).expect("post-cleanup read"),
        None,
        "cleanup must remove the test credential"
    );
}
