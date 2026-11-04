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
pub const MIGRATIONS: &[(i64, &str)] = &[(
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
)];

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
    pub fn pending_summary(&self, user: &str) -> Result<PendingSummary> {
        let count = |state: &str| -> rusqlite::Result<i64> {
            self.conn.query_row("SELECT COUNT(*) FROM pending_ops WHERE user_id = ?1 AND state = ?2", params![user, state], |r| r.get(0))
        };
        Ok(PendingSummary { pending: count("pending")?, in_progress: count("in_progress")?, failed: count("failed")? })
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

    // ------------------------------------------------------------ wipe
    /// Remove every cached item that belongs to `user` (logout, revoked device, expired session). Pending operations are
    /// KEPT unless `include_pending` is set: unsent work is never discarded silently. Settings stay (they are preferences).
    pub fn wipe_user_cache(&mut self, user: &str, include_pending: bool) -> Result<()> {
        let tx = self.conn.transaction()?;
        for t in ["accounts", "organizations", "companies", "workspace_cache", "sync_state", "device"] {
            tx.execute(&format!("DELETE FROM {t} WHERE user_id = ?1"), params![user])?;
        }
        tx.execute("DELETE FROM events WHERE user_id = ?1", params![user])?;
        if include_pending {
            tx.execute("DELETE FROM pending_ops WHERE user_id = ?1", params![user])?;
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
