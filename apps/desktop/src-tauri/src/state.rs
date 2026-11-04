use std::sync::{Arc, Mutex};
use std::time::Duration;

use artify_core::desktop::Desktop;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

#[derive(Debug, Clone, Serialize, Default)]
pub struct Connectivity {
    /// unknown | online | offline
    pub state: String,
    pub last_checked_at: Option<String>,
}

pub struct AppState {
    pub desk: Arc<Desktop>,
    pub api_configured: bool,
    pub api_base: Option<String>,
    /// (organization id, company id) currently open; the background sync refreshes this one
    pub selected: Mutex<Option<(String, String)>>,
    pub connectivity: Mutex<Connectivity>,
}

impl AppState {
    pub fn new(desk: Arc<Desktop>, api_configured: bool, api_base: Option<String>) -> Self {
        Self {
            desk,
            api_configured,
            api_base,
            selected: Mutex::new(None),
            connectivity: Mutex::new(Connectivity { state: "unknown".into(), last_checked_at: None }),
        }
    }
    pub fn set_connectivity(&self, online: bool) {
        if let Ok(mut c) = self.connectivity.lock() {
            c.state = if online { "online" } else { "offline" }.into();
            c.last_checked_at = Some(chrono::Utc::now().to_rfc3339());
        }
    }
}

/// Every 60 s, revalidate the open company's workspace with an ETag request (cheap when nothing changed) and tell the UI.
pub fn spawn_sync_loop(app: AppHandle, desk: Arc<Desktop>) {
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(60)).await;
            let Some(state) = app.try_state::<AppState>() else { continue };
            let target = state.selected.lock().ok().and_then(|s| s.clone());
            if desk.user_id().is_err() {
                continue;
            }
            // unfinished imports are retried in the background (does nothing when the queue is empty or offline)
            let _ = desk.queue_run().await;
            let Some((org, company)) = target else { continue };
            match desk.refresh_workspace(&org, &company).await {
                Ok(out) => {
                    state.set_connectivity(true);
                    if out.changed && out.summary.is_some() {
                        let _ = app.emit("workspace-updated", &out);
                    }
                }
                Err(e) if e.is_session_expired() => {
                    let _ = app.emit("session-expired", ());
                }
                Err(e) => {
                    state.set_connectivity(!e.retryable || e.status.is_some());
                    let _ = app.emit("sync-error", &e);
                }
            }
        }
    });
}
