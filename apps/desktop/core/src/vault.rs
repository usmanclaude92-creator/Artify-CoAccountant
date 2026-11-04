//! Secret storage. Refresh tokens and the cache key live ONLY in the OS credential vault (Windows Credential Manager);
//! never in SQLite, settings, logs or the webview. Tests use the in-memory store.

use std::collections::HashMap;
use std::sync::Mutex;

use crate::error::{AppError, Result};

pub trait SecretStore: Send + Sync {
    fn get(&self, key: &str) -> Result<Option<String>>;
    fn set(&self, key: &str, value: &str) -> Result<()>;
    fn delete(&self, key: &str) -> Result<()>;
}

#[derive(Default)]
pub struct MemoryStore(Mutex<HashMap<String, String>>);

impl MemoryStore {
    pub fn new() -> Self {
        Self::default()
    }
    /// Test helper: every value currently held (to prove nothing secret leaks elsewhere).
    pub fn snapshot(&self) -> HashMap<String, String> {
        self.0.lock().map(|g| g.clone()).unwrap_or_default()
    }
}

impl SecretStore for MemoryStore {
    fn get(&self, key: &str) -> Result<Option<String>> {
        Ok(self.0.lock().map_err(|_| AppError::new("vault_error", "vault unavailable"))?.get(key).cloned())
    }
    fn set(&self, key: &str, value: &str) -> Result<()> {
        self.0.lock().map_err(|_| AppError::new("vault_error", "vault unavailable"))?.insert(key.into(), value.into());
        Ok(())
    }
    fn delete(&self, key: &str) -> Result<()> {
        self.0.lock().map_err(|_| AppError::new("vault_error", "vault unavailable"))?.remove(key);
        Ok(())
    }
}

#[cfg(feature = "os-vault")]
pub struct OsVault {
    service: String,
}

#[cfg(feature = "os-vault")]
impl OsVault {
    pub fn new(service: &str) -> Self {
        Self { service: service.into() }
    }
    fn entry(&self, key: &str) -> Result<keyring::Entry> {
        keyring::Entry::new(&self.service, key).map_err(|_| AppError::new("vault_error", "The system credential store is not available."))
    }
}

#[cfg(feature = "os-vault")]
impl SecretStore for OsVault {
    fn get(&self, key: &str) -> Result<Option<String>> {
        match self.entry(key)?.get_password() {
            Ok(v) => Ok(Some(v)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err(AppError::new("vault_error", "The system credential store could not be read.")),
        }
    }
    fn set(&self, key: &str, value: &str) -> Result<()> {
        self.entry(key)?.set_password(value).map_err(|_| AppError::new("vault_error", "The system credential store rejected the value."))
    }
    fn delete(&self, key: &str) -> Result<()> {
        match self.entry(key)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(AppError::new("vault_error", "The system credential store could not delete the value.")),
        }
    }
}
