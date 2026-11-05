//! Importing documents: staging local files, the persistent upload queue and its worker.
//!
//! Honesty rules: an item is `completed` only after the server confirmed that the object is stored AND verified
//! (`POST .../complete` answered 200). Everything before that is waiting/uploading/verification_pending. The queue lives
//! in the sealed local database, so a restart, a crash or a lost connection never loses or duplicates work: a session
//! already registered on the server is reused through its idempotency key, and received bytes are never sent twice.
//! The webview never supplies a file path to the queue: it refers to files the Rust side staged (picked or dropped).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::api::hex;
use crate::db::{NewQueueRow, QueueRow};
use crate::desktop::{Desktop, Staged};
use crate::error::{AppError, Result};

/// Advisory, for instant feedback only. The server's allow-list is authoritative and its answer always wins.
pub const SUPPORTED_EXTENSIONS: [&str; 15] =
    ["pdf", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "heic", "heif", "doc", "docx", "xls", "xlsx", "csv", "txt"];
const MAX_STAGE_PER_CALL: usize = 200;
const MAX_ATTEMPTS: i64 = 5;
/// Files at least this big are sent in resumable chunks (smaller ones in a single request); chunks are at most this big and
/// never bigger than the server allows (`chunk_max_bytes` of the upload session).
const CHUNKED_FROM: u64 = 8 * 1024 * 1024;
const CHUNK_TARGET: u64 = 8 * 1024 * 1024;
const NOT_RETRYABLE: [&str; 6] = ["quarantined", "malware_detected", "source_missing", "source_changed", "cancelled", "duplicate_document"];

#[derive(Debug, Clone, Serialize)]
pub struct StagedFile {
    /// empty when the file was refused (see `problem`)
    pub id: String,
    pub name: String,
    pub size: u64,
    pub extension: String,
    pub sha256: Option<String>,
    pub problem: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct QueueAdd {
    pub staged_id: String,
    pub node_id: Option<String>,
    pub category_id: Option<String>,
    pub display_name: Option<String>,
    #[serde(default)]
    pub metadata: Value,
    pub filing_rule_id: Option<String>,
    #[serde(default = "block")]
    pub duplicate_policy: String,
    pub target_document_id: Option<String>,
    pub change_reason: Option<String>,
    /// items sharing a value are registered as one import batch on the server
    pub batch_local: Option<String>,
}
fn block() -> String {
    "block".into()
}

#[derive(Debug, Clone, Serialize)]
pub struct QueueView {
    pub id: String,
    pub org_id: String,
    pub company_id: String,
    pub file_name: String,
    pub display_name: Option<String>,
    pub size: i64,
    pub status: String,
    pub bytes_sent: i64,
    pub attempts: i64,
    pub node_id: Option<String>,
    pub category_id: Option<String>,
    pub target_document_id: Option<String>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub duplicates: Value,
    pub result_document_id: Option<String>,
    pub can_retry: bool,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct RunSummary {
    pub completed: usize,
    pub failed: usize,
    pub quarantined: usize,
    pub cancelled: usize,
    pub waiting: usize,
    /// the run stopped early: offline, signed out, or another run is active
    pub stopped: Option<String>,
}

fn view(r: &QueueRow) -> QueueView {
    let p = &r.private;
    let s = |k: &str| p.get(k).and_then(Value::as_str).map(str::to_string);
    QueueView {
        id: r.id.clone(),
        org_id: r.org_id.clone(),
        company_id: r.company_id.clone(),
        file_name: s("file_name").unwrap_or_default(),
        display_name: s("display_name"),
        size: r.size,
        status: r.status.clone(),
        bytes_sent: r.bytes_sent,
        attempts: r.attempts,
        node_id: r.node_id.clone(),
        category_id: r.category_id.clone(),
        target_document_id: r.target_document_id.clone(),
        error_code: r.error_code.clone(),
        error_message: r.error_message.clone(),
        duplicates: p.get("duplicates").cloned().unwrap_or(Value::Null),
        result_document_id: r.result_document_id.clone(),
        can_retry: r.status == "failed" && !r.error_code.as_deref().is_some_and(|c| NOT_RETRYABLE.contains(&c)),
        created_at: r.created_at.clone(),
        updated_at: r.updated_at.clone(),
    }
}

fn mtime_of(md: &std::fs::Metadata) -> Option<i64> {
    md.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs() as i64)
}

fn hash_file(path: &std::path::Path) -> std::io::Result<String> {
    use std::io::Read;
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            return Ok(hex(&h.finalize()));
        }
        h.update(&buf[..n]);
    }
}

