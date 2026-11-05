//! Cloud synchronization commands (Phase 12): pull the server's change feed, report the honest status, and reach the fixed
//! conflict routes. The core validates the operation name, identifiers and filters; the server verifies and decides everything.

use artify_core::error::Result;
use artify_core::sync::{PullSummary, SyncStatus};
use serde_json::Value;
use tauri::State;

use crate::state::AppState;

#[tauri::command]
pub async fn cloud_sync_pull(st: State<'_, AppState>, org: String, company: String) -> Result<PullSummary> {
    st.desk.sync_pull(&org, &company).await
}

#[tauri::command]
pub async fn cloud_sync_status(st: State<'_, AppState>, org: String, company: String) -> Result<SyncStatus> {
    st.desk.sync_status(&org, &company).await
}

#[tauri::command]
pub async fn cloud_sync_call(
    st: State<'_, AppState>,
    org: String,
    company: String,
    op: String,
    arg: Option<String>,
    query: Vec<(String, String)>,
    body: Option<Value>,
) -> Result<Value> {
    st.desk.sync_call(&org, &company, &op, arg.as_deref(), &query, body.unwrap_or(Value::Null)).await
}
