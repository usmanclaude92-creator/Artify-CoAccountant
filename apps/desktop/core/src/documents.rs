//! Documents on the desktop: the import/upload queue, document operations, preview, download and the local mirror.
//!
//! The backend decides everything that matters (who may see or change what, whether a file is acceptable, duplicates,
//! quotas). This module transports, remembers (so a restart or an offline period loses nothing) and reports honestly:
//! an import is `completed` only after the backend has confirmed storage and verification, and a local copy is recorded
//! only after its SHA-256 matched the server's.

use std::collections::HashMap;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::db::now;
use crate::desktop::Desktop;
use crate::error::{AppError, Result};
use crate::imports::{self, ImportJob, ImportMeta, ImportRow, LocalFile, NewImport};
use crate::mirror::{self, MoveReport, TreeNode};
use crate::storage;

const DEFAULT_CHUNK: u64 = 4 * 1024 * 1024;
const AUTO_RETRY_LIMIT: i64 = 6;
const MAX_PREVIEW_BYTES: u64 = 30 * 1024 * 1024;

// ---------------------------------------------------------------- staging: the webview never supplies a path
#[derive(Debug, Clone, Serialize)]
pub struct Staged {
    pub token: String,
    pub name: String,
    pub size: u64,
}

/// Files chosen by the user (native picker or drag-and-drop) are remembered here by an opaque token. The UI refers to a
/// file only by token, so a compromised page cannot ask the app to upload an arbitrary path.
#[derive(Default)]
pub struct Staging(Mutex<HashMap<String, PathBuf>>);

impl Staging {
    pub fn stage(&self, paths: Vec<PathBuf>) -> Vec<Staged> {
        let mut out = Vec::new();
        let Ok(mut map) = self.0.lock() else { return out };
        if map.len() > 5_000 {
            map.clear();
        }
        for p in paths {
            let Ok(meta) = std::fs::metadata(&p) else { continue };
            if !meta.is_file() {
                continue;
            }
            let token = uuid::Uuid::new_v4().simple().to_string();
            out.push(Staged {
                token: token.clone(),
                name: p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
                size: meta.len(),
            });
            map.insert(token, p);
        }
        out
    }
    pub fn take(&self, token: &str) -> Option<PathBuf> {
        self.0.lock().ok()?.remove(token)
    }
}

