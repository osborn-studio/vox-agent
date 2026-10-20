# PR-A: Windows send path (DPAPI journal, owner-only ACL, endpoint default, session renewal)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On Windows, `context_submit_record` stops returning `journal-unavailable`: the Context service gets a real encrypted journal whose keys are wrapped by DPAPI and whose directory has an owner-only ACL; installed builds know the production Context endpoint; the Feishu session survives the one-hour access token through `/me` and `renew`.

**Architecture:** A `DpapiKeyProvider` implements the existing `KeyProvider` trait (`context_keys/mod.rs`) with `CryptProtectData`/`CryptUnprotectData` (user scope, context bytes as entropy). A new `context_acl` module creates the Context directories with a protected DACL (current user + SYSTEM, full access) and verifies it on every open; an unsafe ACL leaves the journal `None`, which keeps delivery blocked by design. `context_commands::context_sign_in` opens the journal through `open_journal()` and passes it to `AccountSlot::publish`. `lib.rs` defaults the endpoint to `https://api.osbornvox.com` when the environment variable is absent. A renewal task stores the refresh token in the credential store and applies `SessionAuthority::refresh` before expiry.

**Tech Stack:** Rust 2021, `windows-sys 0.61` (features `Win32_Foundation`, `Win32_Security_Cryptography`, `Win32_Security_Authorization`, `Win32_Security`, `Win32_System_Memory`, `Win32_Storage_FileSystem`), `zeroize`, existing `KeyRing`/`Journal`/`FileAnchorStore`, `tokio`, `reqwest`, `keyring` (Credential Manager). All Windows code behind `#[cfg(windows)]`; macOS/Linux keep `None`.

Compile and test on `windows-latest` CI (`windows-tauri-test` job runs `cargo fmt --check` and `cargo test --locked`). On this Mac only `cargo check` of the non-Windows paths is possible; push early and let CI compile the Windows paths.

---

### Task 1: Cargo features for DPAPI and ACL APIs

**Files:**
- Modify: `apps/desktop-tauri/src-tauri/Cargo.toml:49-55`

- [ ] **Step 1: Extend the Windows-only `windows-sys` features**

Replace the `windows-sys` line with:

```toml
windows-sys = { version = "0.61.2", features = [
    "Win32_UI_Input_KeyboardAndMouse",
    "Win32_Foundation",
    "Win32_Security_Cryptography",
    "Win32_Security_Authorization",
    "Win32_Security",
    "Win32_System_Memory",
    "Win32_System_Threading",
    "Win32_Storage_FileSystem",
] }
```

- [ ] **Step 2: Keep the lockfile valid**

Run: `cargo metadata --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked --format-version 1 > /dev/null && echo lock-ok`
Expected: `lock-ok` (features do not change the lockfile; if Cargo wants to update it, stop and check that no new crate was pulled in).

- [ ] **Step 3: Commit**

```bash
git add apps/desktop-tauri/src-tauri/Cargo.toml
git commit -m "Tauri: enable windows-sys crypto, security and file-system APIs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2: `DpapiKeyProvider`

**Files:**
- Create: `apps/desktop-tauri/src-tauri/src/context_keys/dpapi.rs`
- Modify: `apps/desktop-tauri/src-tauri/src/context_keys/mod.rs` (module declaration next to `pub mod fake;`)
- Test: `apps/desktop-tauri/src-tauri/tests/context_dpapi.rs`

- [ ] **Step 1: Write the failing Windows-only test**

```rust
// tests/context_dpapi.rs
//! T07b: the DPAPI provider wraps a key for the current user only and binds the
//! wrapping to the caller's context bytes.
#![cfg(windows)]

use vox_agent_desktop_tauri_lib::context_keys::dpapi::DpapiKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::{KeyError, KeyProvider, KeyRing, ProviderKind};

#[test]
fn wrap_unwrap_round_trip_and_context_binding() {
    let provider = DpapiKeyProvider::new();
    assert_eq!(provider.kind(), ProviderKind::DpapiUser);
    let key = [0x5a_u8; 32];
    let wrapped = provider.wrap(b"ctx-a", &key).unwrap();
    assert_ne!(&wrapped[..], &key[..]);
    assert_eq!(&provider.unwrap(b"ctx-a", &wrapped).unwrap()[..], &key[..]);
    assert_eq!(provider.unwrap(b"ctx-b", &wrapped).unwrap_err(), KeyError::Unrecoverable);
}

#[test]
fn tampered_blob_is_unrecoverable_not_a_panic() {
    let provider = DpapiKeyProvider::new();
    let mut wrapped = provider.wrap(b"ctx", &[1_u8; 32]).unwrap();
    let last = wrapped.len() - 1;
    wrapped[last] ^= 0xff;
    assert_eq!(provider.unwrap(b"ctx", &wrapped).unwrap_err(), KeyError::Unrecoverable);
    assert_eq!(provider.unwrap(b"ctx", b"").unwrap_err(), KeyError::Unrecoverable);
}

