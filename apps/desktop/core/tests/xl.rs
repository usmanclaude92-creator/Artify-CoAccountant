//! Phase 9 desktop core tests: only fixed Excel routes can be called, identifiers and filters are validated, bodies travel
//! unchanged, template files are streamed as-is, and a downloaded export is kept only if its checksum matches the record.
mod common;
use artify_core::api::LoginOutcome;
use artify_core::xl::UploadMeta;
use common::*;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use wiremock::matchers::{body_bytes, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

const ORG: &str = "11111111-1111-4111-8111-111111111111";
const CO: &str = "22222222-2222-4222-8222-222222222222";
const T1: &str = "99999999-9999-4999-8999-999999999999";
const EX: &str = "88888888-8888-4888-8888-888888888888";

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH-TOKEN-VALUE")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("accountant@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    r
}
fn p(tail: &str) -> String {
    format!("/api/v1/companies/{CO}/excel{tail}")
}
fn ids(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}
fn hex(b: &[u8]) -> String {
    Sha256::digest(b).iter().map(|x| format!("{x:02x}")).collect()
}

#[tokio::test]
async fn reads_and_writes_use_the_fixed_routes_and_bodies_travel_unchanged() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(p("/templates")))
        .and(query_param("status", "active"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([])))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p("/exports")))
        .respond_with(ResponseTemplate::new(202).set_body_json(json!({"id": EX, "status": "queued"})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p("/reports/standard/trial_balance/run")))
        .and(query_param("limit", "50"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"total": 0})))
        .mount(&s)
        .await;
    Mock::given(method("DELETE"))
        .and(path(p(&format!("/templates/{T1}"))))
        .respond_with(problem(409, "conflict", "Archive it instead."))
        .mount(&s)
        .await;
    let list = r.desktop.xl_call(ORG, CO, "templates", &[], &[("status".into(), "active".into())], Value::Null).await.unwrap();
    assert_eq!(list, json!([]));
    let body = json!({"template_id": T1, "params": {"date_from": "2025-01-01"}, "idempotency_key": "abcdefgh1"});
    let ex = r.desktop.xl_call(ORG, CO, "export_create", &[], &[], body.clone()).await.unwrap();
    assert_eq!(ex["status"], "queued");
    let sent: Vec<Value> = s
        .received_requests()
        .await
        .unwrap()
        .into_iter()
        .filter(|q| q.url.path().ends_with("/excel/exports"))
        .map(|q| serde_json::from_slice(&q.body).unwrap())
        .collect();
    assert_eq!(sent, vec![body]); // forwarded unchanged: the server validates, deduplicates and decides
    let run = r
        .desktop
        .xl_call(ORG, CO, "report_run", &ids(&["trial_balance"]), &[("limit".into(), "50".into())], json!({"params": {}}))
        .await
        .unwrap();
    assert_eq!(run["total"], 0);
    // a refusal arrives as the server wrote it
    let e = r.desktop.xl_call(ORG, CO, "template_delete", &ids(&[T1]), &[], Value::Null).await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(409), "conflict"));
}

#[tokio::test]
async fn unknown_actions_bad_ids_and_unsafe_filters_never_reach_the_server() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let call = |op: &'static str, i: Vec<String>, q: Vec<(String, String)>| {
        let d = &r.desktop;
        async move { d.xl_call(ORG, CO, op, &i, &q, Value::Null).await.unwrap_err().code }
    };
    assert_eq!(call("run_sql", vec![], vec![]).await, "unknown_action");
    assert_eq!(call("template", vec![], vec![]).await, "invalid_id");
    assert_eq!(call("template", ids(&["../../etc/passwd"]), vec![]).await, "invalid_id");
    assert_eq!(call("template", ids(&[T1, T1]), vec![]).await, "invalid_id");
    assert_eq!(call("catalogue", ids(&[T1]), vec![]).await, "invalid_id");
    assert_eq!(call("report_run", ids(&["trial_balance; drop table x"]), vec![]).await, "invalid_id");
    assert_eq!(call("report_run", ids(&[T1]), vec![]).await, "invalid_id"); // a report key is a slug, not an id
    assert_eq!(call("templates", vec![], vec![("evil".into(), "1".into())]).await, "invalid_query");
    assert_eq!(call("templates", vec![], vec![("q".into(), "a&admin=true".into())]).await, "invalid_query");
    assert_eq!(call("template_status", ids(&[T1]), vec![("status".into(), "x".into())]).await, "invalid_query"); // no query on writes
    assert!(s.received_requests().await.unwrap().iter().all(|q| !q.url.path().contains("/excel")), "nothing was sent");
}

