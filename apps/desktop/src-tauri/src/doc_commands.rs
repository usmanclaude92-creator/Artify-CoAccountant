//! Document commands. Like the rest of the command surface they take identifiers and plain data. Local file paths NEVER
//! come from the webview: files are chosen in a native dialog or dropped on the window, both handled here in Rust, which
//! hands the interface opaque staged-file ids.

use std::path::{Path, PathBuf};

use artify_core::desktop::Desktop;
use artify_core::docs::{Preview, SavedCopy};
use artify_core::error::{AppError, Result};
use artify_core::mirror::MirrorReport;
use artify_core::queue::{QueueAdd, QueueView, RunSummary, StagedFile};
use serde_json::Value;
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_dialog::DialogExt;

use crate::state::AppState;

type St<'a> = State<'a, AppState>;

// ------------------------------------------------------------------ importing
#[tauri::command]
pub async fn import_pick(app: AppHandle, st: St<'_>) -> Result<Vec<StagedFile>> {
    let picked = tauri::async_runtime::spawn_blocking(move || app.dialog().file().blocking_pick_files())
        .await
        .map_err(|_| AppError::new("dialog_error", "The file dialog could not be opened."))?;
    let paths: Vec<PathBuf> = picked.unwrap_or_default().into_iter().filter_map(|p| p.into_path().ok()).collect();
    if paths.is_empty() {
        return Ok(vec![]);
    }
    st.desk.stage_paths(paths).await
}

/// Called from the window's drag-and-drop event handler (Rust side), never from the webview.
pub async fn stage_dropped(app: AppHandle, desk: std::sync::Arc<Desktop>, paths: Vec<PathBuf>) {
    match desk.stage_paths(paths).await {
        Ok(files) => {
            let _ = app.emit("files-dropped", &files);
        }
        Err(e) => {
            let _ = app.emit("files-drop-failed", &e);
        }
    }
}

#[tauri::command]
pub fn import_unstage(st: St, id: String) {
    st.desk.unstage(&id);
}

#[tauri::command]
pub fn queue_add(st: St, org: String, company: String, items: Vec<QueueAdd>) -> Result<Vec<QueueView>> {
    let out = st.desk.queue_add(&org, &company, items)?;
    kick(&st);
    Ok(out)
}

fn kick(st: &AppState) {
    let desk = st.desk.clone();
    tauri::async_runtime::spawn(async move {
        let _ = desk.queue_run().await;
    });
}

#[tauri::command]
pub fn queue_list(st: St, company: Option<String>) -> Result<Vec<QueueView>> {
    st.desk.queue_list(company.as_deref())
}
#[tauri::command]
pub async fn queue_run(st: St<'_>) -> Result<RunSummary> {
    st.desk.queue_run().await
}
#[tauri::command]
pub fn queue_retry(st: St, id: String) -> Result<QueueView> {
    let v = st.desk.queue_retry(&id)?;
    kick(&st);
    Ok(v)
}
#[tauri::command]
pub async fn queue_cancel(st: St<'_>, id: String) -> Result<QueueView> {
    st.desk.queue_cancel(&id).await
}
#[tauri::command]
pub fn queue_remove(st: St, id: String) -> Result<bool> {
    st.desk.queue_remove(&id)
}
#[tauri::command]
pub fn queue_clear_finished(st: St, company: Option<String>) -> Result<usize> {
    st.desk.queue_clear_finished(company.as_deref())
}