#[test]
fn key_ring_creates_and_reopens_a_dpapi_key_set() {
    let dir = tempfile::tempdir().unwrap();
    let ring = KeyRing::new(dir.path(), DpapiKeyProvider::new());
    let created = ring.create().unwrap();
    let reopened = ring.open(created.id()).unwrap();
    let aad = vox_agent_desktop_tauri_lib::context_keys::Aad::new_for_test();
    let sealed = created.seal(&aad, b"record").unwrap();
    assert_eq!(&reopened.open(&aad, &sealed).unwrap()[..], b"record");
    assert!(ring.key_path(created.id()).to_string_lossy().ends_with(".dpapi"));
}
```

`Aad::new_for_test()` is a name used here for whatever constructor `Aad` has; read `context_keys/mod.rs:126-135` (`impl Aad { pub fn new(...) }`) and call that real constructor with simple arguments instead. Do not add a test-only constructor to production code.

- [ ] **Step 2: Confirm it fails to compile on CI** (push after Task 2 Step 5; locally `cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked --test context_dpapi` on macOS compiles to an empty test binary because of `#![cfg(windows)]`, which is expected).

- [ ] **Step 3: Implement the provider**

```rust
// src/context_keys/dpapi.rs
//! Windows DPAPI key provider (T07b): CryptProtectData/CryptUnprotectData in
//! current-user scope. The caller's `context` bytes are the optional entropy,
//! so a blob wrapped for one key role cannot be unwrapped for another, and a
//! blob from another Windows user or machine fails as `Unrecoverable`.
#![cfg(windows)]

use super::{KeyError, KeyProvider, ProviderKind};
use windows_sys::Win32::Foundation::LocalFree;
use windows_sys::Win32::Security::Cryptography::{
    CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
};
use zeroize::Zeroizing;

const DESCRIPTION: &[u16] = &['v' as u16, 'o' as u16, 'x' as u16, '-' as u16, 'c' as u16, 't' as u16, 'x' as u16, 0];

#[derive(Debug, Default, Clone, Copy)]
pub struct DpapiKeyProvider;

impl DpapiKeyProvider {
    pub fn new() -> Self {
        Self
    }
}

fn blob(bytes: &[u8]) -> CRYPT_INTEGER_BLOB {
    CRYPT_INTEGER_BLOB {
        cbData: u32::try_from(bytes.len()).unwrap_or(u32::MAX),
        pbData: bytes.as_ptr() as *mut u8,
    }
}

/// Copies the output blob into owned memory and frees the LocalAlloc buffer.
unsafe fn take(out: CRYPT_INTEGER_BLOB) -> Vec<u8> {
    let copy = std::slice::from_raw_parts(out.pbData, out.cbData as usize).to_vec();
    LocalFree(out.pbData as _);
    copy
}

impl KeyProvider for DpapiKeyProvider {
    fn kind(&self) -> ProviderKind {
        ProviderKind::DpapiUser
    }

    fn wrap(&self, context: &[u8], key: &[u8]) -> Result<Vec<u8>, KeyError> {
        if key.is_empty() || key.len() > 4096 || context.len() > 4096 {
            return Err(KeyError::InvalidInput);
        }
        let input = blob(key);
        let entropy = blob(context);
        let mut out = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
        // SAFETY: all pointers reference live stack/slice memory for the call;
        // the output buffer is copied and freed in `take`.
        let ok = unsafe {
            CryptProtectData(
                &input,
                DESCRIPTION.as_ptr(),
                &entropy,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut out,
            )
        };
        if ok == 0 || out.pbData.is_null() {
            return Err(KeyError::Unavailable);
        }
        Ok(unsafe { take(out) })
    }

    fn unwrap(&self, context: &[u8], wrapped: &[u8]) -> Result<Zeroizing<Vec<u8>>, KeyError> {
        if wrapped.len() < 16 || wrapped.len() > 1 << 16 || context.len() > 4096 {
            return Err(KeyError::Unrecoverable);
        }
        let input = blob(wrapped);
        let entropy = blob(context);
        let mut out = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
        // SAFETY: as in `wrap`.
        let ok = unsafe {
            CryptUnprotectData(
                &input,
                std::ptr::null_mut(),
                &entropy,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut out,
            )
        };
        if ok == 0 || out.pbData.is_null() {
            // Wrong user, wrong machine, wrong entropy or tampering all end here.
            return Err(KeyError::Unrecoverable);
        }
        Ok(Zeroizing::new(unsafe { take(out) }))
    }
}
```

In `context_keys/mod.rs`, next to `pub mod fake;` add:

```rust
#[cfg(windows)]
pub mod dpapi;
```

