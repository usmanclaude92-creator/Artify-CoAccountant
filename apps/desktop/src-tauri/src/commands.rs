//! The complete command surface of the app. Each command takes plain identifiers and returns plain data; authorization
//! is decided by the backend on every call, and no command accepts a file path except the three storage commands, which
//! validate it in `artify_core::storage`.

use std::path::PathBuf;

use artify_core::api::LoginOutcome;
use artify_core::db;
use artify_core::desktop::{self, CompanyView, Identity, Listing, RefreshOutcome, WorkspaceView};
use artify_core::error::{AppError, Result};
use artify_core::logs::redact;
use artify_core::settings;
use artify_core::storage::RootReport;
use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;

use crate::state::AppState;
use crate::APP_VERSION;

type St<'a> = State<'a, AppState>;

fn need_api(st: &AppState) -> Result<()> {
    if st.api_configured {
        Ok(())
    } else {
        Err(AppError::new("api_not_configured", "This build has no server address configured. Set ARTIFY_API_URL (https) and restart."))
    }
}

#[derive(Serialize)]
pub struct AppInfo {
    pub name: &'static str,
    pub version: &'static str,
    pub build: &'static str,
    pub os: &'static str,
    pub api_configured: bool,
    pub api_base: Option<String>,
    pub db_schema_version: i64,
    pub db_recovered_from: Option<String>,
    pub data_dir: String,
    pub update_configured: bool,
}

#[tauri::command]
pub fn app_info(st: St) -> AppInfo {
    AppInfo {
        name: "Artify Co-Accountant",
        version: APP_VERSION,
        build: option_env!("ARTIFY_BUILD_ID").unwrap_or("local"),
        os: std::env::consts::OS,
        api_configured: st.api_configured,
        api_base: st.api_base.clone().filter(|_| st.api_configured),
        db_schema_version: st.desk.open_report.schema_version,
        db_recovered_from: st.desk.open_report.recovered_from.clone(),
        data_dir: st.desk.data_dir.display().to_string(),
        update_configured: option_env!("ARTIFY_UPDATER_PUBKEY").is_some_and(|k| !k.is_empty()),
    }
}

// ------------------------------------------------------------------ authentication
#[derive(Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum LoginResult {
    Ok { identity: Identity },
    MfaRequired { mfa_token: String },
    MfaEnrollmentRequired,
}

#[tauri::command]
pub async fn auth_login(st: St<'_>, email: String, password: String) -> Result<LoginResult> {
    need_api(&st)?;
    Ok(match st.desk.login(email.trim(), &password).await? {
        LoginOutcome::Authenticated => LoginResult::Ok { identity: st.desk.current_identity()? },
        LoginOutcome::MfaRequired { mfa_token } => LoginResult::MfaRequired { mfa_token },
        LoginOutcome::MfaEnrollmentRequired => LoginResult::MfaEnrollmentRequired,
    })
}

#[tauri::command]
pub async fn auth_mfa(st: St<'_>, mfa_token: String, code: String) -> Result<Identity> {
    need_api(&st)?;
    st.desk.verify_mfa(&mfa_token, code.trim()).await
}

#[tauri::command]
pub async fn auth_restore(st: St<'_>) -> Result<Option<Identity>> {
    if !st.api_configured {
        return Ok(None);
    }
    st.desk.restore().await
}

#[tauri::command]
pub async fn auth_logout(st: St<'_>) -> Result<()> {
    st.desk.logout().await;
    if let Ok(mut s) = st.selected.lock() {
        *s = None;
    }
    Ok(())
}

// ------------------------------------------------------------------ organizations and companies
#[tauri::command]
pub async fn organizations(st: St<'_>) -> Result<Listing<Value>> {
    st.set_connectivity(true);
    let r = st.desk.organizations().await;
    if let Ok(l) = &r {
        st.set_connectivity(!l.stale);
    }
    r
}

#[tauri::command]
pub async fn companies(st: St<'_>, org: String) -> Result<Listing<CompanyView>> {
    let r = st.desk.companies(&org).await;
    if let Ok(l) = &r {
        st.set_connectivity(!l.stale);
    }
    r
}

#[tauri::command]
pub async fn company_select(st: St<'_>, org: String, company: String) -> Result<Value> {
    let c = st.desk.select_company(&org, &company).await?;
    if let Ok(mut s) = st.selected.lock() {
        *s = Some((org, company));
    }
    Ok(c)
}

#[tauri::command]
pub fn company_favorite(st: St, company: String, favorite: bool) -> Result<()> {
    st.desk.set_favorite(&company, favorite)
}