#[tokio::test]
async fn a_template_is_streamed_unchanged_with_percent_encoded_details() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("Purchase register 2025 & more.xlsx");
    let bytes: Vec<u8> = (0..70_000u32).map(|i| (i % 251) as u8).collect();
    std::fs::write(&file, &bytes).unwrap();
    Mock::given(method("PUT"))
        .and(path(p("/templates/upload")))
        .and(query_param("filename", "Purchase register 2025 & more.xlsx"))
        .and(query_param("name", "Purchases & VAT"))
        .and(query_param("category", "purchase_register"))
        .and(body_bytes(bytes.clone()))
        .respond_with(ResponseTemplate::new(201).set_body_json(json!({"id": T1, "status": "draft"})))
        .mount(&s)
        .await;
    let meta = UploadMeta { name: Some("Purchases & VAT".into()), category: Some("purchase_register".into()), ..Default::default() };
    let out = r.desktop.xl_upload(ORG, CO, &file, &meta).await.unwrap();
    assert_eq!(out["status"], "draft");
    // wrong types and empty files are stopped before anything is sent
    let csv = dir.path().join("data.csv");
    std::fs::write(&csv, b"a,b").unwrap();
    assert_eq!(r.desktop.xl_upload(ORG, CO, &csv, &meta).await.unwrap_err().code, "unsupported_extension");
    let empty = dir.path().join("empty.xlsx");
    std::fs::write(&empty, b"").unwrap();
    assert_eq!(r.desktop.xl_upload(ORG, CO, &empty, &meta).await.unwrap_err().code, "empty_file");
    let bad = UploadMeta { template_id: Some("not-an-id".into()), ..Default::default() };
    assert_eq!(r.desktop.xl_upload(ORG, CO, &file, &bad).await.unwrap_err().code, "invalid_id");
}

#[tokio::test]
async fn an_export_is_kept_only_when_its_checksum_matches_the_record() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let dir = tempfile::tempdir().unwrap();
    let content = b"PK synthetic workbook bytes".to_vec();
    let good = json!({"id": EX, "status": "completed", "sha256": hex(&content)});
    Mock::given(method("GET"))
        .and(path(p(&format!("/exports/{EX}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(good))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(p(&format!("/exports/{EX}/file"))))
        .respond_with(ResponseTemplate::new(200).insert_header("x-export-sha256", hex(&content).as_str()).set_body_bytes(content.clone()))
        .mount(&s)
        .await;
    let dest = dir.path().join("Purchases.xlsx");
    let saved = r.desktop.xl_export_save(ORG, CO, EX, &dest).await.unwrap();
    assert_eq!(std::fs::read(saved).unwrap(), content);
    assert!(std::fs::read_dir(dir.path()).unwrap().all(|e| !e.unwrap().file_name().to_string_lossy().ends_with(".part")));

    // a file that does not match the recorded checksum is discarded and an existing file is left alone
    let s2 = MockServer::start().await;
    let r2 = signed_in(&s2).await;
    let rec = json!({"id": EX, "status": "completed", "sha256": hex(b"what the server recorded")});
    Mock::given(method("GET"))
        .and(path(p(&format!("/exports/{EX}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(rec))
        .mount(&s2)
        .await;
    Mock::given(method("GET"))
        .and(path(p(&format!("/exports/{EX}/file"))))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(b"tampered".to_vec()))
        .mount(&s2)
        .await;
    let keep = dir.path().join("Existing.xlsx");
    std::fs::write(&keep, b"mine").unwrap();
    let e = r2.desktop.xl_export_save(ORG, CO, EX, &keep).await.unwrap_err();
    assert_eq!(e.code, "download_corrupted");
    assert_eq!(std::fs::read(&keep).unwrap(), b"mine");
    assert!(std::fs::read_dir(dir.path()).unwrap().all(|e| !e.unwrap().file_name().to_string_lossy().ends_with(".part")));

    // an export that did not complete has no file to fetch
    let s3 = MockServer::start().await;
    let r3 = signed_in(&s3).await;
    Mock::given(method("GET"))
        .and(path(p(&format!("/exports/{EX}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"id": EX, "status": "failed"})))
        .mount(&s3)
        .await;
    assert_eq!(r3.desktop.xl_export_save(ORG, CO, EX, &dir.path().join("x.xlsx")).await.unwrap_err().code, "not_ready");
}
