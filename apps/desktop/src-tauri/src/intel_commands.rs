//! Document-intelligence commands (Phase 7). Identifiers and plain data only; the review body is forwarded unchanged and the
//! server decides. Page images come back as base64 so the webview never needs a file path or a network URL.

use artify_core::error::Result;
use artify_core::intel::PageImage;
use serde_json::Value;
use tauri::State;

use crate::state::AppState;

type St<'a> = State<'a, AppState>;

#[tauri::command]
pub async fn intel_start(
    st: St<'_>,
    org: String,
    company: String,
    doc: String,
    kind: String,
    languages: Vec<String>,
    reason: Option<String>,
) -> Result<Value> {
    st.desk.intel_start(&org, &company, &doc, &kind, &languages, reason.as_deref()).await
}
#[tauri::command]
pub async fn intel_latest(st: St<'_>, org: String, company: String, doc: String) -> Result<Value> {
    st.desk.intel_latest(&org, &company, &doc).await
}
#[tauri::command]
pub async fn intel_queue(st: St<'_>, org: String, company: String) -> Result<Value> {
    st.desk.intel_queue(&org, &company).await
}
#[tauri::command]
pub async fn intel_jobs(st: St<'_>, org: String, company: String, status: Option<String>) -> Result<Value> {
    st.desk.intel_jobs(&org, &company, status.as_deref()).await
}
#[tauri::command]
pub async fn intel_job_action(st: St<'_>, org: String, company: String, job: String, action: String) -> Result<Value> {
    st.desk.intel_job_action(&org, &company, &job, &action).await
}
#[tauri::command]
pub async fn intel_extraction(st: St<'_>, org: String, company: String, extraction: String) -> Result<Value> {
    st.desk.intel_extraction(&org, &company, &extraction).await
}
#[tauri::command]
pub async fn intel_ocr(st: St<'_>, org: String, company: String, doc: String, page_from: u32, page_to: u32) -> Result<Value> {
    st.desk.intel_ocr(&org, &company, &doc, page_from, page_to).await
}
#[tauri::command]
pub async fn intel_extractions(st: St<'_>, org: String, company: String, doc: String) -> Result<Value> {
    st.desk.intel_extractions(&org, &company, &doc).await
}
#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub async fn intel_review(
    st: St<'_>,
    org: String,
    company: String,
    extraction: String,
    kind: String,
    target: String,
    action: String,
    payload: Value,
    reason: Option<String>,
) -> Result<Value> {
    st.desk.intel_review(&org, &company, &extraction, &kind, &target, &action, payload, reason.as_deref()).await
}
#[tauri::command]
pub async fn intel_add_transaction(st: St<'_>, org: String, company: String, extraction: String, data: Value) -> Result<Value> {
    st.desk.intel_add_transaction(&org, &company, &extraction, data).await
}
#[tauri::command]
pub async fn intel_statement(st: St<'_>, org: String, company: String, extraction: String, patch: Value) -> Result<Value> {
    st.desk.intel_statement(&org, &company, &extraction, patch).await
}
#[tauri::command]
pub async fn intel_classification(
    st: St<'_>,
    org: String,
    company: String,
    doc: String,
    action: String,
    type_key: Option<String>,
) -> Result<Value> {
    st.desk.intel_classification(&org, &company, &doc, &action, type_key.as_deref()).await
}
#[tauri::command]
pub async fn intel_decide(
    st: St<'_>,
    org: String,
    company: String,
    extraction: String,
    action: String,
    acknowledge: bool,
    note: Option<String>,
) -> Result<Value> {
    st.desk.intel_decide(&org, &company, &extraction, &action, acknowledge, note.as_deref()).await
}
#[tauri::command]
pub async fn intel_accept(
    st: St<'_>,
    org: String,
    company: String,
    extraction: String,
    kind: String,
    suggestion: String,
    value: Option<Value>,
) -> Result<Value> {
    st.desk.intel_accept(&org, &company, &extraction, &kind, &suggestion, value).await
}
#[tauri::command]
pub async fn intel_split(st: St<'_>, org: String, company: String, extraction: String, approve: bool) -> Result<Value> {
    st.desk.intel_split(&org, &company, &extraction, approve).await
}
#[tauri::command]
pub async fn intel_page_image(st: St<'_>, org: String, company: String, doc: String, page: u32) -> Result<PageImage> {
    st.desk.intel_page_image(&org, &company, &doc, page).await
}
