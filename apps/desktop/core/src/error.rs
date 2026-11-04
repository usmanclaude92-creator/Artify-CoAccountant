//! One error type for the whole desktop core. It is what the UI receives, so it never contains tokens, passwords or
//! raw response bodies; `detail` carries only the structured, non-secret extras of a problem+json response.

use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Clone, Serialize, thiserror::Error)]
#[error("{code}: {message}")]
pub struct AppError {
    /// Stable machine-readable code, e.g. `network_unavailable`, `session_expired`, `forbidden`.
    pub code: String,
    /// Plain-language message safe to show to a user.
    pub message: String,
    pub status: Option<u16>,
    /// Whether retrying the same request later may succeed.
    pub retryable: bool,
    pub detail: Option<Value>,
}

impl AppError {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self { code: code.into(), message: message.into(), status: None, retryable: false, detail: None }
    }
    pub fn retryable(mut self) -> Self {
        self.retryable = true;
        self
    }
    pub fn with_status(mut self, status: u16) -> Self {
        self.status = Some(status);
        self
    }
    pub fn with_detail(mut self, detail: Value) -> Self {
        self.detail = Some(detail);
        self
    }
    pub fn network(msg: impl Into<String>) -> Self {
        Self::new("network_unavailable", msg).retryable()
    }
    pub fn timeout() -> Self {
        Self::new("timeout", "The server did not answer in time.").retryable()
    }
    pub fn session_expired() -> Self {
        Self::new("session_expired", "Your session has ended. Please sign in again.").with_status(401)
    }
    pub fn malformed(what: &str) -> Self {
        Self::new("malformed_response", format!("The server sent a response the app could not understand ({what})."))
    }
    pub fn database(msg: impl Into<String>) -> Self {
        Self::new("database_error", msg)
    }
    pub fn filesystem(code: &str, msg: impl Into<String>) -> Self {
        Self::new(code, msg)
    }
    pub fn is_session_expired(&self) -> bool {
        self.code == "session_expired"
    }
}

impl From<rusqlite::Error> for AppError {
    fn from(e: rusqlite::Error) -> Self {
        // Never echo SQL text or values: report the class of failure only.
        AppError::database(format!("The local database reported an error ({}).", db_class(&e)))
    }
}

fn db_class(e: &rusqlite::Error) -> &'static str {
    match e {
        rusqlite::Error::SqliteFailure(f, _) => match f.code {
            rusqlite::ErrorCode::DatabaseCorrupt => "corrupt",
            rusqlite::ErrorCode::NotADatabase => "not a database",
            rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked => "busy",
            rusqlite::ErrorCode::DiskFull => "disk full",
            rusqlite::ErrorCode::ReadOnly => "read-only",
            _ => "failure",
        },
        _ => "failure",
    }
}

impl From<std::io::Error> for AppError {
    fn from(e: std::io::Error) -> Self {
        use std::io::ErrorKind::*;
        match e.kind() {
            PermissionDenied => AppError::filesystem("permission_denied", "The app is not allowed to use that location."),
            NotFound => AppError::filesystem("path_missing", "That location does not exist (or the drive is disconnected)."),
            AlreadyExists => AppError::filesystem("already_exists", "Something with that name already exists; nothing was overwritten."),
            _ => AppError::filesystem("filesystem_error", "The file system reported an error."),
        }
    }
}

pub type Result<T> = std::result::Result<T, AppError>;