// ------------------------------------------------------------------ documents
#[tauri::command]
pub async fn upload_config(st: St<'_>, org: String, company: String) -> Result<Value> {
    st.desk.upload_config(&org, &company).await
}
#[tauri::command]
pub async fn docs_list(st: St<'_>, org: String, company: String, query: Value) -> Result<Value> {
    st.desk.docs_list(&org, &company, &query).await
}
#[tauri::command]
pub async fn docs_counts(st: St<'_>, org: String, company: String) -> Result<Value> {
    st.desk.docs_counts(&org, &company).await
}
#[tauri::command]
pub async fn docs_get(st: St<'_>, org: String, company: String, doc: String) -> Result<Value> {
    st.desk.docs_get(&org, &company, &doc).await
}
#[tauri::command]
pub async fn docs_events(st: St<'_>, org: String, company: String, doc: Option<String>) -> Result<Value> {
    st.desk.docs_events(&org, &company, doc.as_deref()).await
}
#[tauri::command]
pub async fn docs_patch(st: St<'_>, org: String, company: String, doc: String, patch: Value) -> Result<Value> {
    st.desk.docs_patch(&org, &company, &doc, patch).await
}
#[tauri::command]
pub async fn docs_move(
    st: St<'_>,
    org: String,
    company: String,
    doc: String,
    node: String,
    category: Option<String>,
    base_row_version: Option<i64>,
) -> Result<Value> {
    st.desk.docs_move(&org, &company, &doc, &node, category.as_deref(), base_row_version).await
}
#[tauri::command]
pub async fn docs_action(st: St<'_>, org: String, company: String, doc: String, action: String, body: Option<Value>) -> Result<Value> {
    st.desk.docs_action(&org, &company, &doc, &action, body).await
}
#[tauri::command]
pub async fn docs_restore_version(st: St<'_>, org: String, company: String, doc: String, version: i64) -> Result<Value> {
    st.desk.docs_restore_version(&org, &company, &doc, version).await
}
#[tauri::command]
pub async fn docs_suggest(st: St<'_>, org: String, company: String, body: Value) -> Result<Value> {
    st.desk.docs_suggest(&org, &company, body).await
}
#[tauri::command]
pub async fn docs_duplicates(st: St<'_>, org: String, company: String, sha256: String) -> Result<Value> {
    st.desk.docs_duplicates(&org, &company, &sha256).await
}
#[tauri::command]
pub async fn docs_preview(st: St<'_>, org: String, company: String, doc: String, version: Option<i64>) -> Result<Preview> {
    st.desk.docs_preview(&org, &company, &doc, version).await
}
#[tauri::command]
pub async fn docs_save_copy(st: St<'_>, org: String, company: String, doc: String, version: Option<i64>) -> Result<SavedCopy> {
    st.desk.docs_save_copy(&org, &company, &doc, version).await
}
/// Save (or reuse) the local copy and open it with the program Windows associates with the file type.
#[tauri::command]
pub async fn docs_open(st: St<'_>, org: String, company: String, doc: String, version: Option<i64>) -> Result<SavedCopy> {
    let saved = st.desk.docs_save_copy(&org, &company, &doc, version).await?;
    open_in_os(Path::new(&saved.path))?;
    Ok(saved)
}

// ------------------------------------------------------------------ local folders
#[tauri::command]
pub fn mirror_sync(st: St, org: String, company: String) -> Result<MirrorReport> {
    st.desk.mirror_sync(&org, &company)
}
#[tauri::command]
pub fn mirror_open(st: St, org: String, company: String, node: Option<String>) -> Result<String> {
    let dir = st.desk.mirror_folder(&org, &company, node.as_deref())?;
    open_in_os(&dir)?;
    Ok(dir.display().to_string())
}

/// Open a file or folder with the OS, passing the path as ONE argument (no shell, no interpolation). Only paths the core
/// derived inside the configured storage root reach this function.
fn open_in_os(path: &Path) -> Result<()> {
    #[cfg(target_os = "windows")]
    let mut cmd = std::process::Command::new("explorer");
    #[cfg(target_os = "macos")]
    let mut cmd = std::process::Command::new("open");
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let mut cmd = std::process::Command::new("xdg-open");
    cmd.arg(path).spawn().map(|_| ()).map_err(|_| AppError::new("open_failed", "The file or folder could not be opened."))
}
