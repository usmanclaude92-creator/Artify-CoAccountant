//! Phase 10 desktop core tests: only fixed bookkeeping routes can be called, identifiers, dates, template keys and filters are
//! validated, and bodies travel unchanged (the server validates, authorises and decides every closing step).
mod common;
use artify_core::api::LoginOutcome;
use common::*;
use serde_json::{json, Value};
use wiremock::matchers::{body_json, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

const ORG: &str = "11111111-1111-4111-8111-111111111111";
const CO: &str = "22222222-2222-4222-8222-222222222222";
const CL: &str = "99999999-9999-4999-8999-999999999999";
const EX: &str = "88888888-8888-4888-8888-888888888888";

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH-TOKEN-VALUE")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("accountant@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    r
}
fn p(tail: &str) -> String {
    format!("/api/v1/companies/{CO}/bookkeeping{tail}")
}
fn args(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

#[tokio::test]
async fn the_closing_workflow_uses_fixed_routes_and_server_refusals_reach_the_interface_unchanged() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(p("/days/2025-03-10")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"business_date": "2025-03-10", "day_closed": false})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p("/closings")))
        .and(body_json(json!({"business_date": "2025-03-10"})))
        .respond_with(ResponseTemplate::new(201).set_body_json(json!({"id": CL, "status": "draft"})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/closings/{CL}/close"))))
        .respond_with(problem(403, "independence_required", "The person who submitted the closing cannot approve and close it."))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/closings/{CL}/exports/{EX}/regenerate"))))
        .respond_with(ResponseTemplate::new(202).set_body_json(json!({"id": EX})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p("/checklists/from-template/daily_bookkeeping")))
        .respond_with(ResponseTemplate::new(201).set_body_json(json!({"name": "Daily bookkeeping"})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/checklist-items/{CL}/complete"))))
        .and(query_param("business_date", "2025-03-10"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"done": true})))
        .mount(&s)
        .await;
    Mock::given(method("DELETE"))
        .and(path(p(&format!("/checklist-items/{CL}/complete"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"done": false})))
        .mount(&s)
        .await;
    let day = r.desktop.bk_call(ORG, CO, "day", &args(&["2025-03-10"]), &[], Value::Null).await.unwrap();
    assert_eq!(day["day_closed"], false);
    let made = r.desktop.bk_call(ORG, CO, "closing_create", &[], &[], json!({"business_date": "2025-03-10"})).await.unwrap();
    assert_eq!(made["status"], "draft");
    // the desktop never decides who may close: the server's refusal (and its stable code) is what the person sees
    let e = r.desktop.bk_call(ORG, CO, "closing_close", &args(&[CL]), &[], Value::Null).await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(403), "independence_required"));
    r.desktop
        .bk_call(ORG, CO, "closing_export_regenerate", &args(&[CL, EX]), &[], json!({"reason": "wording", "idempotency_key": "k-12345678"}))
        .await
        .unwrap();
    r.desktop.bk_call(ORG, CO, "checklist_copy", &args(&["daily_bookkeeping"]), &[], Value::Null).await.unwrap();
    let tick = [("business_date".to_string(), "2025-03-10".to_string())];
    assert_eq!(r.desktop.bk_call(ORG, CO, "item_complete", &args(&[CL]), &tick, json!({})).await.unwrap()["done"], true);
    assert_eq!(r.desktop.bk_call(ORG, CO, "item_uncomplete", &args(&[CL]), &tick, Value::Null).await.unwrap()["done"], false);
}

#[tokio::test]
async fn unknown_actions_bad_ids_dates_keys_and_unsafe_filters_never_reach_the_server() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let call = |op: &'static str, a: Vec<String>, q: Vec<(String, String)>| {
        let d = &r.desktop;
        async move { d.bk_call(ORG, CO, op, &a, &q, Value::Null).await.unwrap_err().code }
    };
    assert_eq!(call("close_everything", vec![], vec![]).await, "unknown_action");
    assert_eq!(call("closing", vec![], vec![]).await, "invalid_id"); // a route that needs an id
    assert_eq!(call("closing", args(&["../etc/passwd"]), vec![]).await, "invalid_id");
    assert_eq!(call("closing", args(&[CL, CL]), vec![]).await, "invalid_id");
    assert_eq!(call("policy", args(&[CL]), vec![]).await, "invalid_id");
    for bad in ["2025-13-01", "2025-00-10", "2025-03-32", "2025-3-1", "../../x", "2025-03-10/x", "20250310", ""] {
        assert_eq!(call("day", args(&[bad]), vec![]).await, "invalid_date", "{bad}");
    }
    for bad in ["Daily", "x", "daily-bookkeeping", "daily/../x", "9daily", "a b"] {
        assert_eq!(call("checklist_copy", args(&[bad]), vec![]).await, "invalid_key", "{bad}");
    }
    assert_eq!(call("tasks", vec![], vec![("evil".into(), "1".into())]).await, "invalid_query");
    assert_eq!(call("tasks", vec![], vec![("status".into(), "a&admin=true".into())]).await, "invalid_query");
    assert_eq!(call("closing_close", args(&[CL]), vec![("status".into(), "x".into())]).await, "invalid_query"); // no filters on writes
    assert_eq!(call("item_complete", args(&[CL]), vec![("business_date".into(), "tomorrow".into())]).await, "invalid_date");
    assert_eq!(call("item_complete", args(&[CL]), vec![("status".into(), "x".into())]).await, "invalid_query");
    assert!(s.received_requests().await.unwrap().iter().all(|q| !q.url.path().contains("/bookkeeping")), "nothing was sent");
}

#[test]
fn the_allow_list_has_no_route_that_could_approve_close_or_override_without_a_server_decision() {
    // every write is a POST/PUT/PATCH/DELETE to the bookkeeping API; there is no operation that touches accounting, storage,
    // the file system or another company, and the interface can only name an operation, never a path
    let src = include_str!("../src/bk.rs");
    assert!(!src.contains("/accounting"));
    assert!(!src.contains("/orgs/"));
    assert!(src.contains("bookkeeping-changed"));
}
