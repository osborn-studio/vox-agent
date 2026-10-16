//! Test-only key provider. Not DPAPI and not compiled into release builds:
//! it wraps keys with XChaCha20-Poly1305 under a caller-supplied seed, which
//! stands in for "the current OS user". A different seed behaves like another
//! user (unwrap fails: `Unrecoverable`); `unavailable` behaves like a locked
//! profile (`Unavailable`).
use super::{KeyError, KeyProvider, ProviderKind};
use chacha20poly1305::aead::{rand_core::RngCore, Aead, KeyInit, OsRng, Payload};
use chacha20poly1305::{XChaCha20Poly1305, XNonce};
use zeroize::Zeroizing;

pub struct FakeKeyProvider {
    seed: Zeroizing<[u8; 32]>,
    available: bool,
}

impl FakeKeyProvider {
    pub fn new(seed: [u8; 32]) -> Self {
        Self {
            seed: Zeroizing::new(seed),
            available: true,
        }
    }
    pub fn unavailable(seed: [u8; 32]) -> Self {
        Self {
            seed: Zeroizing::new(seed),
            available: false,
        }
    }
}

impl KeyProvider for FakeKeyProvider {
    fn kind(&self) -> ProviderKind {
        ProviderKind::FakeTestOnly
    }
    fn wrap(&self, context: &[u8], key: &[u8]) -> Result<Vec<u8>, KeyError> {
        if !self.available {
            return Err(KeyError::Unavailable);
        }
        let mut nonce = [0u8; 24];
        OsRng.fill_bytes(&mut nonce);
        let sealed = XChaCha20Poly1305::new((&*self.seed).into())
            .encrypt(
                XNonce::from_slice(&nonce),
                Payload {
                    msg: key,
                    aad: context,
                },
            )
            .map_err(|_| KeyError::Unrecoverable)?;
        Ok([nonce.as_slice(), &sealed].concat())
    }
    fn unwrap(&self, context: &[u8], wrapped: &[u8]) -> Result<Zeroizing<Vec<u8>>, KeyError> {
        if !self.available {
            return Err(KeyError::Unavailable);
        }
        if wrapped.len() < 24 + 16 {
            return Err(KeyError::Unrecoverable);
        }
        let (nonce, body) = wrapped.split_at(24);
        XChaCha20Poly1305::new((&*self.seed).into())
            .decrypt(
                XNonce::from_slice(nonce),
                Payload {
                    msg: body,
                    aad: context,
                },
            )
            .map(Zeroizing::new)
            .map_err(|_| KeyError::Unrecoverable)
    }
}