fn uuid_ok(s: &str) -> Result<String> {
    uuid::Uuid::parse_str(s).map(|u| u.hyphenated().to_string()).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

impl Desktop {
    // ------------------------------------------------------------------ staging
    /// Verify and hash local files so the user can review them before anything is sent. Folders, empty files, unreadable
    /// files and unsupported types are reported with a reason and not staged. Nothing is uploaded here.
    pub async fn stage_paths(&self, paths: Vec<PathBuf>) -> Result<Vec<StagedFile>> {
        self.user_id()?;
        if paths.len() > MAX_STAGE_PER_CALL {
            return Err(AppError::new("too_many_files", format!("Add at most {MAX_STAGE_PER_CALL} files at a time.")));
        }
        let mut out = Vec::new();
        for path in paths {
            let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            let extension = name.rsplit_once('.').map(|(_, e)| e.to_ascii_lowercase()).unwrap_or_default();
            let refuse = |problem: &str| StagedFile {
                id: String::new(),
                name: name.clone(),
                size: 0,
                extension: extension.clone(),
                sha256: None,
                problem: Some(problem.into()),
            };
            let md = match std::fs::metadata(&path) {
                Ok(m) => m,
                Err(_) => {
                    out.push(refuse("The file cannot be read (it may have been moved or the drive is disconnected)."));
                    continue;
                }
            };
            if !md.is_file() {
                out.push(refuse("Folders cannot be imported; choose the files inside."));
                continue;
            }
            if md.len() == 0 {
                out.push(refuse("The file is empty."));
                continue;
            }
            if !SUPPORTED_EXTENSIONS.contains(&extension.as_str()) {
                out.push(refuse(&format!("Files of type '.{extension}' are not supported.")));
                continue;
            }
            let p2 = path.clone();
            let hashed =
                tokio::task::spawn_blocking(move || hash_file(&p2)).await.map_err(|_| AppError::new("internal", "Hashing failed."))?;
            let Ok(sha) = hashed else {
                out.push(refuse("The file could not be read."));
                continue;
            };
            let id = uuid::Uuid::new_v4().to_string();
            let staged = Staged { path: path.clone(), name: name.clone(), size: md.len(), sha256: sha.clone(), mtime: mtime_of(&md) };
            if let Ok(mut m) = self.staged.lock() {
                m.insert(id.clone(), staged);
            }
            out.push(StagedFile { id, name, size: md.len(), extension, sha256: Some(sha), problem: None });
        }
        Ok(out)
    }

    pub fn unstage(&self, id: &str) {
        if let Ok(mut m) = self.staged.lock() {
            m.remove(id);
        }
    }

    // ------------------------------------------------------------------ queue management
    pub fn queue_add(&self, org: &str, company: &str, items: Vec<QueueAdd>) -> Result<Vec<QueueView>> {
        let user = self.user_id()?;
        let org = uuid_ok(org)?;
        let company = uuid_ok(company)?;
        let mut rows = Vec::new();
        for it in &items {
            let staged: Staged = self
                .staged
                .lock()
                .ok()
                .and_then(|m| m.get(&it.staged_id).cloned())
                .ok_or_else(|| AppError::new("file_not_staged", "That file is no longer available. Add it again."))?;
            let node = it.node_id.as_deref().map(uuid_ok).transpose()?;
            let target = it.target_document_id.as_deref().map(uuid_ok).transpose()?;
            if node.is_none() && target.is_none() {
                return Err(AppError::new("destination_required", format!("Choose a destination for {}.", staged.name)));
            }
            if !matches!(it.duplicate_policy.as_str(), "block" | "allow_copy") {
                return Err(AppError::new("invalid_policy", "Unknown duplicate handling."));
            }
            let id = uuid::Uuid::new_v4().to_string();
            rows.push(NewQueueRow {
                id: id.clone(),
                user_id: user.clone(),
                org_id: org.clone(),
                company_id: company.clone(),
                idempotency_key: format!("dq-{}", uuid::Uuid::new_v4().simple()),
                size: staged.size as i64,
                mtime: staged.mtime,
                sha256: Some(staged.sha256.clone()),
                node_id: node,
                category_id: it.category_id.as_deref().map(uuid_ok).transpose()?,
                filing_rule_id: it.filing_rule_id.as_deref().map(uuid_ok).transpose()?,
                target_document_id: target,
                duplicate_policy: it.duplicate_policy.clone(),
                private: json!({
                    "source_path": staged.path.display().to_string(),
                    "file_name": staged.name,
                    "display_name": it.display_name,
                    "metadata": if it.metadata.is_null() { json!({}) } else { it.metadata.clone() },
                    "change_reason": it.change_reason,
                    "batch_local": it.batch_local,
                }),
            });
        }
        let d = self.db()?;
        let mut out = Vec::new();
        for r in &rows {
            d.queue_insert(r)?;
        }
        for r in &rows {
            if let Some(q) = d.queue_get(&user, &r.id)? {
                out.push(view(&q));
            }
        }
        drop(d);
        for it in &items {
            self.unstage(&it.staged_id);
        }
        self.log.info("queue_added", &format!("{} file(s)", out.len()));
        self.emit("queue-changed", json!({ "company_id": company }));
        Ok(out)
    }

    pub fn queue_list(&self, company: Option<&str>) -> Result<Vec<QueueView>> {
        let user = self.user_id()?;
        let company = company.map(uuid_ok).transpose()?;
        Ok(self.db()?.queue_list(&user, company.as_deref())?.iter().map(view).collect())
    }

    pub fn queue_recover(&self) -> Result<usize> {
        let user = self.user_id()?;
        let n = self.db()?.queue_recover(&user)?;
        if n > 0 {
            self.log.info("queue_recovered", &format!("{n} interrupted item(s) re-queued"));
            self.emit("queue-changed", json!({}));
        }
        Ok(n)
    }

    /// Put a failed item back in the line. The file is read again from the start (no partial resume in this version).
    pub fn queue_retry(&self, id: &str) -> Result<QueueView> {
        let user = self.user_id()?;
        let id = uuid_ok(id)?;
        let d = self.db()?;
        let row = d.queue_get(&user, &id)?.ok_or_else(|| AppError::new("not_found", "Unknown queue item."))?;
        if !(row.status == "failed" && !row.error_code.as_deref().is_some_and(|c| NOT_RETRYABLE.contains(&c))) {
            return Err(AppError::new("not_retryable", "This item cannot be retried. Remove it and add the file again if needed."));
        }
        // a rejected content problem needs a new server session; a transient failure can reuse the registered one
        let transient = matches!(
            row.error_code.as_deref(),
            Some("storage_unavailable" | "scan_unavailable" | "storage_verification_failed" | "network_unavailable" | "timeout")
        );
        d.queue_requeue(&user, &id, !transient)?;
        let q = d.queue_get(&user, &id)?.ok_or_else(|| AppError::new("not_found", "Unknown queue item."))?;
        drop(d);
        self.emit("queue-changed", json!({ "company_id": q.company_id }));
        Ok(view(&q))
    }

    /// Stop an item. In flight: the transfer is interrupted. Registered on the server: the session is cancelled there too
    /// (best effort — if offline, it simply expires). A completed document is never touched.
    pub async fn queue_cancel(&self, id: &str) -> Result<QueueView> {
        let user = self.user_id()?;
        let id = uuid_ok(id)?;
        let row = self.db()?.queue_get(&user, &id)?.ok_or_else(|| AppError::new("not_found", "Unknown queue item."))?;
        if row.status == "completed" {
            return Err(AppError::new("already_completed", "This file is already imported. Archive or delete the document instead."));
        }
        if let Some(flag) = self.transfers.lock().ok().and_then(|t| t.get(&id).cloned()) {
            flag.store(true, Ordering::Relaxed);
        }
        if let Some(op) = &row.server_op_id {
            let _ = self.api.post(&format!("/companies/{}/documents/uploads/{op}/cancel", row.company_id), Some(&row.org_id), None).await;
        }
        let d = self.db()?;
        if !matches!(row.status.as_str(), "cancelled" | "quarantined") {
            d.queue_set_status(&user, &id, "cancelled", Some("cancelled"), Some("Cancelled by you."))?;
        }
        let q = d.queue_get(&user, &id)?.ok_or_else(|| AppError::new("not_found", "Unknown queue item."))?;
        drop(d);
        self.emit("queue-changed", json!({ "company_id": q.company_id }));
        Ok(view(&q))
    }

    pub fn queue_remove(&self, id: &str) -> Result<bool> {
        let user = self.user_id()?;
        let id = uuid_ok(id)?;
        let d = self.db()?;
        let row = d.queue_get(&user, &id)?;
        match row {
            Some(r) if matches!(r.status.as_str(), "uploading" | "preparing") => {
                Err(AppError::new("in_progress", "Cancel the transfer before removing it."))
            }
            Some(_) => Ok(d.queue_remove(&user, &id)?),
            None => Ok(false),
        }
    }

    pub fn queue_clear_finished(&self, company: Option<&str>) -> Result<usize> {
        let user = self.user_id()?;
        let company = company.map(uuid_ok).transpose()?;
        let n = self.db()?.queue_clear_finished(&user, company.as_deref())?;
        self.emit("queue-changed", json!({}));
        Ok(n)
    }

    // ------------------------------------------------------------------ worker
    /// Process every runnable item (waiting, or awaiting server confirmation) one after another. Safe to call at any time;
    /// a second concurrent call returns immediately. Stops early (leaving items waiting) when offline or signed out.
    pub async fn queue_run(&self) -> Result<RunSummary> {
        let Ok(_guard) = self.run_lock.try_lock() else {
            return Ok(RunSummary { stopped: Some("already_running".into()), ..Default::default() });
        };
        let user = self.user_id()?;
        let mut summary = RunSummary::default();
        self.ensure_server_batches(&user).await;
        loop {
            let next =
                self.db()?.queue_list(&user, None)?.into_iter().find(|r| matches!(r.status.as_str(), "waiting" | "verification_pending"));
            let Some(row) = next else { break };
            match self.process_one(&user, row).await {
                Ok(Outcome::Completed) => summary.completed += 1,
                Ok(Outcome::Failed) => summary.failed += 1,
                Ok(Outcome::Quarantined) => summary.quarantined += 1,
                Ok(Outcome::Cancelled) => summary.cancelled += 1,
                Ok(Outcome::Deferred) => {
                    summary.stopped = Some("offline".into());
                    break;
                }
                Err(e) if e.is_session_expired() => {
                    summary.stopped = Some("session_expired".into());
                    break;
                }
                Err(e) => return Err(e),
            }
        }
        summary.waiting =
            self.db()?.queue_list(&user, None)?.iter().filter(|r| matches!(r.status.as_str(), "waiting" | "verification_pending")).count();
        Ok(summary)
    }

    /// One import batch per `batch_local` group on the server (best effort: without it files are simply unbatched).
    async fn ensure_server_batches(&self, user: &str) {
        let Ok(rows) = self.db().and_then(|d| d.queue_list(user, None)) else { return };
        let mut groups: std::collections::BTreeMap<(String, String, String), Vec<String>> = Default::default();
        for r in rows.iter().filter(|r| r.status == "waiting" && r.server_batch_id.is_none() && r.server_op_id.is_none()) {
            if let Some(local) = r.private.get("batch_local").and_then(Value::as_str) {
                groups.entry((r.org_id.clone(), r.company_id.clone(), local.to_string())).or_default().push(r.id.clone());
            }
        }
        for ((org, company, local), ids) in groups {
            let body = json!({ "total_files": ids.len().clamp(1, 1000), "source": "desktop", "client_batch_key": local });
            if let Ok(res) = self.api.post(&format!("/companies/{company}/documents/import-batches"), Some(&org), Some(body)).await {
                if let Some(bid) = res.body.get("id").and_then(Value::as_str) {
                    let _ = self.db().and_then(|d| d.queue_set_batch(user, &ids, bid));
                }
            }
        }
    }

    fn flag(&self, id: &str) -> Arc<AtomicBool> {
        let f = Arc::new(AtomicBool::new(false));
        if let Ok(mut t) = self.transfers.lock() {
            t.insert(id.into(), f.clone());
        }
        f
    }
    fn unflag(&self, id: &str) {
        if let Ok(mut t) = self.transfers.lock() {
            t.remove(id);
        }
    }

    fn set(&self, user: &str, row: &QueueRow, status: &str, code: Option<&str>, msg: Option<&str>) -> Result<()> {
        self.db()?.queue_set_status(user, &row.id, status, code, msg)?;
        self.emit("queue-changed", json!({ "company_id": row.company_id, "id": row.id, "status": status }));
        Ok(())
    }
    fn fail(&self, user: &str, row: &QueueRow, code: &str, msg: &str, detail: Option<Value>) -> Result<Outcome> {
        let quarantined = code == "quarantined";
        if let Some(d) = detail {
            let _ = d; // structured extras (e.g. the duplicate list) are kept in the sealed private record below
        }
        self.set(user, row, if quarantined { "quarantined" } else { "failed" }, Some(code), Some(msg))?;
        self.log.warn("upload_failed", code);
        let _ = self.db()?.add_event(
            user,
            "warn",
            "upload_failed",
            &format!("{}: {msg}", row.private.get("file_name").and_then(Value::as_str).unwrap_or("A file")),
        );
        Ok(if quarantined { Outcome::Quarantined } else { Outcome::Failed })
    }
    /// Transient trouble: keep the item waiting, count the attempt, give up (visibly) after a few tries.
    fn defer(&self, user: &str, row: &QueueRow, e: &AppError) -> Result<Outcome> {
        self.db()?.queue_bump_attempts(user, &row.id)?;
        if row.attempts + 1 >= MAX_ATTEMPTS {
            return self.fail(user, row, &e.code, &e.message, None);
        }
        let status = if row.status == "verification_pending" { "verification_pending" } else { "waiting" };
        self.set(user, row, status, Some(&e.code), Some(&e.message))?;
        Ok(Outcome::Deferred)
    }

    async fn process_one(&self, user: &str, row: QueueRow) -> Result<Outcome> {
        let id = row.id.clone();
        let cancel = self.flag(&id);
        let result = self.process_inner(user, row, &cancel).await;
        self.unflag(&id);
        if result.is_err() {
            // an aborted run (session ended) must leave the item waiting, never stuck in a transient state
            let _ = self.db().and_then(|d| {
                if let Some(q) = d.queue_get(user, &id)? {
                    if matches!(q.status.as_str(), "preparing" | "uploading") {
                        d.queue_set_status(user, &id, "waiting", None, None)?;
                    }
                }
                Ok(())
            });
        }
        result
    }

    async fn process_inner(&self, user: &str, mut row: QueueRow, cancel: &Arc<AtomicBool>) -> Result<Outcome> {
        let company = row.company_id.clone();
        let org = row.org_id.clone();
        let base = format!("/companies/{company}/documents/uploads");
        let confirm_only = row.status == "verification_pending" && row.server_op_id.is_some();
        if !confirm_only {
            self.set(user, &row, "preparing", None, None)?;
            // 1. the file must still be what the user added
            let path = PathBuf::from(row.private.get("source_path").and_then(Value::as_str).unwrap_or_default());
            let md = match std::fs::metadata(&path) {
                Ok(m) if m.is_file() => m,
                _ => {
                    return self.fail(
                        user,
                        &row,
                        "source_missing",
                        "The file was moved, renamed or deleted after it was added. Add it again.",
                        None,
                    )
                }
            };
            if md.len() as i64 != row.size || (row.mtime.is_some() && mtime_of(&md) != row.mtime) {
                let p2 = path.clone();
                let now_sha = tokio::task::spawn_blocking(move || hash_file(&p2)).await.ok().and_then(|r| r.ok());
                if now_sha.as_deref() != row.sha256.as_deref() {
                    return self.fail(user, &row, "source_changed", "The file was changed after it was added. Add it again.", None);
                }
                self.db()?.queue_set_sha(user, &row.id, row.sha256.as_deref().unwrap_or_default(), md.len() as i64, mtime_of(&md))?;
            }
            // 2. register (idempotent: the same key always yields the same server session)
            let op = match &row.server_op_id {
                Some(op_id) => self.api.get(&format!("{base}/{op_id}"), Some(&org)).await,
                None => {
                    let p = &row.private;
                    let body = json!({
                        "idempotency_key": row.idempotency_key,
                        "filename": p.get("file_name"),
                        "size": row.size,
                        "sha256": row.sha256,
                        "node_id": row.node_id,
                        "category_id": row.category_id,
                        "display_name": p.get("display_name"),
                        "metadata": p.get("metadata").cloned().unwrap_or(json!({})),
                        "filing_rule_id": row.filing_rule_id,
                        "duplicate_policy": row.duplicate_policy,
                        "target_document_id": row.target_document_id,
                        "change_reason": p.get("change_reason"),
                        "batch_id": row.server_batch_id,
                    });
                    self.api.post(&base, Some(&org), Some(body)).await
                }
            };
            let op = match op {
                Ok(r) => r.body,
                Err(e) if e.is_session_expired() => return Err(e),
                Err(e) if e.retryable => return self.defer(user, &row, &e),
                Err(e) => return self.fail_from(user, &row, &e),
            };
            let op_id = op.get("id").and_then(Value::as_str).ok_or_else(|| AppError::malformed("upload"))?.to_string();
            self.db()?.queue_set_server(user, &row.id, None, &op_id)?;
            row.server_op_id = Some(op_id.clone());
            match op.get("status").and_then(Value::as_str).unwrap_or_default() {
                "completed" => {
                    if let Some(doc) = op.get("result_document_id").and_then(Value::as_str) {
                        self.db()?.queue_set_result(user, &row.id, doc)?;
                        self.emit("queue-changed", json!({ "company_id": company, "id": row.id, "status": "completed" }));
                        return Ok(Outcome::Completed);
                    }
                }
                "uploaded" => {} // bytes already received by the server: only confirmation is missing
                "awaiting_upload" | "uploading" => {
                    if let Some(out) = self.send_bytes(user, &mut row, &path, &base, &org, cancel, &op).await? {
                        return Ok(out);
                    }
                }
                "expired" | "cancelled" => {
                    self.db()?.queue_reset_session(user, &row.id, &format!("dq-{}", uuid::Uuid::new_v4().simple()))?;
                    return Ok(Outcome::Deferred);
                }
                other => {
                    let code = op.get("error_code").and_then(Value::as_str).unwrap_or(other).to_string();
                    let msg =
                        op.get("error_message").and_then(Value::as_str).unwrap_or("The server could not accept this file.").to_string();
                    return self.fail(user, &row, if other == "quarantined" { "quarantined" } else { &code }, &msg, None);
                }
            }
            if cancel.load(Ordering::Relaxed) {
                let _ = self.api.post(&format!("{base}/{op_id}/cancel"), Some(&org), None).await;
                self.set(user, &row, "cancelled", Some("cancelled"), Some("Cancelled by you."))?;
                return Ok(Outcome::Cancelled);
            }
            self.set(user, &row, "verification_pending", None, None)?;
            row.status = "verification_pending".into();
        }
        // 3. confirmation: only now does the server create the document
        let op_id = row.server_op_id.clone().ok_or_else(|| AppError::malformed("upload"))?;
        match self.api.post(&format!("{base}/{op_id}/complete"), Some(&org), None).await {
            Ok(r) => {
                let doc = r.body.pointer("/document/id").and_then(Value::as_str).ok_or_else(|| AppError::malformed("complete"))?;
                self.db()?.queue_set_result(user, &row.id, doc)?;
                self.log.info("upload_completed", "document stored and verified");
                self.emit("queue-changed", json!({ "company_id": company, "id": row.id, "status": "completed" }));
                self.emit("documents-changed", json!({ "company_id": company }));
                Ok(Outcome::Completed)
            }
            Err(e) if e.is_session_expired() => Err(e),
            Err(e) if e.retryable => self.defer(user, &row, &e),
            Err(e) => self.fail_from(user, &row, &e),
        }
    }

    /// Stream the bytes. `Ok(None)`: sent, continue to confirmation. `Ok(Some(outcome))`: this item is finished/deferred.
    #[allow(clippy::too_many_arguments)]
    async fn send_bytes(
        &self,
        user: &str,
        row: &mut QueueRow,
        path: &std::path::Path,
        base: &str,
        org: &str,
        cancel: &Arc<AtomicBool>,
        op: &Value,
    ) -> Result<Option<Outcome>> {
        let op_id = row.server_op_id.clone().unwrap_or_default();
        self.set(user, row, "uploading", None, None)?;
        let sent = Arc::new(AtomicU64::new(0));
        let progress: Arc<dyn Fn(u64) + Send + Sync> = {
            let sent = sent.clone();
            Arc::new(move |n| sent.store(n, Ordering::Relaxed))
        };
        // Big files go in consecutive chunks the server can resume (it counts the bytes it holds); small files in one request.
        let chunk_max = op.get("chunk_max_bytes").and_then(Value::as_u64).unwrap_or(0);
        let put = async {
            if chunk_max > 0 && row.size as u64 >= CHUNKED_FROM {
                let held = op.get("received_bytes").and_then(Value::as_u64).unwrap_or(0);
                self.send_chunks(&format!("{base}/{op_id}"), org, path, row.size as u64, held, chunk_max, cancel, progress.clone()).await
            } else {
                let url = format!("{base}/{op_id}/content");
                self.api.put_file(&url, Some(org), path, row.size as u64, cancel.clone(), progress.clone()).await
            }
        };
        tokio::pin!(put);
        let mut tick = tokio::time::interval(Duration::from_millis(300));
        let res = loop {
            tokio::select! {
                r = &mut put => break r,
                _ = tick.tick() => {
                    let n = sent.load(Ordering::Relaxed) as i64;
                    self.db()?.queue_set_progress(user, &row.id, n)?;
                    self.emit("queue-progress", json!({ "id": row.id, "company_id": row.company_id, "bytes_sent": n }));
                }
            }
        };
        match res {
            Ok(_) => {
                self.db()?.queue_set_progress(user, &row.id, row.size)?;
                Ok(None)
            }
            Err(e) if e.code == "cancelled" => {
                let _ = self.api.post(&format!("{base}/{op_id}/cancel"), Some(org), None).await;
                self.set(user, row, "cancelled", Some("cancelled"), Some("Cancelled by you."))?;
                Ok(Some(Outcome::Cancelled))
            }
            Err(e) if e.is_session_expired() => Err(e),
            Err(e) => {
                let op_status = e.detail.as_ref().and_then(|d| d.pointer("/operation/status")).and_then(Value::as_str).map(str::to_string);
                let op_code =
                    e.detail.as_ref().and_then(|d| d.pointer("/operation/error_code")).and_then(Value::as_str).map(str::to_string);
                if let Some(dups) = e.detail.as_ref().and_then(|d| d.get("duplicates")).cloned() {
                    let mut p = row.private.clone();
                    p["duplicates"] = dups;
                    self.db()?.queue_update_private(user, &row.id, &p)?;
                    row.private = p;
                }
                match op_status.as_deref() {
                    Some("quarantined") => Ok(Some(self.fail(user, row, "quarantined", &e.message, None)?)),
                    Some("failed") if e.retryable => {
                        // the server recorded a transient failure on its side: reopen the same session and try again later
                        let _ = self.api.post(&format!("{base}/{op_id}/retry"), Some(org), None).await;
                        Ok(Some(self.defer(
                            user,
                            row,
                            &AppError::new(op_code.as_deref().unwrap_or(&e.code), e.message.clone()).retryable(),
                        )?))
                    }
                    Some("failed") => Ok(Some(self.fail(user, row, op_code.as_deref().unwrap_or(&e.code), &e.message, None)?)),
                    _ if e.retryable => Ok(Some(self.defer(user, row, &e)?)),
                    _ => Ok(Some(self.fail_from(user, row, &e)?)),
                }
            }
        }
    }

    /// Resumable transfer: PATCH consecutive chunks starting where the server says it is. A 409 `offset_mismatch` carries the
    /// offset the server really holds (it may have lost staging, or an earlier chunk's answer was lost): continue from there.
    /// Gives up (retryable) after a few corrections in a row so a confused server cannot keep the loop spinning.
    #[allow(clippy::too_many_arguments)]
    async fn send_chunks(
        &self,
        op_base: &str,
        org: &str,
        path: &std::path::Path,
        size: u64,
        mut offset: u64,
        chunk_max: u64,
        cancel: &Arc<AtomicBool>,
        progress: Arc<dyn Fn(u64) + Send + Sync>,
    ) -> Result<crate::api::Response> {
        let chunk = chunk_max.clamp(1, CHUNK_TARGET);
        let mut corrections = 0;
        let mut last: Option<crate::api::Response> = None;
        while offset < size {
            if cancel.load(Ordering::Relaxed) {
                return Err(AppError::new("cancelled", "The upload was cancelled."));
            }
            let len = chunk.min(size - offset);
            let base_offset = offset;
            let p2 = progress.clone();
            let chunk_progress: Arc<dyn Fn(u64) + Send + Sync> = Arc::new(move |n| p2(base_offset + n));
            let url = format!("{op_base}/chunk?offset={offset}");
            match self.api.patch_file_range(&url, Some(org), path, offset, len, cancel.clone(), chunk_progress).await {
                Ok(r) => {
                    offset += len;
                    corrections = 0;
                    last = Some(r);
                }
                Err(e) if e.status == Some(409) && e.code == "offset_mismatch" => {
                    corrections += 1;
                    let held = e.detail.as_ref().and_then(|d| d.get("received_bytes")).and_then(Value::as_u64);
                    match held {
                        Some(h) if corrections <= 5 && h <= size => {
                            offset = h;
                            progress(h);
                        }
                        _ => {
                            return Err(AppError::new(
                                "upload_out_of_step",
                                "The server and this computer disagree about how much was sent.",
                            )
                            .retryable())
                        }
                    }
                }
                Err(e) => return Err(e),
            }
        }
        last.ok_or_else(|| AppError::new("upload_out_of_step", "Nothing was left to send.").retryable())
    }

    fn fail_from(&self, user: &str, row: &QueueRow, e: &AppError) -> Result<Outcome> {
        let (code, msg) = match e.status {
            Some(403 | 404) => (
                "destination_unavailable",
                "The destination is no longer available to you (the workspace changed or access was removed).".to_string(),
            ),
            _ => (e.code.as_str(), e.message.clone()),
        };
        if e.code == "duplicate_document" {
            if let Some(dups) = e.detail.as_ref().and_then(|d| d.get("duplicates")).cloned() {
                let mut p = row.private.clone();
                p["duplicates"] = dups;
                let _ = self.db().and_then(|d| d.queue_update_private(user, &row.id, &p));
            }
        }
        self.fail(user, row, code, &msg, None)
    }
}

enum Outcome {
    Completed,
    Failed,
    Quarantined,
    Cancelled,
    Deferred,
}