If the `windows-sys` 0.61 signatures differ (`CryptProtectData` takes `*const CRYPT_INTEGER_BLOB` and `*mut CRYPT_INTEGER_BLOB`; the description is `PCWSTR`), adapt the casts to the generated signature; the control flow and error mapping stay as written.

- [ ] **Step 4: Compile what macOS can**

Run: `cargo check --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked`
Expected: OK (the module is `cfg(windows)`, so this only proves the rest still compiles).

- [ ] **Step 5: Commit and push to get the Windows compile**

```bash
git add apps/desktop-tauri/src-tauri/src/context_keys/dpapi.rs apps/desktop-tauri/src-tauri/src/context_keys/mod.rs apps/desktop-tauri/src-tauri/tests/context_dpapi.rs
git commit -m "Context keys: DPAPI user-scope key provider for Windows

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin HEAD
```

Open a draft PR now (`gh pr create --draft --title "PR-A: Windows send path (DPAPI journal, ACL, endpoint, renew)" --body "Draft; see docs/superpowers/plans/2026-10-20-win-pra-send-path.md"`), then watch `windows-tauri-test`: `gh pr checks --watch`. Fix compile errors in `dpapi.rs` until `context_dpapi` passes on Windows before continuing.

### Task 3: Owner-only ACL module

**Files:**
- Create: `apps/desktop-tauri/src-tauri/src/context_acl.rs`
- Modify: `apps/desktop-tauri/src-tauri/src/lib.rs` (add `pub mod context_acl;` where the other `context_*` modules are declared)
- Test: `apps/desktop-tauri/src-tauri/tests/context_acl.rs`

- [ ] **Step 1: Write the failing tests**

```rust
// tests/context_acl.rs
#![cfg(windows)]
use std::process::Command;
use vox_agent_desktop_tauri_lib::context_acl::{ensure_private_dir, verify_private_dir, AclError};

#[test]
fn creates_directory_readable_only_by_owner_and_system() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("context");
    ensure_private_dir(&dir).unwrap();
    assert!(dir.is_dir());
    verify_private_dir(&dir).unwrap();
    // icacls must not list Users or Everyone.
    let out = Command::new("icacls").arg(&dir).output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout).to_lowercase();
    assert!(!text.contains("builtin\\users") && !text.contains("everyone"), "{text}");
}

#[test]
fn rejects_a_directory_granted_to_everyone() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("open");
    std::fs::create_dir(&dir).unwrap();
    let status = Command::new("icacls").arg(&dir).args(["/grant", "Everyone:(OI)(CI)R"]).status().unwrap();
    assert!(status.success());
    assert_eq!(verify_private_dir(&dir).unwrap_err(), AclError::TooPermissive);
}

#[test]
fn ensure_is_idempotent_and_repairs_inherited_aces() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("ctx");
    ensure_private_dir(&dir).unwrap();
    ensure_private_dir(&dir).unwrap();
    verify_private_dir(&dir).unwrap();
}
```

- [ ] **Step 2: Implement**

