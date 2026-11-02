//! Windows DPAPI key provider (T07b): `CryptProtectData` /
//! `CryptUnprotectData` in current-user scope. The caller's `context` bytes
//! are the optional entropy, so a blob wrapped for one key role cannot be
//! unwrapped for another, and a blob from another Windows user or machine
//! fails as `Unrecoverable`. No UI is ever shown (`CRYPTPROTECT_UI_FORBIDDEN`).
#![cfg(windows)]

use super::{KeyError, KeyProvider, ProviderKind};
use windows_sys::Win32::Foundation::LocalFree;
use windows_sys::Win32::Security::Cryptography::{
    CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
};
use zeroize::{Zeroize, Zeroizing};

/// Blob description (`vox-ctx`), stored by DPAPI next to the ciphertext.
const DESCRIPTION: &[u16] = &[
    'v' as u16, 'o' as u16, 'x' as u16, '-' as u16, 'c' as u16, 't' as u16, 'x' as u16, 0,
];
const MAX_KEY_BYTES: usize = 4096;
const MAX_CONTEXT_BYTES: usize = 4096;
/// Largest wrapped blob accepted: a DPAPI blob for a 32-byte key is a few
/// hundred bytes; anything near this bound is not one of ours.
const MAX_WRAPPED_BYTES: usize = 1 << 16;
/// A DPAPI blob is never shorter than its fixed header.
const MIN_WRAPPED_BYTES: usize = 16;

#[derive(Debug, Default, Clone, Copy)]
pub struct DpapiKeyProvider;

impl DpapiKeyProvider {
    pub fn new() -> Self {
        Self
    }
}

/// Input view over a slice. DPAPI only reads through `pbData`; the `*mut` is
/// the generated signature, not a write.
fn blob(bytes: &[u8]) -> CRYPT_INTEGER_BLOB {
    CRYPT_INTEGER_BLOB {
        cbData: u32::try_from(bytes.len()).unwrap_or(u32::MAX),
        pbData: bytes.as_ptr() as *mut u8,
    }
}

/// Copies the output blob into owned memory and frees the `LocalAlloc`
/// buffer DPAPI returned. With `secret`, the buffer is zeroed first, so an
/// unwrapped key does not linger in freed memory.
///
/// # Safety
/// `out` must be an output blob filled by a successful DPAPI call that has
/// not been freed yet.
unsafe fn take(out: CRYPT_INTEGER_BLOB, secret: bool) -> Vec<u8> {
    let buffer = std::slice::from_raw_parts_mut(out.pbData, out.cbData as usize);
    let copy = buffer.to_vec();
    if secret {
        buffer.zeroize();
    }
    LocalFree(out.pbData as _);
    copy
}

impl KeyProvider for DpapiKeyProvider {
    fn kind(&self) -> ProviderKind {
        ProviderKind::DpapiUser
    }

    fn wrap(&self, context: &[u8], key: &[u8]) -> Result<Vec<u8>, KeyError> {
        if key.is_empty() || key.len() > MAX_KEY_BYTES || context.len() > MAX_CONTEXT_BYTES {
            return Err(KeyError::InvalidInput);
        }
        let input = blob(key);
        let entropy = blob(context);
        let mut out = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: std::ptr::null_mut(),
        };
        // SAFETY: every pointer references live slice or stack memory for the
        // duration of the call; the output buffer is copied and freed in `take`.
        let ok = unsafe {
            CryptProtectData(
                &input,
                DESCRIPTION.as_ptr(),
                &entropy,
                std::ptr::null(),
                std::ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut out,
            )
        };
        if ok == 0 || out.pbData.is_null() {
            return Err(KeyError::Unavailable);
        }
        // SAFETY: `out` was filled by the successful call above.
        let wrapped = unsafe { take(out, false) };
        if wrapped.is_empty() {
            return Err(KeyError::Unavailable);
        }
        Ok(wrapped)
    }

    fn unwrap(&self, context: &[u8], wrapped: &[u8]) -> Result<Zeroizing<Vec<u8>>, KeyError> {
        if wrapped.len() < MIN_WRAPPED_BYTES
            || wrapped.len() > MAX_WRAPPED_BYTES
            || context.len() > MAX_CONTEXT_BYTES
        {
            return Err(KeyError::Unrecoverable);
        }
        let input = blob(wrapped);
        let entropy = blob(context);
        let mut out = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: std::ptr::null_mut(),
        };
        // SAFETY: as in `wrap`; the description out-pointer is null, so DPAPI
        // allocates nothing for it.
        let ok = unsafe {
            CryptUnprotectData(
                &input,
                std::ptr::null_mut(),
                &entropy,
                std::ptr::null(),
                std::ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut out,
            )
        };
        if ok == 0 || out.pbData.is_null() {
            // Wrong user, wrong machine, wrong entropy and tampering all end here.
            return Err(KeyError::Unrecoverable);
        }
        // SAFETY: `out` was filled by the successful call above.
        Ok(Zeroizing::new(unsafe { take(out, true) }))
    }
}
