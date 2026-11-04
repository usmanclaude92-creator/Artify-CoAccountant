//! Persisted desktop settings: a closed allow-list of keys with validated values. Anything else is rejected, so the UI
//! cannot use the settings store as a general-purpose key/value vault.

use crate::error::{AppError, Result};

pub const KEYS: &[&str] = &[
    "theme",
    "language",
    "start_on_login",
    "default_company_id",
    "notifications",
    "storage_root",
    "update_channel",
    "last_org_id",
    "last_company_id",
];

pub fn validate(key: &str, value: &str) -> Result<()> {
    let ok = match key {
        "theme" => matches!(value, "system" | "light" | "dark"),
        "language" => matches!(value, "en" | "ar"),
        "start_on_login" | "notifications" => matches!(value, "true" | "false"),
        "update_channel" => matches!(value, "stable" | "beta"),
        "default_company_id" | "last_org_id" | "last_company_id" => {
            value.is_empty() || (value.len() <= 64 && value.chars().all(|c| c.is_ascii_hexdigit() || c == '-'))
        }
        "storage_root" => value.len() <= 400 && !value.contains('\0'),
        _ => return Err(AppError::new("unknown_setting", "That setting does not exist.")),
    };
    if ok {
        Ok(())
    } else {
        Err(AppError::new("invalid_setting", format!("'{value}' is not a valid value for {key}.")))
    }
}
