//! Field-level encryption of cached payloads (AES-256-GCM). The key lives in the OS vault, so a copied database file is
//! unreadable on another machine/profile. Each blob is bound (as AAD) to its user + table + row, so a blob cannot be
//! replayed into another user's or company's row. Decision recorded in ADR-047 (instead of SQLCipher).

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::{engine::general_purpose::STANDARD, Engine};
use rand::RngCore;

use crate::error::{AppError, Result};
use crate::vault::SecretStore;

const KEY_NAME: &str = "cache-key-v1";

pub struct Sealer {
    cipher: Aes256Gcm,
}

impl Sealer {
    /// Load the cache key from the vault, creating it on first use.
    pub fn from_vault(store: &dyn SecretStore) -> Result<Self> {
        let key = match store.get(KEY_NAME)? {
            Some(k) => STANDARD.decode(k).map_err(|_| AppError::new("vault_error", "The stored cache key is damaged."))?,
            None => {
                let mut k = [0u8; 32];
                rand::rngs::OsRng.fill_bytes(&mut k);
                store.set(KEY_NAME, &STANDARD.encode(k))?;
                k.to_vec()
            }
        };
        Self::from_key(&key)
    }

    pub fn from_key(key: &[u8]) -> Result<Self> {
        let cipher = Aes256Gcm::new_from_slice(key).map_err(|_| AppError::new("vault_error", "The cache key has the wrong size."))?;
        Ok(Self { cipher })
    }

    pub fn seal(&self, aad: &str, plaintext: &[u8]) -> Result<Vec<u8>> {
        let mut nonce = [0u8; 12];
        rand::rngs::OsRng.fill_bytes(&mut nonce);
        let ct = self
            .cipher
            .encrypt(Nonce::from_slice(&nonce), Payload { msg: plaintext, aad: aad.as_bytes() })
            .map_err(|_| AppError::database("The cache could not be encrypted."))?;
        let mut out = Vec::with_capacity(12 + ct.len());
        out.extend_from_slice(&nonce);
        out.extend_from_slice(&ct);
        Ok(out)
    }

    pub fn open(&self, aad: &str, blob: &[u8]) -> Result<Vec<u8>> {
        if blob.len() < 12 + 16 {
            return Err(AppError::database("A cached item is damaged."));
        }
        let (nonce, ct) = blob.split_at(12);
        self.cipher
            .decrypt(Nonce::from_slice(nonce), Payload { msg: ct, aad: aad.as_bytes() })
            .map_err(|_| AppError::database("A cached item could not be decrypted (wrong key or tampered data)."))
    }
}
