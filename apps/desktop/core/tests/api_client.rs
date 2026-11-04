mod common;
use std::sync::Arc;

use artify_core::api::{ApiClient, ApiConfig, LoginOutcome};
use artify_core::vault::{MemoryStore, SecretStore};
use common::*;
use serde_json::json;
use wiremock::matchers::{body_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

async fn count(server: &MockServer, m: &str, p: &str) -> usize {
    server.received_requests().await.unwrap().iter().filter(|r| r.method.as_str() == m && r.url.path() == format!("/api/v1{p}")).count()
}

#[tokio::test]
async fn successful_login_keeps_the_refresh_token_in_the_vault_only() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("ACCESS1", "REFRESH1")).await;
    let (api, store) = client(&s);
    assert_eq!(api.login("a@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    assert!(api.is_authenticated());
    assert_eq!(store.get(&format!("refresh-token:{}", api.base_url())).unwrap().as_deref(), Some("REFRESH1"));
}

#[tokio::test]
async fn invalid_credentials_are_reported_without_a_session() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/login"))
        .respond_with(problem(401, "unauthenticated", "Invalid email or password."))
        .mount(&s)
        .await;
    let (api, store) = client(&s);
    let e = api.login("a@example.com", "wrong").await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(401), "unauthenticated"));
    assert!(!api.is_authenticated() && store.snapshot().is_empty());
    assert_eq!(count(&s, "POST", "/auth/login").await, 1, "a failed login is not retried");
}

#[tokio::test]
async fn mfa_challenge_then_verification() {
    let s = MockServer::start().await;
    mount_json(
        &s,
        "POST",
        "/auth/login",
        json!({"status": "mfa_required", "tokens": null, "mfa_token": "CHALLENGE", "mfa_enrollment_required": false}),
    )
    .await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/mfa/verify"))
        .and(body_json(json!({"mfa_token": "CHALLENGE", "code": "123456"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(tokens("A2", "R2")))
        .mount(&s)
        .await;
    let (api, store) = client(&s);
    assert_eq!(api.login("a@example.com", "pw").await.unwrap(), LoginOutcome::MfaRequired { mfa_token: "CHALLENGE".into() });
    assert!(!api.is_authenticated());
    api.verify_mfa("CHALLENGE", "123456").await.unwrap();
    assert!(api.is_authenticated());
    assert_eq!(store.get(&format!("refresh-token:{}", api.base_url())).unwrap().as_deref(), Some("R2"));
}

#[tokio::test]
async fn a_wrong_mfa_code_is_an_error_and_not_a_session() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/mfa/verify"))
        .respond_with(problem(401, "unauthenticated", "Invalid code."))
        .mount(&s)
        .await;
    let (api, _) = client(&s);
    assert_eq!(api.verify_mfa("C", "000000").await.unwrap_err().status, Some(401));
    assert!(!api.is_authenticated());
}

#[tokio::test]
async fn session_restoration_uses_the_vault_and_rotates_the_token() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/refresh"))
        .and(body_json(json!({"refresh_token": "OLD"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(tokens("NEWACCESS", "NEWREFRESH")))
        .mount(&s)
        .await;
    mount_authed(&s, "GET", "/me", "NEWACCESS", me("u1")).await;
    let store = Arc::new(MemoryStore::new());
    let api = ApiClient::new(cfg(&s), store.clone()).unwrap();
    store.set(&format!("refresh-token:{}", api.base_url()), "OLD").unwrap();
    assert!(api.restore().await.unwrap());
    assert_eq!(store.get(&format!("refresh-token:{}", api.base_url())).unwrap().as_deref(), Some("NEWREFRESH"));
    assert_eq!(api.get("/me", None).await.unwrap().body["user_id"], "u1");
}

#[tokio::test]
async fn restoring_with_nothing_stored_means_sign_in() {
    let s = MockServer::start().await;
    let (api, _) = client(&s);
    assert!(!api.restore().await.unwrap());
    assert_eq!(count(&s, "POST", "/auth/refresh").await, 0);
}

#[tokio::test]
async fn an_ended_session_cannot_be_restored_and_the_stored_token_is_removed() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/refresh"))
        .respond_with(problem(401, "unauthenticated", "Refresh token expired or revoked."))
        .mount(&s)
        .await;
    let store = Arc::new(MemoryStore::new());
    let api = ApiClient::new(cfg(&s), store.clone()).unwrap();
    let key = format!("refresh-token:{}", api.base_url());
    store.set(&key, "DEAD").unwrap();
    assert!(!api.restore().await.unwrap());
    assert!(store.get(&key).unwrap().is_none(), "a dead token is not kept");
}

#[tokio::test]
async fn being_offline_at_startup_keeps_the_stored_session() {
    let store = Arc::new(MemoryStore::new());
    let mut c = ApiConfig::new("http://127.0.0.1:1/api/v1", "t");
    c.max_retries = 0;
    let api = ApiClient::new(c, store.clone()).unwrap();
    let key = format!("refresh-token:{}", api.base_url());
    store.set(&key, "KEEP").unwrap();
    let e = api.restore().await.unwrap_err();
    assert_eq!((e.code.as_str(), e.retryable), ("network_unavailable", true));
    assert_eq!(store.get(&key).unwrap().as_deref(), Some("KEEP"));
}

#[tokio::test]
async fn an_expired_access_token_is_refreshed_once_and_the_request_repeated() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("OLD", "R1")).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/me"))
        .and(wiremock::matchers::header("authorization", "Bearer OLD"))
        .respond_with(problem(401, "unauthenticated", "expired"))
        .mount(&s)
        .await;
    mount_json(&s, "POST", "/auth/refresh", tokens("FRESH", "R2")).await;
    mount_authed(&s, "GET", "/me", "FRESH", me("u1")).await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    assert_eq!(api.get("/me", None).await.unwrap().body["user_id"], "u1");
    assert_eq!(count(&s, "POST", "/auth/refresh").await, 1);
}

#[tokio::test]
async fn concurrent_expired_requests_share_one_refresh() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("OLD", "R1")).await;
    Mock::given(method("GET"))
        .and(wiremock::matchers::header("authorization", "Bearer OLD"))
        .respond_with(problem(401, "unauthenticated", "expired"))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/refresh"))
        .respond_with(ResponseTemplate::new(200).set_delay(std::time::Duration::from_millis(80)).set_body_json(tokens("FRESH", "R2")))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(wiremock::matchers::header("authorization", "Bearer FRESH"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .mount(&s)
        .await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    let (a, b, c) = tokio::join!(api.get("/x", None), api.get("/y", None), api.get("/z", None));
    assert!(a.is_ok() && b.is_ok() && c.is_ok());
    assert_eq!(count(&s, "POST", "/auth/refresh").await, 1, "a rotating refresh token must be used exactly once");
}

#[tokio::test]
async fn when_the_refresh_is_refused_the_session_is_over() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("OLD", "R1")).await;
    Mock::given(method("GET")).and(path("/api/v1/me")).respond_with(problem(401, "unauthenticated", "expired")).mount(&s).await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/refresh"))
        .respond_with(problem(401, "unauthenticated", "revoked (device revoked)"))
        .mount(&s)
        .await;
    let (api, store) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    let e = api.get("/me", None).await.unwrap_err();
    assert!(e.is_session_expired());
    assert!(!api.is_authenticated() && store.snapshot().is_empty(), "tokens are gone from memory and the vault");
    assert!(api.get("/me", None).await.unwrap_err().is_session_expired(), "later calls fail fast without the network");
}

