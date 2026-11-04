//! The local import queue: every file the user asks to import is a row that survives restarts. The queue never claims a
//! document is stored: `completed` is written only after the backend confirmed storage and verification, and a restart
//! turns anything that was mid-flight back into `waiting` (the server then tells us how far the upload really got).

use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::db::{now, Db};
use crate::error::{AppError, Result};

pub const PREPARING: &str = "preparing";
pub const WAITING: &str = "waiting";
pub const UPLOADING: &str = "uploading";
pub const VERIFYING: &str = "verifying";
pub const COMPLETED: &str = "completed";
pub const FAILED: &str = "failed";
pub const CANCELLED: &str = "cancelled";
pub const QUARANTINED: &str = "quarantined";

/// Sealed with the rest of the cache: it contains the local path and the user's file and display names.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ImportMeta {
    pub source_path: String,
    pub file_name: String,
    pub display_name: Option<String>,
    pub field_values: Value,
    pub change_reason: Option<String>,
    pub filing_rule_id: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ImportRow {
    pub id: String,
    pub org_id: String,
    pub company_id: String,
    pub batch_id: Option<String>,
    pub file_name: String,
    pub display_name: Option<String>,
    pub size: i64,
    pub sha256: Option<String>,
    pub node_id: String,
    pub category_id: Option<String>,
    /// set when the import adds a NEW VERSION to this document
    pub document_id: Option<String>,
    pub duplicate_policy: String,
    pub status: String,
    pub bytes_sent: i64,
    pub attempts: i64,
    pub next_retry_at: Option<String>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub error_detail: Option<Value>,
    /// the document that was created once the backend confirmed storage
    pub result_document_id: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

/// Internal view: everything needed to run the upload.
#[derive(Debug, Clone)]
pub struct ImportJob {
    pub row: ImportRow,
    pub session_id: Option<String>,
    pub idem_key: String,
    pub meta: ImportMeta,
}

pub struct NewImport<'a> {
    pub user: &'a str,
    pub org: &'a str,
    pub company: &'a str,
    pub batch_id: Option<&'a str>,
    pub node_id: &'a str,
    pub category_id: Option<&'a str>,
    pub document_id: Option<&'a str>,
    pub size: i64,
    pub duplicate_policy: &'a str,
    pub meta: ImportMeta,
}

const COLS: &str = "id, org_id, company_id, batch_id, node_id, category_id, document_id, size, sha256, duplicate_policy, status, session_id, \
     idem_key, bytes_sent, attempts, next_retry_at, error_code, error_message, error_detail, result_document_id, meta, created_at, updated_at";

impl Db {
    fn import_aad(user: &str, id: &str, what: &str) -> String {
        format!("{user}|imports|{id}|{what}")
    }

    pub fn add_import(&self, n: NewImport) -> Result<String> {
        let id = uuid::Uuid::new_v4().to_string();
        let meta = self.sealer.seal(&Self::import_aad(n.user, &id, "meta"), serde_json::to_vec(&n.meta).unwrap_or_default().as_slice())?;
        let ts = now();
        self.conn.execute(
            "INSERT INTO imports (id, user_id, org_id, company_id, batch_id, node_id, category_id, document_id, size, duplicate_policy, status, idem_key, meta, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, 'preparing', ?11, ?12, ?13, ?13)",
            params![id, n.user, n.org, n.company, n.batch_id, n.node_id, n.category_id, n.document_id, n.size, n.duplicate_policy, uuid::Uuid::new_v4().to_string(), meta, ts],
        )?;
        Ok(id)
    }

