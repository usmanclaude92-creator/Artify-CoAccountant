//! Phase 8 desktop core tests: only fixed accounting routes can be called, identifiers and filters are validated, and bodies
//! travel unchanged (the server does every calculation and every approval).
mod common;
use artify_core::api::LoginOutcome;
use common::*;
use serde_json::{json, Value};
use wiremock::matchers::{body_json, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

const ORG: &str = "11111111-1111-4111-8111-111111111111";
const CO: &str = "22222222-2222-4222-8222-222222222222";
const TX: &str = "99999999-9999-4999-8999-999999999999";
const MATCH: &str = "88888888-8888-4888-8888-888888888888";

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH-TOKEN-VALUE")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("accountant@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    r
}
fn p(tail: &str) -> String {
    format!("/api/v1/companies/{CO}/accounting{tail}")
}
async fn bodies(s: &MockServer, m: &str, suffix: &str) -> Vec<Value> {
    s.received_requests()
        .await
        .unwrap()
        .into_iter()
        .filter(|r| r.method.as_str() == m && r.url.path().ends_with(suffix))
        .map(|r| serde_json::from_slice(&r.body).unwrap_or(Value::Null))
        .collect()
}
fn ids(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

#[tokio::test]
async fn reads_and_writes_use_the_fixed_routes_and_bodies_travel_unchanged() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(p("/transactions")))
        .and(query_param("status", "awaiting_approval"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"items": [], "total": 0})))
        .mount(&s)
        .await;
    Mock::given(method("PUT"))
        .and(path(p(&format!("/transactions/{TX}/journal-lines"))))
        .and(body_json(json!({"lines": [{"account_id": "x", "debit": "10.000"}]})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"balanced": false})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/transactions/{TX}/approve"))))
        .respond_with(problem(
            403,
            "forbidden",
            "You prepared this transaction; the company's policy requires a different person to approve it.",
        ))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/transactions/{TX}/duplicates/{MATCH}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .mount(&s)
        .await;
    let list =
        r.desktop.acct_call(ORG, CO, "transactions", &[], &[("status".into(), "awaiting_approval".into())], Value::Null).await.unwrap();
    assert_eq!(list["total"], 0);
    let out = r
        .desktop
        .acct_call(ORG, CO, "txn_journal_lines", &ids(&[TX]), &[], json!({"lines": [{"account_id": "x", "debit": "10.000"}]}))
        .await
        .unwrap();
    assert_eq!(out["balanced"], false);
    // a server refusal reaches the interface unchanged: the desktop never decides who may approve
    let e = r.desktop.acct_call(ORG, CO, "txn_approve", &ids(&[TX]), &[], json!({"acknowledge": false, "note": null})).await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(403), "forbidden"));
    assert_eq!(bodies(&s, "POST", "/approve").await[0], json!({"acknowledge": false, "note": null}));
    r.desktop
        .acct_call(ORG, CO, "txn_duplicate", &ids(&[TX, MATCH]), &[], json!({"decision": "not_duplicate", "note": "recurring"}))
        .await
        .unwrap();
}

#[tokio::test]
async fn unknown_actions_bad_ids_and_unsafe_filters_never_reach_the_server() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let call = |op: &'static str, i: Vec<String>, q: Vec<(String, String)>| {
        let d = &r.desktop;
        async move { d.acct_call(ORG, CO, op, &i, &q, Value::Null).await.unwrap_err().code }
    };
    assert_eq!(call("delete_everything", vec![], vec![]).await, "unknown_action");
    assert_eq!(call("transaction", vec![], vec![]).await, "invalid_id"); // a route that needs an id
    assert_eq!(call("transaction", ids(&["../etc/passwd"]), vec![]).await, "invalid_id");
    assert_eq!(call("transaction", ids(&[TX, TX]), vec![]).await, "invalid_id");
    assert_eq!(call("config", ids(&[TX]), vec![]).await, "invalid_id");
    assert_eq!(call("transactions", vec![], vec![("evil".into(), "1".into())]).await, "invalid_query");
    assert_eq!(call("transactions", vec![], vec![("q".into(), "a&admin=true".into())]).await, "invalid_query");
    assert_eq!(call("txn_approve", ids(&[TX]), vec![("status".into(), "x".into())]).await, "invalid_query"); // no query on writes
    assert!(s.received_requests().await.unwrap().iter().all(|q| !q.url.path().contains("/accounting")), "nothing was sent");
}
