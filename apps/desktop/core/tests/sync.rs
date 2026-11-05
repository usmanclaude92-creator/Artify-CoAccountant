//! Phase 12 desktop core tests: the change-feed checkpoint, the honest status, the conflict passthrough and resumable chunked
//! uploads - against a simulated server that follows the real protocol.
mod common;
use artify_core::api::LoginOutcome;
use artify_core::queue::QueueAdd;
use artify_core::sync::fold_state;
use common::*;
use serde_json::{json, Value};
use wiremock::matchers::{body_json, method, path, query_param, query_param_is_missing};
use wiremock::{Mock, MockServer, ResponseTemplate};

const ORG: &str = "11111111-1111-4111-8111-111111111111";
const CO: &str = "22222222-2222-4222-8222-222222222222";
const NODE: &str = "33333333-3333-4333-8333-333333333333";
const OP: &str = "44444444-4444-4444-8444-444444444444";
const DOC: &str = "55555555-5555-4555-8555-555555555555";
const CF: &str = "66666666-6666-4666-8666-666666666666";

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH-TOKEN-VALUE")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("accountant@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    r
}
fn sy(tail: &str) -> String {
    format!("/api/v1/companies/{CO}/sync{tail}")
}
fn item(kind: &str, id: &str, rev: i64) -> Value {
    json!({"kind": kind, "id": id, "revision": rev, "status": "active", "changed_at": "2025-03-10T10:00:00Z", "data": {}})
}
fn page(items: Vec<Value>, next: &str, more: bool) -> Value {
    json!({"items": items, "next_cursor": next, "has_more": more, "server_time": "2025-03-10T10:00:05Z", "kinds": ["document"]})
}
fn feed(r: &Rig) -> artify_core::db::FeedState {
    let u = r.desktop.user_id().unwrap();
    r.desktop.with_db(|d| d.feed_get(&u, CO)).unwrap()
}

#[tokio::test]
async fn the_feed_is_read_in_pages_and_the_checkpoint_survives_to_the_next_pull() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .and(query_param_is_missing("cursor"))
        .respond_with(ResponseTemplate::new(200).set_body_json(page(
            vec![item("document", "d1", 1), item("transaction", "t1", 2)],
            "C1",
            true,
        )))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .and(query_param("cursor", "C1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(page(vec![item("document", "d1", 3)], "C2", false)))
        .mount(&s)
        .await;
    let sum = r.desktop.sync_pull(ORG, CO).await.unwrap();
    assert_eq!((sum.pages, sum.items, sum.caught_up), (2, 3, true));
    let f = feed(&r);
    assert_eq!((f.cursor.as_deref(), f.caught_up, f.pulled_total, f.last_error.is_none()), (Some("C2"), true, 3, true));
    let u = r.desktop.user_id().unwrap();
    // the newest revision of an item wins; an older one never replaces it
    assert_eq!(r.desktop.with_db(|d| d.feed_item_revision(&u, CO, "document", "d1")).unwrap(), Some(3));
    assert_eq!(r.desktop.with_db(|d| d.feed_counts(&u, CO)).unwrap(), vec![("document".to_string(), 1), ("transaction".to_string(), 1)]);
    // the next pull continues from the saved cursor (and re-reads a few seconds before it, safely)
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .and(query_param("cursor", "C2"))
        .and(query_param("overlap_seconds", "5"))
        .respond_with(ResponseTemplate::new(200).set_body_json(page(vec![], "C2", false)))
        .expect(1)
        .mount(&s)
        .await;
    let again = r.desktop.sync_pull(ORG, CO).await.unwrap();
    assert_eq!((again.pages, again.items, again.caught_up), (1, 0, true));
}

#[tokio::test]
async fn a_failed_page_never_advances_the_checkpoint_and_a_rejected_cursor_restarts() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .and(query_param_is_missing("cursor"))
        .respond_with(ResponseTemplate::new(200).set_body_json(page(vec![item("document", "d1", 1)], "C1", true)))
        .up_to_n_times(1)
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .and(query_param("cursor", "C1"))
        .respond_with(ResponseTemplate::new(503))
        .mount(&s)
        .await;
    let err = r.desktop.sync_pull(ORG, CO).await.unwrap_err();
    assert!(err.retryable || err.status == Some(503), "{err:?}");
    let f = feed(&r);
    assert_eq!(f.cursor.as_deref(), Some("C1"), "page 1 was stored with its cursor");
    assert!(f.last_error.is_some() && !f.caught_up, "the failure is recorded, not hidden");

    // the server later refuses the saved cursor: start again from the beginning, once
    s.reset().await;
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .and(query_param("cursor", "C1"))
        .respond_with(problem(422, "invalid_cursor", "That cursor is not valid."))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .and(query_param_is_missing("cursor"))
        .respond_with(ResponseTemplate::new(200).set_body_json(page(vec![item("document", "d1", 4)], "C9", false)))
        .mount(&s)
        .await;
    let sum = r.desktop.sync_pull(ORG, CO).await.unwrap();
    assert!(sum.restarted && sum.caught_up);
    assert_eq!(feed(&r).cursor.as_deref(), Some("C9"));
}

