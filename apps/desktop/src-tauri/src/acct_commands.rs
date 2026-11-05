//! Accounting command (Phase 8): one allow-listed passthrough. The operation name, identifiers and filters are validated in
//! the core; the server performs and decides everything.

use artify_core::error::Result;
use serde_json::Value;
use tauri::State;

use crate::state::AppState;

#[tauri::command]
pub async fn acct_call(
    st: State<'_, AppState>,
    org: String,
    company: String,
    op: String,
    ids: Vec<String>,
    query: Vec<(String, String)>,
    body: Option<Value>,
) -> Result<Value> {
    st.desk.acct_call(&org, &company, &op, &ids, &query, body.unwrap_or(Value::Null)).await
}
