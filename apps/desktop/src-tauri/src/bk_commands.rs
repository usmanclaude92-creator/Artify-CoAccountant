//! Daily bookkeeping command (Phase 10): one allow-listed passthrough. The operation name, identifiers, dates and filters are
//! validated in the core; the server validates, authorises and decides everything.

use artify_core::error::Result;
use serde_json::Value;
use tauri::State;

use crate::state::AppState;

#[tauri::command]
pub async fn bk_call(
    st: State<'_, AppState>,
    org: String,
    company: String,
    op: String,
    ids: Vec<String>,
    query: Vec<(String, String)>,
    body: Option<Value>,
) -> Result<Value> {
    st.desk.bk_call(&org, &company, &op, &ids, &query, body.unwrap_or(Value::Null)).await
}