    fn import_from_row(&self, user: &str, r: &rusqlite::Row) -> rusqlite::Result<std::result::Result<ImportJob, AppError>> {
        let id: String = r.get(0)?;
        let meta_blob: Vec<u8> = r.get(20)?;
        let detail_blob: Option<Vec<u8>> = r.get(18)?;
        let meta: ImportMeta = match self.sealer.open(&Self::import_aad(user, &id, "meta"), &meta_blob) {
            Ok(b) => serde_json::from_slice(&b).unwrap_or_default(),
            Err(e) => return Ok(Err(e)),
        };
        let detail = detail_blob
            .and_then(|b| self.sealer.open(&Self::import_aad(user, &id, "detail"), &b).ok())
            .and_then(|b| serde_json::from_slice(&b).ok());
        let row = ImportRow {
            id,
            org_id: r.get(1)?,
            company_id: r.get(2)?,
            batch_id: r.get(3)?,
            file_name: meta.file_name.clone(),
            display_name: meta.display_name.clone(),
            node_id: r.get(4)?,
            category_id: r.get(5)?,
            document_id: r.get(6)?,
            size: r.get(7)?,
            sha256: r.get(8)?,
            duplicate_policy: r.get(9)?,
            status: r.get(10)?,
            bytes_sent: r.get(13)?,
            attempts: r.get(14)?,
            next_retry_at: r.get(15)?,
            error_code: r.get(16)?,
            error_message: r.get(17)?,
            error_detail: detail,
            result_document_id: r.get(19)?,
            created_at: r.get(21)?,
            updated_at: r.get(22)?,
        };
        Ok(Ok(ImportJob { row, session_id: r.get(11)?, idem_key: r.get(12)?, meta }))
    }

    pub fn get_import(&self, user: &str, id: &str) -> Result<Option<ImportJob>> {
        let sql = format!("SELECT {COLS} FROM imports WHERE user_id = ?1 AND id = ?2");
        match self.conn.query_row(&sql, params![user, id], |r| self.import_from_row(user, r)).optional()? {
            Some(Ok(j)) => Ok(Some(j)),
            Some(Err(e)) => Err(e),
            None => Ok(None),
        }
    }

    /// Newest first. `company`/`status` narrow the list; a corrupted row is skipped (and never crashes the queue).
    pub fn list_imports(&self, user: &str, company: Option<&str>, statuses: &[&str], limit: i64) -> Result<Vec<ImportJob>> {
        let sql = format!("SELECT {COLS} FROM imports WHERE user_id = ?1 AND (?2 IS NULL OR company_id = ?2) ORDER BY created_at DESC, rowid DESC LIMIT ?3");
        let mut st = self.conn.prepare(&sql)?;
        let rows = st.query_map(params![user, company, limit], |r| self.import_from_row(user, r))?;
        let mut out = Vec::new();
        for r in rows {
            if let Ok(job) = r? {
                if statuses.is_empty() || statuses.contains(&job.row.status.as_str()) {
                    out.push(job);
                }
            }
        }
        Ok(out)
    }

    /// Uploads that should run now: waiting, and past their retry time. Oldest first.
    pub fn due_imports(&self, user: &str, limit: i64) -> Result<Vec<ImportJob>> {
        let mut all = self.list_imports(user, None, &[PREPARING, WAITING], 500)?;
        let t = now();
        all.retain(|j| j.row.next_retry_at.as_deref().is_none_or(|n| n <= t.as_str()));
        all.reverse();
        all.truncate(limit as usize);
        Ok(all)
    }

    /// Take an upload for this worker: only one worker can move it from `waiting` to `uploading`.
    pub fn claim_import(&self, user: &str, id: &str) -> Result<bool> {
        Ok(self.conn.execute(
            "UPDATE imports SET status = 'uploading', updated_at = ?3 WHERE user_id = ?1 AND id = ?2 AND status = 'waiting'",
            params![user, id, now()],
        )? > 0)
    }

    pub fn set_import_hash(&self, user: &str, id: &str, sha256: &str, size: i64) -> Result<()> {
        self.conn.execute(
            "UPDATE imports SET sha256 = ?3, size = ?4, status = CASE WHEN status = 'preparing' THEN 'waiting' ELSE status END, updated_at = ?5
             WHERE user_id = ?1 AND id = ?2",
            params![user, id, sha256, size, now()],
        )?;
        Ok(())
    }

    pub fn set_import_status(&self, user: &str, id: &str, status: &str) -> Result<()> {
        self.conn
            .execute("UPDATE imports SET status = ?3, updated_at = ?4 WHERE user_id = ?1 AND id = ?2", params![user, id, status, now()])?;
        Ok(())
    }