```rust
// src/context_acl.rs
//! Owner-only directories for the Context journal on Windows (spec §3.5).
//!
//! `ensure_private_dir` creates (or re-secures) a directory with a protected
//! DACL that grants full access only to the current user and SYSTEM, with no
//! inheritance from the parent. `verify_private_dir` re-reads the DACL and
//! fails on any other allow ACE, so a directory someone widened later is not
//! used. Both are no-ops that return `Ok(())` on non-Windows targets because
//! those builds never open a journal (see `context_commands::open_journal`).
use std::path::Path;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AclError {
    /// Could not read the current user's SID or apply the descriptor.
    Io,
    /// An allow ACE for a principal other than the owner or SYSTEM exists.
    TooPermissive,
}

#[cfg(not(windows))]
pub fn ensure_private_dir(dir: &Path) -> Result<(), AclError> {
    std::fs::create_dir_all(dir).map_err(|_| AclError::Io)
}

#[cfg(not(windows))]
pub fn verify_private_dir(_dir: &Path) -> Result<(), AclError> {
    Ok(())
}

#[cfg(windows)]
mod win {
    use super::AclError;
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;
    use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, HANDLE};
    use windows_sys::Win32::Security::Authorization::{
        ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
        GetNamedSecurityInfoW, SetNamedSecurityInfoW, SE_FILE_OBJECT, SDDL_REVISION_1,
    };
    use windows_sys::Win32::Security::{
        GetAce, GetSecurityDescriptorDacl, GetTokenInformation, IsValidSid, TokenUser, ACCESS_ALLOWED_ACE,
        ACE_HEADER, ACL, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, TOKEN_QUERY,
        TOKEN_USER, ACCESS_ALLOWED_ACE_TYPE,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

    const SYSTEM_SID: &str = "S-1-5-18";

    fn wide(s: &OsStr) -> Vec<u16> {
        s.encode_wide().chain(std::iter::once(0)).collect()
    }

    fn sid_to_string(sid: *mut core::ffi::c_void) -> Result<String, AclError> {
        let mut out: *mut u16 = std::ptr::null_mut();
        // SAFETY: `sid` is a valid SID pointer from the token or an ACE.
        if unsafe { IsValidSid(sid) } == 0 || unsafe { ConvertSidToStringSidW(sid, &mut out) } == 0 {
            return Err(AclError::Io);
        }
        let mut len = 0;
        while unsafe { *out.add(len) } != 0 {
            len += 1;
        }
        let text = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(out, len) });
        unsafe { LocalFree(out as _) };
        Ok(text)
    }

    /// String SID of the user running this process.
    pub fn current_user_sid() -> Result<String, AclError> {
        let mut token: HANDLE = std::ptr::null_mut();
        // SAFETY: standard token query on the current process.
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(AclError::Io);
        }
        let mut needed = 0u32;
        unsafe { GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut needed) };
        let mut buffer = vec![0u8; needed as usize];
        let ok = unsafe {
            GetTokenInformation(token, TokenUser, buffer.as_mut_ptr() as _, needed, &mut needed)
        };
        unsafe { CloseHandle(token) };
        if ok == 0 {
            return Err(AclError::Io);
        }
        let user = unsafe { &*(buffer.as_ptr() as *const TOKEN_USER) };
        sid_to_string(user.User.Sid)
    }

    pub fn ensure_private_dir(dir: &Path) -> Result<(), AclError> {
        std::fs::create_dir_all(dir).map_err(|_| AclError::Io)?;
        let user = current_user_sid()?;
        // Protected DACL (no inheritance), full access for the owner and SYSTEM,
        // inherited by children (OICI).
        let sddl = format!("D:P(A;OICI;FA;;;{SYSTEM_SID})(A;OICI;FA;;;{user})");
        let sddl_w = wide(OsStr::new(&sddl));
        let mut descriptor: *mut core::ffi::c_void = std::ptr::null_mut();
        // SAFETY: SDDL string is NUL-terminated; descriptor is LocalAlloc'd on success.
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl_w.as_ptr(), SDDL_REVISION_1, &mut descriptor, std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(AclError::Io);
        }
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut present = 0i32;
        let mut defaulted = 0i32;
        let ok = unsafe { GetSecurityDescriptorDacl(descriptor, &mut present, &mut dacl, &mut defaulted) };
        if ok == 0 || present == 0 || dacl.is_null() {
            unsafe { LocalFree(descriptor as _) };
            return Err(AclError::Io);
        }
        let path_w = wide(dir.as_os_str());
        let status = unsafe {
            SetNamedSecurityInfoW(
                path_w.as_ptr(), SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(), std::ptr::null_mut(), dacl, std::ptr::null_mut(),
            )
        };
        unsafe { LocalFree(descriptor as _) };
        if status != 0 {
            return Err(AclError::Io);
        }
        verify_private_dir(dir)
    }

    pub fn verify_private_dir(dir: &Path) -> Result<(), AclError> {
        let user = current_user_sid()?;
        let path_w = wide(dir.as_os_str());
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut descriptor: *mut core::ffi::c_void = std::ptr::null_mut();
        // SAFETY: out-params are filled on success; descriptor freed below.
        let status = unsafe {
            GetNamedSecurityInfoW(
                path_w.as_ptr(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(), std::ptr::null_mut(), &mut dacl, std::ptr::null_mut(), &mut descriptor,
            )
        };
        if status != 0 || descriptor.is_null() {
            return Err(AclError::Io);
        }
        let result = (|| {
            if dacl.is_null() {
                // A NULL DACL grants everyone full access.
                return Err(AclError::TooPermissive);
            }
            let count = unsafe { (*dacl).AceCount } as u32;
            for index in 0..count {
                let mut ace: *mut core::ffi::c_void = std::ptr::null_mut();
                if unsafe { GetAce(dacl, index, &mut ace) } == 0 {
                    return Err(AclError::Io);
                }
                let header = unsafe { &*(ace as *const ACE_HEADER) };
                if u32::from(header.AceType) != ACCESS_ALLOWED_ACE_TYPE {
                    continue; // deny ACEs only narrow access
                }
                let allowed = unsafe { &*(ace as *const ACCESS_ALLOWED_ACE) };
                let sid = sid_to_string(&allowed.SidStart as *const u32 as *mut core::ffi::c_void)?;
                if sid != user && sid != SYSTEM_SID {
                    return Err(AclError::TooPermissive);
                }
            }
            Ok(())
        })();
        unsafe { LocalFree(descriptor as _) };
        result
    }
}

#[cfg(windows)]
pub use win::{ensure_private_dir, verify_private_dir};
```

- [ ] **Step 3: Push and make the Windows tests pass**

