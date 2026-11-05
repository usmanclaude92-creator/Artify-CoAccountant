//! Local SQLite: settings, cached identity/organizations/companies/workspace, sync metadata, device registration and the
//! pending-operation queue skeleton. The cloud is the source of truth: everything here is a cache or local metadata and
//! is never consulted for authorization. Sensitive cached payloads are sealed (AES-256-GCM) per user + row.

use std::path::{Path, PathBuf};

use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

use crate::error::{AppError, Result};
use crate::seal::Sealer;

/// Forward-only migrations. Never edit a shipped entry; append a new one.
pub const MIGRATIONS: &[(i64, &str)] = &[
    (
        1,
        "CREATE TABLE settings (
            user_id TEXT NOT NULL DEFAULT '', key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key));
         CREATE TABLE accounts (user_id TEXT PRIMARY KEY, payload BLOB NOT NULL, updated_at TEXT NOT NULL);
         CREATE TABLE organizations (
            user_id TEXT NOT NULL, org_id TEXT NOT NULL, position INTEGER NOT NULL, payload BLOB NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, org_id));
         CREATE TABLE companies (
            user_id TEXT NOT NULL, org_id TEXT NOT NULL, company_id TEXT NOT NULL, status TEXT NOT NULL,
            favorite INTEGER NOT NULL DEFAULT 0, last_opened_at TEXT, payload BLOB NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, company_id));
         CREATE INDEX companies_by_org ON companies (user_id, org_id, status);
         CREATE INDEX companies_recent ON companies (user_id, last_opened_at);
         CREATE TABLE workspace_cache (
            user_id TEXT NOT NULL, company_id TEXT NOT NULL, version INTEGER NOT NULL, etag TEXT,
            payload BLOB NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (user_id, company_id));
         CREATE TABLE sync_state (
            user_id TEXT NOT NULL, company_id TEXT NOT NULL, last_success_at TEXT, last_attempt_at TEXT,
            last_error TEXT, last_version INTEGER, PRIMARY KEY (user_id, company_id));
         CREATE TABLE device (
            user_id TEXT NOT NULL, org_id TEXT NOT NULL, device_id TEXT NOT NULL, name TEXT NOT NULL,
            app_version TEXT NOT NULL, registered_at TEXT NOT NULL, last_seen_at TEXT, status TEXT NOT NULL,
            PRIMARY KEY (user_id, org_id));
         CREATE TABLE local_dirs (
            user_id TEXT NOT NULL, company_id TEXT NOT NULL, dir_name TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, company_id), UNIQUE (user_id, dir_name));
         CREATE TABLE pending_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, company_id TEXT, kind TEXT NOT NULL,
            payload BLOB NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT, last_error TEXT, created_at TEXT NOT NULL);
         CREATE INDEX pending_ops_due ON pending_ops (user_id, state, next_retry_at);
         CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL, level TEXT NOT NULL,
            kind TEXT NOT NULL, message TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0);
         CREATE INDEX events_recent ON events (user_id, id);",
    ),
    (
        2,
        "CREATE TABLE upload_queue (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, org_id TEXT NOT NULL, company_id TEXT NOT NULL,
            status TEXT NOT NULL, idempotency_key TEXT NOT NULL, size INTEGER NOT NULL, mtime INTEGER, sha256 TEXT,
            node_id TEXT, category_id TEXT, filing_rule_id TEXT, target_document_id TEXT,
            duplicate_policy TEXT NOT NULL DEFAULT 'block', server_batch_id TEXT, server_op_id TEXT,
            bytes_sent INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT, error_message TEXT, result_document_id TEXT, private BLOB NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
         CREATE INDEX upload_queue_state ON upload_queue (user_id, company_id, status, created_at);
         CREATE TABLE mirror_nodes (
            user_id TEXT NOT NULL, company_id TEXT NOT NULL, node_id TEXT NOT NULL, rel_path TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, company_id, node_id));
         CREATE TABLE mirror_files (
            user_id TEXT NOT NULL, company_id TEXT NOT NULL, document_id TEXT NOT NULL, version_no INTEGER NOT NULL,
            rel_path TEXT NOT NULL, sha256 TEXT NOT NULL, saved_at TEXT NOT NULL,
            PRIMARY KEY (user_id, company_id, document_id, version_no));",
    ),
    (
        3,
        "CREATE TABLE sync_feed (
            user_id TEXT NOT NULL, company_id TEXT NOT NULL, cursor TEXT, last_pull_at TEXT, last_error TEXT,
            pulled_total INTEGER NOT NULL DEFAULT 0, caught_up INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, company_id));
         CREATE TABLE sync_items (
            user_id TEXT NOT NULL, company_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
            revision INTEGER NOT NULL, status TEXT, changed_at TEXT,
            PRIMARY KEY (user_id, company_id, kind, item_id));",
    ),
];

pub fn latest_version() -> i64 {
    MIGRATIONS.last().map(|m| m.0).unwrap_or(0)
}

pub fn now() -> String {
    Utc::now().to_rfc3339()
}

/// Result of opening the database: if the file was unusable it is quarantined (kept for support) and recreated empty.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct OpenReport {
    pub schema_version: i64,
    pub recovered_from: Option<String>,
    pub migrated_from: Option<i64>,
}

pub struct Db {
    conn: Connection,
    sealer: Sealer,
    pub path: PathBuf,
}

