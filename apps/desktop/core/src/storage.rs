//! Local storage foundation: Windows-safe names, root validation, per-company directories. The frontend never receives a
//! general file-system capability: it can only ask for these operations, and every path is checked to stay inside the
//! configured root. No documents are written in Phase 5 (the mirror arrives with document management).

use std::fs;
use std::path::{Component, Path, PathBuf};

use serde::Serialize;

use crate::error::{AppError, Result};

const RESERVED: [&str; 22] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
    "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];
/// Keep full paths comfortably below the classic 260-character Windows limit (documents add file names below this).
pub const MAX_COMPANY_DIR_PATH: usize = 160;
pub const MIN_FREE_BYTES_BLOCK: u64 = 100 * 1024 * 1024;
pub const MIN_FREE_BYTES_WARN: u64 = 2 * 1024 * 1024 * 1024;

/// Turn any display name into one safe Windows path segment. Total function: never returns an empty or reserved name.
pub fn sanitize_segment(name: &str) -> String {
    let mut s: String = name.chars().map(|c| if c.is_control() || "<>:\"/\\|?*".contains(c) { '-' } else { c }).collect();
    s = s.split_whitespace().collect::<Vec<_>>().join(" ");
    // ".." can never survive as a segment; also collapse runs of dots
    while s.contains("..") {
        s = s.replace("..", ".");
    }
    let s = s.trim_matches(|c: char| c == '.' || c == ' ' || c == '-').to_string();
    let s: String = s.chars().take(80).collect();
    let s = s.trim_end_matches(['.', ' ']).to_string();
    let stem = s.split('.').next().unwrap_or("").to_ascii_uppercase();
    if s.is_empty() {
        "Company".into()
    } else if RESERVED.contains(&stem.as_str()) {
        // Windows reserves the device name with ANY extension ("LPT9.log"), so the suffix goes on the stem
        match s.split_once('.') {
            Some((head, rest)) => format!("{head}_.{rest}"),
            None => format!("{s}_"),
        }
    } else {
        s
    }
}

