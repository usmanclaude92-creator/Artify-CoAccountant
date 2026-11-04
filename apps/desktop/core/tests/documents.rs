//! Phase 6 desktop core tests: staging, the persistent upload queue (against a simulated server that follows the real
//! protocol), recovery, cancellation, honest statuses, downloads, the local folder mirror and path safety.
mod common;
use std::collections::HashMap;
use std::path::Path;
use std::time::Duration;

use artify_core::api::LoginOutcome;
use artify_core::mirror::{self, MirrorNode};
use artify_core::queue::QueueAdd;
use common::*;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use wiremock::matchers::{header, method, path, path_regex};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

const ORG: &str = "11111111-1111-4111-8111-111111111111";
const CO: &str = "22222222-2222-4222-8222-222222222222";
const NODE: &str = "33333333-3333-4333-8333-333333333333";
const OP: &str = "44444444-4444-4444-8444-444444444444";
const DOC: &str = "55555555-5555-4555-8555-555555555555";

fn sha(b: &[u8]) -> String {
    artify_core::api::hex(&Sha256::digest(b))
}
fn base(p: &str) -> String {
    format!("/api/v1/companies/{CO}/documents{p}")
}
fn pdf(tag: &str) -> Vec<u8> {
    format!("%PDF-1.4\n% {tag}\ntrailer\n%%EOF\n").into_bytes()
}

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH-TOKEN-VALUE")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("accountant@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    r
}

fn op(status: &str) -> Value {
    json!({"id": OP, "status": status, "company_id": CO, "result_document_id": null})
}
fn write(dir: &Path, name: &str, content: &[u8]) -> std::path::PathBuf {
    let p = dir.join(name);
    std::fs::write(&p, content).unwrap();
    p
}

async fn stage_and_queue(r: &Rig, p: &Path) -> String {
    let staged = r.desktop.stage_paths(vec![p.to_path_buf()]).await.unwrap();
    assert!(staged[0].problem.is_none(), "{:?}", staged[0].problem);
    let add = QueueAdd {
        staged_id: staged[0].id.clone(),
        node_id: Some(NODE.into()),
        category_id: None,
        display_name: None,
        metadata: json!({}),
        filing_rule_id: None,
        duplicate_policy: "block".into(),
        target_document_id: None,
        change_reason: None,
        batch_local: None,
    };
    r.desktop.queue_add(ORG, CO, vec![add]).unwrap().remove(0).id
}

fn item(r: &Rig, id: &str) -> artify_core::queue::QueueView {
    r.desktop.queue_list(None).unwrap().into_iter().find(|q| q.id == id).unwrap()
}

