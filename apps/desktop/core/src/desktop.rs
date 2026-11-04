//! The application service used by the Tauri commands and by the integration tests. It owns the API client, the local
//! database and the logger, and implements the Phase 5 flows: sign-in, organization/company selection (verified by the
//! backend), workspace synchronization, storage setup and device registration.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};

use serde::Serialize;
use serde_json::{json, Value};

use crate::api::{ApiClient, LoginOutcome};
use crate::db::{self, CachedCompany, Db, LocalDevice, OpenReport};
use crate::error::{AppError, Result};
use crate::logs::Logger;
use crate::storage::{self, RootReport};
use crate::workspace;

pub struct Desktop {
    pub api: Arc<ApiClient>,
    db: Mutex<Db>,
    pub log: Logger,
    pub app_version: String,
    pub open_report: OpenReport,
    user: Mutex<Option<String>>,
    pub data_dir: PathBuf,
}

#[derive(Debug, Clone, Serialize)]
pub struct Identity {
    pub user_id: String,
    pub email: String,
    pub display_name: Option<String>,
    pub memberships: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct Listing<T: Serialize> {
    pub items: Vec<T>,
    /// true when the network was unavailable and the list comes from the local cache
    pub stale: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct CompanyView {
    pub id: String,
    pub status: String,
    pub favorite: bool,
    pub last_opened_at: Option<String>,
    pub name: String,
    pub code: String,
    pub country: String,
    pub base_currency: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct RefreshOutcome {
    pub changed: bool,
    pub version: i64,
    pub summary: Option<String>,
    pub changes: Vec<workspace::Change>,
}

#[derive(Debug, Clone, Serialize)]
pub struct WorkspaceView {
    pub company_id: String,
    pub version: i64,
    pub fetched_at: String,
    pub data: Value,
    pub stale: bool,
    pub last_success_at: Option<String>,
    pub last_error: Option<String>,
}

impl Desktop {
    pub fn new(api: Arc<ApiClient>, db: Db, open_report: OpenReport, log: Logger, data_dir: &Path, app_version: &str) -> Self {
        Self {
            api,
            db: Mutex::new(db),
            log,
            app_version: app_version.into(),
            open_report,
            user: Mutex::new(None),
            data_dir: data_dir.to_path_buf(),
        }
    }

    fn db(&self) -> Result<MutexGuard<'_, Db>> {
        self.db.lock().map_err(|_| AppError::database("The local database is busy."))
    }
    pub fn user_id(&self) -> Result<String> {
        self.user.lock().ok().and_then(|u| u.clone()).ok_or_else(AppError::session_expired)
    }
    pub fn with_db<T>(&self, f: impl FnOnce(&mut Db) -> Result<T>) -> Result<T> {
        let mut guard = self.db()?;
        f(&mut guard)
    }

    // ---------------------------------------------------------------- sign-in
    pub async fn login(&self, email: &str, password: &str) -> Result<LoginOutcome> {
        let out = self.api.login(email, password).await.inspect_err(|e| self.log.warn("login_failed", &e.code))?;
        if out == LoginOutcome::Authenticated {
            self.finish_sign_in().await?;
        }
        Ok(out)
    }
    pub async fn verify_mfa(&self, mfa_token: &str, code: &str) -> Result<Identity> {
        self.api.verify_mfa(mfa_token, code).await.inspect_err(|e| self.log.warn("mfa_failed", &e.code))?;
        self.finish_sign_in().await
    }
    /// Resume from the vault at startup. `Ok(None)` means the user has to sign in.
    pub async fn restore(&self) -> Result<Option<Identity>> {
        if !self.api.restore().await? {
            return Ok(None);
        }
        Ok(Some(self.finish_sign_in().await?))
    }
    async fn finish_sign_in(&self) -> Result<Identity> {
        let me = self.api.get("/me", None).await?;
        let id = Self::identity(&me.body)?;
        if let Ok(mut u) = self.user.lock() {
            *u = Some(id.user_id.clone());
        }
        self.db()?.put_account(
            &id.user_id,
            &json!({ "user_id": id.user_id, "email": id.email, "display_name": id.display_name, "memberships": id.memberships }),
        )?;
        self.log.info("signed_in", "session established");
        Ok(id)
    }
    fn identity(me: &Value) -> Result<Identity> {
        let s = |k: &str| me.get(k).and_then(Value::as_str).map(str::to_string);
        Ok(Identity {
            user_id: s("user_id").ok_or_else(|| AppError::malformed("me"))?,
            email: s("email").ok_or_else(|| AppError::malformed("me"))?,
            display_name: s("display_name"),
            memberships: me.get("memberships").cloned().unwrap_or(Value::Array(vec![])),
        })
    }

    /// Sign out: tell the server, drop tokens from memory and the vault, wipe this user's cached data.
    pub async fn logout(&self) {
        let user = self.user_id().ok();
        self.api.logout().await;
        if let Some(u) = user {
            self.end_session_cleanup(&u, "logout");
        }
        if let Ok(mut u) = self.user.lock() {
            *u = None;
        }
    }
    /// Called when the server ends the session (expiry, revoked device): same cleanup, no network call.
    pub fn session_ended(&self, reason: &str) {
        if let Ok(u) = self.user_id() {
            self.end_session_cleanup(&u, reason);
        }
        if let Ok(mut u) = self.user.lock() {
            *u = None;
        }
    }
    fn end_session_cleanup(&self, user: &str, reason: &str) {
        match self.db().and_then(|mut d| d.wipe_user_cache(user, false)) {
            Ok(()) => self.log.info("cache_wiped", reason),
            Err(e) => self.log.error("cache_wipe_failed", &e.code),
        }
    }

    /// The signed-in identity from the sealed cache (written at sign-in).
    pub fn current_identity(&self) -> Result<Identity> {
        let user = self.user_id()?;
        let v = self.db()?.get_account(&user)?.ok_or_else(AppError::session_expired)?;
        Self::identity(&v)
    }

    // ---------------------------------------------------------------- organizations and companies
    pub async fn organizations(&self) -> Result<Listing<Value>> {
        let user = self.user_id()?;
        match self.api.get("/orgs", None).await {
            Ok(r) => {
                let items: Vec<Value> = r.body.as_array().cloned().ok_or_else(|| AppError::malformed("organizations"))?;
                let keyed: Vec<(String, Value)> =
                    items.iter().filter_map(|o| Some((o.get("id")?.as_str()?.to_string(), o.clone()))).collect();
                self.db()?.replace_organizations(&user, &keyed)?;
                Ok(Listing { items, stale: false })
            }
            Err(e) if e.retryable => {
                let cached = self.db()?.list_organizations(&user)?;
                self.note_offline(&user, "organizations");
                Ok(Listing { items: cached.into_iter().map(|c| c.1).collect(), stale: true })
            }
            Err(e) => Err(self.classify(e)),
        }
    }

    pub async fn companies(&self, org: &str) -> Result<Listing<CompanyView>> {
        let user = self.user_id()?;
        match self.api.get(&format!("/orgs/{org}/companies"), Some(org)).await {
            Ok(r) => {
                let items = r.body.as_array().ok_or_else(|| AppError::malformed("companies"))?;
                let keyed: Vec<(String, String, Value)> = items
                    .iter()
                    .filter_map(|c| Some((c.get("id")?.as_str()?.to_string(), c.get("status")?.as_str()?.to_string(), c.clone())))
                    .collect();
                self.db()?.replace_companies(&user, org, &keyed)?;
                Ok(Listing { items: self.company_views(&user, org)?, stale: false })
            }
            Err(e) if e.retryable => {
                self.note_offline(&user, "companies");
                Ok(Listing { items: self.company_views(&user, org)?, stale: true })
            }
            Err(e) => Err(self.classify(e)),
        }
    }
    fn company_views(&self, user: &str, org: &str) -> Result<Vec<CompanyView>> {
        let rows: Vec<CachedCompany> = self.db()?.list_companies(user, org)?;
        let g = |d: &Value, k: &str| d.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let mut v: Vec<CompanyView> = rows
            .into_iter()
            .map(|c| CompanyView {
                name: g(&c.data, "name"),
                code: g(&c.data, "code"),
                country: g(&c.data, "country"),
                base_currency: g(&c.data, "base_currency"),
                id: c.id,
                status: c.status,
                favorite: c.favorite,
                last_opened_at: c.last_opened_at,
            })
            .collect();
        v.sort_by_key(|c| c.name.to_lowercase());
        Ok(v)
    }

    /// Opening a company is ALWAYS verified by the backend, whatever the local cache says or the UI sends. An id the user
    /// may not reach answers 404 (the server never confirms that it exists) and is dropped from the cache.
    pub async fn select_company(&self, org: &str, company: &str) -> Result<Value> {
        let user = self.user_id()?;
        match self.api.get(&format!("/companies/{company}"), Some(org)).await {
            Ok(r) => {
                if r.body.get("id").and_then(Value::as_str) != Some(company) {
                    return Err(AppError::malformed("company"));
                }
                let d = self.db()?;
                d.touch_company(&user, company)?;
                d.set_setting(&user, "last_org_id", org)?;
                d.set_setting(&user, "last_company_id", company)?;
                Ok(r.body)
            }
            Err(e) if e.status == Some(404) || e.status == Some(403) => {
                let _ = self.db().and_then(|mut d| d.replace_companies_remove(&user, company));
                self.log.warn("company_denied", &e.code);
                Err(AppError::new("company_unavailable", "You no longer have access to this company, or it does not exist.")
                    .with_status(e.status.unwrap_or(404)))
            }
            Err(e) => Err(self.classify(e)),
        }
    }

    pub fn set_favorite(&self, company: &str, favorite: bool) -> Result<()> {
        let user = self.user_id()?;
        if self.db()?.set_favorite(&user, company, favorite)? {
            Ok(())
        } else {
            Err(AppError::new("not_found", "Unknown company."))
        }
    }

    // ---------------------------------------------------------------- workspace synchronization
    /// Fetch the company's workspace if the server's version differs (ETag), validate it, and update the cache only with a
    /// NEWER, well-formed configuration. Pending local operations are never touched.
    pub async fn refresh_workspace(&self, org: &str, company: &str) -> Result<RefreshOutcome> {
        let user = self.user_id()?;
        let cached = self.db()?.get_workspace(&user, company)?;
        let cached_ws = match &cached {
            Some(c) => workspace::parse(&c.data, company).ok(),
            None => None,
        };
        let res = self
            .api
            .get_if_changed(&format!("/companies/{company}/workspace"), Some(org), cached.as_ref().and_then(|c| c.etag.as_deref()))
            .await;
        let res = match res {
            Ok(r) => r,
            Err(e) => {
                let e = self.classify(e);
                let _ = self.db()?.record_sync(&user, company, false, Some(&e.code), None);
                if !e.is_session_expired() {
                    let _ = self.db()?.add_event(&user, "warn", "sync_failed", &format!("Workspace synchronization failed: {}", e.message));
                }
                return Err(e);
            }
        };
        if res.not_modified {
            let v = cached.as_ref().map(|c| c.version).unwrap_or(0);
            self.db()?.record_sync(&user, company, true, None, Some(v))?;
            return Ok(RefreshOutcome { changed: false, version: v, summary: None, changes: vec![] });
        }
        let fresh = match workspace::parse(&res.body, company) {
            Ok(w) => w,
            Err(e) => {
                self.log.error("workspace_rejected", &e.code);
                self.db()?.record_sync(&user, company, false, Some(&e.code), None)?;
                self.db()?.add_event(
                    &user,
                    "error",
                    "sync_rejected",
                    "The server sent a workspace the app could not use; the previous one is still shown.",
                )?;
                return Err(e);
            }
        };
        if let Some(c) = &cached {
            if fresh.version < c.version {
                // never overwrite newer cached data with an older response (stale replica / replayed answer)
                self.db()?.record_sync(&user, company, false, Some("stale_response"), None)?;
                return Err(
                    AppError::new("stale_response", "The server answered with an older workspace version; it was ignored.").retryable()
                );
            }
        }
        let changes = cached_ws.as_ref().map(|old| workspace::diff(old, &fresh)).unwrap_or_default();
        let summary = if cached.is_some() { Some(workspace::summarize(&changes)) } else { None };
        {
            let d = self.db()?;
            d.put_workspace(&user, company, fresh.version, res.etag.as_deref(), &res.body)?;
            d.record_sync(&user, company, true, None, Some(fresh.version))?;
            if let (Some(s), true) = (&summary, !changes.is_empty()) {
                d.add_event(&user, "info", "workspace_updated", s)?;
            }
        }
        Ok(RefreshOutcome { changed: true, version: fresh.version, summary, changes })
    }

    /// The workspace to show: refreshed from the server when reachable, otherwise the sealed cache (marked stale).
    pub async fn workspace(&self, org: &str, company: &str) -> Result<WorkspaceView> {
        let user = self.user_id()?;
        let refresh = self.refresh_workspace(org, company).await;
        if let Err(e) = &refresh {
            if e.is_session_expired() || matches!(e.status, Some(403) | Some(404)) {
                return Err(self.classify(e.clone()));
            }
        }
        let d = self.db()?;
        let cached = d.get_workspace(&user, company)?;
        let sync = d.get_sync(&user, company)?.unwrap_or_default();
        match cached {
            Some(c) => Ok(WorkspaceView {
                company_id: company.into(),
                version: c.version,
                fetched_at: c.fetched_at,
                data: c.data,
                stale: refresh.is_err(),
                last_success_at: sync.last_success_at,
                last_error: sync.last_error,
            }),
            None => Err(refresh.err().unwrap_or_else(|| AppError::new("workspace_unavailable", "No workspace is available yet."))),
        }
    }

    // ---------------------------------------------------------------- storage
    pub fn storage_root(&self) -> Result<Option<PathBuf>> {
        let user = self.user_id()?;
        Ok(self.db()?.get_setting(&user, "storage_root")?.filter(|s| !s.is_empty()).map(PathBuf::from))
    }
    pub fn storage_status(&self) -> Result<Option<RootReport>> {
        match self.storage_root()? {
            None => Ok(None),
            Some(p) => Ok(Some(storage::inspect_root(&p, false)?)),
        }
    }
    pub fn set_storage_root(&self, new: &Path) -> Result<RootReport> {
        let user = self.user_id()?;
        let old = self.storage_root()?;
        let report = storage::validate_new_root(new, old.as_deref())?;
        self.db()?.set_setting(&user, "storage_root", &new.display().to_string())?;
        self.log.info("storage_root_changed", "storage location updated");
        Ok(report)
    }
    /// Create (never overwrite) the local folder of one company. The name is chosen once and then kept, so renaming the
    /// company in the Control Center never orphans or moves local data.
    pub fn ensure_company_dir(&self, org: &str, company: &str) -> Result<PathBuf> {
        let user = self.user_id()?;
        let root = self.storage_root()?.ok_or_else(|| AppError::new("storage_not_configured", "Choose a storage location first."))?;
        let name = {
            let d = self.db()?;
            match d.dir_name_for(&user, company)? {
                Some(n) => n,
                None => {
                    let display = d
                        .list_companies(&user, org)?
                        .into_iter()
                        .find(|c| c.id == company)
                        .and_then(|c| c.data.get("name").and_then(Value::as_str).map(str::to_string))
                        .ok_or_else(|| AppError::new("not_found", "Unknown company."))?;
                    let n = storage::company_dir_name(&display, company, &|cand| d.dir_name_taken(&user, cand).unwrap_or(true));
                    d.set_dir_name(&user, company, &n)?;
                    n
                }
            }
        };
        storage::ensure_company_dir(&root, &name)
    }

    // ---------------------------------------------------------------- device
    /// Register this installation (first time) or bind the new session to it and record activity. A device the server no
    /// longer knows as active means it was revoked: the cache is wiped and the session cannot continue.
    pub async fn ensure_device(&self, org: &str, name: &str) -> Result<LocalDevice> {
        let user = self.user_id()?;
        let existing = self.db()?.get_device(&user, org)?;
        if let Some(d) = existing {
            return match self.api.post(&format!("/devices/{}/session", d.device_id), Some(org), None).await {
                Ok(r) => {
                    let updated = LocalDevice {
                        last_seen_at: r.body.get("last_seen_at").and_then(Value::as_str).map(str::to_string),
                        status: "active".into(),
                        ..d
                    };
                    self.db()?.put_device(&user, org, &updated)?;
                    Ok(updated)
                }
                Err(e) if e.status == Some(404) => {
                    let revoked = LocalDevice { status: "revoked".into(), ..d };
                    self.db()?.put_device(&user, org, &revoked)?;
                    self.log.warn("device_revoked", "server no longer lists this device as active");
                    let _ = self.db()?.add_event(
                        &user,
                        "error",
                        "device_revoked",
                        "This device was revoked. Sign in again and register it, or contact your administrator.",
                    );
                    Err(AppError::new("device_revoked", "This device has been revoked. Contact your administrator or register it again.")
                        .with_status(404))
                }
                Err(e) => Err(self.classify(e)),
            };
        }
        let body = json!({ "platform": "windows", "name": name, "app_version": self.app_version });
        let r = self.api.post("/devices", Some(org), Some(body)).await.map_err(|e| self.classify(e))?;
        let s = |k: &str| r.body.get(k).and_then(Value::as_str).map(str::to_string);
        let dev = LocalDevice {
            device_id: s("id").ok_or_else(|| AppError::malformed("device"))?,
            name: name.into(),
            app_version: self.app_version.clone(),
            registered_at: s("registered_at").unwrap_or_else(db::now),
            last_seen_at: s("last_seen_at"),
            status: "active".into(),
        };
        self.db()?.put_device(&user, org, &dev)?;
        Ok(dev)
    }
    pub fn local_device(&self, org: &str) -> Result<Option<LocalDevice>> {
        let user = self.user_id()?;
        self.db()?.get_device(&user, org)
    }

    // ---------------------------------------------------------------- helpers
    fn note_offline(&self, user: &str, what: &str) {
        self.log.info("offline_cache", what);
        let _ = self.db().and_then(|d| {
            d.add_event(user, "warn", "offline", &format!("Showing the saved copy of your {what}; the server could not be reached."))
        });
    }
    /// A 401 that survived the refresh attempt means the session is over: clean up and tell the UI to sign in again.
    pub fn classify(&self, e: AppError) -> AppError {
        if e.is_session_expired() {
            self.session_ended("session_expired");
        }
        e
    }
}

/// Name shown in the Control Center device list (the Windows computer name).
pub fn device_name() -> String {
    hostname::get()
        .ok()
        .and_then(|h| h.into_string().ok())
        .filter(|h| !h.trim().is_empty())
        .map(|h| h.chars().take(100).collect())
        .unwrap_or_else(|| "Windows PC".into())
}