```bash
cargo check --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked
git add apps/desktop-tauri/src-tauri/src/context_acl.rs apps/desktop-tauri/src-tauri/src/lib.rs apps/desktop-tauri/src-tauri/tests/context_acl.rs
git commit -m "Context: owner-only directory ACLs on Windows

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push
gh pr checks --watch
```

Expected on CI: `context_acl` tests pass. Typical fixes if not: `ACCESS_ALLOWED_ACE_TYPE` is a `u8` constant in some generations (compare without `u32::from`); `SidStart` is a `u32` field whose address is the SID start (as written); `SetNamedSecurityInfoW` takes `*const u16` for the path (as written, since `wide()` returns a `Vec<u16>`).

### Task 4: Open the journal on Windows and pass it to the service

**Files:**
- Modify: `apps/desktop-tauri/src-tauri/src/context_commands.rs` (`context_sign_in`, `diagnostics`)
- Modify: `apps/desktop-tauri/src-tauri/src/context_account.rs:393-425` (`publish` signature)
- Test: `apps/desktop-tauri/src-tauri/tests/context_windows_journal.rs`

- [ ] **Step 1: Write the failing tests**

```rust
// tests/context_windows_journal.rs
//! On Windows a signed-in account owns a DPAPI-backed journal; elsewhere it stays None.
use vox_agent_desktop_tauri_lib::context_commands::{open_journal, JournalOpenReport};

#[test]
fn open_journal_reports_platform_support() {
    let root = tempfile::tempdir().unwrap();
    let report = open_journal(root.path());
    if cfg!(windows) {
        assert!(matches!(report, JournalOpenReport::Opened(_)), "{report:?}");
        assert!(root.path().join("context").join("journal").is_dir());
        assert!(root.path().join("context").join("keys").is_dir());
    } else {
        assert!(matches!(report, JournalOpenReport::NotSupported));
    }
}

#[cfg(windows)]
#[test]
fn open_journal_refuses_a_widened_directory() {
    let root = tempfile::tempdir().unwrap();
    let dir = root.path().join("context");
    std::fs::create_dir_all(&dir).unwrap();
    let status = std::process::Command::new("icacls").arg(&dir).args(["/grant", "Everyone:(OI)(CI)R"]).status().unwrap();
    assert!(status.success());
    assert!(matches!(open_journal(root.path()), JournalOpenReport::UnsafeAcl));
}
```

- [ ] **Step 2: Implement `open_journal` and the report type** in `context_commands.rs`

```rust
use crate::context_acl::{ensure_private_dir, verify_private_dir, AclError};
use crate::context_journal::{FileAnchorStore, Journal};
use crate::context_keys::KeyRing;

/// Why a build has (or lacks) an encrypted journal. Labels only.
#[derive(Debug)]
pub enum JournalOpenReport {
    Opened(Journal),
    NotSupported,
    UnsafeAcl,
    Busy,
    Failed,
}

impl JournalOpenReport {
    pub fn key_store_label(&self) -> &'static str {
        match self {
            Self::Opened(_) => "dpapi-user",
            Self::NotSupported => "not-available-in-this-build",
            Self::UnsafeAcl => "unsafe-acl",
            Self::Busy | Self::Failed => "unavailable",
        }
    }
}

/// `<data_dir>/context/{keys,journal,anchors}`; the anchor store is outside the
/// journal directory as `Journal::open` requires.
pub fn open_journal(data_dir: &std::path::Path) -> JournalOpenReport {
    #[cfg(not(windows))]
    {
        let _ = data_dir;
        JournalOpenReport::NotSupported
    }
    #[cfg(windows)]
    {
        use crate::context_journal::JournalError;
        use crate::context_keys::dpapi::DpapiKeyProvider;
        let root = data_dir.join("context");
        for dir in [&root, &root.join("keys"), &root.join("journal"), &root.join("anchors")] {
            match ensure_private_dir(dir).and_then(|_| verify_private_dir(dir)) {
                Ok(()) => {}
                Err(AclError::TooPermissive) => return JournalOpenReport::UnsafeAcl,
                Err(AclError::Io) => return JournalOpenReport::Failed,
            }
        }
        let ring = KeyRing::new(&root.join("keys"), DpapiKeyProvider::new());
        match Journal::open(&root.join("journal"), &ring, Box::new(FileAnchorStore::new(&root.join("anchors")))) {
            Ok(opened) => JournalOpenReport::Opened(opened.into_journal()),
            Err(JournalError::Busy) => JournalOpenReport::Busy,
            Err(_) => JournalOpenReport::Failed,
        }
    }
}
```

Check the exact names: `FileAnchorStore` lives where `tests/context_record_e2e.rs` imports it from (`grep -n "FileAnchorStore" apps/desktop-tauri/src-tauri/tests/context_record_e2e.rs | head -2`); `Opened::into_journal()` exists (used by that test). If `Journal::open` returns a different error variant for "already locked", map that one to `Busy`.