fn base_pragmas(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 3000;")
}

fn healthy(conn: &Connection) -> bool {
    conn.query_row("PRAGMA quick_check", [], |r| r.get::<_, String>(0)).map(|s| s == "ok").unwrap_or(false)
}

fn open_checked(path: &Path) -> rusqlite::Result<Option<Connection>> {
    let conn = Connection::open(path)?;
    base_pragmas(&conn)?;
    Ok(if healthy(&conn) { Some(conn) } else { None })
}

impl Db {
    pub fn open(path: &Path, sealer: Sealer) -> Result<(Self, OpenReport)> {
        Self::open_with(path, sealer, MIGRATIONS)
    }

    /// `open` with an explicit migration list (the production list is `MIGRATIONS`; tests exercise upgrade paths).
    pub fn open_with(path: &Path, sealer: Sealer, migrations: &'static [(i64, &'static str)]) -> Result<(Self, OpenReport)> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let mut recovered_from = None;
        let conn = match open_checked(path) {
            Ok(Some(c)) => c,
            _ => {
                // corrupt or not a database: keep it for diagnostics, start clean (it is only a cache)
                let stamp = Utc::now().format("%Y%m%dT%H%M%S");
                let quarantine = path.with_extension(format!("corrupt-{stamp}"));
                for ext in ["", "-wal", "-shm"] {
                    let from = PathBuf::from(format!("{}{ext}", path.display()));
                    let to = PathBuf::from(format!("{}{ext}", quarantine.display()));
                    if from.exists() {
                        let _ = std::fs::rename(from, to);
                    }
                }
                recovered_from = Some(quarantine.display().to_string());
                let c = Connection::open(path)?;
                base_pragmas(&c)?;
                c
            }
        };
        let mut db = Db { conn, sealer, path: path.to_path_buf() };
        let (schema_version, migrated_from) = db.migrate(migrations)?;
        Ok((db, OpenReport { schema_version, recovered_from, migrated_from }))
    }

    fn migrate(&mut self, migrations: &[(i64, &str)]) -> Result<(i64, Option<i64>)> {
        let latest = migrations.last().map(|m| m.0).unwrap_or(0);
        self.conn.execute_batch("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);")?;
        let current: i64 = self.conn.query_row("SELECT COALESCE(MAX(version), 0) FROM schema_migrations", [], |r| r.get(0))?;
        if current > latest {
            return Err(AppError::new(
                "database_too_new",
                "The local database was created by a newer version of the app. Update the app; nothing was changed.",
            ));
        }
        for (version, sql) in migrations.iter().filter(|m| m.0 > current) {
            let tx = self.conn.transaction()?;
            tx.execute_batch(sql)?;
            tx.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?1, ?2)", params![version, now()])?;
            tx.commit()?;
        }
        Ok((latest, if current > 0 && current < latest { Some(current) } else { None }))
    }

    pub fn schema_version(&self) -> Result<i64> {
        Ok(self.conn.query_row("SELECT COALESCE(MAX(version), 0) FROM schema_migrations", [], |r| r.get(0))?)
    }

    /// For tests and diagnostics: raw connection (read-mostly).
    pub fn conn(&self) -> &Connection {
        &self.conn
    }

    // ------------------------------------------------------------ settings
    pub fn set_setting(&self, user: &str, key: &str, value: &str) -> Result<()> {
        self.conn.execute(
            "INSERT INTO settings (user_id, key, value, updated_at) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            params![user, key, value, now()],
        )?;
        Ok(())
    }
    pub fn get_setting(&self, user: &str, key: &str) -> Result<Option<String>> {
        Ok(self
            .conn
            .query_row("SELECT value FROM settings WHERE user_id = ?1 AND key = ?2", params![user, key], |r| r.get(0))
            .optional()?)
    }

    // ------------------------------------------------------------ sealed cache helpers
    fn aad(user: &str, table: &str, id: &str) -> String {
        format!("{user}|{table}|{id}")
    }

    pub fn put_account(&self, user: &str, json: &serde_json::Value) -> Result<()> {
        let blob = self.sealer.seal(&Self::aad(user, "accounts", user), json.to_string().as_bytes())?;
        self.conn.execute(
            "INSERT INTO accounts (user_id, payload, updated_at) VALUES (?1, ?2, ?3)
             ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
            params![user, blob, now()],
        )?;
        Ok(())
    }
    pub fn get_account(&self, user: &str) -> Result<Option<serde_json::Value>> {
        let blob: Option<Vec<u8>> =
            self.conn.query_row("SELECT payload FROM accounts WHERE user_id = ?1", params![user], |r| r.get(0)).optional()?;
        blob.map(|b| self.open_json(&Self::aad(user, "accounts", user), &b)).transpose()
    }

    fn open_json(&self, aad: &str, blob: &[u8]) -> Result<serde_json::Value> {
        serde_json::from_slice(&self.sealer.open(aad, blob)?).map_err(|_| AppError::database("A cached item is damaged."))
    }

    pub fn replace_organizations(&mut self, user: &str, orgs: &[(String, serde_json::Value)]) -> Result<()> {
        let sealed: Vec<(String, Vec<u8>)> = orgs
            .iter()
            .map(|(id, v)| Ok((id.clone(), self.sealer.seal(&Self::aad(user, "organizations", id), v.to_string().as_bytes())?)))
            .collect::<Result<_>>()?;
        let tx = self.conn.transaction()?;
        tx.execute("DELETE FROM organizations WHERE user_id = ?1", params![user])?;
        for (pos, (id, blob)) in sealed.iter().enumerate() {
            tx.execute(
                "INSERT INTO organizations (user_id, org_id, position, payload, updated_at) VALUES (?1, ?2, ?3, ?4, ?5)",
                params![user, id, pos as i64, blob, now()],
            )?;
        }
        tx.commit()?;
        Ok(())
    }
    pub fn list_organizations(&self, user: &str) -> Result<Vec<(String, serde_json::Value)>> {
        let mut st = self.conn.prepare("SELECT org_id, payload FROM organizations WHERE user_id = ?1 ORDER BY position")?;
        let rows: Vec<(String, Vec<u8>)> = st.query_map(params![user], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        rows.into_iter().map(|(id, b)| Ok((id.clone(), self.open_json(&Self::aad(user, "organizations", &id), &b)?))).collect()
    }

    /// Replace the cached company list of one organization. Favorites and last-opened survive the refresh; companies the
    /// server no longer returns (revoked membership, archived out of scope) disappear from the cache at once.
    pub fn replace_companies(&mut self, user: &str, org: &str, items: &[(String, String, serde_json::Value)]) -> Result<()> {
        let sealed: Vec<(String, String, Vec<u8>)> = items
            .iter()
            .map(|(id, status, v)| {
                Ok((id.clone(), status.clone(), self.sealer.seal(&Self::aad(user, "companies", id), v.to_string().as_bytes())?))
            })
            .collect::<Result<_>>()?;
        let tx = self.conn.transaction()?;
        let keep: Vec<&str> = sealed.iter().map(|s| s.0.as_str()).collect();
        let existing: Vec<String> = {
            let mut st = tx.prepare("SELECT company_id FROM companies WHERE user_id = ?1 AND org_id = ?2")?;
            let rows = st.query_map(params![user, org], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
            rows
        };
        for gone in existing.iter().filter(|e| !keep.contains(&e.as_str())) {
            tx.execute("DELETE FROM companies WHERE user_id = ?1 AND company_id = ?2", params![user, gone])?;
            tx.execute("DELETE FROM workspace_cache WHERE user_id = ?1 AND company_id = ?2", params![user, gone])?;
            tx.execute("DELETE FROM sync_state WHERE user_id = ?1 AND company_id = ?2", params![user, gone])?;
        }
        for (id, status, blob) in &sealed {
            tx.execute(
                "INSERT INTO companies (user_id, org_id, company_id, status, payload, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                 ON CONFLICT(user_id, company_id) DO UPDATE SET org_id = excluded.org_id, status = excluded.status,
                   payload = excluded.payload, updated_at = excluded.updated_at",
                params![user, org, id, status, blob, now()],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn list_companies(&self, user: &str, org: &str) -> Result<Vec<CachedCompany>> {
        let mut st = self.conn.prepare(
            "SELECT company_id, status, favorite, last_opened_at, payload FROM companies WHERE user_id = ?1 AND org_id = ?2 ORDER BY company_id",
        )?;
        type Row = (String, String, i64, Option<String>, Vec<u8>);
        let rows: Vec<Row> = st
            .query_map(params![user, org], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?
            .collect::<rusqlite::Result<_>>()?;
        rows.into_iter()
            .map(|(id, status, fav, last, blob)| {
                Ok(CachedCompany {
                    id: id.clone(),
                    status,
                    favorite: fav != 0,
                    last_opened_at: last,
                    data: self.open_json(&Self::aad(user, "companies", &id), &blob)?,
                })
            })
            .collect()
    }
    /// Drop one company the server no longer lets this user open (and everything cached for it).
    pub fn replace_companies_remove(&mut self, user: &str, company: &str) -> Result<()> {
        let tx = self.conn.transaction()?;
        for t in ["companies", "workspace_cache", "sync_state"] {
            tx.execute(&format!("DELETE FROM {t} WHERE user_id = ?1 AND company_id = ?2"), params![user, company])?;
        }
        tx.commit()?;
        Ok(())
    }
    pub fn set_favorite(&self, user: &str, company: &str, favorite: bool) -> Result<bool> {
        Ok(self.conn.execute(
            "UPDATE companies SET favorite = ?3 WHERE user_id = ?1 AND company_id = ?2",
            params![user, company, favorite as i64],
        )? > 0)
    }
    pub fn touch_company(&self, user: &str, company: &str) -> Result<()> {
        self.conn
            .execute("UPDATE companies SET last_opened_at = ?3 WHERE user_id = ?1 AND company_id = ?2", params![user, company, now()])?;
        Ok(())
    }

    // ------------------------------------------------------------ workspace cache
    pub fn put_workspace(&self, user: &str, company: &str, version: i64, etag: Option<&str>, json: &serde_json::Value) -> Result<()> {
        let blob = self.sealer.seal(&Self::aad(user, "workspace_cache", company), json.to_string().as_bytes())?;
        self.conn.execute(
            "INSERT INTO workspace_cache (user_id, company_id, version, etag, payload, fetched_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
             ON CONFLICT(user_id, company_id) DO UPDATE SET version = excluded.version, etag = excluded.etag,
               payload = excluded.payload, fetched_at = excluded.fetched_at",
            params![user, company, version, etag, blob, now()],
        )?;
        Ok(())
    }
    pub fn get_workspace(&self, user: &str, company: &str) -> Result<Option<CachedWorkspace>> {
        let row: Option<(i64, Option<String>, Vec<u8>, String)> = self
            .conn
            .query_row(
                "SELECT version, etag, payload, fetched_at FROM workspace_cache WHERE user_id = ?1 AND company_id = ?2",
                params![user, company],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .optional()?;
        row.map(|(version, etag, blob, fetched_at)| {
            Ok(CachedWorkspace { version, etag, fetched_at, data: self.open_json(&Self::aad(user, "workspace_cache", company), &blob)? })
        })
        .transpose()
    }
    pub fn workspace_version(&self, user: &str, company: &str) -> Result<Option<i64>> {
        Ok(self
            .conn
            .query_row("SELECT version FROM workspace_cache WHERE user_id = ?1 AND company_id = ?2", params![user, company], |r| r.get(0))
            .optional()?)
    }

    // ------------------------------------------------------------ sync metadata
    pub fn record_sync(&self, user: &str, company: &str, ok: bool, error: Option<&str>, version: Option<i64>) -> Result<()> {
        let t = now();
        self.conn.execute(
            "INSERT INTO sync_state (user_id, company_id, last_success_at, last_attempt_at, last_error, last_version) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
             ON CONFLICT(user_id, company_id) DO UPDATE SET
               last_success_at = CASE WHEN ?7 THEN excluded.last_success_at ELSE sync_state.last_success_at END,
               last_attempt_at = excluded.last_attempt_at, last_error = excluded.last_error,
               last_version = COALESCE(excluded.last_version, sync_state.last_version)",
            params![user, company, if ok { Some(t.clone()) } else { None }, t, error, version, ok],
        )?;
        Ok(())
    }
    pub fn get_sync(&self, user: &str, company: &str) -> Result<Option<SyncState>> {
        Ok(self
            .conn
            .query_row(
                "SELECT last_success_at, last_attempt_at, last_error, last_version FROM sync_state WHERE user_id = ?1 AND company_id = ?2",
                params![user, company],
                |r| {
                    Ok(SyncState { last_success_at: r.get(0)?, last_attempt_at: r.get(1)?, last_error: r.get(2)?, last_version: r.get(3)? })
                },
            )
            .optional()?)
    }

    // ------------------------------------------------------------ device
    pub fn put_device(&self, user: &str, org: &str, d: &LocalDevice) -> Result<()> {
        self.conn.execute(
            "INSERT INTO device (user_id, org_id, device_id, name, app_version, registered_at, last_seen_at, status) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
             ON CONFLICT(user_id, org_id) DO UPDATE SET device_id = excluded.device_id, name = excluded.name, app_version = excluded.app_version,
               registered_at = excluded.registered_at, last_seen_at = excluded.last_seen_at, status = excluded.status",
            params![user, org, d.device_id, d.name, d.app_version, d.registered_at, d.last_seen_at, d.status],
        )?;
        Ok(())
    }
    pub fn get_device(&self, user: &str, org: &str) -> Result<Option<LocalDevice>> {
        Ok(self
            .conn
            .query_row(
                "SELECT device_id, name, app_version, registered_at, last_seen_at, status FROM device WHERE user_id = ?1 AND org_id = ?2",
                params![user, org],
                |r| {
                    Ok(LocalDevice {
                        device_id: r.get(0)?,
                        name: r.get(1)?,
                        app_version: r.get(2)?,
                        registered_at: r.get(3)?,
                        last_seen_at: r.get(4)?,
                        status: r.get(5)?,
                    })
                },
            )
            .optional()?)
    }

    // ------------------------------------------------------------ local directory names (stable per company)
    pub fn dir_name_for(&self, user: &str, company: &str) -> Result<Option<String>> {
        Ok(self
            .conn
            .query_row("SELECT dir_name FROM local_dirs WHERE user_id = ?1 AND company_id = ?2", params![user, company], |r| r.get(0))
            .optional()?)
    }
    pub fn dir_name_taken(&self, user: &str, dir_name: &str) -> Result<bool> {
        Ok(self
            .conn
            .query_row("SELECT 1 FROM local_dirs WHERE user_id = ?1 AND lower(dir_name) = lower(?2)", params![user, dir_name], |_| Ok(()))
            .optional()?
            .is_some())
    }
    pub fn set_dir_name(&self, user: &str, company: &str, dir_name: &str) -> Result<()> {
        self.conn.execute(
            "INSERT OR IGNORE INTO local_dirs (user_id, company_id, dir_name, created_at) VALUES (?1, ?2, ?3, ?4)",
            params![user, company, dir_name, now()],
        )?;
        Ok(())
    }

    // ------------------------------------------------------------ pending operations (skeleton; processing arrives in Phase 6)
    pub fn enqueue_op(&self, user: &str, company: Option<&str>, kind: &str, payload: &serde_json::Value) -> Result<i64> {
        let blob = self.sealer.seal(&format!("{user}|pending_ops|{kind}"), payload.to_string().as_bytes())?;
        self.conn.execute(
            "INSERT INTO pending_ops (user_id, company_id, kind, payload, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![user, company, kind, blob, now()],
        )?;
        Ok(self.conn.last_insert_rowid())
    }
    /// Unfinished work, for the dashboard. The upload queue is what the user cares about; the generic skeleton table is
    /// counted too so nothing queued is ever hidden.
    pub fn pending_summary(&self, user: &str) -> Result<PendingSummary> {
        let count = |state: &str| -> rusqlite::Result<i64> {
            self.conn.query_row("SELECT COUNT(*) FROM pending_ops WHERE user_id = ?1 AND state = ?2", params![user, state], |r| r.get(0))
        };
        let q = |states: &str| -> rusqlite::Result<i64> {
            self.conn.query_row(
                &format!("SELECT COUNT(*) FROM upload_queue WHERE user_id = ?1 AND status IN ({states})"),
                params![user],
                |r| r.get(0),
            )
        };
        Ok(PendingSummary {
            pending: count("pending")? + q("'preparing','waiting'")?,
            in_progress: count("in_progress")? + q("'uploading','verification_pending'")?,
            failed: count("failed")? + q("'failed','quarantined'")?,
        })
    }

    // ------------------------------------------------------------ events (notifications)
    pub fn add_event(&self, user: &str, level: &str, kind: &str, message: &str) -> Result<()> {
        self.conn.execute(
            "INSERT INTO events (user_id, ts, level, kind, message) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![user, now(), level, kind, message],
        )?;
        self.conn.execute(
            "DELETE FROM events WHERE user_id = ?1 AND id <= (SELECT COALESCE(MAX(id), 0) - 200 FROM events WHERE user_id = ?1)",
            params![user],
        )?;
        Ok(())
    }
    pub fn list_events(&self, user: &str, limit: i64) -> Result<Vec<EventRow>> {
        let mut st =
            self.conn.prepare("SELECT id, ts, level, kind, message, read FROM events WHERE user_id = ?1 ORDER BY id DESC LIMIT ?2")?;
        let rows = st
            .query_map(params![user, limit], |r| {
                Ok(EventRow {
                    id: r.get(0)?,
                    ts: r.get(1)?,
                    level: r.get(2)?,
                    kind: r.get(3)?,
                    message: r.get(4)?,
                    read: r.get::<_, i64>(5)? != 0,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }
    pub fn mark_events_read(&self, user: &str) -> Result<()> {
        self.conn.execute("UPDATE events SET read = 1 WHERE user_id = ?1", params![user])?;
        Ok(())
    }

    // ------------------------------------------------------------ upload queue (survives restarts; only the owner sees it)
    pub fn queue_insert(&self, r: &NewQueueRow) -> Result<()> {
        let blob = self.sealer.seal(&Self::aad(&r.user_id, "upload_queue", &r.id), r.private.to_string().as_bytes())?;
        self.conn.execute(
            "INSERT INTO upload_queue (id, user_id, org_id, company_id, status, idempotency_key, size, mtime, sha256, node_id, category_id,
               filing_rule_id, target_document_id, duplicate_policy, private, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, 'waiting', ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?15)",
            params![
                r.id,
                r.user_id,
                r.org_id,
                r.company_id,
                r.idempotency_key,
                r.size,
                r.mtime,
                r.sha256,
                r.node_id,
                r.category_id,
                r.filing_rule_id,
                r.target_document_id,
                r.duplicate_policy,
                blob,
                now()
            ],
        )?;
        Ok(())
    }
    fn queue_row(&self, user: &str, r: &rusqlite::Row<'_>) -> rusqlite::Result<(QueueRow, Vec<u8>)> {
        let _ = user;
        Ok((
            QueueRow {
                id: r.get("id")?,
                org_id: r.get("org_id")?,
                company_id: r.get("company_id")?,
                status: r.get("status")?,
                idempotency_key: r.get("idempotency_key")?,
                size: r.get("size")?,
                mtime: r.get("mtime")?,
                sha256: r.get("sha256")?,
                node_id: r.get("node_id")?,
                category_id: r.get("category_id")?,
                filing_rule_id: r.get("filing_rule_id")?,
                target_document_id: r.get("target_document_id")?,
                duplicate_policy: r.get("duplicate_policy")?,
                server_batch_id: r.get("server_batch_id")?,
                server_op_id: r.get("server_op_id")?,
                bytes_sent: r.get("bytes_sent")?,
                attempts: r.get("attempts")?,
                error_code: r.get("error_code")?,
                error_message: r.get("error_message")?,
                result_document_id: r.get("result_document_id")?,
                created_at: r.get("created_at")?,
                updated_at: r.get("updated_at")?,
                private: serde_json::Value::Null,
            },
            r.get("private")?,
        ))
    }
    fn queue_open(&self, user: &str, rows: Vec<(QueueRow, Vec<u8>)>) -> Result<Vec<QueueRow>> {
        rows.into_iter()
            .map(|(mut q, blob)| {
                q.private = self.open_json(&Self::aad(user, "upload_queue", &q.id), &blob)?;
                Ok(q)
            })
            .collect()
    }
    pub fn queue_get(&self, user: &str, id: &str) -> Result<Option<QueueRow>> {
        let row = self
            .conn
            .query_row("SELECT * FROM upload_queue WHERE user_id = ?1 AND id = ?2", params![user, id], |r| self.queue_row(user, r))
            .optional()?;
        Ok(self.queue_open(user, row.into_iter().collect())?.into_iter().next())
    }
    pub fn queue_list(&self, user: &str, company: Option<&str>) -> Result<Vec<QueueRow>> {
        let mut st = self
            .conn
            .prepare("SELECT * FROM upload_queue WHERE user_id = ?1 AND (?2 IS NULL OR company_id = ?2) ORDER BY created_at, id")?;
        let rows = st.query_map(params![user, company], |r| self.queue_row(user, r))?.collect::<rusqlite::Result<Vec<_>>>()?;
        self.queue_open(user, rows)
    }
    pub fn queue_set_status(&self, user: &str, id: &str, status: &str, code: Option<&str>, message: Option<&str>) -> Result<()> {
        self.conn.execute(
            "UPDATE upload_queue SET status = ?3, error_code = ?4, error_message = ?5, updated_at = ?6 WHERE user_id = ?1 AND id = ?2",
            params![user, id, status, code, message, now()],
        )?;
        Ok(())
    }
    pub fn queue_set_server(&self, user: &str, id: &str, batch: Option<&str>, op: &str) -> Result<()> {
        self.conn.execute(
            "UPDATE upload_queue SET server_batch_id = COALESCE(?3, server_batch_id), server_op_id = ?4, updated_at = ?5 WHERE user_id = ?1 AND id = ?2",
            params![user, id, batch, op, now()],
        )?;
        Ok(())
    }
    pub fn queue_set_sha(&self, user: &str, id: &str, sha: &str, size: i64, mtime: Option<i64>) -> Result<()> {
        self.conn.execute(
            "UPDATE upload_queue SET sha256 = ?3, size = ?4, mtime = ?5, updated_at = ?6 WHERE user_id = ?1 AND id = ?2",
            params![user, id, sha, size, mtime, now()],
        )?;
        Ok(())
    }
    pub fn queue_set_progress(&self, user: &str, id: &str, bytes: i64) -> Result<()> {
        self.conn.execute("UPDATE upload_queue SET bytes_sent = ?3 WHERE user_id = ?1 AND id = ?2", params![user, id, bytes])?;
        Ok(())
    }
    pub fn queue_bump_attempts(&self, user: &str, id: &str) -> Result<()> {
        self.conn.execute("UPDATE upload_queue SET attempts = attempts + 1 WHERE user_id = ?1 AND id = ?2", params![user, id])?;
        Ok(())
    }
    pub fn queue_set_result(&self, user: &str, id: &str, document_id: &str) -> Result<()> {
        self.conn.execute(
            "UPDATE upload_queue SET status = 'completed', result_document_id = ?3, error_code = NULL, error_message = NULL, updated_at = ?4
             WHERE user_id = ?1 AND id = ?2",
            params![user, id, document_id, now()],
        )?;
        Ok(())
    }
    /// A retry starts the file again: the server-side session may be reused (idempotent) but byte progress restarts.
    pub fn queue_requeue(&self, user: &str, id: &str, forget_session: bool) -> Result<()> {
        self.conn.execute(
            "UPDATE upload_queue SET status = 'waiting', error_code = NULL, error_message = NULL, bytes_sent = 0,
               server_op_id = CASE WHEN ?3 THEN NULL ELSE server_op_id END, updated_at = ?4 WHERE user_id = ?1 AND id = ?2",
            params![user, id, forget_session, now()],
        )?;
        Ok(())
    }
    pub fn queue_remove(&self, user: &str, id: &str) -> Result<bool> {
        Ok(self.conn.execute("DELETE FROM upload_queue WHERE user_id = ?1 AND id = ?2", params![user, id])? > 0)
    }
    pub fn queue_clear_finished(&self, user: &str, company: Option<&str>) -> Result<usize> {
        Ok(self.conn.execute(
            "DELETE FROM upload_queue WHERE user_id = ?1 AND (?2 IS NULL OR company_id = ?2) AND status IN ('completed', 'cancelled')",
            params![user, company],
        )?)
    }
    /// After a restart nothing is really in flight: preparing/uploading items go back to waiting. A session that is
    /// already registered on the server is reused (same idempotency key), so the file is never registered twice.
    pub fn queue_recover(&self, user: &str) -> Result<usize> {
        Ok(self.conn.execute(
            "UPDATE upload_queue SET status = 'waiting', bytes_sent = 0, updated_at = ?2 WHERE user_id = ?1 AND status IN ('preparing', 'uploading')",
            params![user, now()],
        )?)
    }

    // ------------------------------------------------------------ local mirror bookkeeping
    pub fn mirror_nodes(&self, user: &str, company: &str) -> Result<std::collections::HashMap<String, String>> {
        let mut st = self.conn.prepare("SELECT node_id, rel_path FROM mirror_nodes WHERE user_id = ?1 AND company_id = ?2")?;
        let rows = st
            .query_map(params![user, company], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows.into_iter().collect())
    }
    pub fn mirror_put_node(&self, user: &str, company: &str, node: &str, rel: &str) -> Result<()> {
        self.conn.execute(
            "INSERT INTO mirror_nodes (user_id, company_id, node_id, rel_path, updated_at) VALUES (?1, ?2, ?3, ?4, ?5)
             ON CONFLICT(user_id, company_id, node_id) DO UPDATE SET rel_path = excluded.rel_path, updated_at = excluded.updated_at",
            params![user, company, node, rel, now()],
        )?;
        Ok(())
    }
    pub fn mirror_put_file(&self, user: &str, company: &str, doc: &str, version: i64, rel: &str, sha: &str) -> Result<()> {
        self.conn.execute(
            "INSERT INTO mirror_files (user_id, company_id, document_id, version_no, rel_path, sha256, saved_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
             ON CONFLICT(user_id, company_id, document_id, version_no) DO UPDATE SET rel_path = excluded.rel_path, sha256 = excluded.sha256,
               saved_at = excluded.saved_at",
            params![user, company, doc, version, rel, sha, now()],
        )?;
        Ok(())
    }
    pub fn mirror_get_file(&self, user: &str, company: &str, doc: &str, version: i64) -> Result<Option<(String, String)>> {
        Ok(self
            .conn
            .query_row(
                "SELECT rel_path, sha256 FROM mirror_files WHERE user_id = ?1 AND company_id = ?2 AND document_id = ?3 AND version_no = ?4",
                params![user, company, doc, version],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?)
    }
    pub fn mirror_files_under(&self, user: &str, company: &str) -> Result<Vec<(String, i64, String)>> {
        let mut st =
            self.conn.prepare("SELECT document_id, version_no, rel_path FROM mirror_files WHERE user_id = ?1 AND company_id = ?2")?;
        let rows =
            st.query_map(params![user, company], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }
    pub fn mirror_rewrite_prefix(&self, user: &str, company: &str, old_prefix: &str, new_prefix: &str) -> Result<()> {
        // files saved inside a renamed/moved folder keep pointing at their real location
        for (doc, version, rel) in self.mirror_files_under(user, company)? {
            if rel == old_prefix || rel.starts_with(&format!("{old_prefix}/")) {
                let updated = format!("{new_prefix}{}", &rel[old_prefix.len()..]);
                self.conn.execute(
                    "UPDATE mirror_files SET rel_path = ?5 WHERE user_id = ?1 AND company_id = ?2 AND document_id = ?3 AND version_no = ?4",
                    params![user, company, doc, version, updated],
                )?;
            }
        }
        Ok(())
    }
    /// A server session that can no longer be reused (expired/cancelled on the server): start a fresh one for the same file.
    pub fn queue_reset_session(&self, user: &str, id: &str, new_key: &str) -> Result<()> {
        self.conn.execute(
            "UPDATE upload_queue SET idempotency_key = ?3, server_op_id = NULL, status = 'waiting', bytes_sent = 0, updated_at = ?4
             WHERE user_id = ?1 AND id = ?2",
            params![user, id, new_key, now()],
        )?;
        Ok(())
    }
    pub fn queue_update_private(&self, user: &str, id: &str, private: &serde_json::Value) -> Result<()> {
        let blob = self.sealer.seal(&Self::aad(user, "upload_queue", id), private.to_string().as_bytes())?;
        self.conn.execute("UPDATE upload_queue SET private = ?3 WHERE user_id = ?1 AND id = ?2", params![user, id, blob])?;
        Ok(())
    }
    pub fn queue_set_batch(&self, user: &str, ids: &[String], batch: &str) -> Result<()> {
        for id in ids {
            self.conn.execute("UPDATE upload_queue SET server_batch_id = ?3 WHERE user_id = ?1 AND id = ?2", params![user, id, batch])?;
        }
        Ok(())
    }

    // ------------------------------------------------------------ change-feed checkpoint (Phase 12)
    pub fn feed_get(&self, user: &str, company: &str) -> Result<FeedState> {
        Ok(self
            .conn
            .query_row(
                "SELECT cursor, last_pull_at, last_error, pulled_total, caught_up FROM sync_feed WHERE user_id = ?1 AND company_id = ?2",
                params![user, company],
                |r| {
                    Ok(FeedState {
                        cursor: r.get(0)?,
                        last_pull_at: r.get(1)?,
                        last_error: r.get(2)?,
                        pulled_total: r.get(3)?,
                        caught_up: r.get::<_, i64>(4)? != 0,
                    })
                },
            )
            .optional()?
            .unwrap_or_default())
    }

    /// Store one page of the feed and its checkpoint in ONE transaction: the cursor only advances together with the items it
    /// covers, so a crash in between re-reads the page (items are idempotent by kind + id + revision) and never skips one.
    pub fn feed_save_page(&mut self, user: &str, company: &str, cursor: Option<&str>, items: &[FeedItem], caught_up: bool) -> Result<()> {
        let tx = self.conn.transaction()?;
        for i in items {
            tx.execute(
                "INSERT INTO sync_items (user_id, company_id, kind, item_id, revision, status, changed_at) VALUES (?1,?2,?3,?4,?5,?6,?7)
                 ON CONFLICT (user_id, company_id, kind, item_id) DO UPDATE SET revision = excluded.revision, status = excluded.status,
                 changed_at = excluded.changed_at WHERE excluded.revision >= sync_items.revision",
                params![user, company, i.kind, i.id, i.revision, i.status, i.changed_at],
            )?;
        }
        tx.execute(
            "INSERT INTO sync_feed (user_id, company_id, cursor, last_pull_at, last_error, pulled_total, caught_up) VALUES (?1,?2,?3,?4,NULL,?5,?6)
             ON CONFLICT (user_id, company_id) DO UPDATE SET cursor = COALESCE(excluded.cursor, sync_feed.cursor), last_pull_at = excluded.last_pull_at,
             last_error = NULL, pulled_total = sync_feed.pulled_total + ?5, caught_up = excluded.caught_up",
            params![user, company, cursor, now(), items.len() as i64, caught_up as i64],
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn feed_record_error(&self, user: &str, company: &str, error: &str) -> Result<()> {
        self.conn.execute(
            "INSERT INTO sync_feed (user_id, company_id, last_error) VALUES (?1,?2,?3)
             ON CONFLICT (user_id, company_id) DO UPDATE SET last_error = excluded.last_error, caught_up = 0",
            params![user, company, error],
        )?;
        Ok(())
    }

    /// Forget the checkpoint (the server said the cursor is invalid): the next pull starts from the beginning.
    pub fn feed_reset(&self, user: &str, company: &str) -> Result<()> {
        self.conn.execute("DELETE FROM sync_feed WHERE user_id = ?1 AND company_id = ?2", params![user, company])?;
        Ok(())
    }

    pub fn feed_item_revision(&self, user: &str, company: &str, kind: &str, id: &str) -> Result<Option<i64>> {
        Ok(self
            .conn
            .query_row(
                "SELECT revision FROM sync_items WHERE user_id = ?1 AND company_id = ?2 AND kind = ?3 AND item_id = ?4",
                params![user, company, kind, id],
                |r| r.get(0),
            )
            .optional()?)
    }

    pub fn feed_counts(&self, user: &str, company: &str) -> Result<Vec<(String, i64)>> {
        let mut st = self
            .conn
            .prepare("SELECT kind, count(*) FROM sync_items WHERE user_id = ?1 AND company_id = ?2 GROUP BY kind ORDER BY kind")?;
        let rows = st.query_map(params![user, company], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
    }

    // ------------------------------------------------------------ wipe
    /// Remove every cached item that belongs to `user` (logout, revoked device, expired session). Pending operations are
    /// KEPT unless `include_pending` is set: unsent work is never discarded silently. Settings stay (they are preferences).
    pub fn wipe_user_cache(&mut self, user: &str, include_pending: bool) -> Result<()> {
        let tx = self.conn.transaction()?;
        for t in [
            "accounts",
            "organizations",
            "companies",
            "workspace_cache",
            "sync_state",
            "device",
            "mirror_nodes",
            "mirror_files",
            "sync_feed",
            "sync_items",
        ] {
            tx.execute(&format!("DELETE FROM {t} WHERE user_id = ?1"), params![user])?;
        }
        tx.execute("DELETE FROM events WHERE user_id = ?1", params![user])?;
        if include_pending {
            tx.execute("DELETE FROM pending_ops WHERE user_id = ?1", params![user])?;
            tx.execute("DELETE FROM upload_queue WHERE user_id = ?1", params![user])?;
        }
        tx.commit()?;
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct CachedCompany {
    pub id: String,
    pub status: String,
    pub favorite: bool,
    pub last_opened_at: Option<String>,
    pub data: serde_json::Value,
}
#[derive(Debug, Clone, Serialize)]
pub struct CachedWorkspace {
    pub version: i64,
    pub etag: Option<String>,
    pub fetched_at: String,
    pub data: serde_json::Value,
}
#[derive(Debug, Clone, Serialize, Default, PartialEq, Eq)]
pub struct FeedState {
    pub cursor: Option<String>,
    pub last_pull_at: Option<String>,
    pub last_error: Option<String>,
    pub pulled_total: i64,
    pub caught_up: bool,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedItem {
    pub kind: String,
    pub id: String,
    pub revision: i64,
    pub status: Option<String>,
    pub changed_at: Option<String>,
}
#[derive(Debug, Clone, Serialize, Default)]
pub struct SyncState {
    pub last_success_at: Option<String>,
    pub last_attempt_at: Option<String>,
    pub last_error: Option<String>,
    pub last_version: Option<i64>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LocalDevice {
    pub device_id: String,
    pub name: String,
    pub app_version: String,
    pub registered_at: String,
    pub last_seen_at: Option<String>,
    pub status: String,
}
#[derive(Debug, Clone, Serialize)]
pub struct PendingSummary {
    pub pending: i64,
    pub in_progress: i64,
    pub failed: i64,
}
#[derive(Debug, Clone, Serialize)]
pub struct EventRow {
    pub id: i64,
    pub ts: String,
    pub level: String,
    pub kind: String,
    pub message: String,
    pub read: bool,
}

#[derive(Debug, Clone)]
pub struct NewQueueRow {
    pub id: String,
    pub user_id: String,
    pub org_id: String,
    pub company_id: String,
    pub idempotency_key: String,
    pub size: i64,
    pub mtime: Option<i64>,
    pub sha256: Option<String>,
    pub node_id: Option<String>,
    pub category_id: Option<String>,
    pub filing_rule_id: Option<String>,
    pub target_document_id: Option<String>,
    pub duplicate_policy: String,
    /// sealed at rest: source path, file name, requested display name, metadata values, change reason
    pub private: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct QueueRow {
    pub id: String,
    pub org_id: String,
    pub company_id: String,
    pub status: String,
    pub idempotency_key: String,
    pub size: i64,
    pub mtime: Option<i64>,
    pub sha256: Option<String>,
    pub node_id: Option<String>,
    pub category_id: Option<String>,
    pub filing_rule_id: Option<String>,
    pub target_document_id: Option<String>,
    pub duplicate_policy: String,
    pub server_batch_id: Option<String>,
    pub server_op_id: Option<String>,
    pub bytes_sent: i64,
    pub attempts: i64,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub result_document_id: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub private: serde_json::Value,
}