async fn mount_protocol(s: &MockServer, final_status: u16) {
    Mock::given(method("POST"))
        .and(path(base("/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(op("awaiting_upload")))
        .mount(s)
        .await;
    Mock::given(method("PUT"))
        .and(path(base(&format!("/uploads/{OP}/content"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(op("uploaded")))
        .mount(s)
        .await;
    let t = if final_status == 200 {
        ResponseTemplate::new(200).set_body_json(json!({"operation": op("completed"), "document": {"id": DOC}}))
    } else {
        problem(final_status, "storage_unavailable", "down")
    };
    Mock::given(method("POST")).and(path(base(&format!("/uploads/{OP}/complete")))).respond_with(t).mount(s).await;
}

async fn requests(s: &MockServer, m: &str, suffix: &str) -> Vec<Request> {
    s.received_requests().await.unwrap().into_iter().filter(|r| r.method.as_str() == m && r.url.path().ends_with(suffix)).collect()
}

// ------------------------------------------------------------------ staging
#[tokio::test]
async fn staging_verifies_hashes_and_refuses_unusable_files_without_leaking_paths() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let d = r.dir.path().join("in");
    std::fs::create_dir_all(d.join("folder")).unwrap();
    let good = write(&d, "invoice.PDF", &pdf("a"));
    let empty = write(&d, "empty.pdf", b"");
    let exe = write(&d, "run.exe", b"MZ");
    let out = r.desktop.stage_paths(vec![good.clone(), empty, exe, d.join("folder"), d.join("missing.pdf")]).await.unwrap();
    assert!(out[0].problem.is_none() && !out[0].id.is_empty() && out[0].sha256.as_deref() == Some(sha(&pdf("a")).as_str()));
    assert_eq!(out[0].extension, "pdf");
    for refused in &out[1..] {
        assert!(refused.id.is_empty() && refused.problem.is_some() && refused.sha256.is_none());
    }
    assert!(out[1].problem.as_deref().unwrap().contains("empty") && out[2].problem.as_deref().unwrap().contains("not supported"));
    assert!(out[3].problem.as_deref().unwrap().contains("Folders"));
    // ids, not paths, are what the interface gets back
    assert!(!serde_json::to_string(&out).unwrap().contains(d.to_str().unwrap()));
    assert_eq!(r.desktop.stage_paths((0..201).map(|i| d.join(format!("{i}.pdf"))).collect()).await.unwrap_err().code, "too_many_files");
}

#[tokio::test]
async fn the_queue_accepts_only_staged_files_and_valid_destinations() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let item = |staged: &str, node: Option<&str>| QueueAdd {
        staged_id: staged.into(),
        node_id: node.map(str::to_string),
        category_id: None,
        display_name: None,
        metadata: json!({}),
        filing_rule_id: None,
        duplicate_policy: "block".into(),
        target_document_id: None,
        change_reason: None,
        batch_local: None,
    };
    assert_eq!(r.desktop.queue_add(ORG, CO, vec![item("../../etc/passwd", Some(NODE))]).unwrap_err().code, "file_not_staged");
    let p = write(r.dir.path(), "a.pdf", &pdf("q"));
    let st = r.desktop.stage_paths(vec![p]).await.unwrap();
    assert_eq!(r.desktop.queue_add(ORG, CO, vec![item(&st[0].id, None)]).unwrap_err().code, "destination_required");
    assert_eq!(r.desktop.queue_add(ORG, CO, vec![item(&st[0].id, Some("../x"))]).unwrap_err().code, "invalid_id");
    assert_eq!(r.desktop.queue_add("not-an-org", CO, vec![item(&st[0].id, Some(NODE))]).unwrap_err().code, "invalid_id");
    assert!(r.desktop.queue_list(None).unwrap().is_empty(), "a rejected request queues nothing");
}

// ------------------------------------------------------------------ the happy path and honest statuses
#[tokio::test]
async fn an_item_is_completed_only_after_the_server_confirmed_storage() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_protocol(&s, 503).await; // the final confirmation is not available yet
    let content = pdf("honest");
    let p = write(r.dir.path(), "honest.pdf", &content);
    let id = stage_and_queue(&r, &p).await;
    assert_eq!(item(&r, &id).status, "waiting");
    let run = r.desktop.queue_run().await.unwrap();
    assert_eq!(run.completed, 0);
    let q = item(&r, &id);
    assert_eq!(q.status, "verification_pending", "bytes were sent, but nothing is reported as imported");
    assert!(q.result_document_id.is_none());
    assert_eq!(requests(&s, "PUT", "/content").await[0].body, content, "the exact file bytes were streamed");
    // the server becomes able to confirm: the next run only confirms, it does not upload again
    s.reset().await;
    mount_json(&s, "POST", "/auth/refresh", tokens("A", "R")).await;
    Mock::given(method("POST"))
        .and(path(base(&format!("/uploads/{OP}/complete"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"operation": op("completed"), "document": {"id": DOC}})))
        .mount(&s)
        .await;
    let run = r.desktop.queue_run().await.unwrap();
    assert_eq!(run.completed, 1);
    let q = item(&r, &id);
    assert_eq!((q.status.as_str(), q.result_document_id.as_deref()), ("completed", Some(DOC)));
    assert!(requests(&s, "PUT", "/content").await.is_empty() && requests(&s, "POST", "/uploads").await.is_empty());
}

#[tokio::test]
async fn registration_carries_the_destination_metadata_and_a_stable_idempotency_key() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_protocol(&s, 200).await;
    let p = write(r.dir.path(), "meta.pdf", &pdf("meta"));
    let st = r.desktop.stage_paths(vec![p]).await.unwrap();
    let add = QueueAdd {
        staged_id: st[0].id.clone(),
        node_id: Some(NODE.into()),
        category_id: Some("66666666-6666-4666-8666-666666666666".into()),
        display_name: Some("Supplier Acme.pdf".into()),
        metadata: json!({"supplier": "Acme"}),
        filing_rule_id: None,
        duplicate_policy: "allow_copy".into(),
        target_document_id: None,
        change_reason: None,
        batch_local: None,
    };
    let id = r.desktop.queue_add(ORG, CO, vec![add]).unwrap().remove(0).id;
    r.desktop.queue_run().await.unwrap();
    assert_eq!(item(&r, &id).status, "completed");
    let reg: Value = serde_json::from_slice(&requests(&s, "POST", "/uploads").await[0].body).unwrap();
    assert_eq!(reg["node_id"], NODE);
    assert_eq!((reg["display_name"].as_str(), reg["duplicate_policy"].as_str()), (Some("Supplier Acme.pdf"), Some("allow_copy")));
    assert_eq!(reg["metadata"], json!({"supplier": "Acme"}));
    assert_eq!(reg["sha256"], sha(&pdf("meta")));
    assert!(reg["idempotency_key"].as_str().unwrap().len() >= 8);
    let hdr = &requests(&s, "POST", "/uploads").await[0];
    assert_eq!(hdr.headers.get("x-organization-id").unwrap().to_str().unwrap(), ORG);
}

#[tokio::test]
async fn batches_register_one_server_batch_per_import() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_protocol(&s, 200).await;
    Mock::given(method("POST"))
        .and(path(base("/import-batches")))
        .respond_with(ResponseTemplate::new(201).set_body_json(json!({"id": "77777777-7777-4777-8777-777777777777", "total_files": 2})))
        .mount(&s)
        .await;
    let a = write(r.dir.path(), "a.pdf", &pdf("a"));
    let b = write(r.dir.path(), "b.pdf", &pdf("b"));
    let st = r.desktop.stage_paths(vec![a, b]).await.unwrap();
    let mk = |i: usize| QueueAdd {
        staged_id: st[i].id.clone(),
        node_id: Some(NODE.into()),
        category_id: None,
        display_name: None,
        metadata: json!({}),
        filing_rule_id: None,
        duplicate_policy: "block".into(),
        target_document_id: None,
        change_reason: None,
        batch_local: Some("import-1".into()),
    };
    r.desktop.queue_add(ORG, CO, vec![mk(0), mk(1)]).unwrap();
    r.desktop.queue_run().await.unwrap();
    assert_eq!(requests(&s, "POST", "/import-batches").await.len(), 1);
    for reg in requests(&s, "POST", "/uploads").await {
        let b: Value = serde_json::from_slice(&reg.body).unwrap();
        assert_eq!(b["batch_id"], "77777777-7777-4777-8777-777777777777");
    }
}

// ------------------------------------------------------------------ failures are reported, never hidden
#[tokio::test]
async fn rejected_content_dangerous_files_duplicates_and_limits_are_reported_with_their_reason() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let ops = [
        (
            "a.pdf",
            422,
            json!({"code": "content_mismatch", "detail": "The file is not a PDF document.", "operation": {"status": "failed", "error_code": "content_mismatch"}}),
            "failed",
            "content_mismatch",
        ),
        (
            "b.pdf",
            422,
            json!({"code": "executable_content", "detail": "Windows executables are not accepted.", "operation": {"status": "quarantined", "error_code": "executable_content"}}),
            "quarantined",
            "quarantined",
        ),
    ];
    for (name, status, body, expect, code) in ops {
        s.reset().await;
        mount_json(&s, "POST", "/auth/refresh", tokens("A", "R")).await;
        Mock::given(method("POST"))
            .and(path(base("/uploads")))
            .respond_with(ResponseTemplate::new(201).set_body_json(op("awaiting_upload")))
            .mount(&s)
            .await;
        Mock::given(method("PUT"))
            .and(path_regex(r"/content$"))
            .respond_with(ResponseTemplate::new(status).insert_header("content-type", "application/problem+json").set_body_json(body))
            .mount(&s)
            .await;
        let id = stage_and_queue(&r, &write(r.dir.path(), name, &pdf(name))).await;
        let run = r.desktop.queue_run().await.unwrap();
        let q = item(&r, &id);
        assert_eq!((q.status.as_str(), q.error_code.as_deref()), (expect, Some(code)), "{name}");
        assert!(!q.can_retry || expect == "failed");
        assert_eq!(run.completed, 0);
    }
    // duplicate at registration: nothing is sent, the existing document is named
    s.reset().await;
    Mock::given(method("POST")).and(path(base("/uploads"))).respond_with(
        ResponseTemplate::new(409).insert_header("content-type", "application/problem+json").set_body_json(json!({"code": "duplicate_document", "detail": "A document with identical content already exists in this company.", "duplicates": [{"id": DOC, "display_name": "Existing.pdf"}]}))).mount(&s).await;
    let id = stage_and_queue(&r, &write(r.dir.path(), "dup.pdf", &pdf("dup"))).await;
    r.desktop.queue_run().await.unwrap();
    let q = item(&r, &id);
    assert_eq!((q.status.as_str(), q.error_code.as_deref(), q.can_retry), ("failed", Some("duplicate_document"), false));
    assert_eq!(q.duplicates[0]["display_name"], "Existing.pdf");
    assert!(requests(&s, "PUT", "/content").await.is_empty());
    // plan limit
    s.reset().await;
    Mock::given(method("POST"))
        .and(path(base("/uploads")))
        .respond_with(problem(402, "entitlement_exceeded", "Limit quota.storage_bytes reached (5 of 5 in use)."))
        .mount(&s)
        .await;
    let id = stage_and_queue(&r, &write(r.dir.path(), "full.pdf", &pdf("full"))).await;
    r.desktop.queue_run().await.unwrap();
    let q = item(&r, &id);
    assert_eq!((q.status.as_str(), q.error_code.as_deref()), ("failed", Some("entitlement_exceeded")));
    assert!(q.error_message.unwrap().contains("Limit"));
    // destination removed / access revoked meanwhile
    s.reset().await;
    Mock::given(method("POST"))
        .and(path(base("/uploads")))
        .respond_with(problem(404, "not_found", "Workspace item not found."))
        .mount(&s)
        .await;
    let id = stage_and_queue(&r, &write(r.dir.path(), "gone.pdf", &pdf("gone"))).await;
    r.desktop.queue_run().await.unwrap();
    assert_eq!(item(&r, &id).error_code.as_deref(), Some("destination_unavailable"));
}