#[tokio::test]
async fn requests_without_a_session_never_reach_the_server() {
    let s = MockServer::start().await;
    let (api, _) = client(&s);
    assert!(api.get("/me", None).await.unwrap_err().is_session_expired());
    assert!(s.received_requests().await.unwrap().is_empty());
}

#[tokio::test]
async fn a_forbidden_response_is_not_confused_with_an_expired_session() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A", "R")).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs/o/members"))
        .respond_with(problem(403, "forbidden", "You do not have permission to perform this action."))
        .mount(&s)
        .await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    let e = api.get("/orgs/o/members", Some("o")).await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(403), "forbidden"));
    assert!(api.is_authenticated());
    assert_eq!(count(&s, "POST", "/auth/refresh").await, 0);
}

#[tokio::test]
async fn transient_server_errors_are_retried_for_reads_only() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A", "R")).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/flaky"))
        .respond_with(ResponseTemplate::new(503))
        .up_to_n_times(2)
        .with_priority(1)
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path("/api/v1/flaky"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": 1})))
        .with_priority(2)
        .mount(&s)
        .await;
    Mock::given(method("POST")).and(path("/api/v1/write")).respond_with(ResponseTemplate::new(503)).mount(&s).await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    assert_eq!(api.get("/flaky", None).await.unwrap().body["ok"], 1);
    assert_eq!(count(&s, "GET", "/flaky").await, 3, "two retries, then success");
    let e = api.post("/write", None, Some(json!({}))).await.unwrap_err();
    assert!(e.retryable && e.status == Some(503));
    assert_eq!(count(&s, "POST", "/write").await, 1, "writes are never retried automatically");
}

#[tokio::test]
async fn retries_are_bounded() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A", "R")).await;
    Mock::given(method("GET")).and(path("/api/v1/down")).respond_with(ResponseTemplate::new(502)).mount(&s).await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    assert_eq!(api.get("/down", None).await.unwrap_err().status, Some(502));
    assert_eq!(count(&s, "GET", "/down").await, 3);
}

