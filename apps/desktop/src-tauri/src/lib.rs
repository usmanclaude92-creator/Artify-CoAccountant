//! Artify Co-Accountant Desktop — Tauri shell. The webview gets ONLY the commands registered below; there is no file-system,
//! shell, HTTP or credential capability exposed to it. Tokens stay in Rust (memory + Windows Credential Manager).

mod acct_commands;
mod commands;
mod doc_commands;
mod intel_commands;
mod state;

use std::sync::Arc;

use artify_core::api::{ApiClient, ApiConfig};
use artify_core::db::Db;
use artify_core::desktop::Desktop;
use artify_core::logs::Logger;
use artify_core::seal::Sealer;
use artify_core::vault::{OsVault, SecretStore};
use tauri::{Emitter, Manager};

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
            let handle = app.handle().clone();
            desk.set_emitter(Arc::new(move |name, payload| {
                let _ = handle.emit(name, payload);
            }));
            // files dropped on the window are staged here, in Rust: the webview never sees a path
            if let Some(win) = app.get_webview_window("main") {
                let (h, d) = (app.handle().clone(), desk.clone());
                win.on_window_event(move |ev| {
                    if let tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) = ev {
                        let (h, d, paths) = (h.clone(), d.clone(), paths.clone());
                        tauri::async_runtime::spawn(doc_commands::stage_dropped(h, d, paths));
                    }
                });
            }
            state::spawn_sync_loop(app.handle().clone(), desk);
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
            doc_commands::import_pick,
            doc_commands::import_unstage,
            doc_commands::queue_add,
            doc_commands::queue_list,
            doc_commands::queue_run,
            doc_commands::queue_retry,
            doc_commands::queue_cancel,
            doc_commands::queue_remove,
            doc_commands::queue_clear_finished,
            doc_commands::upload_config,
            doc_commands::docs_list,
            doc_commands::docs_counts,
            doc_commands::docs_get,
            doc_commands::docs_events,
            doc_commands::docs_patch,
            doc_commands::docs_move,
            doc_commands::docs_action,
            doc_commands::docs_restore_version,
            doc_commands::docs_suggest,
            doc_commands::docs_duplicates,
            doc_commands::docs_preview,
            doc_commands::docs_save_copy,
            doc_commands::docs_open,
            doc_commands::mirror_sync,
            doc_commands::mirror_open,
            acct_commands::acct_call,
            intel_commands::intel_start,
            intel_commands::intel_latest,
            intel_commands::intel_queue,
            intel_commands::intel_jobs,
            intel_commands::intel_job_action,
            intel_commands::intel_extraction,
            intel_commands::intel_extractions,
            intel_commands::intel_ocr,
            intel_commands::intel_review,
            intel_commands::intel_add_transaction,
            intel_commands::intel_statement,
            intel_commands::intel_classification,
            intel_commands::intel_decide,
            intel_commands::intel_accept,
            intel_commands::intel_split,
            intel_commands::intel_page_image,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Artify Co-Accountant");
}
