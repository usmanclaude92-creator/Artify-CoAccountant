//! Excel commands (Phase 9): one allow-listed passthrough plus the two file transfers. The webview never supplies a file
//! path: the template is chosen, and the export's destination is chosen, in native dialogs handled here in Rust.

use artify_core::error::{AppError, Result};
use artify_core::xl::UploadMeta;
use serde_json::Value;
use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;

use crate::state::AppState;

#[tauri::command]
pub async fn xl_call(
    st: State<'_, AppState>,
    org: String,
    company: String,
    op: String,
    ids: Vec<String>,
    query: Vec<(String, String)>,
    body: Option<Value>,
) -> Result<Value> {
    st.desk.xl_call(&org, &company, &op, &ids, &query, body.unwrap_or(Value::Null)).await
}

/// Choose a workbook in the native dialog and upload it. `None` when the person cancels.
#[tauri::command]
pub async fn xl_upload_pick(
    app: AppHandle,
    st: State<'_, AppState>,
    org: String,
    company: String,
    meta: UploadMeta,
) -> Result<Option<Value>> {
    let picked = tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().add_filter("Excel workbook", &["xlsx", "xlsm"]).blocking_pick_file()
    })
    .await
    .map_err(|_| AppError::new("dialog_error", "The file dialog could not be opened."))?;
    let Some(path) = picked.and_then(|p| p.into_path().ok()) else { return Ok(None) };
    Ok(Some(st.desk.xl_upload(&org, &company, &path, &meta).await?))
}

/// Ask where to save a completed export, then download and verify it there. `None` when the person cancels.
#[tauri::command]
pub async fn xl_export_save(
    app: AppHandle,
    st: State<'_, AppState>,
    org: String,
    company: String,
    export_id: String,
    suggested_name: String,
) -> Result<Option<String>> {
    let name: String = suggested_name.chars().filter(|c| !"\\/:*?\"<>|".contains(*c)).take(150).collect();
    let picked = tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().add_filter("Excel workbook", &["xlsx", "xlsm"]).set_file_name(name).blocking_save_file()
    })
    .await
    .map_err(|_| AppError::new("dialog_error", "The file dialog could not be opened."))?;
    let Some(dest) = picked.and_then(|p| p.into_path().ok()) else { return Ok(None) };
    let saved = st.desk.xl_export_save(&org, &company, &export_id, &dest).await?;
    Ok(Some(saved.display().to_string()))
}