#[tokio::test]
async fn the_status_says_synchronized_only_when_the_server_confirmed_everything() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    // nothing pulled yet: not synchronized, whatever the server says
    Mock::given(method("GET"))
        .and(path(sy("/status")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"open_conflicts": 0})))
        .up_to_n_times(1)
        .mount(&s)
        .await;
    let st = r.desktop.sync_status(ORG, CO).await.unwrap();
    assert_eq!((st.state.as_str(), st.open_conflicts), ("pending", Some(0)));
    Mock::given(method("GET"))
        .and(path(sy("/changes")))
        .respond_with(ResponseTemplate::new(200).set_body_json(page(vec![], "C1", false)))
        .mount(&s)
        .await;
    r.desktop.sync_pull(ORG, CO).await.unwrap();
    Mock::given(method("GET"))
        .and(path(sy("/status")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"open_conflicts": 0})))
        .up_to_n_times(1)
        .mount(&s)
        .await;
    assert_eq!(r.desktop.sync_status(ORG, CO).await.unwrap().state, "synchronized");
    Mock::given(method("GET"))
        .and(path(sy("/status")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"open_conflicts": 2})))
        .up_to_n_times(1)
        .mount(&s)
        .await;
    let c = r.desktop.sync_status(ORG, CO).await.unwrap();
    assert_eq!((c.state.as_str(), c.open_conflicts), ("conflict_detected", Some(2)));
    // the server cannot be asked: the honest answer is "pending", never "synchronized"
    Mock::given(method("GET")).and(path(sy("/status"))).respond_with(ResponseTemplate::new(503)).mount(&s).await;
    let n = r.desktop.sync_status(ORG, CO).await.unwrap();
    assert_eq!((n.state.as_str(), n.open_conflicts), ("pending", None));
}

#[test]
fn state_folding_is_strict() {
    assert_eq!(fold_state(0, 0, 0, Some(0), true, true, None).0, "synchronized");
    assert_eq!(fold_state(0, 0, 0, None, true, true, None).0, "pending");
}

#[tokio::test]
async fn only_fixed_conflict_routes_can_be_called_and_the_body_travels_unchanged() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let report =
        json!({"kind": "document", "id": DOC, "client_id": "laptop-1", "base_revision": 3, "local_summary": {"display_name": "Mine"}});
    Mock::given(method("POST"))
        .and(path(sy("/conflicts")))
        .and(body_json(report.clone()))
        .respond_with(ResponseTemplate::new(201).set_body_json(json!({"id": CF, "state": "open", "conflict_type": "stale"})))
        .mount(&s)
        .await;
    let decision = json!({"resolution": "keep_server"});
    Mock::given(method("POST"))
        .and(path(sy(&format!("/conflicts/{CF}/resolve"))))
        .and(body_json(decision.clone()))
        .respond_with(problem(403, "forbidden", "Only the person who found the conflict can decide it."))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(sy("/conflicts")))
        .and(query_param("state", "open"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"items": [], "total": 0})))
        .mount(&s)
        .await;
    let made = r.desktop.sync_call(ORG, CO, "conflict_report", None, &[], report).await.unwrap();
    assert_eq!(made["id"], CF);
    r.desktop.sync_call(ORG, CO, "conflicts", None, &[("state".into(), "open".into())], Value::Null).await.unwrap();
    // the server's refusal reaches the interface unchanged
    let e = r.desktop.sync_call(ORG, CO, "conflict_resolve", Some(CF), &[], decision).await.unwrap_err();
    assert_eq!((e.code.as_str(), e.status), ("forbidden", Some(403)));
    // everything else is refused locally
    for (op, arg, q) in [
        ("delete_everything", None, vec![]),
        ("conflict", Some("../../etc"), vec![]),
        ("conflict", None, vec![]),
        ("conflicts", Some(CF), vec![]),
        ("conflicts", None, vec![("sql".to_string(), "x".to_string())]),
        ("conflict_report", None, vec![("state".to_string(), "open".to_string())]),
    ] {
        let err = r.desktop.sync_call(ORG, CO, op, arg, &q, Value::Null).await.unwrap_err();
        assert!(matches!(err.code.as_str(), "unknown_action" | "invalid_id" | "invalid_query"), "{op}: {err:?}");
    }
    assert_eq!(s.received_requests().await.unwrap().iter().filter(|q| q.url.path().contains("/sync/")).count(), 3);
}