#[tokio::test]
async fn a_slow_server_times_out_with_a_retryable_error() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A", "R")).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/slow"))
        .respond_with(ResponseTemplate::new(200).set_delay(std::time::Duration::from_millis(1500)).set_body_json(json!({})))
        .mount(&s)
        .await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    let t = std::time::Instant::now();
    let e = api.get("/slow", None).await.unwrap_err();
    assert_eq!((e.code.as_str(), e.retryable), ("timeout", true));
    assert!(t.elapsed() < std::time::Duration::from_millis(1400), "bounded by the configured timeout, not the server's delay");
}

#[tokio::test]
async fn an_unreachable_server_is_reported_as_offline() {
    let store = Arc::new(MemoryStore::new());
    let mut c = ApiConfig::new("http://127.0.0.1:1/api/v1", "t");
    c.max_retries = 1;
    c.retry_base = std::time::Duration::from_millis(2);
    let api = ApiClient::new(c, store).unwrap();
    let e = api.login("a@example.com", "pw").await.unwrap_err();
    assert_eq!((e.code.as_str(), e.retryable), ("network_unavailable", true));
}

#[tokio::test]
async fn malformed_responses_are_rejected_not_trusted() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/login"))
        .respond_with(ResponseTemplate::new(200).set_body_string("<html>captive portal</html>"))
        .mount(&s)
        .await;
    let (api, _) = client(&s);
    assert_eq!(api.login("a@example.com", "pw").await.unwrap_err().code, "malformed_response");
    let s2 = MockServer::start().await;
    mount_json(&s2, "POST", "/auth/login", json!({"status": "ok"})).await; // "ok" without tokens
    let (api2, store2) = client(&s2);
    assert_eq!(api2.login("a@example.com", "pw").await.unwrap_err().code, "malformed_response");
    assert!(store2.snapshot().is_empty());
}

#[tokio::test]
async fn problem_details_keep_structured_extras_but_no_raw_body() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A", "R")).await;
    Mock::given(method("POST")).and(path("/api/v1/devices")).respond_with(ResponseTemplate::new(402).set_body_json(json!({
        "code": "entitlement_exceeded", "detail": "Limit limit.devices reached (1 of 1 in use).", "key": "limit.devices", "limit": 1, "current": 1, "requested": 1,
        "type": "x", "title": "y", "status": 402, "instance": "/devices", "correlation_id": "abc"}))).mount(&s).await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    let e = api.post("/devices", Some("o"), Some(json!({"platform": "windows", "name": "PC"}))).await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(402), "entitlement_exceeded"));
    let d = e.detail.unwrap();
    assert_eq!(d["key"], "limit.devices");
    assert!(d.get("correlation_id").is_none() && d.get("instance").is_none());
}

#[tokio::test]
async fn etag_revalidation_returns_not_modified() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A", "R")).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/w"))
        .and(wiremock::matchers::header("if-none-match", "W/\"1\""))
        .respond_with(ResponseTemplate::new(304))
        .with_priority(1)
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path("/api/v1/w"))
        .respond_with(ResponseTemplate::new(200).insert_header("etag", "W/\"1\"").set_body_json(json!({"v": 1})))
        .with_priority(2)
        .mount(&s)
        .await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    let first = api.get_if_changed("/w", None, None).await.unwrap();
    assert_eq!((first.not_modified, first.etag.as_deref()), (false, Some("W/\"1\"")));
    assert!(api.get_if_changed("/w", None, first.etag.as_deref()).await.unwrap().not_modified);
}

#[tokio::test]
async fn the_organization_claim_travels_as_a_header_and_the_token_as_a_bearer() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("TOK", "R")).await;
    mount_json(&s, "GET", "/orgs/o/companies", json!([])).await;
    let (api, _) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    api.get("/orgs/o/companies", Some("o-claim")).await.unwrap();
    let req = s.received_requests().await.unwrap().into_iter().find(|r| r.url.path().ends_with("/companies")).unwrap();
    assert_eq!(req.headers.get("x-organization-id").unwrap().to_str().unwrap(), "o-claim");
    assert_eq!(req.headers.get("authorization").unwrap().to_str().unwrap(), "Bearer TOK");
    assert!(req.headers.get("user-agent").unwrap().to_str().unwrap().starts_with("ArtifyCoAccountantDesktop/"));
}

#[tokio::test]
async fn logout_tells_the_server_and_clears_everything_even_if_the_server_is_down() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A", "R")).await;
    Mock::given(method("POST")).and(path("/api/v1/auth/logout")).respond_with(ResponseTemplate::new(500)).mount(&s).await;
    let (api, store) = client(&s);
    api.login("a@example.com", "pw").await.unwrap();
    api.logout().await;
    assert!(!api.is_authenticated() && store.snapshot().is_empty());
    assert_eq!(count(&s, "POST", "/auth/logout").await, 1);
}