#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct QueueItem {
    pub token: String,
    pub node_id: String,
    #[serde(default)]
    pub category_id: Option<String>,
    #[serde(default)]
    pub display_name: Option<String>,
    #[serde(default)]
    pub field_values: Option<Value>,
    #[serde(default)]
    pub duplicate_policy: Option<String>,
    /// set to add a new VERSION to an existing document
    #[serde(default)]
    pub document_id: Option<String>,
    #[serde(default)]
    pub change_reason: Option<String>,
    #[serde(default)]
    pub filing_rule_id: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Rejected {
    pub name: String,
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct QueueResult {
    pub queued: Vec<ImportRow>,
    pub rejected: Vec<Rejected>,
}

#[derive(Debug, Clone, Serialize)]
pub struct LocalCopy {
    pub path: String,
    pub rel_path: String,
    pub size: u64,
    pub already_present: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Preview {
    pub mime: String,
    pub size: u64,
    pub sha256: String,
    pub data_base64: String,
}

/// What the UI may ask about documents. Each variant maps to exactly one backend route; ids are validated as UUIDs.
#[derive(Debug, Clone)]
pub enum DocOp {
    Limits,
    Counts,
    Storage,
    List(Vec<(String, String)>),
    Get(String),
    Versions(String),
    Events(String),
    Patch(String, Value),
    Move(String, Value),
    Archive(String),
    Restore(String),
    Delete(String, String),
    Undelete(String),
    RestoreVersion(String, i64, Option<String>),
    Suggest(Value),
    CheckDuplicate(String),
}

impl DocOp {
    /// Build an operation from the UI's request. Unknown operations and malformed arguments are refused here, so the
    /// webview can only ever reach the listed backend routes.
    pub fn parse(op: &str, args: &Value) -> Result<DocOp> {
        let s = |k: &str| args.get(k).and_then(Value::as_str).map(str::to_string);
        let need = |k: &str| s(k).ok_or_else(|| AppError::new("invalid_request", format!("Missing {k}.")));
        let obj = |keys: &[&str]| -> Value {
            let mut m = serde_json::Map::new();
            for k in keys {
                if let Some(v) = args.get(*k) {
                    m.insert((*k).to_string(), v.clone());
                }
            }
            Value::Object(m)
        };
        Ok(match op {
            "limits" => DocOp::Limits,
            "counts" => DocOp::Counts,
            "storage" => DocOp::Storage,
            "list" => DocOp::List(
                args.get("query")
                    .and_then(Value::as_object)
                    .map(|m| {
                        m.iter()
                            .filter_map(|(k, v)| {
                                Some((k.clone(), v.as_str().map(str::to_string).or_else(|| v.as_i64().map(|n| n.to_string()))?))
                            })
                            .collect()
                    })
                    .unwrap_or_default(),
            ),
            "get" => DocOp::Get(need("id")?),
            "versions" => DocOp::Versions(need("id")?),
            "events" => DocOp::Events(need("id")?),
            "patch" => DocOp::Patch(need("id")?, obj(&["display_name", "field_values", "row_version"])),
            "move" => DocOp::Move(need("id")?, obj(&["node_id", "category_id", "row_version"])),
            "archive" => DocOp::Archive(need("id")?),
            "restore" => DocOp::Restore(need("id")?),
            "delete" => DocOp::Delete(need("id")?, need("reason")?),
            "undelete" => DocOp::Undelete(need("id")?),
            "restore_version" => DocOp::RestoreVersion(
                need("id")?,
                args.get("number").and_then(Value::as_i64).ok_or_else(|| AppError::new("invalid_request", "Missing version number."))?,
                s("reason"),
            ),
            "suggest" => DocOp::Suggest(obj(&["filename", "category_id", "node_id", "field_values", "document_date"])),
            "check_duplicate" => DocOp::CheckDuplicate(need("sha256")?),
            _ => return Err(AppError::new("invalid_request", "Unknown document operation.")),
        })
    }
}

fn valid_id(id: &str) -> Result<&str> {
    uuid::Uuid::parse_str(id).map(|_| id).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

fn enc(v: &str) -> String {
    v.bytes()
        .map(|b| if b.is_ascii_alphanumeric() || b"-_.~:".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect()
}

fn sha256_file(path: &Path) -> std::io::Result<(String, u64)> {
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1024 * 1024];
    let mut total = 0u64;
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
        total += n as u64;
    }
    Ok((h.finalize().iter().map(|b| format!("{b:02x}")).collect(), total))
}

fn backoff(attempts: i64) -> chrono::Duration {
    chrono::Duration::seconds(match attempts {
        0 | 1 => 30,
        2 => 120,
        3 => 600,
        4 => 1800,
        _ => 3600,
    })
}

enum Outcome {
    Retry,
    Terminal,
    Quarantine,
    NewSession,
}

fn classify(e: &AppError) -> Outcome {
    match e.code.as_str() {
        "quarantined" | "malware_detected" => Outcome::Quarantine,
        "upload_expired" | "session_closed" => Outcome::NewSession,
        "storage_error" | "scan_unavailable" | "network_unavailable" | "timeout" | "integrity_error" => Outcome::Retry,
        _ if e.status == Some(410) => Outcome::NewSession,
        _ if e.retryable => Outcome::Retry,
        _ => Outcome::Terminal,
    }
}

impl Desktop {
    // ---------------------------------------------------------------- staging and queueing
    pub fn stage_paths(&self, paths: Vec<PathBuf>) -> Vec<Staged> {
        self.staging.stage(paths)
    }

    pub async fn document_limits(&self, org: &str, company: &str) -> Option<Value> {
        self.api.get(&format!("/companies/{}/documents/limits", valid_id(company).ok()?), Some(org)).await.ok().map(|r| r.body)
    }

    /// Put files on the queue. Nothing is uploaded here: each file becomes a durable `preparing` row (hashing and the upload
    /// happen in `process_due`), so a crash, restart or lost connection cannot lose the request.
    pub async fn queue_imports(&self, org: &str, company: &str, items: Vec<QueueItem>) -> Result<QueueResult> {
        let user = self.user_id()?;
        valid_id(company)?;
        let limits = self.document_limits(org, company).await;
        let max_size = limits.as_ref().and_then(|l| l["max_file_size_bytes"].as_u64());
        let allowed: Option<Vec<String>> = limits
            .as_ref()
            .and_then(|l| l["allowed_extensions"].as_array())
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect());
        let batch_id = if items.len() > 1 {
            self.api
                .post(
                    &format!("/companies/{company}/document-batches"),
                    Some(org),
                    Some(json!({"file_count": items.len(), "source": "desktop"})),
                )
                .await
                .ok()
                .and_then(|r| r.body["id"].as_str().map(str::to_string))
        } else {
            None
        };
        let mut result = QueueResult { queued: vec![], rejected: vec![] };
        for it in items {
            let Some(path) = self.staging.take(&it.token) else {
                result.rejected.push(Rejected {
                    name: "file".into(),
                    code: "unknown_file".into(),
                    message: "That file selection expired. Choose the file again.".into(),
                });
                continue;
            };
            let file_name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            let reject = |code: &str, message: &str| Rejected { name: file_name.clone(), code: code.into(), message: message.into() };
            let Ok(meta) = std::fs::metadata(&path) else {
                result.rejected.push(reject("file_unreadable", "The file could not be read. Is it still there?"));
                continue;
            };
            if !meta.is_file() || meta.len() == 0 {
                result
                    .rejected
                    .push(reject("empty_file", if meta.len() == 0 { "The file is empty." } else { "Only files can be imported." }));
                continue;
            }
            let ext = file_name.rsplit_once('.').map(|x| x.1.to_ascii_lowercase()).unwrap_or_default();
            let ext = match ext.as_str() {
                "jpeg" => "jpg".to_string(),
                "tiff" => "tif".to_string(),
                e => e.to_string(),
            };
            if let Some(a) = &allowed {
                let canon: Vec<String> = a
                    .iter()
                    .map(|e| match e.as_str() {
                        "jpeg" => "jpg".into(),
                        "tiff" => "tif".into(),
                        x => x.to_string(),
                    })
                    .collect();
                if !canon.contains(&ext) {
                    result.rejected.push(reject("unsupported_type", &format!("Files of this type are not accepted (.{ext}).")));
                    continue;
                }
            }
            if max_size.is_some_and(|m| meta.len() > m) {
                result.rejected.push(reject("file_too_large", "The file is larger than your plan allows."));
                continue;
            }
            let policy = it.duplicate_policy.as_deref().filter(|p| matches!(*p, "block" | "allow_copy")).unwrap_or("block");
            let id = self.db()?.add_import(NewImport {
                user: &user,
                org,
                company,
                batch_id: batch_id.as_deref(),
                node_id: valid_id(&it.node_id)?,
                category_id: it.category_id.as_deref().map(valid_id).transpose()?,
                document_id: it.document_id.as_deref().map(valid_id).transpose()?,
                size: meta.len() as i64,
                duplicate_policy: policy,
                meta: ImportMeta {
                    source_path: path.display().to_string(),
                    file_name: file_name.clone(),
                    display_name: it.display_name.clone().filter(|s| !s.trim().is_empty()),
                    field_values: it.field_values.clone().unwrap_or(json!({})),
                    change_reason: it.change_reason.clone(),
                    filing_rule_id: it.filing_rule_id.clone(),
                },
            })?;
            if let Some(job) = self.db()?.get_import(&user, &id)? {
                result.queued.push(job.row);
            }
        }
        Ok(result)
    }

    pub fn list_imports(&self, company: Option<&str>) -> Result<Vec<ImportRow>> {
        let user = self.user_id()?;
        Ok(self.db()?.list_imports(&user, company, &[], 300)?.into_iter().map(|j| j.row).collect())
    }
    pub fn cancel_import_local(&self, id: &str) -> Result<Option<Option<String>>> {
        let user = self.user_id()?;
        self.db()?.cancel_import(&user, id)
    }
    /// Cancel: stop locally at once, and release the server-side upload when reachable (otherwise it simply expires).
    pub async fn cancel_import(&self, org: &str, id: &str) -> Result<()> {
        let user = self.user_id()?;
        let job = self.db()?.get_import(&user, id)?;
        if let (Some(job), Some(Some(session))) = (job, self.cancel_import_local(id)?) {
            let _ = self.api.post(&format!("/companies/{}/uploads/{session}/cancel", job.row.company_id), Some(org), None).await;
        }
        Ok(())
    }
    pub fn retry_import(&self, id: &str, duplicate_policy: Option<&str>, node: Option<(&str, Option<&str>)>) -> Result<bool> {
        let user = self.user_id()?;
        if let Some(p) = duplicate_policy {
            if !matches!(p, "block" | "allow_copy") {
                return Err(AppError::new("invalid_choice", "Unknown duplicate choice."));
            }
        }
        self.db()?.retry_import(&user, id, duplicate_policy, node)
    }
    pub fn remove_import(&self, id: &str) -> Result<bool> {
        let user = self.user_id()?;
        self.db()?.remove_import(&user, id)
    }
    /// Called after sign-in/restore: uploads that were running when the app closed continue from the server's state.
    pub fn recover_imports(&self) -> Result<usize> {
        let user = self.user_id()?;
        self.db()?.recover_imports(&user)
    }
    pub fn import_counts(&self) -> Result<Value> {
        let user = self.user_id()?;
        self.db()?.import_counts(&user)
    }

    // ---------------------------------------------------------------- running the queue
    /// Run what is due (at most `parallel` uploads at once). Returns how many were attempted. A session that ended stops the
    /// run with `session_expired` so the UI can sign in again; queued work stays.
    pub async fn process_due(self: &Arc<Self>, parallel: usize) -> Result<usize> {
        let user = self.user_id()?;
        let jobs = self.db()?.due_imports(&user, parallel.max(1) as i64)?;
        let mut set = tokio::task::JoinSet::new();
        let n = jobs.len();
        for j in jobs {
            let me = self.clone();
            set.spawn(async move { me.run_import(&j.row.id).await });
        }
        let mut expired = false;
        while let Some(r) = set.join_next().await {
            if let Ok(Err(e)) = r {
                expired |= e.is_session_expired();
            }
        }
        if expired {
            return Err(AppError::session_expired());
        }
        Ok(n)
    }

    async fn prepare(&self, user: &str, job: &ImportJob) -> Result<()> {
        let path = PathBuf::from(&job.meta.source_path);
        let hashed = tokio::task::spawn_blocking(move || sha256_file(&path))
            .await
            .map_err(|_| AppError::new("local_io", "Preparing the file failed."))?;
        match hashed {
            Ok((sha, size)) if size > 0 => self.db()?.set_import_hash(user, &job.row.id, &sha, size as i64),
            Ok(_) => Err(AppError::new("empty_file", "The file is empty.")),
            Err(_) => Err(AppError::new("file_unreadable", "The file could not be read. Was it moved or deleted?")),
        }
    }

    /// Upload one import end to end. Errors are recorded on the row (and returned only for `session_expired`).
    pub async fn run_import(&self, id: &str) -> Result<ImportRow> {
        let user = self.user_id()?;
        let Some(mut job) = self.db()?.get_import(&user, id)? else { return Err(AppError::new("not_found", "Unknown import.")) };
        if job.row.status == imports::PREPARING {
            if let Err(e) = self.prepare(&user, &job).await {
                self.db()?.finish_import(&user, id, imports::FAILED, Some(&e), None, false, None)?;
                return self.row(&user, id);
            }
            job = self.db()?.get_import(&user, id)?.ok_or_else(|| AppError::new("not_found", "Unknown import."))?;
        }
        if !self.db()?.claim_import(&user, id)? {
            return self.row(&user, id); // another worker has it, or it was cancelled
        }
        match self.upload(&user, &job).await {
            Ok(Some(doc_id)) => {
                self.db()?.finish_import(&user, id, imports::COMPLETED, None, None, false, Some(&doc_id))?;
                self.log.info("import_completed", "a document was stored and verified by the server");
                let _ = self.db()?.add_event(&user, "info", "import_completed", &format!("Imported {}", job.meta.file_name));
            }
            Ok(None) => {} // cancelled while running
            Err(e) if e.is_session_expired() => {
                self.db()?.set_import_status(&user, id, imports::WAITING)?;
                return Err(e);
            }
            Err(e) => {
                let attempts = job.row.attempts + 1;
                match classify(&e) {
                    Outcome::Quarantine => self.db()?.finish_import(&user, id, imports::QUARANTINED, Some(&e), None, false, None)?,
                    Outcome::NewSession => {
                        self.db()?.reset_import_session(&user, id)?;
                        self.db()?.finish_import(&user, id, imports::WAITING, None, None, true, None)?;
                    }
                    Outcome::Retry if attempts < AUTO_RETRY_LIMIT => {
                        let next = (chrono::Utc::now() + backoff(attempts)).to_rfc3339();
                        let shown = if e.code == "network_unavailable" || e.code == "timeout" {
                            AppError::new(
                                &e.code,
                                "The cloud cannot be reached, so this document is not stored yet. It will be retried automatically.",
                            )
                        } else {
                            e.clone()
                        };
                        self.db()?.finish_import(&user, id, imports::WAITING, Some(&shown), Some(&next), true, None)?;
                    }
                    _ => self.db()?.finish_import(&user, id, imports::FAILED, Some(&e), None, true, None)?,
                }
            }
        }
        self.row(&user, id)
    }

    fn row(&self, user: &str, id: &str) -> Result<ImportRow> {
        self.db()?.get_import(user, id)?.map(|j| j.row).ok_or_else(|| AppError::new("not_found", "Unknown import."))
    }

    fn is_cancelled(&self, user: &str, id: &str) -> bool {
        self.db().ok().and_then(|d| d.get_import(user, id).ok().flatten()).is_none_or(|j| j.row.status == imports::CANCELLED)
    }

    /// Returns the created document id, or `None` when the user cancelled meanwhile.
    async fn upload(&self, user: &str, job: &ImportJob) -> Result<Option<String>> {
        let (org, company, id) = (job.row.org_id.as_str(), job.row.company_id.as_str(), job.row.id.as_str());
        let path = PathBuf::from(&job.meta.source_path);
        let meta = std::fs::metadata(&path)
            .map_err(|_| AppError::new("file_unreadable", "The file could not be read. Was it moved or deleted?"))?;
        if meta.len() as i64 != job.row.size {
            return Err(AppError::new("file_changed", "The file changed after it was added. Remove it and add it again."));
        }
        let limits = self.document_limits(org, company).await;
        let chunk = limits.as_ref().and_then(|l| l["chunk_max_bytes"].as_u64()).unwrap_or(DEFAULT_CHUNK).clamp(64 * 1024, DEFAULT_CHUNK);
        // 1. the server-side upload (idempotent: the same key always returns the same session)
        let mut sid = job.session_id.clone();
        if sid.is_none() {
            let device = self.db()?.get_device(user, org)?.map(|d| d.device_id);
            let mut body = json!({
                "filename": job.meta.file_name, "size": job.row.size, "sha256": job.row.sha256, "node_id": job.row.node_id,
                "category_id": job.row.category_id, "source": "desktop", "display_name": job.meta.display_name,
                "field_values": job.meta.field_values, "duplicate_policy": job.row.duplicate_policy,
                "change_reason": job.meta.change_reason, "filing_rule_id": job.meta.filing_rule_id, "device_id": device, "batch_id": job.row.batch_id,
            });
            if let Some(d) = &job.row.document_id {
                body["document_id"] = json!(d);
            }
            let res = self.api.post_keyed(&format!("/companies/{company}/uploads"), Some(org), body, &job.idem_key).await?;
            let new = res.body["id"].as_str().ok_or_else(|| AppError::malformed("upload session"))?.to_string();
            self.db()?.set_import_session(user, id, Some(&new), 0)?;
            sid = Some(new);
        }
        let sid = sid.unwrap_or_default();
        // 2. ask the SERVER how much it has (never trust our own counter after a crash)
        let state = self.api.get(&format!("/companies/{company}/uploads/{sid}"), Some(org)).await?;
        match state.body["status"].as_str() {
            Some("open" | "uploading") => {}
            Some("completed") => return Ok(state.body["result_document_id"].as_str().map(str::to_string)),
            _ => return Err(AppError::new("session_closed", "The upload session ended.").with_status(410)),
        }
        let mut offset = state.body["received_bytes"].as_u64().unwrap_or(0);
        // 3. send the rest in chunks
        let mut stalls = 0;
        while offset < job.row.size as u64 {
            if self.is_cancelled(user, id) {
                return Ok(None);
            }
            let want = chunk.min(job.row.size as u64 - offset) as usize;
            let p = path.clone();
            let bytes = tokio::task::spawn_blocking(move || -> std::io::Result<Vec<u8>> {
                let mut f = std::fs::File::open(p)?;
                f.seek(SeekFrom::Start(offset))?;
                let mut buf = vec![0u8; want];
                f.read_exact(&mut buf)?;
                Ok(buf)
            })
            .await
            .map_err(|_| AppError::new("local_io", "Reading the file failed."))?
            .map_err(|_| AppError::new("file_unreadable", "The file could not be read. Was it moved or deleted?"))?;
            match self.api.put_chunk(&format!("/companies/{company}/uploads/{sid}/content?offset={offset}"), Some(org), bytes).await {
                Ok(r) => offset = r.body["received_bytes"].as_u64().unwrap_or(offset + want as u64),
                Err(e) if e.code == "offset_mismatch" => {
                    // the server has more or less than we thought (a lost response): continue from ITS offset
                    stalls += 1;
                    let seen = e.detail.as_ref().and_then(|d| d["received_bytes"].as_u64());
                    match seen {
                        Some(s) if stalls < 5 => offset = s,
                        _ => return Err(e),
                    }
                }
                Err(e) => return Err(e),
            }
            self.db()?.set_import_progress(user, id, offset as i64)?;
        }
        if self.is_cancelled(user, id) {
            return Ok(None);
        }
        // 4. only now ask the server to verify and store. "Uploaded" is not "stored": wait for its answer.
        self.db()?.set_import_status(user, id, imports::VERIFYING)?;
        let done = self.api.post(&format!("/companies/{company}/uploads/{sid}/complete"), Some(org), None).await?;
        let doc = done.body["document"]["id"].as_str().or_else(|| done.body["session"]["result_document_id"].as_str());
        doc.map(|d| Some(d.to_string())).ok_or_else(|| AppError::malformed("upload completion"))
    }

    // ---------------------------------------------------------------- document operations
    pub async fn doc_op(&self, org: &str, company: &str, op: DocOp) -> Result<Value> {
        valid_id(company)?;
        let base = format!("/companies/{company}/documents");
        let r = match op {
            DocOp::Limits => self.api.get(&format!("{base}/limits"), Some(org)).await,
            DocOp::Counts => self.api.get(&format!("{base}/counts"), Some(org)).await,
            DocOp::Storage => self.api.get(&format!("{base}/storage"), Some(org)).await,
            DocOp::List(q) => {
                let allowed = [
                    "q",
                    "node_id",
                    "include_subfolders",
                    "category_id",
                    "status",
                    "family",
                    "ext",
                    "uploaded_by",
                    "created_from",
                    "created_to",
                    "field",
                    "sort",
                    "order",
                    "limit",
                    "offset",
                ];
                let qs: Vec<String> =
                    q.iter().filter(|(k, _)| allowed.contains(&k.as_str())).map(|(k, v)| format!("{k}={}", enc(v))).collect();
                self.api
                    .get(&format!("{base}{}", if qs.is_empty() { String::new() } else { format!("?{}", qs.join("&")) }), Some(org))
                    .await
            }
            DocOp::Get(d) => self.api.get(&format!("{base}/{}", valid_id(&d)?), Some(org)).await,
            DocOp::Versions(d) => self.api.get(&format!("{base}/{}/versions", valid_id(&d)?), Some(org)).await,
            DocOp::Events(d) => self.api.get(&format!("{base}/{}/events", valid_id(&d)?), Some(org)).await,
            DocOp::Patch(d, body) => self.api.patch(&format!("{base}/{}", valid_id(&d)?), Some(org), body).await,
            DocOp::Move(d, body) => self.api.post(&format!("{base}/{}/move", valid_id(&d)?), Some(org), Some(body)).await,
            DocOp::Archive(d) => self.api.post(&format!("{base}/{}/archive", valid_id(&d)?), Some(org), None).await,
            DocOp::Restore(d) => self.api.post(&format!("{base}/{}/restore", valid_id(&d)?), Some(org), None).await,
            DocOp::Delete(d, reason) => {
                self.api.post(&format!("{base}/{}/delete", valid_id(&d)?), Some(org), Some(json!({"reason": reason}))).await
            }
            DocOp::Undelete(d) => self.api.post(&format!("{base}/{}/undelete", valid_id(&d)?), Some(org), None).await,
            DocOp::RestoreVersion(d, n, reason) => {
                self.api.post(&format!("{base}/{}/versions/{n}/restore", valid_id(&d)?), Some(org), Some(json!({"reason": reason}))).await
            }
            DocOp::Suggest(body) => self.api.post(&format!("{base}/suggest"), Some(org), Some(body)).await,
            DocOp::CheckDuplicate(sha) => self.api.post(&format!("{base}/check-duplicate"), Some(org), Some(json!({"sha256": sha}))).await,
        };
        r.map(|x| x.body).map_err(|e| self.classify(e))
    }

    /// Bytes for the in-app viewer (images, PDF). Verified against the server's checksum; capped so a huge file is
    /// downloaded or opened externally instead of being held in memory.
    pub async fn preview_document(&self, org: &str, company: &str, doc: &str, version: Option<i64>) -> Result<Preview> {
        let detail = self
            .api
            .get(&format!("/companies/{}/documents/{}", valid_id(company)?, valid_id(doc)?), Some(org))
            .await
            .map_err(|e| self.classify(e))?;
        let size = detail.body["size_bytes"].as_u64().unwrap_or(0);
        if version.is_none() && size > MAX_PREVIEW_BYTES {
            return Err(AppError::new(
                "preview_too_large",
                "This file is too large to preview here. Open it with its own application instead.",
            ));
        }
        let tmp = self.temp_file()?;
        let path = format!(
            "/companies/{company}/documents/{doc}/content?inline=true{}",
            version.map(|v| format!("&version={v}")).unwrap_or_default()
        );
        let got = self.api.download_to(&path, Some(org), &tmp).await.map_err(|e| self.classify(e));
        let out = match got {
            Ok(d) if d.size <= MAX_PREVIEW_BYTES => {
                let bytes = std::fs::read(&tmp).map_err(|_| AppError::new("local_io", "The preview could not be read."));
                bytes.map(|b| Preview { mime: d.content_type, size: d.size, sha256: d.sha256, data_base64: STANDARD.encode(b) })
            }
            Ok(_) => {
                Err(AppError::new("preview_too_large", "This file is too large to preview here. Open it with its own application instead."))
            }
            Err(e) => Err(e),
        };
        let _ = std::fs::remove_file(&tmp);
        out
    }

    fn temp_file(&self) -> Result<PathBuf> {
        let dir = self.data_dir.join("tmp");
        std::fs::create_dir_all(&dir).map_err(|_| AppError::new("local_io", "The app's temporary folder could not be created."))?;
        Ok(dir.join(format!("{}.part", uuid::Uuid::new_v4().simple())))
    }

    /// Remove leftovers of interrupted downloads/previews (called at start-up and sign-out).
    pub fn clean_temp(&self) {
        let _ = std::fs::remove_dir_all(self.data_dir.join("tmp"));
    }

    // ---------------------------------------------------------------- the local mirror
    fn tree(&self, user: &str, company: &str) -> Result<Vec<TreeNode>> {
        let ws = self
            .db()?
            .get_workspace(user, company)?
            .ok_or_else(|| AppError::new("workspace_unavailable", "Open the company's workspace first."))?;
        Ok(ws.data["nodes"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|n| {
                        Some(TreeNode {
                            id: n["id"].as_str()?.to_string(),
                            parent_id: n["parent_id"].as_str().map(str::to_string),
                            name: n["name"].as_str()?.to_string(),
                            position: n["position"].as_i64().unwrap_or(0),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default())
    }

    fn usable_root(&self) -> Result<PathBuf> {
        let root =
            self.storage_root()?.ok_or_else(|| AppError::new("storage_not_configured", "Choose a storage location in Settings first."))?;
        let report = storage::inspect_root(&root, false)?;
        match report.status.as_str() {
            "ok" | "low_space" => Ok(root),
            "missing" => Err(AppError::filesystem("storage_unavailable", "The storage folder is missing or its drive is disconnected.")),
            _ => Err(AppError::filesystem("storage_unavailable", "The storage folder is not usable.")),
        }
    }

    /// Save a document (default: its current version) into the company's local folder, placed by its CLOUD folder.
    pub async fn download_document(&self, org: &str, company: &str, doc: &str, version: Option<i64>) -> Result<LocalCopy> {
        let user = self.user_id()?;
        valid_id(company)?;
        valid_id(doc)?;
        let root = self.usable_root()?;
        let detail = self.api.get(&format!("/companies/{company}/documents/{doc}"), Some(org)).await.map_err(|e| self.classify(e))?.body;
        let (node, name) =
            (detail["node_id"].as_str().unwrap_or_default().to_string(), detail["display_name"].as_str().unwrap_or("document").to_string());
        let wanted_sha = match version {
            Some(v) => detail["versions"]
                .as_array()
                .and_then(|a| a.iter().find(|x| x["version_number"].as_i64() == Some(v)))
                .and_then(|x| x["sha256"].as_str())
                .map(str::to_string),
            None => detail["sha256"].as_str().map(str::to_string),
        };
        // already here with the right content: do not download or create a second copy
        if let Some(local) = self.db()?.get_local_file(&user, doc)? {
            let p = mirror::resolve_under(&root, &local.rel_path)?;
            if p.is_file() && wanted_sha.as_deref() == Some(local.sha256.as_str()) {
                return Ok(LocalCopy {
                    path: p.display().to_string(),
                    rel_path: local.rel_path,
                    size: local.size as u64,
                    already_present: true,
                });
            }
        }
        let tmp = self.temp_file()?;
        let path = format!("/companies/{company}/documents/{doc}/content{}", version.map(|v| format!("?version={v}")).unwrap_or_default());
        let got = self.api.download_to(&path, Some(org), &tmp).await.map_err(|e| {
            let _ = std::fs::remove_file(&tmp);
            self.classify(e)
        })?;
        self.ensure_company_dir(org, company)?;
        let dir_name = self.db()?.dir_name_for(&user, company)?.ok_or_else(|| AppError::new("not_found", "Unknown company."))?;
        let nodes = self.tree(&user, company)?;
        let segments = match mirror::node_segments(&nodes, &node) {
            Some(s) => s,
            None => {
                let _ = self.refresh_workspace(org, company).await; // the cached tree may predate this folder
                mirror::node_segments(&self.tree(&user, company)?, &node).ok_or_else(|| {
                    AppError::new("workspace_unavailable", "The document's folder is not in the downloaded workspace yet.")
                })?
            }
        };
        let placed = mirror::place_file(&root, &dir_name, &segments, &name, &tmp).inspect_err(|_| {
            let _ = std::fs::remove_file(&tmp);
        })?;
        let version_id = detail["current_version_id"].as_str().unwrap_or_default();
        self.db()?.put_local_file(
            &user,
            company,
            &LocalFile {
                document_id: doc.into(),
                version_id: version_id.into(),
                node_id: node.clone(),
                rel_path: placed.rel_path.clone(),
                sha256: got.sha256.clone(),
                size: got.size as i64,
            },
        )?;
        Ok(LocalCopy { path: placed.abs_path.display().to_string(), rel_path: placed.rel_path, size: got.size, already_present: false })
    }

    /// After the cloud folder tree changed, move the local files to where the tree now says they belong (rename, never copy).
    pub async fn mirror_reconcile(&self, org: &str, company: &str) -> Result<MoveReport> {
        let user = self.user_id()?;
        let root = self.usable_root()?;
        let _ = self.refresh_workspace(org, company).await;
        let dir_name = self.db()?.dir_name_for(&user, company)?.ok_or_else(|| AppError::new("not_found", "Unknown company."))?;
        let nodes = self.tree(&user, company)?;
        let files = self.db()?.list_local_files(&user, company)?;
        let moves = mirror::plan_moves(&files, &nodes, &dir_name);
        let (report, new_paths) = mirror::apply_moves(&root, &moves)?;
        for (doc, rel) in new_paths {
            if let Some(f) = files.iter().find(|f| f.document_id == doc) {
                self.db()?.put_local_file(&user, company, &LocalFile { rel_path: rel, ..f.clone() })?;
            }
        }
        Ok(report)
    }

    /// How the local copy of one company looks right now (drive present? files missing?).
    pub fn mirror_status(&self, company: &str) -> Result<Value> {
        let user = self.user_id()?;
        let root = self.storage_root()?;
        let report = match &root {
            Some(r) => Some(storage::inspect_root(r, false)?),
            None => None,
        };
        let files = self.db()?.list_local_files(&user, company)?;
        let missing = match (&root, report.as_ref().is_some_and(|r| r.status == "ok" || r.status == "low_space")) {
            (Some(r), true) => files.iter().filter(|f| mirror::resolve_under(r, &f.rel_path).map(|p| !p.is_file()).unwrap_or(true)).count(),
            _ => 0,
        };
        let policy = self.db()?.get_setting(&user, "mirror_policy")?.unwrap_or_else(|| "on_demand".into());
        Ok(
            json!({"root": report, "files": files.len(), "missing": missing, "available": report.as_ref().is_some_and(|r| r.status == "ok" || r.status == "low_space"), "policy": policy}),
        )
    }

    /// Download documents the policy asks for (`recent`: the last 30 days, `all`: everything) that are not here yet.
    /// Bounded per run so a large company never blocks the app; call again to continue.
    pub async fn mirror_sync(&self, org: &str, company: &str, max_files: usize) -> Result<Value> {
        let user = self.user_id()?;
        let policy = self.db()?.get_setting(&user, "mirror_policy")?.unwrap_or_else(|| "on_demand".into());
        if policy == "on_demand" {
            return Ok(json!({"policy": policy, "downloaded": 0, "remaining": 0}));
        }
        self.usable_root()?;
        let have: std::collections::HashSet<String> =
            self.db()?.list_local_files(&user, company)?.into_iter().map(|f| f.document_id).collect();
        let mut query = vec![
            ("limit".to_string(), "100".to_string()),
            ("sort".to_string(), "created".to_string()),
            ("order".to_string(), "desc".to_string()),
        ];
        if policy == "recent" {
            query.push(("created_from".into(), (chrono::Utc::now() - chrono::Duration::days(30)).to_rfc3339()));
        }
        let listing = self.doc_op(org, company, DocOp::List(query)).await?;
        let wanted: Vec<String> = listing["items"]
            .as_array()
            .map(|a| a.iter().filter_map(|d| d["id"].as_str().map(str::to_string)).filter(|id| !have.contains(id)).collect())
            .unwrap_or_default();
        let mut downloaded = 0usize;
        for id in wanted.iter().take(max_files) {
            self.download_document(org, company, id, None).await?;
            downloaded += 1;
        }
        Ok(json!({"policy": policy, "downloaded": downloaded, "remaining": wanted.len().saturating_sub(downloaded)}))
    }

    /// Where an opened document lives: the company folder when a storage location is configured, else a temp copy.
    pub async fn open_document_path(&self, org: &str, company: &str, doc: &str, version: Option<i64>) -> Result<String> {
        if self.usable_root().is_ok() {
            return Ok(self.download_document(org, company, doc, version).await?.path);
        }
        let tmp_dir = self.data_dir.join("tmp").join("open");
        std::fs::create_dir_all(&tmp_dir).map_err(|_| AppError::new("local_io", "The app's temporary folder could not be created."))?;
        let detail = self
            .api
            .get(&format!("/companies/{}/documents/{}", valid_id(company)?, valid_id(doc)?), Some(org))
            .await
            .map_err(|e| self.classify(e))?
            .body;
        let name = mirror::safe_file_name(detail["display_name"].as_str().unwrap_or("document"));
        let dir = tmp_dir.join(uuid::Uuid::new_v4().simple().to_string());
        std::fs::create_dir_all(&dir).map_err(|_| AppError::new("local_io", "The app's temporary folder could not be created."))?;
        let dest = dir.join(name);
        let path = format!("/companies/{company}/documents/{doc}/content{}", version.map(|v| format!("?version={v}")).unwrap_or_default());
        self.api.download_to(&path, Some(org), &dest).await.map_err(|e| self.classify(e))?;
        Ok(dest.display().to_string())
    }

    pub fn now_marker(&self) -> String {
        now()
    }
}