#[tokio::test]
async fn a_file_that_changed_or_vanished_after_it_was_added_is_never_uploaded() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_protocol(&s, 200).await;
    let p = write(r.dir.path(), "edit.pdf", &pdf("v1"));
    let changed = stage_and_queue(&r, &p).await;
    std::fs::write(&p, pdf("v2-changed-after-staging")).unwrap();
    let q = write(r.dir.path(), "gone.pdf", &pdf("g"));
    let gone = stage_and_queue(&r, &q).await;
    std::fs::remove_file(&q).unwrap();
    r.desktop.queue_run().await.unwrap();
    assert_eq!(item(&r, &changed).error_code.as_deref(), Some("source_changed"));
    assert_eq!(item(&r, &gone).error_code.as_deref(), Some("source_missing"));
    assert!(!item(&r, &changed).can_retry && !item(&r, &gone).can_retry);
    assert!(requests(&s, "POST", "/uploads").await.is_empty(), "no session was even registered for them");
}

// ------------------------------------------------------------------ resilience: offline, restart, expired session
#[tokio::test]
async fn a_failing_network_defers_the_item_then_gives_up_visibly_and_a_retry_works() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST")).and(path(base("/uploads"))).respond_with(problem(503, "storage_unavailable", "down")).mount(&s).await;
    let id = stage_and_queue(&r, &write(r.dir.path(), "net.pdf", &pdf("net"))).await;
    for attempt in 1..=4 {
        let run = r.desktop.queue_run().await.unwrap();
        assert_eq!(run.stopped.as_deref(), Some("offline"));
        let q = item(&r, &id);
        assert_eq!((q.status.as_str(), q.attempts), ("waiting", attempt), "kept waiting, nothing lost");
    }
    r.desktop.queue_run().await.unwrap();
    let q = item(&r, &id);
    assert_eq!((q.status.as_str(), q.attempts >= 5, q.can_retry), ("failed", true, true), "after repeated failures it is shown as failed");
    s.reset().await;
    mount_protocol(&s, 200).await;
    assert_eq!(r.desktop.queue_retry(&id).unwrap().status, "waiting");
    r.desktop.queue_run().await.unwrap();
    assert_eq!(item(&r, &id).status, "completed");
}

