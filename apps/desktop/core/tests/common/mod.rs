#![allow(dead_code)]
use std::sync::Arc;
use std::time::Duration;

use artify_core::api::{ApiClient, ApiConfig};
use artify_core::db::Db;
use artify_core::desktop::Desktop;
use artify_core::logs::Logger;
use artify_core::seal::Sealer;
use artify_core::vault::MemoryStore;
use serde_json::{json, Value};
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

pub fn cfg(server: &MockServer) -> ApiConfig {
    let mut c = ApiConfig::new(&format!("{}/api/v1", server.uri()), "0.1.0-test");
    c.request_timeout = Duration::from_millis(400);
    c.retry_base = Duration::from_millis(5);
    c
}

pub fn client(server: &MockServer) -> (Arc<ApiClient>, Arc<MemoryStore>) {
    let store = Arc::new(MemoryStore::new());
    (Arc::new(ApiClient::new(cfg(server), store.clone()).unwrap()), store)
}

pub fn tokens(access: &str, refresh: &str) -> Value {
    json!({"access_token": access, "refresh_token": refresh, "token_type": "bearer", "expires_in": 900, "mfa": true})
}

pub fn ok_login(access: &str, refresh: &str) -> Value {
    json!({"status": "ok", "tokens": tokens(access, refresh), "mfa_token": null, "mfa_enrollment_required": false})
}

pub fn problem(status: u16, code: &str, detail: &str) -> ResponseTemplate {
    ResponseTemplate::new(status).insert_header("content-type", "application/problem+json").set_body_json(json!({
        "type": format!("https://api.artify.example/problems/{code}"), "title": "x", "status": status, "code": code, "detail": detail, "instance": "/x"
    }))
}

pub async fn mount_json(server: &MockServer, m: &str, p: &str, body: Value) {
    Mock::given(method(m))
        .and(path(format!("/api/v1{p}")))
        .respond_with(ResponseTemplate::new(200).set_body_json(body))
        .mount(server)
        .await;
}
pub async fn mount_authed(server: &MockServer, m: &str, p: &str, bearer: &str, body: Value) {
    Mock::given(method(m))
        .and(path(format!("/api/v1{p}")))
        .and(header("authorization", format!("Bearer {bearer}").as_str()))
        .respond_with(ResponseTemplate::new(200).set_body_json(body))
        .mount(server)
        .await;
}

pub fn me(user: &str) -> Value {
    json!({"user_id": user, "email": "accountant@example.com", "display_name": "Test Accountant", "memberships": [{"organization_id": "org-1", "organization_name": "Firm", "organization_status": "active", "organization_role": "accountant", "company_roles": {}}], "permissions": []})
}

pub struct Rig {
    pub desktop: Desktop,
    pub dir: tempfile::TempDir,
    pub store: Arc<MemoryStore>,
}

pub fn rig(server: &MockServer) -> Rig {
    rig_with_store(server, Arc::new(MemoryStore::new()))
}

/// A fresh process (new database and API client) that shares an existing credential vault.
pub fn rig_with_store(server: &MockServer, store: Arc<MemoryStore>) -> Rig {
    let dir = tempfile::tempdir().unwrap();
    let api = Arc::new(ApiClient::new(cfg(server), store.clone()).unwrap());
    let (db, report) = Db::open(&dir.path().join("data/app.db"), Sealer::from_vault(store.as_ref()).unwrap()).unwrap();
    let log = Logger::new(&dir.path().join("logs")).unwrap();
    let desktop = Desktop::new(api, db, report, log, &dir.path().join("data"), "0.1.0-test");
    Rig { desktop, dir, store }
}

pub fn all_bytes(dir: &std::path::Path) -> String {
    let mut s = Vec::new();
    fn walk(p: &std::path::Path, out: &mut Vec<u8>) {
        if let Ok(rd) = std::fs::read_dir(p) {
            for e in rd.flatten() {
                let q = e.path();
                if q.is_dir() {
                    walk(&q, out)
                } else if let Ok(b) = std::fs::read(&q) {
                    out.extend(b)
                }
            }
        }
    }
    walk(dir, &mut s);
    String::from_utf8_lossy(&s).into_owned()
}