- [ ] **Step 3: Thread the journal through sign-in**

In `context_account.rs`, change `publish` to take the journal:

```rust
    pub fn publish(
        &self,
        ticket: &SignInTicket<'_>,
        endpoint: ContextEndpoint,
        signed: SignedIn,
        store: S,
        journal: Option<Journal>,
    ) -> Result<Option<String>, AccountError> {
```

and pass `journal` instead of `None` into `ContextService::new(...)`. Update every caller (`grep -rn "\.publish(" apps/desktop-tauri/src-tauri/src apps/desktop-tauri/src-tauri/tests`): tests pass `None`.

In `context_commands::context_sign_in`, before `.publish(...)`:

```rust
    let report = open_journal(&crate::state::vox_agent_data_dir());
    state.set_journal_label(report.key_store_label());
    let journal = match report {
        JournalOpenReport::Opened(journal) => Some(journal),
        _ => None,
    };
```

and pass `journal` as the new last argument. Add to `ContextState` a `journal_label: Mutex<&'static str>` (default `"not-available-in-this-build"`) with `set_journal_label` / `journal_label()`; in `diagnostics(...)` replace the constant `key_store: "not-available-in-this-build"` with the state's label (change the function signature to take `&ContextState` or pass the label in; follow how `diagnostics` is called from its command).

- [ ] **Step 4: Update the JS expectation, if any**

Run: `grep -rn "not-available-in-this-build" apps/desktop-tauri/src apps/desktop-tauri/test`
Expected: either nothing, or a test asserting the diagnostics label. If a test exists, extend it to accept `dpapi-user`, `unsafe-acl`, `unavailable` and `not-available-in-this-build`, and surface `unsafe-acl` in `src/context/status-copy.js` as "本机加密目录权限不安全，已停用投递" / "Local encrypted directory permissions are unsafe; delivery disabled".

- [ ] **Step 5: Tests, fmt, push**

```bash
cargo fmt --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --all
cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked
npm test --workspace apps/desktop-tauri
git add -A apps/desktop-tauri/src-tauri/src apps/desktop-tauri/src-tauri/tests apps/desktop-tauri/src apps/desktop-tauri/test
git commit -m "Context: open a DPAPI-backed journal on Windows sign-in

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push && gh pr checks --watch
```

Expected: macOS `cargo test` passes with `NotSupported`; CI Windows passes `Opened`.

### Task 5: Production Context endpoint default

**Files:**
- Modify: `apps/desktop-tauri/src-tauri/src/lib.rs:109-121`
- Test: `apps/desktop-tauri/src-tauri/tests/context_endpoint_default.rs`

- [ ] **Step 1: Write the failing test**

```rust
// tests/context_endpoint_default.rs
use vox_agent_desktop_tauri_lib::context_endpoint_from_env;

#[test]
fn env_override_wins_and_default_is_production() {
    let dev = context_endpoint_from_env(Some("http://127.0.0.1:18900")).unwrap();
    assert_eq!(dev.canonical(), "http://127.0.0.1:18900");
    let prod = context_endpoint_from_env(None).unwrap();
    assert_eq!(prod.canonical(), "https://api.osbornvox.com");
    assert!(context_endpoint_from_env(Some("ftp://nope")).is_none() || context_endpoint_from_env(Some("ftp://nope")).unwrap().canonical() == "https://api.osbornvox.com");
}
```

Tighten the last assertion to whichever behaviour you implement: an invalid override must never silently become production; implement "invalid override → `None` (sign-in reports not-available)" and assert `is_none()`.

- [ ] **Step 2: Implement**

In `lib.rs` replace `context_state()` with:

```rust
pub const PRODUCTION_CONTEXT_ENDPOINT: &str = "https://api.osbornvox.com";

/// `VOX_AGENT_CONTEXT_ENDPOINT` overrides for development; an invalid override
/// disables sign-in instead of falling back to production. Without the
/// variable, installed builds use the production origin fixed by T49p.
pub fn context_endpoint_from_env(value: Option<&str>) -> Option<context_wire::ContextEndpoint> {
    match value {
        Some(raw) => context_wire::ContextEndpoint::parse(raw).ok(),
        None => context_wire::ContextEndpoint::parse(PRODUCTION_CONTEXT_ENDPOINT).ok(),
    }
}

fn context_state() -> context_commands::ContextState {
    let state = context_commands::ContextState::default();
    let raw = std::env::var("VOX_AGENT_CONTEXT_ENDPOINT").ok();
    if let Some(endpoint) = context_endpoint_from_env(raw.as_deref()) {
        state.set_endpoint(endpoint);
    }
    state
}
```

Check `ContextEndpoint::canonical()` is the accessor name used by tests (`grep -n "pub fn canonical" apps/desktop-tauri/src-tauri/src/context_wire.rs`).

