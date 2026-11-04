//! Structured JSON-lines logging with redaction. Secrets must never reach a log file: values are scrubbed on the way in,
//! and callers are expected to log identifiers and outcomes, not payloads.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use chrono::Utc;
use serde_json::json;

const MAX_BYTES: u64 = 1_000_000;
const KEYS: [&str; 7] = ["password", "access_token", "refresh_token", "mfa_token", "token", "secret", "authorization"];

/// Remove anything that looks like a credential from free text: `Bearer xyz`, and `key": "value"` / `key=value` for the
/// credential-like keys above (case-insensitive). Over-redaction is acceptable; leaking is not.
pub fn redact(input: &str) -> String {
    let lower = input.to_ascii_lowercase();
    let bytes = input.as_bytes();
    let mut out = String::with_capacity(input.len());
    let mut i = 0;
    while i < input.len() {
        if lower[i..].starts_with("bearer ") {
            out.push_str("Bearer [redacted]");
            i += 7;
            while i < input.len() && !bytes[i].is_ascii_whitespace() && bytes[i] != b'"' {
                i += 1;
            }
            continue;
        }
        if let Some(k) = KEYS.iter().find(|k| lower[i..].starts_with(**k)) {
            let mut j = i + k.len();
            while j < input.len() && matches!(bytes[j], b'"' | b'\'') {
                j += 1;
            }
            while j < input.len() && bytes[j] == b' ' {
                j += 1;
            }
            if j < input.len() && matches!(bytes[j], b':' | b'=') {
                j += 1;
                while j < input.len() && matches!(bytes[j], b' ' | b'"' | b'\'') {
                    j += 1;
                }
                if lower[j..].starts_with("bearer ") {
                    j += 7;
                }
                out.push_str(&input[i..i + k.len()]);
                out.push_str("=[redacted]");
                while j < input.len() && !matches!(bytes[j], b'"' | b'\'' | b',' | b'}' | b'&' | b' ' | b'\n') {
                    j += 1;
                }
                i = j;
                continue;
            }
        }
        let ch = input[i..].chars().next().unwrap_or(' ');
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

pub struct Logger {
    path: PathBuf,
    guard: Mutex<()>,
}

impl Logger {
    pub fn new(dir: &Path) -> std::io::Result<Self> {
        fs::create_dir_all(dir)?;
        Ok(Self { path: dir.join("artify-desktop.log"), guard: Mutex::new(()) })
    }

    pub fn log(&self, level: &str, event: &str, message: &str) {
        let _g = self.guard.lock();
        if fs::metadata(&self.path).map(|m| m.len() > MAX_BYTES).unwrap_or(false) {
            let _ = fs::rename(&self.path, self.path.with_extension("log.1"));
        }
        let line = json!({ "ts": Utc::now().to_rfc3339(), "level": level, "event": event, "message": redact(message) });
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&self.path) {
            let _ = writeln!(f, "{line}");
        }
    }
    pub fn info(&self, event: &str, message: &str) {
        self.log("info", event, message)
    }
    pub fn warn(&self, event: &str, message: &str) {
        self.log("warn", event, message)
    }
    pub fn error(&self, event: &str, message: &str) {
        self.log("error", event, message)
    }

    /// The most recent `max_lines` log lines (already redacted when written; redacted again on read).
    pub fn tail(&self, max_lines: usize) -> Vec<String> {
        let mut lines: Vec<String> = Vec::new();
        for p in [self.path.with_extension("log.1"), self.path.clone()] {
            if let Ok(s) = fs::read_to_string(p) {
                lines.extend(s.lines().map(redact));
            }
        }
        let n = lines.len();
        lines.split_off(n.saturating_sub(max_lines))
    }
}