/// Assigned accountants, for the roles the backend allows to see them. A 403 simply means "not shown to you".
#[tauri::command]
pub async fn company_assignments(st: St<'_>, org: String, company: String) -> Result<Value> {
    match st.desk.api.get(&format!("/companies/{company}/assignments"), Some(&org)).await {
        Ok(r) => Ok(r.body),
        Err(e) if e.status == Some(403) || e.status == Some(404) => Ok(json!([])),
        Err(e) => Err(e),
    }
}

// ------------------------------------------------------------------ workspace
#[tauri::command]
pub async fn workspace_get(st: St<'_>, org: String, company: String) -> Result<WorkspaceView> {
    let r = st.desk.workspace(&org, &company).await;
    if let Ok(v) = &r {
        st.set_connectivity(!v.stale);
    }
    r
}

#[tauri::command]
pub async fn workspace_refresh(st: St<'_>, org: String, company: String) -> Result<RefreshOutcome> {
    let r = st.desk.refresh_workspace(&org, &company).await;
    st.set_connectivity(r.as_ref().map(|_| true).unwrap_or_else(|e| !e.retryable));
    r
}

// ------------------------------------------------------------------ settings
const DEFAULTS: &[(&str, &str)] = &[
    ("theme", "system"),
    ("language", "en"),
    ("start_on_login", "false"),
    ("default_company_id", ""),
    ("notifications", "true"),
    ("update_channel", "stable"),
];

#[tauri::command]
pub fn settings_get(st: St) -> Result<Value> {
    let user = st.desk.user_id()?;
    let mut out = serde_json::Map::new();
    for (k, d) in DEFAULTS {
        let v = st.desk.with_db(|db| db.get_setting(&user, k))?.unwrap_or_else(|| d.to_string());
        out.insert(k.to_string(), Value::String(v));
    }
    Ok(Value::Object(out))
}

#[tauri::command]
pub fn settings_set(st: St, key: String, value: String) -> Result<()> {
    if key == "storage_root" {
        return Err(AppError::new("invalid_setting", "Use the storage location controls to change the folder."));
    }
    settings::validate(&key, &value)?;
    let user = st.desk.user_id()?;
    st.desk.with_db(|db| db.set_setting(&user, &key, &value))
}

// ------------------------------------------------------------------ storage (the only commands that touch paths)
#[tauri::command]
pub async fn storage_pick(app: AppHandle) -> Result<Option<String>> {
    let picked = tauri::async_runtime::spawn_blocking(move || app.dialog().file().blocking_pick_folder())
        .await
        .map_err(|_| AppError::new("dialog_error", "The folder dialog could not be opened."))?;
    Ok(picked.and_then(|p| p.into_path().ok()).map(|p| p.display().to_string()))
}

#[tauri::command]
pub fn storage_set_root(st: St, path: String) -> Result<RootReport> {
    st.desk.set_storage_root(&PathBuf::from(path))
}

#[tauri::command]
pub fn storage_status(st: St) -> Result<Option<RootReport>> {
    st.desk.storage_status()
}

#[tauri::command]
pub fn company_dir(st: St, org: String, company: String) -> Result<String> {
    Ok(st.desk.ensure_company_dir(&org, &company)?.display().to_string())
}

// ------------------------------------------------------------------ devices
#[tauri::command]
pub async fn device_ensure(st: St<'_>, org: String) -> Result<db::LocalDevice> {
    need_api(&st)?;
    let r = st.desk.ensure_device(&org, &desktop::device_name()).await;
    if let Err(e) = &r {
        if e.code == "device_revoked" {
            st.desk.session_ended("device_revoked");
        }
    }
    r
}

#[tauri::command]
pub async fn devices_list(st: St<'_>, org: String) -> Result<Value> {
    Ok(st.desk.api.get("/devices", Some(&org)).await?.body)
}

#[tauri::command]
pub async fn device_revoke(st: St<'_>, org: String, device_id: String) -> Result<()> {
    st.desk.api.delete(&format!("/devices/{device_id}"), Some(&org)).await?;
    Ok(())
}

// ------------------------------------------------------------------ sync, notifications, diagnostics, updates
#[derive(Serialize)]
pub struct SyncStatus {
    pub connectivity: crate::state::Connectivity,
    pub api_configured: bool,
    pub company: Option<db::SyncState>,
    pub cached_workspace_version: Option<i64>,
    pub pending: db::PendingSummary,
}