- [ ] **Step 3: Test, commit**

```bash
cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked --test context_endpoint_default
git add apps/desktop-tauri/src-tauri/src/lib.rs apps/desktop-tauri/src-tauri/tests/context_endpoint_default.rs
git commit -m "Context: default to the production endpoint when no override is set

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 6: `/me` on startup and renewal before expiry

**Files:**
- Modify: `apps/desktop-tauri/src-tauri/src/context_login.rs` (`Redeemed`, `SignedIn`, new `renew`/`me` requests)
- Modify: `apps/desktop-tauri/src-tauri/src/context_session.rs` (store the refresh token)
- Modify: `apps/desktop-tauri/src-tauri/src/context_commands.rs` (maintenance task)
- Test: `apps/desktop-tauri/src-tauri/tests/context_renewal.rs`

Server contract (from `engine/feishu_login.py` and `engine/feishu_session_renewal.py`): `redeem` returns `token` (`vox_sso_…`), `refreshToken` (`vox_refresh_…`), `expiresAt`, `expiresIn` (3600), `refreshExpiresAt`, `profile`, `targetIds`. `POST /v1/auth/feishu/renew` with `Authorization: Bearer <refreshToken>` returns the same shape with a new access token; failures are `401 SESSION_EXPIRED` / `401 ACCOUNT_DISABLED` (sign out), `503 MEMBERSHIP_CHECK_UNAVAILABLE` and `429 RENEWAL_RATE_LIMIT` (retry later), `409 RENEWAL_IN_PROGRESS` (retry later). `GET /v1/auth/feishu/me` with the access token returns `expiresAt`, `expiresIn`.

- [ ] **Step 1: Write the failing tests with the mock server already used by `tests/context_login.rs`**

Read `tests/context_login.rs:1-60` for its loopback mock helper and reuse it. Cover:

```rust
#[tokio::test]
async fn redeem_keeps_refresh_token_and_expiry() { /* mock redeem returns refreshToken + expiresIn 3600; assert SignedIn.refresh_token == Some("vox_refresh_x…") and expires_in == 3600 */ }

#[tokio::test]
async fn renew_replaces_access_token_and_keeps_principal() { /* mock renew returns new token for the same profile; assert RenewOutcome::Renewed { token, expires_in } */ }

#[tokio::test]
async fn renew_401_signals_sign_out_and_503_signals_retry() { /* assert RenewOutcome::SignedOut("SESSION_EXPIRED") and RenewOutcome::RetryLater("MEMBERSHIP_CHECK_UNAVAILABLE") */ }