/// Directory name for a company, unique among `taken` names (case-insensitive, as NTFS is): `Name`, then `Name (xxxxxxxx)`.
pub fn company_dir_name(display_name: &str, company_id: &str, is_taken: &dyn Fn(&str) -> bool) -> String {
    let base = sanitize_segment(display_name);
    if !is_taken(&base) {
        return base;
    }
    let short: String =
        company_id.chars().filter(|c| c.is_ascii_alphanumeric()).rev().take(8).collect::<Vec<_>>().into_iter().rev().collect();
    let candidate = format!("{base} ({short})");
    if !is_taken(&candidate) {
        return candidate;
    }
    format!("{base} ({short}-{})", uuid::Uuid::new_v4().simple().to_string().chars().take(4).collect::<String>())
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct RootReport {
    pub path: String,
    pub exists: bool,
    pub is_dir: bool,
    pub writable: bool,
    pub free_bytes: Option<u64>,
    /// ok | missing | not_a_directory | not_writable | low_space
    pub status: String,
    pub warnings: Vec<String>,
}

fn has_parent_refs(p: &Path) -> bool {
    p.components().any(|c| matches!(c, Component::ParentDir))
}

/// Check a candidate (or the configured) root. Creates nothing unless `create` is set and the parent exists.
pub fn inspect_root(path: &Path, create: bool) -> Result<RootReport> {
    if path.as_os_str().is_empty() || !path.is_absolute() || has_parent_refs(path) {
        return Err(AppError::new("invalid_path", "Choose a full folder path (for example D:\\Artify). Relative paths are not accepted."));
    }
    if path.parent().is_none() {
        return Err(AppError::new("invalid_path", "Choose a folder, not the root of a drive."));
    }
    let mut report = RootReport {
        path: path.display().to_string(),
        exists: path.exists(),
        is_dir: path.is_dir(),
        writable: false,
        free_bytes: None,
        status: "missing".into(),
        warnings: vec![],
    };
    if !report.exists && create {
        match path.parent() {
            Some(parent) if parent.is_dir() => {
                fs::create_dir(path)?;
                report.exists = true;
                report.is_dir = true;
            }
            _ => return Err(AppError::filesystem("path_missing", "The parent folder does not exist (or the drive is disconnected).")),
        }
    }
    if !report.exists {
        return Ok(report);
    }
    if !report.is_dir {
        report.status = "not_a_directory".into();
        return Ok(report);
    }
    let probe = path.join(format!(".artify-write-test-{}", uuid::Uuid::new_v4().simple()));
    report.writable = match fs::OpenOptions::new().write(true).create_new(true).open(&probe) {
        Ok(_) => {
            let _ = fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    };
    report.free_bytes = fs2::available_space(path).ok();
    report.status = if !report.writable {
        "not_writable".into()
    } else if report.free_bytes.is_some_and(|f| f < MIN_FREE_BYTES_BLOCK) {
        "low_space".into()
    } else {
        "ok".into()
    };
    if report.status == "ok" && report.free_bytes.is_some_and(|f| f < MIN_FREE_BYTES_WARN) {
        report.warnings.push("Less than 2 GB of free space is available on this drive.".into());
    }
    if path.display().to_string().to_ascii_lowercase().contains("onedrive") {
        report.warnings.push("This folder is inside OneDrive. Files may be uploaded to Microsoft or kept online-only; choose a local folder if that is not intended.".into());
    }
    Ok(report)
}

/// Validate a NEW root before it is adopted. It must be usable and must not be nested inside (or contain) the old root:
/// two roots that overlap would mix company data. Existing files are never moved or deleted by changing the root.
pub fn validate_new_root(new: &Path, old: Option<&Path>) -> Result<RootReport> {
    let report = inspect_root(new, false)?;
    match report.status.as_str() {
        "ok" => {}
        "missing" => return Err(AppError::filesystem("path_missing", "That folder does not exist. Create it first, then choose it.")),
        "not_a_directory" => return Err(AppError::filesystem("not_a_directory", "That path is a file, not a folder.")),
        "not_writable" => return Err(AppError::filesystem("permission_denied", "The app cannot write to that folder.")),
        "low_space" => return Err(AppError::filesystem("low_space", "That drive has too little free space (under 100 MB).")),
        _ => {}
    }
    if let Some(old) = old {
        let (n, o) = (new.canonicalize()?, old.canonicalize().unwrap_or_else(|_| old.to_path_buf()));
        if n == o || n.starts_with(&o) || o.starts_with(&n) {
            return Err(AppError::new("overlapping_root", "The new location overlaps the current one. Choose a separate folder."));
        }
    }
    Ok(report)
}

/// Create (never overwrite) the folder for one company under `root`. The result is verified to stay inside `root`.
pub fn ensure_company_dir(root: &Path, dir_name: &str) -> Result<PathBuf> {
    let report = inspect_root(root, false)?;
    if report.status != "ok" && report.status != "low_space" {
        return Err(match report.status.as_str() {
            "missing" => AppError::filesystem("storage_unavailable", "The storage folder is missing or its drive is disconnected."),
            "not_writable" => AppError::filesystem("permission_denied", "The app cannot write to the storage folder."),
            _ => AppError::filesystem("storage_unavailable", "The storage folder is not usable."),
        });
    }
    if report.status == "low_space" {
        return Err(AppError::filesystem("low_space", "The drive has too little free space."));
    }
    if dir_name != sanitize_segment(dir_name) || dir_name.is_empty() {
        return Err(AppError::new("invalid_path", "Invalid folder name."));
    }
    let target = root.join(dir_name);
    if target.display().to_string().chars().count() > MAX_COMPANY_DIR_PATH {
        return Err(AppError::new("path_too_long", "The storage location is too deep. Choose a shorter folder path."));
    }
    if target.exists() && !target.is_dir() {
        return Err(AppError::filesystem("already_exists", "A file with that name exists; nothing was overwritten."));
    }
    fs::create_dir_all(&target)?;
    let (canon_root, canon_target) = (root.canonicalize()?, target.canonicalize()?);
    if !canon_target.starts_with(&canon_root) {
        return Err(AppError::new("path_escape", "The folder resolves outside the storage location and was refused."));
    }
    Ok(target)
}
