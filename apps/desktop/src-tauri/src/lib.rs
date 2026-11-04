//! Artify Co-Accountant Desktop — Tauri shell. The webview gets ONLY the commands registered below; there is no file-system,
//! shell, HTTP or credential capability exposed to it. Tokens stay in Rust (memory + Windows Credential Manager).

mod commands;
mod state;

use std::sync::Arc;

use artify_core::api::{ApiClient, ApiConfig};
use artify_core::db::Db;
use artify_core::desktop::Desktop;
use artify_core::logs::Logger;
use artify_core::seal::Sealer;
use artify_core::vault::{OsVault, SecretStore};
use tauri::Manager;

pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

/// API base: runtime `ARTIFY_API_URL`, else the value baked in at build time. Debug builds fall back to a local server;
/// release builds without configuration refuse to guess.
fn api_base() -> Option<String> {
    let v =
        std::env::var("ARTIFY_API_URL").ok().or_else(|| option_env!("ARTIFY_API_URL").map(str::to_string)).filter(|s| !s.trim().is_empty());
    match v {
        Some(v) => Some(v),
        None if cfg!(debug_assertions) => Some("http://localhost:8000/api/v1".into()),
        None => None,
    }
}

fn is_secure_enough(url: &str) -> bool {
    url.starts_with("https://") || url.starts_with("http://localhost") || url.starts_with("http://127.0.0.1")
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .on_window_event(state::on_window_event)
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let log = Logger::new(&data_dir.join("logs"))?;
            let vault: Arc<dyn SecretStore> = Arc::new(OsVault::new("ArtifyCoAccountant"));
            let base = api_base();
            let configured = base.as_deref().is_some_and(is_secure_enough);
            let cfg = ApiConfig::new(base.as_deref().filter(|_| configured).unwrap_or("http://invalid.invalid/api/v1"), APP_VERSION);
            let api = Arc::new(ApiClient::new(cfg, vault.clone())?);
            // a database that cannot be sealed (no vault) must stop the app, never fall back to plaintext
            let sealer = Sealer::from_vault(vault.as_ref())?;
            let (db, report) = Db::open(&data_dir.join("artify.db"), sealer)?;
            if let Some(q) = &report.recovered_from {
                log.warn("db_recovered", &format!("damaged local database quarantined at {q}"));
            }
            log.info("startup", &format!("version {APP_VERSION}, api configured: {configured}"));
            let desk = Arc::new(Desktop::new(api, db, report, log, &data_dir, APP_VERSION));
            app.manage(state::AppState::new(desk.clone(), configured, base));
            state::spawn_sync_loop(app.handle().clone(), desk.clone());
            state::spawn_import_loop(app.handle().clone(), desk);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::app_info,
            commands::auth_login,
            commands::auth_mfa,
            commands::auth_restore,
            commands::auth_logout,
            commands::organizations,
            commands::companies,
            commands::company_select,
            commands::company_favorite,
            commands::company_assignments,
            commands::workspace_get,
            commands::workspace_refresh,
            commands::settings_get,
            commands::settings_set,
            commands::storage_pick,
            commands::storage_set_root,
            commands::storage_status,
            commands::company_dir,
            commands::device_ensure,
            commands::devices_list,
            commands::device_revoke,
            commands::sync_status,
            commands::notifications_list,
            commands::notifications_read,
            commands::diagnostics_export,
            commands::update_check,
            commands::files_pick,
            commands::imports_queue,
            commands::imports_list,
            commands::imports_counts,
            commands::import_cancel,
            commands::import_retry,
            commands::import_remove,
            commands::imports_process,
            commands::documents_op,
            commands::document_preview,
            commands::document_download,
            commands::document_open,
            commands::mirror_status,
            commands::mirror_reconcile,
            commands::mirror_sync,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Artify Co-Accountant");
}