#[test]
fn renewal_is_due_five_minutes_before_expiry() {
    use vox_agent_desktop_tauri_lib::context_login::renewal_due;
    assert!(!renewal_due(3600, 0));
    assert!(renewal_due(3600, 3600 - 299));
    assert!(renewal_due(3600, 4000));
}
```

- [ ] **Step 2: Implement the wire pieces** in `context_login.rs`

```rust
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Redeemed {
    token: String,
    profile: Profile,
    #[serde(default)]
    target_ids: Vec<String>,
    #[serde(default)]
    refresh_token: Option<String>,
    #[serde(default)]
    expires_in: Option<u64>,
}
```

(keep whatever `serde` attribute the struct already has for `target_ids`; if it was snake_case on the wire, the server sends `targetIds`, so `rename_all = "camelCase"` is the correct shape; confirm against `tests/context_login.rs` fixtures).

Add to `SignedIn`: `pub refresh_token: Option<Zeroizing<String>>, pub expires_in: Option<u64>` and fill them in `sign_in` after validating `refresh_token.starts_with("vox_refresh_") && valid_token(...)` (otherwise `None`, which disables renewal as on older servers).

```rust
#[derive(Debug)]
pub enum RenewOutcome {
    Renewed { token: Zeroizing<String>, refresh_token: Zeroizing<String>, expires_in: u64 },
    SignedOut(&'static str),
    RetryLater(&'static str),
}

pub fn renewal_due(expires_in_secs: u64, elapsed_secs: u64) -> bool {
    elapsed_secs + 300 >= expires_in_secs
}

pub async fn renew(endpoint: &ContextEndpoint, refresh_token: &str) -> RenewOutcome {
    let client = reqwest::Client::new();
    let url = match endpoint.join("/v1/auth/feishu/renew") { Some(u) => u, None => return RenewOutcome::RetryLater("ENDPOINT") };
    let response = match client.post(url).bearer_auth(refresh_token).send().await {
        Ok(r) => r,
        Err(_) => return RenewOutcome::RetryLater("NETWORK"),
    };
    let status = response.status().as_u16();
    let body = response.bytes().await.unwrap_or_default();
    let code = serde_json::from_slice::<serde_json::Value>(&body).ok()
        .and_then(|v| v.get("detail").and_then(|d| d.as_str()).map(str::to_owned));
    match (status, code.as_deref()) {
        (200, _) => match serde_json::from_slice::<Redeemed>(&body) {
            Ok(r) if r.token.starts_with("vox_sso_") && r.refresh_token.as_deref().is_some_and(|t| t.starts_with("vox_refresh_")) => RenewOutcome::Renewed {
                token: Zeroizing::new(r.token),
                refresh_token: Zeroizing::new(r.refresh_token.unwrap_or_default()),
                expires_in: r.expires_in.unwrap_or(3600).min(3600),
            },
            _ => RenewOutcome::RetryLater("INVALID_RESPONSE"),
        },
        (401, Some("ACCOUNT_DISABLED")) => RenewOutcome::SignedOut("ACCOUNT_DISABLED"),
        (401, _) => RenewOutcome::SignedOut("SESSION_EXPIRED"),
        (429, _) => RenewOutcome::RetryLater("RENEWAL_RATE_LIMIT"),
        (409, _) => RenewOutcome::RetryLater("RENEWAL_IN_PROGRESS"),
        (503, _) => RenewOutcome::RetryLater("MEMBERSHIP_CHECK_UNAVAILABLE"),
        _ => RenewOutcome::RetryLater("UNEXPECTED_STATUS"),
    }
}
```

Use the crate's existing HTTP helper (the one `sign_in` uses for `begin`/`redeem`) instead of a bare `reqwest::Client::new()` if it enforces the endpoint allow-list; mirror its construction. `ContextEndpoint::join` is the helper in `context_account.rs:217` (`fn join(endpoint, path) -> Option<reqwest::Url>`); move or re-export it so both modules share one implementation.

- [ ] **Step 3: Store the refresh token and schedule renewal**

In `context_session.rs` add constants `REFRESH_ACCOUNT: &str = "context_refresh_token"` and methods `set_refresh_token(&mut self, token: Option<&str>) -> Result<(), SessionError>` (write or delete in the store) and `refresh_token(&self) -> Option<String>` (read). `sign_out` also deletes `REFRESH_ACCOUNT`.

In `context_commands.rs`, after a successful `publish`, store the refresh token and expiry (`expires_in`, `signed_at: Instant`) on `ContextState`, and spawn one maintenance task per app (guard with a `OnceLock`):

```rust
pub fn spawn_session_maintenance(app: tauri::AppHandle) {
    tauri::async_runtime::spawn(async move {
        let mut tick = tokio::time::interval(std::time::Duration::from_secs(60));
        loop {
            tick.tick().await;
            let state = app.state::<ContextState>();
            let Some((endpoint, refresh, expires_in, signed_at)) = state.renewal_inputs() else { continue };
            if !crate::context_login::renewal_due(expires_in, signed_at.elapsed().as_secs()) { continue; }
            match crate::context_login::renew(&endpoint, &refresh).await {
                crate::context_login::RenewOutcome::Renewed { token, refresh_token, expires_in } => {
                    state.apply_renewal(token, refresh_token, expires_in);
                }
                crate::context_login::RenewOutcome::SignedOut(code) => state.sign_out_with_reason(code),
                crate::context_login::RenewOutcome::RetryLater(code) => state.note_renewal_problem(code),
            }
        }
    });
}
```

`apply_renewal` takes a `SessionLease` from the current service (`ContextService` exposes its session; see how `context_delivery.rs` obtains a lease) and calls `SessionAuthority::refresh(&lease, principal, token)` then `set_refresh_token`. `note_renewal_problem` only records a fixed status label for `diagnostics` (`"renewal-unavailable:<CODE>"`); `sign_out_with_reason` calls the existing `end(...)` path and records `"signed-out:<CODE>"`. Call `spawn_session_maintenance(app.handle().clone())` from the Tauri `setup` closure in `lib.rs`.

On startup, when a stored principal and token exist, the existing restore path (if any) should call `GET /v1/auth/feishu/me`; if none exists, treat startup like "signed_at = now, expires_in = 0" so the first tick renews immediately with the stored refresh token. Record which of the two you implemented in the PR description.

- [ ] **Step 4: Tests, fmt, push, CI**

```bash
cargo fmt --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --all
cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked
git add -A apps/desktop-tauri/src-tauri
git commit -m "Context: store the Feishu refresh token and renew before expiry

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push && gh pr checks --watch
```

### Task 7: Finish the PR

- [ ] **Step 1: Mark ready, fill the description** with: what macOS could compile, what only Windows CI compiled and tested (`context_dpapi`, `context_acl`, `context_windows_journal`), the restore behaviour chosen in Task 6, and that device evidence is PR-F.

- [ ] **Step 2: Codex audit** per the master plan; append the record to `docs/verification/windows-voice-flow-2026-10.md`; close every high-severity finding.
