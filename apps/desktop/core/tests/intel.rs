//! Phase 7 desktop core tests: the processing calls validate identifiers, fix the set of routes, carry the review body
//! unchanged, and fetch page images without writing anything but a temporary file that is removed.
mod common;
use artify_core::api::LoginOutcome;
use common::*;
use serde_json::{json, Value};
use wiremock::matchers::{body_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

const ORG: &str = "11111111-1111-4111-8111-111111111111";
const CO: &str = "22222222-2222-4222-8222-222222222222";
const DOC: &str = "55555555-5555-4555-8555-555555555555";
const EX: &str = "66666666-6666-4666-8666-666666666666";
const TX: &str = "77777777-7777-4777-8777-777777777777";

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH-TOKEN-VALUE")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("accountant@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    r
}
fn p(tail: &str) -> String {
    format!("/api/v1/companies/{CO}{tail}")
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

#[tokio::test]
async fn starting_processing_sends_a_fresh_idempotency_key_and_only_known_kinds() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/documents/{DOC}/process"))))
        .respond_with(ResponseTemplate::new(202).set_body_json(json!({"id": "job-1", "status": "queued"})))
        .mount(&s)
        .await;
    let a = r.desktop.intel_start(ORG, CO, DOC, "full", &["eng".into(), "ara".into()], Some("re-run")).await.unwrap();
    assert_eq!(a["status"], "queued");
    r.desktop.intel_start(ORG, CO, DOC, "extract", &[], None).await.unwrap();
    let sent = bodies(&s, "POST", "/process").await;
    assert_eq!(sent[0]["languages"], json!(["eng", "ara"]));
    assert_ne!(sent[0]["idempotency_key"], sent[1]["idempotency_key"]);
    assert!(sent[0]["idempotency_key"].as_str().unwrap().len() >= 8);
    assert_eq!(r.desktop.intel_start(ORG, CO, DOC, "format-disk", &[], None).await.unwrap_err().code, "invalid_kind");
    for bad in ["../x", "nope"] {
        assert_eq!(r.desktop.intel_start(ORG, CO, bad, "full", &[], None).await.unwrap_err().code, "invalid_id");
        assert_eq!(r.desktop.intel_start(ORG, bad, DOC, "full", &[], None).await.unwrap_err().code, "invalid_id");
    }
}

#[tokio::test]
async fn review_actions_are_whitelisted_and_the_correction_travels_unchanged() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("PATCH"))
        .and(path(p(&format!("/extractions/{EX}/fields/invoice_number"))))
        .and(body_json(json!({"action": "correct", "value": "INV-9", "reason": "typo"})))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(json!({"field_key": "invoice_number", "value": "INV-9", "review_status": "corrected"})),
        )
        .mount(&s)
        .await;
    Mock::given(method("PATCH"))
        .and(path(p(&format!("/extractions/{EX}/transactions/{TX}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"id": TX, "review_status": "corrected"})))
        .mount(&s)
        .await;
    let out =
        r.desktop.intel_review(ORG, CO, EX, "field", "invoice_number", "correct", json!({"value": "INV-9"}), Some("typo")).await.unwrap();
    assert_eq!(out["review_status"], "corrected");
    r.desktop.intel_review(ORG, CO, EX, "transactions", TX, "correct", json!({"patch": {"debit": "40.000"}}), None).await.unwrap();
    assert_eq!(bodies(&s, "PATCH", TX).await[0]["patch"], json!({"debit": "40.000"}));
    for (kind, target, action, code) in [
        ("field", "invoice_number", "approve", "unknown_action"),
        ("field", "../etc", "confirm", "invalid_id"),
        ("field", "Bad Key", "confirm", "invalid_id"),
        ("transactions", "not-a-uuid", "confirm", "invalid_id"),
        ("bogus", "x", "confirm", "unknown_action"),
    ] {
        assert_eq!(
            r.desktop.intel_review(ORG, CO, EX, kind, target, action, json!({}), None).await.unwrap_err().code,
            code,
            "{kind}/{target}/{action}"
        );
    }
    assert_eq!(bodies(&s, "PATCH", "invoice_number").await.len(), 1, "rejected requests never reached the server");
}

#[tokio::test]
async fn approval_is_a_deliberate_call_and_server_refusals_reach_the_interface() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/extractions/{EX}/approve"))))
        .respond_with(problem(409, "conflict", "Resolve every item that needs review before approving."))
        .mount(&s)
        .await;
    let e = r.desktop.intel_decide(ORG, CO, EX, "approve", false, None).await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(409), "conflict"));
    assert_eq!(r.desktop.intel_decide(ORG, CO, EX, "approve-all", false, None).await.unwrap_err().code, "unknown_action");
    let sent = bodies(&s, "POST", "/approve").await;
    assert_eq!(sent[0], json!({"acknowledge_validation_errors": false, "note": null}));
}

#[tokio::test]
async fn page_images_come_back_as_base64_and_leave_no_file_behind() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let png = vec![0x89, b'P', b'N', b'G', 1, 2, 3];
    Mock::given(method("GET"))
        .and(path(p(&format!("/documents/{DOC}/pages/2/image"))))
        .respond_with(ResponseTemplate::new(200).insert_header("content-type", "image/png").set_body_bytes(png.clone()))
        .mount(&s)
        .await;
    let img = r.desktop.intel_page_image(ORG, CO, DOC, 2).await.unwrap();
    use base64::Engine;
    assert_eq!(base64::engine::general_purpose::STANDARD.decode(img.data_base64).unwrap(), png);
    let tmp = r.desktop.data_dir.join("tmp");
    assert!(std::fs::read_dir(&tmp).map(|d| d.count()).unwrap_or(0) == 0, "temporary page file removed");
    Mock::given(method("GET"))
        .and(path(p(&format!("/documents/{DOC}/pages/9/image"))))
        .respond_with(problem(404, "not_found", "Page image not found."))
        .mount(&s)
        .await;
    assert_eq!(r.desktop.intel_page_image(ORG, CO, DOC, 9).await.unwrap_err().status, Some(404));
}

#[tokio::test]
async fn suggestions_are_accepted_through_fixed_routes() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let sg = "88888888-8888-4888-8888-888888888888";
    Mock::given(method("POST"))
        .and(path(p(&format!("/extractions/{EX}/workspace-suggestions/{sg}/accept"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"accepted": sg, "moved": true})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/extractions/{EX}/metadata-suggestions/{sg}/accept"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"accepted": sg})))
        .mount(&s)
        .await;
    assert_eq!(r.desktop.intel_accept(ORG, CO, EX, "workspace", sg, None).await.unwrap()["moved"], true);
    r.desktop.intel_accept(ORG, CO, EX, "metadata", sg, Some(json!("hq"))).await.unwrap();
    assert_eq!(bodies(&s, "POST", "/accept").await[1], json!({"value": "hq"}));
    assert_eq!(r.desktop.intel_accept(ORG, CO, EX, "bogus", sg, None).await.unwrap_err().code, "unknown_action");
}