// ------------------------------------------------------------------ resumable uploads
const MIB: usize = 1024 * 1024;

fn op_json(status: &str, received: u64) -> Value {
    json!({"id": OP, "status": status, "company_id": CO, "result_document_id": null, "chunk_max_bytes": 4 * MIB, "received_bytes": received})
}

async fn queue_big(r: &Rig, bytes: &[u8]) -> String {
    let p = r.dir.path().join("big.pdf");
    std::fs::write(&p, bytes).unwrap();
    let staged = r.desktop.stage_paths(vec![p]).await.unwrap();
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

fn big() -> Vec<u8> {
    let mut v = b"%PDF-1.4\n".to_vec();
    v.extend((0..(9 * MIB)).map(|i| (i % 251) as u8));
    v.extend(b"\n%%EOF\n");
    v
}

fn chunk_calls(reqs: &[wiremock::Request]) -> Vec<(u64, usize)> {
    reqs.iter()
        .filter(|q| q.method.as_str() == "PATCH" && q.url.path().ends_with("/chunk"))
        .map(|q| (q.url.query_pairs().find(|(k, _)| k == "offset").unwrap().1.parse().unwrap(), q.body.len()))
        .collect()
}

async fn mount_complete(s: &MockServer) {
    Mock::given(method("POST"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads/{OP}/complete")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"operation": op_json("completed", 0), "document": {"id": DOC}})))
        .mount(s)
        .await;
}

#[tokio::test]
async fn a_big_file_goes_in_chunks_and_resumes_where_the_server_says_it_is() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let data = big();
    let size = data.len();
    Mock::given(method("POST"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(op_json("awaiting_upload", 0)))
        .mount(&s)
        .await;
    // first attempt: the server accepts chunk 1, then the connection drops on chunk 2
    Mock::given(method("PATCH"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads/{OP}/chunk")))
        .and(query_param("offset", "0"))
        .respond_with(ResponseTemplate::new(200).set_body_json(op_json("uploading", (4 * MIB) as u64)))
        .mount(&s)
        .await;
    Mock::given(method("PATCH"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads/{OP}/chunk")))
        .and(query_param("offset", (4 * MIB).to_string()))
        .respond_with(ResponseTemplate::new(503))
        .up_to_n_times(1)
        .mount(&s)
        .await;
    let id = queue_big(&r, &data).await;
    r.desktop.queue_run().await.unwrap();
    let first = chunk_calls(&s.received_requests().await.unwrap());
    assert_eq!(first, vec![(0, 4 * MIB), ((4 * MIB) as u64, 4 * MIB)], "two chunks tried, the second was refused");
    let v = r.desktop.queue_list(None).unwrap().into_iter().find(|q| q.id == id).unwrap();
    assert_ne!(v.status, "completed", "never shown as uploaded before the server said so");

    // the next run asks the server where it is (4 MiB) and sends only the rest
    s.reset().await;
    Mock::given(method("GET"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads/{OP}")))
        .respond_with(ResponseTemplate::new(200).set_body_json(op_json("uploading", (4 * MIB) as u64)))
        .mount(&s)
        .await;
    Mock::given(method("PATCH"))
        .and(path_regex_chunk())
        .and(query_param("offset", (4 * MIB).to_string()))
        .respond_with(ResponseTemplate::new(200).set_body_json(op_json("uploading", (8 * MIB) as u64)))
        .mount(&s)
        .await;
    Mock::given(method("PATCH"))
        .and(path_regex_chunk())
        .and(query_param("offset", (8 * MIB).to_string()))
        .respond_with(ResponseTemplate::new(200).set_body_json(op_json("uploaded", size as u64)))
        .mount(&s)
        .await;
    mount_complete(&s).await;
    force_due(&r, &id);
    r.desktop.queue_run().await.unwrap();
    let second = chunk_calls(&s.received_requests().await.unwrap());
    assert_eq!(second, vec![((4 * MIB) as u64, 4 * MIB), ((8 * MIB) as u64, size - 8 * MIB)], "resumed, not restarted");
    let v = r.desktop.queue_list(None).unwrap().into_iter().find(|q| q.id == id).unwrap();
    assert_eq!((v.status.as_str(), v.result_document_id.as_deref()), ("completed", Some(DOC)));
    // the bytes that arrived are exactly the file
    let mut got = Vec::new();
    for q in s.received_requests().await.unwrap().iter().filter(|q| q.method.as_str() == "PATCH") {
        got.extend_from_slice(&q.body);
    }
    assert_eq!(&got[..], &data[4 * MIB..]);
}

#[tokio::test]
async fn a_server_that_holds_a_different_offset_is_believed_and_a_confused_one_stops_the_loop() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let data = big();
    Mock::given(method("POST"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(op_json("awaiting_upload", 0)))
        .mount(&s)
        .await;
    // chunk at 0 -> "I hold 4 MiB" (an earlier answer was lost); the client continues from 4 MiB
    Mock::given(method("PATCH"))
        .and(path_regex_chunk())
        .and(query_param("offset", "0"))
        .respond_with(
            ResponseTemplate::new(409)
                .set_body_json(json!({"code": "offset_mismatch", "status": 409, "detail": "x", "received_bytes": 4 * MIB})),
        )
        .up_to_n_times(1)
        .mount(&s)
        .await;
    Mock::given(method("PATCH"))
        .and(path_regex_chunk())
        .and(query_param("offset", (4 * MIB).to_string()))
        .respond_with(ResponseTemplate::new(200).set_body_json(op_json("uploading", (8 * MIB) as u64)))
        .mount(&s)
        .await;
    Mock::given(method("PATCH"))
        .and(path_regex_chunk())
        .and(query_param("offset", (8 * MIB).to_string()))
        .respond_with(ResponseTemplate::new(200).set_body_json(op_json("uploaded", data.len() as u64)))
        .mount(&s)
        .await;
    mount_complete(&s).await;
    let id = queue_big(&r, &data).await;
    r.desktop.queue_run().await.unwrap();
    let calls = chunk_calls(&s.received_requests().await.unwrap());
    assert_eq!(calls.iter().map(|c| c.0).collect::<Vec<_>>(), vec![0, (4 * MIB) as u64, (8 * MIB) as u64]);
    assert_eq!(r.desktop.queue_list(None).unwrap().into_iter().find(|q| q.id == id).unwrap().status, "completed");

    // a server that keeps answering with a nonsense offset cannot spin the client forever
    let s2 = MockServer::start().await;
    let r2 = signed_in(&s2).await;
    Mock::given(method("POST"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(op_json("awaiting_upload", 0)))
        .mount(&s2)
        .await;
    Mock::given(method("PATCH"))
        .and(path_regex_chunk())
        .respond_with(
            ResponseTemplate::new(409).set_body_json(json!({"code": "offset_mismatch", "status": 409, "detail": "x", "received_bytes": 1})),
        )
        .mount(&s2)
        .await;
    let id2 = queue_big(&r2, &data).await;
    r2.desktop.queue_run().await.unwrap();
    assert!(chunk_calls(&s2.received_requests().await.unwrap()).len() <= 8, "bounded");
    assert_ne!(r2.desktop.queue_list(None).unwrap().into_iter().find(|q| q.id == id2).unwrap().status, "completed");
}

#[tokio::test]
async fn small_files_still_use_the_single_request() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(op_json("awaiting_upload", 0)))
        .mount(&s)
        .await;
    Mock::given(method("PUT"))
        .and(path(format!("/api/v1/companies/{CO}/documents/uploads/{OP}/content")))
        .respond_with(ResponseTemplate::new(200).set_body_json(op_json("uploaded", 0)))
        .mount(&s)
        .await;
    mount_complete(&s).await;
    let id = queue_big(&r, b"%PDF-1.4\n% small\n%%EOF\n").await;
    r.desktop.queue_run().await.unwrap();
    assert!(chunk_calls(&s.received_requests().await.unwrap()).is_empty());
    assert_eq!(r.desktop.queue_list(None).unwrap().into_iter().find(|q| q.id == id).unwrap().status, "completed");
}

fn path_regex_chunk() -> wiremock::matchers::PathRegexMatcher {
    wiremock::matchers::path_regex(format!(r"^/api/v1/companies/{CO}/documents/uploads/{OP}/chunk$"))
}

/// A deferred item waits for its retry time; the test makes it due now.
fn force_due(r: &Rig, id: &str) {
    let u = r.desktop.user_id().unwrap();
    r.desktop.with_db(|d| d.queue_requeue(&u, id, false)).unwrap();
}