#[tauri::command]
pub fn sync_status(st: St, company: Option<String>) -> Result<SyncStatus> {
    let user = st.desk.user_id()?;
    let (sync, ver, pending) = st.desk.with_db(|db| {
        Ok((
            match &company {
                Some(c) => db.get_sync(&user, c)?,
                None => None,
            },
            match &company {
                Some(c) => db.workspace_version(&user, c)?,
                None => None,
            },
            db.pending_summary(&user)?,
        ))
    })?;
    Ok(SyncStatus {
        connectivity: st.connectivity.lock().map(|c| c.clone()).unwrap_or_default(),
        api_configured: st.api_configured,
        company: sync,
        cached_workspace_version: ver,
        pending,
    })
}

#[tauri::command]
pub fn notifications_list(st: St) -> Result<Vec<db::EventRow>> {
    let user = st.desk.user_id()?;
    st.desk.with_db(|db| db.list_events(&user, 100))
}

#[tauri::command]
pub fn notifications_read(st: St) -> Result<()> {
    let user = st.desk.user_id()?;
    st.desk.with_db(|db| db.mark_events_read(&user))
}

/// Support bundle: app/version/OS, database schema, sync state counters and the last redacted log lines. It never
/// contains tokens, passwords, document content, company names or workspace data.
#[tauri::command]
pub fn diagnostics_export(st: St) -> Result<String> {
    let dir = st.desk.data_dir.join("diagnostics");
    std::fs::create_dir_all(&dir)?;
    let stamp = chrono::Utc::now().format("%Y%m%dT%H%M%SZ");
    let file = dir.join(format!("artify-diagnostics-{stamp}.json"));
    let signed_in = st.desk.user_id().is_ok();
    let pending = st.desk.user_id().ok().and_then(|u| st.desk.with_db(|db| db.pending_summary(&u)).ok());
    let bundle = json!({
        "generated_at": chrono::Utc::now().to_rfc3339(),
        "app": { "name": "Artify Co-Accountant", "version": APP_VERSION, "build": option_env!("ARTIFY_BUILD_ID").unwrap_or("local"), "os": std::env::consts::OS, "arch": std::env::consts::ARCH },
        "api_configured": st.api_configured,
        "signed_in": signed_in,
        "database": { "schema_version": st.desk.open_report.schema_version, "recovered_from_corruption": st.desk.open_report.recovered_from.is_some() },
        "pending_operations": pending,
        "connectivity": st.connectivity.lock().map(|c| c.clone()).unwrap_or_default(),
        "log_tail": st.desk.log.tail(300).into_iter().map(|l| redact(&l)).collect::<Vec<_>>(),
    });
    std::fs::write(
        &file,
        serde_json::to_vec_pretty(&bundle).map_err(|_| AppError::new("diagnostics_error", "Could not build the bundle."))?,
    )?;
    Ok(file.display().to_string())
}

#[derive(Serialize)]
pub struct UpdateStatus {
    pub configured: bool,
    pub available: bool,
    pub version: Option<String>,
    pub message: String,
}

/// Update check. Until a signing public key AND an endpoint are compiled in (ARTIFY_UPDATER_PUBKEY / ARTIFY_UPDATER_ENDPOINT)
/// this reports "not configured" truthfully instead of pretending to work.
#[tauri::command]
pub async fn update_check(app: AppHandle) -> Result<UpdateStatus> {
    use tauri_plugin_updater::UpdaterExt;
    let (key, endpoint) = (option_env!("ARTIFY_UPDATER_PUBKEY").unwrap_or(""), option_env!("ARTIFY_UPDATER_ENDPOINT").unwrap_or(""));
    if key.is_empty() || endpoint.is_empty() {
        return Ok(UpdateStatus {
            configured: false,
            available: false,
            version: None,
            message: "Automatic updates are not configured in this build (no signing key or update server).".into(),
        });
    }
    let url = endpoint.parse().map_err(|_| AppError::new("update_error", "The update server address is invalid."))?;
    let updater = app
        .updater_builder()
        .pubkey(key.to_string())
        .endpoints(vec![url])
        .map_err(|_| AppError::new("update_error", "The update configuration is invalid."))?
        .build()
        .map_err(|_| AppError::new("update_error", "The updater could not be started."))?;
    match updater.check().await {
        Ok(Some(u)) => Ok(UpdateStatus {
            configured: true,
            available: true,
            version: Some(u.version.clone()),
            message: format!("Version {} is available.", u.version),
        }),
        Ok(None) => Ok(UpdateStatus { configured: true, available: false, version: None, message: "You have the latest version.".into() }),
        Err(_) => Err(AppError::new("update_error", "The update check failed. Try again later.").retryable()),
    }
}
