//! Excel templates, exports and reports (Phase 9) for the desktop: a fixed, validated passthrough to the Excel API plus two
//! file transfers (template upload, export download). The server validates workbooks, applies mappings, generates the file
//! and decides who may do what; this layer only fixes WHICH routes the interface may call, checks identifiers and filters,
//! forwards bodies unchanged, and moves files: it never opens or evaluates a workbook and never chooses where a download goes
//! (the person does, in a native "save as" dialog).

use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use serde_json::Value;

use crate::api::Downloaded;
use crate::desktop::Desktop;
use crate::error::{AppError, Result};

fn id(s: &str) -> Result<String> {
    uuid::Uuid::parse_str(s).map(|u| u.hyphenated().to_string()).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum M {
    Get,
    Post,
    Patch,
    Delete,
}

/// operation -> (method, path under /companies/{company}/excel, number of ids it needs)
const OPS: &[(&str, M, &str, u8)] = &[
    ("catalogue", M::Get, "/catalogue", 0),
    ("templates", M::Get, "/templates", 0),
    ("template", M::Get, "/templates/{id}", 1),
    ("template_update", M::Patch, "/templates/{id}", 1),
    ("template_status", M::Post, "/templates/{id}/status", 1),
    ("template_duplicate", M::Post, "/templates/{id}/duplicate", 1),
    ("template_delete", M::Delete, "/templates/{id}", 1),
    ("template_analysis", M::Get, "/templates/{id}/analysis", 1),
    ("mappings", M::Get, "/templates/{id}/mappings", 1),
    ("mapping_save", M::Post, "/templates/{id}/mappings", 1),
    ("mapping", M::Get, "/mappings/{id}", 1),
    ("mapping_validate", M::Post, "/mappings/{id}/validate", 1),
    ("mapping_activate", M::Post, "/mappings/{id}/activate", 1),
    ("mapping_preview", M::Post, "/mappings/{id}/preview", 1),
    ("export_validate", M::Post, "/exports/validate", 0),
    ("export_preview", M::Post, "/exports/preview", 0),
    ("export_create", M::Post, "/exports", 0),
    ("exports", M::Get, "/exports", 0),
    ("export", M::Get, "/exports/{id}", 1),
    ("export_transactions", M::Get, "/exports/{id}/transactions", 1),
    ("export_verify", M::Post, "/exports/{id}/verify", 1),
    ("export_regenerate", M::Post, "/exports/{id}/regenerate", 1),
    ("exports_compare", M::Get, "/exports/compare", 0),
    ("reports_standard", M::Get, "/reports/standard", 0),
    ("report_run", M::Post, "/reports/standard/{key}/run", 0),
    ("reports_custom", M::Get, "/reports/custom", 0),
    ("custom_validate", M::Post, "/reports/custom/validate", 0),
    ("custom_create", M::Post, "/reports/custom", 0),
    ("custom_update", M::Patch, "/reports/custom/{id}", 1),
    ("custom_archive", M::Post, "/reports/custom/{id}/archive", 1),
    ("custom_run", M::Post, "/reports/custom/{id}/run", 1),
    ("custom_export", M::Post, "/reports/custom/{id}/export", 1),
];
const QUERY_KEYS: &[&str] =
    &["status", "category", "q", "limit", "offset", "template_id", "version", "a", "b", "include_archived", "restore"];

fn query_string(query: &[(String, String)]) -> Result<String> {
    let mut parts = Vec::new();
    for (k, v) in query {
        if !QUERY_KEYS.contains(&k.as_str()) || v.len() > 100 || !v.chars().all(|c| c.is_ascii_alphanumeric() || " _-.".contains(c)) {
            return Err(AppError::new("invalid_query", "That filter is not allowed."));
        }
        parts.push(format!("{k}={}", v.replace(' ', "%20")));
    }
    Ok(if parts.is_empty() { String::new() } else { format!("?{}", parts.join("&")) })
}

/// Percent-encode everything but RFC 3986 unreserved characters (file names may contain any character).
fn pct(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// A report key is a slug chosen by the server's catalogue; accept only that shape.
fn slug(s: &str) -> Result<&str> {
    if !s.is_empty() && s.len() <= 60 && s.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_') {
        Ok(s)
    } else {
        Err(AppError::new("invalid_id", "That report is not valid."))
    }
}

/// What the person typed about a template upload. Every field is optional text; the server validates them.
#[derive(Debug, Default, Clone, serde::Deserialize)]
pub struct UploadMeta {
    pub name: Option<String>,
    pub category: Option<String>,
    pub description: Option<String>,
    pub template_id: Option<String>,
    pub change_note: Option<String>,
}

const UPLOAD_MAX_BYTES: u64 = 64 * 1024 * 1024; // a sanity cap; the server enforces the plan's real limit

impl Desktop {
    /// One allow-listed Excel call. `ids` fill the `{id}` placeholder; a standard-report key travels as the first element of
    /// `ids` for `report_run` and is checked as a slug instead of a UUID.
    pub async fn xl_call(
        &self,
        org: &str,
        company: &str,
        op: &str,
        ids: &[String],
        query: &[(String, String)],
        body: Value,
    ) -> Result<Value> {
        let Some((_, method, tail, need)) = OPS.iter().find(|o| o.0 == op) else {
            return Err(AppError::new("unknown_action", "Unknown Excel action."));
        };
        let wants_key = tail.contains("{key}");
        let expected = if wants_key { 1 } else { *need as usize };
        if ids.len() != expected {
            return Err(AppError::new("invalid_id", "That identifier is not valid."));
        }
        let mut path = format!("/companies/{}/excel{tail}", id(company)?);
        if wants_key {
            path = path.replace("{key}", slug(&ids[0])?);
        } else if let Some(first) = ids.first() {
            path = path.replace("{id}", &id(first)?);
        }
        let takes_query = matches!(op, "custom_archive" | "report_run" | "custom_run" | "export_preview" | "mapping_preview");
        if *method != M::Get && !query.is_empty() && !takes_query {
            return Err(AppError::new("invalid_query", "That filter is not allowed."));
        }
        path.push_str(&query_string(query)?);
        let res = match method {
            M::Get => self.api.get(&path, Some(org)).await,
            M::Post => self.api.post(&path, Some(org), Some(body)).await,
            M::Patch => self.api.patch(&path, Some(org), body).await,
            M::Delete => self.api.delete(&path, Some(org)).await,
        };
        let r = res.map_err(|e| self.classify(e))?;
        if *method != M::Get
            && !matches!(
                op,
                "export_validate"
                    | "export_preview"
                    | "mapping_validate"
                    | "mapping_preview"
                    | "report_run"
                    | "custom_validate"
                    | "custom_run"
                    | "export_verify"
            )
        {
            self.emit("excel-changed", serde_json::json!({ "company_id": company }));
        }
        Ok(r.body)
    }

    /// Stream a workbook the person chose to the server as a new template (or a new version of `template_id`). The file is
    /// read from disk in chunks and never parsed here.
    pub async fn xl_upload(&self, org: &str, company: &str, file: &Path, meta: &UploadMeta) -> Result<Value> {
        let company = id(company)?;
        let ext = file.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).unwrap_or_default();
        if ext != "xlsx" && ext != "xlsm" {
            return Err(AppError::new(
                "unsupported_extension",
                "Choose an Excel workbook (.xlsx, or .xlsm if your plan allows macro-enabled files).",
            ));
        }
        let size = std::fs::metadata(file)?.len();
        if size == 0 {
            return Err(AppError::new("empty_file", "That file is empty."));
        }
        if size > UPLOAD_MAX_BYTES {
            return Err(AppError::new("file_too_large", "That file is too large to be an Excel template."));
        }
        let filename =
            file.file_name().and_then(|n| n.to_str()).ok_or_else(|| AppError::new("invalid_path", "That file name cannot be used."))?;
        let mut q = format!("filename={}", pct(filename));
        for (k, v) in
            [("name", &meta.name), ("category", &meta.category), ("description", &meta.description), ("change_note", &meta.change_note)]
        {
            if let Some(v) = v.as_deref().map(str::trim).filter(|v| !v.is_empty()) {
                q.push_str(&format!("&{k}={}", pct(v)));
            }
        }
        if let Some(t) = meta.template_id.as_deref() {
            q.push_str(&format!("&template_id={}", id(t)?));
        }
        let path = format!("/companies/{company}/excel/templates/upload?{q}");
        let r = self
            .api
            .put_file(&path, Some(org), file, size, Arc::new(AtomicBool::new(false)), Arc::new(|_| {}))
            .await
            .map_err(|e| self.classify(e))?;
        self.emit("excel-changed", serde_json::json!({ "company_id": company }));
        Ok(r.body)
    }

    /// Download a COMPLETED export to a path the person chose. The file is written beside the destination under a temporary
    /// name, its SHA-256 is compared with the checksum recorded when the server generated it, and only then is it renamed. A
    /// damaged download is discarded and nothing existing is touched until the verified file is ready.
    pub async fn xl_export_save(&self, org: &str, company: &str, export_id: &str, dest: &Path) -> Result<PathBuf> {
        let (company, export_id) = (id(company)?, id(export_id)?);
        let rec = self.xl_call(org, &company, "export", std::slice::from_ref(&export_id), &[], Value::Null).await?;
        if rec.get("status").and_then(Value::as_str) != Some("completed") {
            return Err(AppError::new("not_ready", "This export has no file: it did not complete."));
        }
        let recorded = rec.get("sha256").and_then(Value::as_str).ok_or_else(|| AppError::malformed("export"))?.to_string();
        let dir = dest.parent().ok_or_else(|| AppError::new("invalid_path", "Choose a location for the file."))?;
        if !dir.is_dir() {
            return Err(AppError::filesystem("storage_unavailable", "That folder does not exist."));
        }
        let name =
            dest.file_name().and_then(|n| n.to_str()).ok_or_else(|| AppError::new("invalid_path", "That file name cannot be used."))?;
        let part = dir.join(format!(".{name}.{}.part", uuid::Uuid::new_v4().simple()));
        let path = format!("/companies/{company}/excel/exports/{export_id}/file");
        let got: Downloaded =
            self.api.download_to(&path, Some(org), &part, Arc::new(AtomicBool::new(false)), Arc::new(|_| {})).await.map_err(|e| {
                let _ = std::fs::remove_file(&part);
                self.classify(e)
            })?;
        let announced_ok = got.announced_sha256.as_deref().map(|a| a.eq_ignore_ascii_case(&got.sha256)).unwrap_or(true);
        if !announced_ok || !got.sha256.eq_ignore_ascii_case(&recorded) {
            let _ = std::fs::remove_file(&part);
            return Err(AppError::new(
                "download_corrupted",
                "The downloaded file did not match its checksum and was discarded. Try again.",
            )
            .retryable());
        }
        std::fs::rename(&part, dest).map_err(|e| {
            let _ = std::fs::remove_file(&part);
            AppError::from(e)
        })?;
        self.log.info("excel_export_saved", "export written to the chosen location");
        Ok(dest.to_path_buf())
    }
}