#[tokio::test]
async fn restart_recovery_resumes_without_registering_twice_or_resending_received_bytes() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let id = stage_and_queue(&r, &write(r.dir.path(), "crash.pdf", &pdf("crash"))).await;
    // simulate: the app died mid-transfer; the server already holds a registered session
    r.desktop
        .with_db(|d| {
            let u = "user-1";
            d.queue_set_server(u, &id, None, OP)?;
            d.queue_set_status(u, &id, "uploading", None, None)
        })
        .unwrap();
    assert_eq!(r.desktop.queue_recover().unwrap(), 1);
    assert_eq!(item(&r, &id).status, "waiting");
    // the server says the bytes had in fact arrived
    Mock::given(method("GET"))
        .and(path(base(&format!("/uploads/{OP}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(op("uploaded")))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(base(&format!("/uploads/{OP}/complete"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"operation": op("completed"), "document": {"id": DOC}})))
        .mount(&s)
        .await;
    r.desktop.queue_run().await.unwrap();
    assert_eq!(item(&r, &id).status, "completed");
    assert!(requests(&s, "POST", "/uploads").await.is_empty(), "the registered session was reused");
    assert!(requests(&s, "PUT", "/content").await.is_empty(), "received bytes are not sent again");
    // a session that expired on the server is replaced by a fresh one with a new key
    let id2 = stage_and_queue(&r, &write(r.dir.path(), "old.pdf", &pdf("old"))).await;
    r.desktop.with_db(|d| d.queue_set_server("user-1", &id2, None, OP)).unwrap();
    s.reset().await;
    Mock::given(method("GET"))
        .and(path(base(&format!("/uploads/{OP}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(op("expired")))
        .mount(&s)
        .await;
    r.desktop.queue_run().await.unwrap();
    let q = item(&r, &id2);
    assert_eq!(q.status, "waiting");
    r.desktop
        .with_db(|d| {
            assert!(d.queue_get("user-1", &id2)?.unwrap().server_op_id.is_none());
            Ok(())
        })
        .unwrap();
}

#[tokio::test]
async fn an_ended_session_pauses_the_queue_without_losing_anything_and_users_never_share_queues() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST")).and(path(base("/uploads"))).respond_with(problem(401, "unauthenticated", "expired")).mount(&s).await;
    Mock::given(method("POST")).and(path("/api/v1/auth/refresh")).respond_with(problem(401, "unauthenticated", "revoked")).mount(&s).await;
    let id = stage_and_queue(&r, &write(r.dir.path(), "s.pdf", &pdf("s"))).await;
    let run = r.desktop.queue_run().await.unwrap();
    assert_eq!(run.stopped.as_deref(), Some("session_expired"));
    r.desktop
        .with_db(|d| {
            assert_eq!(d.queue_get("user-1", &id)?.unwrap().status.as_str(), "waiting", "unsent work survives the sign-out");
            assert!(d.queue_get("another-user", &id)?.is_none() && d.queue_list("another-user", None)?.is_empty());
            Ok(())
        })
        .unwrap();
}

#[tokio::test]
async fn cancelling_stops_an_active_transfer_and_cancels_the_server_session() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST"))
        .and(path(base("/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(op("awaiting_upload")))
        .mount(&s)
        .await;
    Mock::given(method("PUT"))
        .and(path_regex(r"/content$"))
        .respond_with(ResponseTemplate::new(200).set_delay(Duration::from_secs(3)).set_body_json(op("uploaded")))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(base(&format!("/uploads/{OP}/cancel"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(op("cancelled")))
        .mount(&s)
        .await;
    let big = vec![b'%'; 3 * 1024 * 1024];
    let mut content = b"%PDF-1.4\n".to_vec();
    content.extend(&big);
    content.extend(b"\n%%EOF\n");
    let id = stage_and_queue(&r, &write(r.dir.path(), "slow.pdf", &content)).await;
    let (run, cancel) = tokio::join!(r.desktop.queue_run(), async {
        tokio::time::sleep(Duration::from_millis(400)).await;
        r.desktop.queue_cancel(&id).await
    });
    run.unwrap();
    let q = cancel.unwrap();
    assert_eq!(q.status, "cancelled");
    assert_eq!(item(&r, &id).status, "cancelled");
    assert!(!requests(&s, "POST", "/cancel").await.is_empty(), "the server session was cancelled as well");
    assert!(r.desktop.queue_remove(&id).unwrap());
    assert_eq!(r.desktop.queue_cancel(&id).await.unwrap_err().code, "not_found");
}

#[tokio::test]
async fn finished_items_can_be_cleared_but_unfinished_ones_are_kept() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_protocol(&s, 200).await;
    let done = stage_and_queue(&r, &write(r.dir.path(), "d.pdf", &pdf("d"))).await;
    r.desktop.queue_run().await.unwrap();
    s.reset().await;
    let waiting = stage_and_queue(&r, &write(r.dir.path(), "w.pdf", &pdf("w"))).await;
    assert_eq!(r.desktop.queue_clear_finished(None).unwrap(), 1);
    let ids: Vec<String> = r.desktop.queue_list(None).unwrap().into_iter().map(|q| q.id).collect();
    assert_eq!(ids, vec![waiting]);
    assert!(!ids.contains(&done));
    assert_eq!(r.desktop.queue_retry(&ids[0]).unwrap_err().code, "not_retryable");
}

// ------------------------------------------------------------------ downloads, previews and the folder mirror
fn node(id: &str, parent: Option<&str>, name: &str, archived: bool) -> Value {
    json!({"id": id, "parent_id": parent, "node_type": if parent.is_some() {"folder"} else {"section"}, "key": id, "name": name, "display_name": null,
           "description": "", "position": 0, "depth": 1, "is_active": true, "archived_at": if archived { Some("2026-01-01T00:00:00Z") } else { None }})
}
async fn with_workspace(s: &MockServer, r: &Rig, nodes: Vec<Value>, version: i64) {
    s.reset().await;
    mount_json(s, "POST", "/auth/refresh", tokens("A", "R")).await;
    Mock::given(method("GET"))
        .and(path(format!("/api/v1/companies/{CO}/workspace")))
        .respond_with(ResponseTemplate::new(200).insert_header("etag", format!("W/\"{version}\"").as_str()).set_body_json(json!({"company_id": CO, "version": version, "workspace_state": "configured", "access_mode": "open", "nodes": nodes, "categories": [], "fields": []})))
        .mount(s)
        .await;
    r.desktop.refresh_workspace(ORG, CO).await.unwrap();
}
fn root(r: &Rig) {
    mount_company_cache(r);
}
fn mount_company_cache(r: &Rig) {
    let dir = r.dir.path().join("Artify Data");
    std::fs::create_dir_all(&dir).unwrap();
    r.desktop.set_storage_root(&dir).unwrap();
    r.desktop
        .with_db(|d| {
            d.replace_companies(
                "user-1",
                ORG,
                &[(
                    CO.to_string(),
                    "active".to_string(),
                    json!({"id": CO, "name": "Acme Trading", "code": "ACME", "country": "OM", "base_currency": "OMR", "status": "active"}),
                )],
            )
        })
        .unwrap();
}

#[tokio::test]
async fn mirror_follows_renames_and_moves_by_id_and_never_deletes_or_overwrites() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    root(&r);
    let (a, b, c) =
        ("a0000000-0000-4000-8000-000000000001", "a0000000-0000-4000-8000-000000000002", "a0000000-0000-4000-8000-000000000003");
    with_workspace(&s, &r, vec![node(a, None, "Sales", false), node(b, None, "Purchases", false), node(c, Some(a), "Invoices", false)], 1)
        .await;
    let rep = r.desktop.mirror_sync(ORG, CO).unwrap();
    assert_eq!((rep.created, rep.renamed), (3, 0));
    let base = r.desktop.ensure_company_dir(ORG, CO).unwrap();
    assert!(base.join("Sales/Invoices").is_dir() && base.join("Purchases").is_dir());
    std::fs::write(base.join("Sales/Invoices/keep.txt"), "user file").unwrap();
    // rename Sales -> Revenue, move Invoices under Purchases: the SAME folders follow, content included
    with_workspace(
        &s,
        &r,
        vec![node(a, None, "Revenue", false), node(b, None, "Purchases", false), node(c, Some(b), "Invoices", false)],
        2,
    )
    .await;
    let rep = r.desktop.mirror_sync(ORG, CO).unwrap();
    assert_eq!((rep.renamed, rep.created), (2, 0), "{rep:?}");
    assert!(base.join("Revenue").is_dir() && !base.join("Sales").exists());
    assert_eq!(std::fs::read_to_string(base.join("Purchases/Invoices/keep.txt")).unwrap(), "user file");
    // archiving a folder leaves its local folder (and files) in place
    with_workspace(&s, &r, vec![node(a, None, "Revenue", false), node(b, None, "Purchases", true), node(c, Some(b), "Invoices", false)], 3)
        .await;
    let rep = r.desktop.mirror_sync(ORG, CO).unwrap();
    assert_eq!(rep.orphaned, 2);
    assert!(base.join("Purchases/Invoices/keep.txt").exists());
    // a pre-existing local folder with the target name is a conflict, not an overwrite
    std::fs::create_dir(base.join("Taken")).unwrap();
    std::fs::write(base.join("Taken/precious.txt"), "mine").unwrap();
    with_workspace(&s, &r, vec![node(a, None, "Taken", false), node(b, None, "Purchases", true), node(c, Some(b), "Invoices", false)], 4)
        .await;
    let rep = r.desktop.mirror_sync(ORG, CO).unwrap();
    assert_eq!(rep.conflicts, vec!["Taken".to_string()]);
    assert_eq!(std::fs::read_to_string(base.join("Taken/precious.txt")).unwrap(), "mine");
    assert!(base.join("Revenue").is_dir(), "the node keeps its previous folder while the name is taken");
}

#[tokio::test]
async fn an_unavailable_drive_stops_the_mirror_before_anything_is_created() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    root(&r);
    with_workspace(&s, &r, vec![node("a0000000-0000-4000-8000-000000000001", None, "Sales", false)], 1).await;
    std::fs::remove_dir_all(r.dir.path().join("Artify Data")).unwrap();
    assert_eq!(r.desktop.mirror_sync(ORG, CO).unwrap_err().code, "storage_unavailable");
    assert!(!r.dir.path().join("Artify Data").exists(), "a missing root is never recreated implicitly");
}

#[test]
fn mirror_planning_is_deterministic_safe_and_collision_free() {
    let n = |id: &str, parent: Option<&str>, name: &str, pos: i64, archived: bool| MirrorNode {
        id: id.into(),
        parent_id: parent.map(str::to_string),
        name: name.into(),
        position: pos,
        archived,
    };
    let nodes = vec![
        n("id-aaaaaa", None, "Bank/Statements", 0, false),
        n("id-bbbbbb", None, "bank statements", 1, false), // clashes case-insensitively once sanitized? different text -> distinct
        n("id-cccccc", None, "CON", 2, false),
        n("id-dddddd", None, "Same", 3, false),
        n("id-eeeeee", None, "same", 4, false),
        n("id-ffffff", Some("id-dddddd"), "..", 0, false),
        n("id-gggggg", Some("id-zzzzzz-missing"), "Orphan parent", 0, false),
        n("id-hhhhhh", None, "Old", 5, true),
        n("id-iiiiii", Some("id-hhhhhh"), "Under archived", 0, false),
    ];
    let p = mirror::plan(&nodes);
    assert!(!p.contains_key("id-hhhhhh") && !p.contains_key("id-iiiiii"), "archived nodes and their children are not mirrored");
    assert_eq!(p["id-aaaaaa"], "Bank-Statements");
    assert_eq!(p["id-cccccc"], "CON_");
    assert_eq!(p["id-dddddd"], "Same");
    assert_eq!(p["id-eeeeee"], "same (eeeeee)", "a case-insensitive clash gets a deterministic suffix");
    assert!(p["id-gggggg"] == "Orphan parent", "a node whose parent is unknown is treated as a root");
    for rel in p.values() {
        assert!(!rel.contains("..") && !rel.starts_with('/') && !rel.contains('\\') && !rel.contains(':'), "{rel}");
    }
    let again = mirror::plan(&nodes.iter().rev().cloned().collect::<Vec<_>>());
    assert_eq!(p, again, "independent of input order");
    let mut seen = std::collections::HashSet::new();
    assert!(p.values().all(|v| seen.insert(v.to_lowercase())), "no two nodes share a folder (case-insensitive)");
}

#[test]
fn file_names_are_windows_safe_unique_and_keep_their_extension() {
    let d = tempfile::tempdir().unwrap();
    assert_eq!(mirror::safe_file_name("..\\..\\evil:name?.PDF"), "evil-name.pdf");
    assert_eq!(mirror::safe_file_name("CON.pdf"), "CON_.pdf");
    let long = format!("{}.pdf", "x".repeat(300));
    let s = mirror::safe_file_name(&long);
    assert!(s.ends_with(".pdf") && s.chars().count() <= 106);
    assert_eq!(mirror::unique_file_name(d.path(), "a.pdf"), "a.pdf");
    std::fs::write(d.path().join("a.pdf"), "1").unwrap();
    assert_eq!(mirror::unique_file_name(d.path(), "a.pdf"), "a (2).pdf");
    std::fs::write(d.path().join("a (2).pdf"), "2").unwrap();
    assert_eq!(mirror::unique_file_name(d.path(), "a.pdf"), "a (3).pdf");
    assert_eq!(mirror::unique_file_name(d.path(), "noext"), "noext");
}

fn detail(version: i64, size: u64, mime: &str) -> Value {
    json!({"id": DOC, "node_id": NODE, "display_name": "Invoice.pdf", "current_version_no": version, "size_bytes": size, "mime_type": mime})
}

#[tokio::test]
async fn saving_a_copy_lands_in_the_workspace_folder_verified_and_is_never_overwritten() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    root(&r);
    with_workspace(&s, &r, vec![node(NODE, None, "Receipts", false)], 1).await;
    let v1 = pdf("version one");
    Mock::given(method("GET"))
        .and(path(base(&format!("/{DOC}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(detail(1, v1.len() as u64, "application/pdf")))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(base(&format!("/{DOC}/content"))))
        .respond_with(ResponseTemplate::new(200).insert_header("x-document-sha256", sha(&v1).as_str()).set_body_bytes(v1.clone()))
        .mount(&s)
        .await;
    let saved = r.desktop.docs_save_copy(ORG, CO, DOC, None).await.unwrap();
    assert!(saved.created && saved.path.contains("Receipts") && saved.path.ends_with("Invoice.pdf"));
    assert_eq!(std::fs::read(&saved.path).unwrap(), v1);
    let again = r.desktop.docs_save_copy(ORG, CO, DOC, None).await.unwrap();
    assert!(!again.created && again.path == saved.path, "an identical saved copy is reused, not downloaded again");
    assert_eq!(requests(&s, "GET", "/content").await.len(), 1);
    // the user edits the local copy; saving again must not overwrite it
    std::fs::write(&saved.path, b"my own edits").unwrap();
    let third = r.desktop.docs_save_copy(ORG, CO, DOC, None).await.unwrap();
    assert!(third.created && third.path != saved.path && third.name == "Invoice (2).pdf");
    assert_eq!(std::fs::read(&saved.path).unwrap(), b"my own edits");
    // no half-written files are left behind
    let dir = Path::new(&saved.path).parent().unwrap();
    assert!(std::fs::read_dir(dir).unwrap().flatten().all(|e| !e.file_name().to_string_lossy().ends_with(".part")));
}

#[tokio::test]
async fn a_damaged_download_is_refused_and_leaves_nothing_behind() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    root(&r);
    with_workspace(&s, &r, vec![node(NODE, None, "Receipts", false)], 1).await;
    let good = pdf("good");
    Mock::given(method("GET"))
        .and(path(base(&format!("/{DOC}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(detail(1, good.len() as u64, "application/pdf")))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(base(&format!("/{DOC}/content"))))
        .respond_with(ResponseTemplate::new(200).insert_header("x-document-sha256", sha(&good).as_str()).set_body_bytes(pdf("tampered")))
        .mount(&s)
        .await;
    let e = r.desktop.docs_save_copy(ORG, CO, DOC, None).await.unwrap_err();
    assert_eq!(e.code, "download_corrupted");
    let base_dir = r.desktop.ensure_company_dir(ORG, CO).unwrap();
    let files: Vec<_> = walk(&base_dir);
    assert!(files.is_empty(), "{files:?}");
}
fn walk(p: &Path) -> Vec<std::path::PathBuf> {
    let mut out = vec![];
    for e in std::fs::read_dir(p).unwrap().flatten() {
        if e.path().is_dir() {
            out.extend(walk(&e.path()));
        } else {
            out.push(e.path());
        }
    }
    out
}

#[tokio::test]
async fn previews_are_limited_to_safe_types_and_size_and_verified() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let body = pdf("preview");
    Mock::given(method("GET"))
        .and(path(base(&format!("/{DOC}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(detail(1, body.len() as u64, "application/pdf")))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(base(&format!("/{DOC}/content"))))
        .and(header("authorization", "Bearer ACCESS"))
        .respond_with(ResponseTemplate::new(200).insert_header("x-document-sha256", sha(&body).as_str()).set_body_bytes(body.clone()))
        .mount(&s)
        .await;
    let p = r.desktop.docs_preview(ORG, CO, DOC, None).await.unwrap();
    assert_eq!((p.mime.as_str(), p.size), ("application/pdf", body.len() as u64));
    use base64::Engine;
    assert_eq!(base64::engine::general_purpose::STANDARD.decode(p.data_base64).unwrap(), body);
    assert!(requests(&s, "GET", "/content").await[0].url.query().unwrap().contains("disposition=inline"));
    for (mime, size, code) in [
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", 10u64, "not_previewable"),
        ("application/pdf", 50 * 1024 * 1024, "too_large_to_preview"),
        ("image/tiff", 10, "not_previewable"),
    ] {
        s.reset().await;
        mount_json(&s, "POST", "/auth/refresh", tokens("A", "R")).await;
        Mock::given(method("GET"))
            .and(path(base(&format!("/{DOC}"))))
            .respond_with(ResponseTemplate::new(200).set_body_json(detail(1, size, mime)))
            .mount(&s)
            .await;
        assert_eq!(r.desktop.docs_preview(ORG, CO, DOC, None).await.unwrap_err().code, code);
        assert!(requests(&s, "GET", "/content").await.is_empty(), "nothing is downloaded for a file that cannot be previewed");
    }
}

#[tokio::test]
async fn document_calls_validate_ids_and_whitelist_query_parameters() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(base("")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"items": [], "total": 0, "limit": 50, "offset": 0})))
        .mount(&s)
        .await;
    for bad in ["../x", "not-a-uuid", "1/../../etc"] {
        assert_eq!(r.desktop.docs_get(ORG, CO, bad).await.unwrap_err().code, "invalid_id");
        assert_eq!(r.desktop.docs_get(ORG, bad, DOC).await.unwrap_err().code, "invalid_id");
        assert_eq!(r.desktop.docs_action(ORG, CO, bad, "archive", None).await.unwrap_err().code, "invalid_id");
    }
    assert_eq!(r.desktop.docs_action(ORG, CO, DOC, "format-disk", None).await.unwrap_err().code, "unknown_action");
    r.desktop.docs_list(ORG, CO, &json!({"q": "inv oice&evil=1", "node_id": NODE, "sort": "name", "limit": 25, "offset": 50, "organization_id": "x", "company_id": "y", "status": "archived"})).await.unwrap();
    let q = requests(&s, "GET", "/documents").await.pop().unwrap().url.query().unwrap().to_string();
    assert!(q.contains("sort=name") && q.contains("limit=25") && q.contains("offset=50") && q.contains("status=archived"), "{q}");
    assert!(q.contains("q=inv+oice%26evil%3D1"), "user input is encoded, not interpreted: {q}");
    assert!(!q.contains("organization_id") && !q.contains("company_id"), "tenant parameters are never forwarded: {q}");
    assert_eq!(r.desktop.docs_list(ORG, CO, &json!({"node_id": "../../x"})).await.unwrap_err().code, "invalid_id");
    let _: HashMap<String, String> = HashMap::new();
}
