use std::sync::{Arc, Mutex};
use std::time::Duration;

use artify_core::desktop::Desktop;
use serde::Serialize;
use tauri::{AppHandle, DragDropEvent, Emitter, Manager, Window, WindowEvent};

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

/// Files dropped on the window are staged here (the core remembers the path behind an opaque token) and the UI is told
/// the tokens, names and sizes. Nothing is queued until the user chooses a destination in the UI.
pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if let WindowEvent::DragDrop(DragDropEvent::Drop { paths, .. }) = event {
        let Some(state) = window.try_state::<AppState>() else { return };
        if state.desk.user_id().is_err() {
            return;
        }
        let staged = state.desk.stage_paths(paths.clone());
        let _ = window.emit("files-dropped", &staged);
    }
}

/// Every 10 s run the uploads that are due (queued, or waiting for a retry) with up to three in parallel, and tell the UI
/// when something changed. Uploads that were interrupted by closing the app are put back in the queue at sign-in.
pub fn spawn_import_loop(app: AppHandle, desk: Arc<Desktop>) {
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(10)).await;
            let Some(state) = app.try_state::<AppState>() else { continue };
            if !state.api_configured || desk.user_id().is_err() {
                continue;
            }
            match desk.process_due(3).await {
                Ok(0) => {}
                Ok(_) => {
                    state.set_connectivity(true);
                    let _ = app.emit("imports-updated", ());
                }
                Err(e) if e.is_session_expired() => {
                    let _ = app.emit("session-expired", ());
                }
                Err(e) => {
                    let _ = app.emit("sync-error", &e);
                }
            }
        }
    });
}