    pub fn set_import_session(&self, user: &str, id: &str, session: Option<&str>, bytes_sent: i64) -> Result<()> {
        self.conn.execute(
            "UPDATE imports SET session_id = ?3, bytes_sent = ?4, updated_at = ?5 WHERE user_id = ?1 AND id = ?2",
            params![user, id, session, bytes_sent, now()],
        )?;
        Ok(())
    }

    pub fn set_import_progress(&self, user: &str, id: &str, bytes_sent: i64) -> Result<()> {
        // never overwrite a cancel that arrived while a chunk was in flight
        self.conn.execute(
            "UPDATE imports SET bytes_sent = ?3, status = 'uploading', updated_at = ?4 WHERE user_id = ?1 AND id = ?2 AND status IN ('uploading', 'waiting', 'preparing')",
            params![user, id, bytes_sent, now()],
        )?;
        Ok(())
    }

    /// Record an outcome. `next_retry_at` is set only for failures that retry by themselves.
    #[allow(clippy::too_many_arguments)]
    pub fn finish_import(
        &self,
        user: &str,
        id: &str,
        status: &str,
        err: Option<&AppError>,
        next_retry_at: Option<&str>,
        count_attempt: bool,
        result_document: Option<&str>,
    ) -> Result<()> {
        let detail = match err.and_then(|e| e.detail.as_ref()) {
            Some(d) => Some(self.sealer.seal(&Self::import_aad(user, id, "detail"), d.to_string().as_bytes())?),
            None => None,
        };
        self.conn.execute(
            "UPDATE imports SET status = ?3, error_code = ?4, error_message = ?5, error_detail = ?6, next_retry_at = ?7,
               attempts = attempts + ?8, result_document_id = COALESCE(?9, result_document_id), updated_at = ?10
             WHERE user_id = ?1 AND id = ?2 AND status NOT IN ('cancelled', 'completed')",
            params![
                user,
                id,
                status,
                err.map(|e| e.code.as_str()),
                err.map(|e| e.message.as_str()),
                detail,
                next_retry_at,
                i64::from(count_attempt),
                result_document,
                now()
            ],
        )?;
        Ok(())
    }

    /// New server session needed (the old one expired or was closed): fresh idempotency key, start from zero.
    pub fn reset_import_session(&self, user: &str, id: &str) -> Result<()> {
        self.conn.execute(
            "UPDATE imports SET session_id = NULL, bytes_sent = 0, idem_key = ?3, updated_at = ?4 WHERE user_id = ?1 AND id = ?2",
            params![user, id, uuid::Uuid::new_v4().to_string(), now()],
        )?;
        Ok(())
    }

    /// The user asks to try again (optionally with another duplicate decision or another destination).
    pub fn retry_import(&self, user: &str, id: &str, duplicate_policy: Option<&str>, node: Option<(&str, Option<&str>)>) -> Result<bool> {
        let n = self.conn.execute(
            "UPDATE imports SET status = 'waiting', attempts = 0, next_retry_at = NULL, error_code = NULL, error_message = NULL, error_detail = NULL,
               duplicate_policy = COALESCE(?3, duplicate_policy), node_id = COALESCE(?4, node_id),
               category_id = CASE WHEN ?4 IS NULL THEN category_id ELSE ?5 END, updated_at = ?6
             WHERE user_id = ?1 AND id = ?2 AND status IN ('failed', 'cancelled', 'waiting')",
            params![user, id, duplicate_policy, node.map(|n| n.0), node.and_then(|n| n.1), now()],
        )?;
        Ok(n > 0)
    }

    /// Cancel an import that has not completed. Returns the server session (if any) so the caller can release it.
    pub fn cancel_import(&self, user: &str, id: &str) -> Result<Option<Option<String>>> {
        let session: Option<Option<String>> = self
            .conn
            .query_row(
                "SELECT session_id FROM imports WHERE user_id = ?1 AND id = ?2 AND status IN ('preparing', 'waiting', 'uploading', 'verifying', 'failed')",
                params![user, id],
                |r| r.get(0),
            )
            .optional()?;
        if session.is_some() {
            self.conn.execute(
                "UPDATE imports SET status = 'cancelled', next_retry_at = NULL, updated_at = ?3 WHERE user_id = ?1 AND id = ?2",
                params![user, id, now()],
            )?;
        }
        Ok(session)
    }

    pub fn remove_import(&self, user: &str, id: &str) -> Result<bool> {
        Ok(self.conn.execute(
            "DELETE FROM imports WHERE user_id = ?1 AND id = ?2 AND status IN ('completed', 'cancelled', 'failed', 'quarantined')",
            params![user, id],
        )? > 0)
    }

    /// After a restart nothing is "uploading" any more: those uploads continue from what the SERVER says it has.
    pub fn recover_imports(&self, user: &str) -> Result<usize> {
        Ok(self.conn.execute(
            "UPDATE imports SET status = 'waiting', updated_at = ?2 WHERE user_id = ?1 AND status IN ('uploading', 'verifying')",
            params![user, now()],
        )?)
    }

    pub fn import_counts(&self, user: &str) -> Result<Value> {
        let mut out = serde_json::Map::new();
        let mut st = self.conn.prepare("SELECT status, COUNT(*) FROM imports WHERE user_id = ?1 GROUP BY status")?;
        for r in st.query_map(params![user], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))? {
            let (k, v) = r?;
            out.insert(k, json!(v));
        }
        Ok(Value::Object(out))
    }

    // ------------------------------------------------------------ local copies (the mirror)
    pub fn put_local_file(&self, user: &str, company: &str, f: &LocalFile) -> Result<()> {
        let (document, version, node, rel_path, sha256, size) = (&f.document_id, &f.version_id, &f.node_id, &f.rel_path, &f.sha256, f.size);
        let blob = self.sealer.seal(&format!("{user}|local_files|{document}"), rel_path.as_bytes())?;
        self.conn.execute(
            "INSERT INTO local_files (user_id, document_id, version_id, company_id, node_id, rel_path, sha256, size, placed_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
             ON CONFLICT(user_id, document_id) DO UPDATE SET version_id = excluded.version_id, node_id = excluded.node_id, rel_path = excluded.rel_path,
               sha256 = excluded.sha256, size = excluded.size, placed_at = excluded.placed_at",
            params![user, document, version, company, node, blob, sha256, size, now()],
        )?;
        Ok(())
    }

    pub fn list_local_files(&self, user: &str, company: &str) -> Result<Vec<LocalFile>> {
        let mut st = self.conn.prepare(
            "SELECT document_id, version_id, node_id, rel_path, sha256, size FROM local_files WHERE user_id = ?1 AND company_id = ?2",
        )?;
        let rows = st.query_map(params![user, company], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, Vec<u8>>(3)?,
                r.get::<_, String>(4)?,
                r.get::<_, i64>(5)?,
            ))
        })?;
        let mut out = Vec::new();
        for r in rows {
            let (document_id, version_id, node_id, blob, sha256, size) = r?;
            if let Ok(b) = self.sealer.open(&format!("{user}|local_files|{document_id}"), &blob) {
                out.push(LocalFile { document_id, version_id, node_id, rel_path: String::from_utf8_lossy(&b).into_owned(), sha256, size });
            }
        }
        Ok(out)
    }

    pub fn get_local_file(&self, user: &str, document: &str) -> Result<Option<LocalFile>> {
        let r = self
            .conn
            .query_row("SELECT company_id FROM local_files WHERE user_id = ?1 AND document_id = ?2", params![user, document], |r| {
                r.get::<_, String>(0)
            })
            .optional()?;
        match r {
            Some(company) => Ok(self.list_local_files(user, &company)?.into_iter().find(|f| f.document_id == document)),
            None => Ok(None),
        }
    }

    pub fn delete_local_file(&self, user: &str, document: &str) -> Result<()> {
        self.conn.execute("DELETE FROM local_files WHERE user_id = ?1 AND document_id = ?2", params![user, document])?;
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct LocalFile {
    pub document_id: String,
    pub version_id: String,
    pub node_id: String,
    pub rel_path: String,
    pub sha256: String,
    pub size: i64,
}
