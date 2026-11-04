mod common;
use std::path::PathBuf;
use std::sync::Arc;

use artify_core::api::LoginOutcome;
use artify_core::documents::{DocOp, QueueItem};
use artify_core::imports::{CANCELLED, COMPLETED, FAILED, QUARANTINED, WAITING};
use common::*;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use wiremock::matchers::{header_exists, method, path, path_regex, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

const CO: &str = "00000000-0000-4000-8000-0000000000c1";
const NODE: &str = "00000000-0000-4000-8000-0000000000a1";
const SID: &str = "00000000-0000-4000-8000-0000000000e1";
const DOC: &str = "00000000-0000-4000-8000-0000000000d1";

fn sha(b: &[u8]) -> String {
    Sha256::digest(b).iter().map(|x| format!("{x:02x}")).collect()
}
fn p(s: &str) -> String {
    format!("/api/v1/companies/{CO}{s}")
}

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    mount_json(s, "GET", "/orgs/org-1/companies", json!([{"id": CO, "organization_id": "org-1", "name": "Gulf Trading", "code": "G1", "country": "OM", "base_currency": "OMR", "status": "active"}])).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("a@example.com", "pw").await.unwrap(), LoginOutcome::Authenticated);
    r
}
fn file(dir: &std::path::Path, name: &str, bytes: &[u8]) -> PathBuf {
    let f = dir.join(name);
    std::fs::write(&f, bytes).unwrap();
    f
}
async fn mount_limits(s: &MockServer, chunk: u64) {
    Mock::given(method("GET")).and(path(p("/documents/limits")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"max_file_size_bytes": 1000000, "chunk_max_bytes": chunk, "allowed_extensions": ["pdf", "png", "txt"], "import_batch_max_files": 10, "max_concurrent_uploads": 3})))
        .mount(s).await;
}
fn session(status: &str, received: u64) -> Value {
    json!({"id": SID, "status": status, "received_bytes": received, "result_document_id": null})
}
fn item(token: &str) -> QueueItem {
    QueueItem { token: token.into(), node_id: NODE.into(), ..Default::default() }
}
async fn queue_one(r: &Rig, f: PathBuf) -> String {
    let staged = r.desktop.stage_paths(vec![f]);
    let res = r.desktop.queue_imports("org-1", CO, vec![item(&staged[0].token)]).await.unwrap();
    assert!(res.rejected.is_empty(), "{:?}", res.rejected);
    res.queued[0].id.clone()
}

#[tokio::test]
async fn an_import_is_completed_only_after_the_server_confirms_and_chunks_resume_at_the_servers_offset() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let data: Vec<u8> = (0..150_000u32).map(|i| (i % 251) as u8).collect();
    let f = file(r.dir.path(), "statement.pdf", &data);
    Mock::given(method("POST"))
        .and(path(p("/uploads")))
        .and(header_exists("idempotency-key"))
        .respond_with(ResponseTemplate::new(201).set_body_json(session("open", 0)))
        .expect(1)
        .mount(&s)
        .await;
    // the server already holds the first 65536 bytes (a previous run was interrupted): resume there, not at zero
    Mock::given(method("GET"))
        .and(path(p(&format!("/uploads/{SID}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(session("uploading", 65536)))
        .mount(&s)
        .await;
    for (off, total) in [(65536u64, 131072u64), (131072, 150000)] {
        Mock::given(method("PUT"))
            .and(path(p(&format!("/uploads/{SID}/content"))))
            .and(query_param("offset", off.to_string()))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"received_bytes": total, "remaining": 150000 - total})))
            .expect(1)
            .mount(&s)
            .await;
    }
    Mock::given(method("POST"))
        .and(path(p(&format!("/uploads/{SID}/complete"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"session": session("completed", 150000), "document": {"id": DOC}})))
        .expect(1)
        .mount(&s)
        .await;
    let id = queue_one(&r, f).await;
    let before = r.desktop.list_imports(Some(CO)).unwrap();
    assert_eq!(before[0].status, "preparing", "queued, nothing sent yet");
    let row = r.desktop.run_import(&id).await.unwrap();
    assert_eq!(row.status, COMPLETED);
    assert_eq!(row.result_document_id.as_deref(), Some(DOC));
    assert_eq!(row.sha256.as_deref(), Some(sha(&data).as_str()));
    assert_eq!(row.bytes_sent, 150_000);
}

#[tokio::test]
async fn a_failed_verification_never_shows_as_stored_and_retries_by_itself() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let f = file(r.dir.path(), "a.txt", b"hello world");
    Mock::given(method("POST"))
        .and(path(p("/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(session("open", 0)))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(p(&format!("/uploads/{SID}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(session("uploading", 0)))
        .mount(&s)
        .await;
    Mock::given(method("PUT"))
        .and(path(p(&format!("/uploads/{SID}/content"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"received_bytes": 11, "remaining": 0})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/uploads/{SID}/complete"))))
        .respond_with(problem(503, "storage_error", "The file could not be stored safely; retry."))
        .mount(&s)
        .await;
    let id = queue_one(&r, f).await;
    let row = r.desktop.run_import(&id).await.unwrap();
    assert_eq!(row.status, WAITING, "not completed: the server did not confirm storage");
    assert_eq!(row.error_code.as_deref(), Some("storage_error"));
    assert!(row.next_retry_at.is_some() && row.result_document_id.is_none() && row.attempts == 1);
}

#[tokio::test]
async fn offline_keeps_the_import_and_says_the_cloud_was_not_reached() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let f = file(r.dir.path(), "a.txt", b"offline file");
    let id = queue_one(&r, f).await;
    // the cloud stops answering (the test client gives up after 400 ms)
    Mock::given(method("POST"))
        .and(path(p("/uploads")))
        .respond_with(ResponseTemplate::new(201).set_delay(std::time::Duration::from_secs(3)))
        .mount(&s)
        .await;
    let row = r.desktop.run_import(&id).await.unwrap();
    assert_eq!(row.status, WAITING, "{row:?}");
    assert_eq!(row.error_code.as_deref(), Some("timeout"));
    assert!(row.error_message.unwrap().contains("not stored yet"));
    assert!(row.result_document_id.is_none());
}

#[tokio::test]
async fn a_duplicate_waits_for_the_users_decision_and_then_goes_ahead() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let f = file(r.dir.path(), "copy.txt", b"same bytes");
    Mock::given(method("POST"))
        .and(path(p("/uploads")))
        .respond_with(problem(409, "duplicate_detected", "This exact file is already stored in this company."))
        .up_to_n_times(1)
        .mount(&s)
        .await;
    let id = queue_one(&r, f).await;
    let row = r.desktop.run_import(&id).await.unwrap();
    assert_eq!(row.status, FAILED);
    assert_eq!(row.error_code.as_deref(), Some("duplicate_detected"));
    assert!(r.desktop.retry_import(&id, Some("allow_copy"), None).unwrap());
    let again = r.desktop.list_imports(Some(CO)).unwrap().remove(0);
    assert_eq!((again.status.as_str(), again.duplicate_policy.as_str(), again.attempts), ("waiting", "allow_copy", 0));
    assert!(r.desktop.retry_import(&id, Some("overwrite"), None).is_err(), "there is no overwrite option");
}

#[tokio::test]
async fn quarantined_files_are_marked_and_cannot_be_retried_into_success() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let f = file(r.dir.path(), "x.txt", b"scanned and held");
    Mock::given(method("POST"))
        .and(path(p("/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(session("open", 0)))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path(p(&format!("/uploads/{SID}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(session("uploading", 0)))
        .mount(&s)
        .await;
    Mock::given(method("PUT"))
        .and(path(p(&format!("/uploads/{SID}/content"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"received_bytes": 16, "remaining": 0})))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/uploads/{SID}/complete"))))
        .respond_with(problem(422, "quarantined", "The PDF contains JavaScript."))
        .mount(&s)
        .await;
    let id = queue_one(&r, f).await;
    let row = r.desktop.run_import(&id).await.unwrap();
    assert_eq!(row.status, QUARANTINED);
    assert!(row.next_retry_at.is_none());
}

#[tokio::test]
async fn cancel_stops_the_upload_and_releases_the_server_session() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let f = file(r.dir.path(), "c.txt", b"cancel me");
    Mock::given(method("POST"))
        .and(path(p("/uploads")))
        .respond_with(ResponseTemplate::new(201).set_body_json(session("open", 0)))
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path(p(&format!("/uploads/{SID}/cancel"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(session("cancelled", 0)))
        .expect(1)
        .mount(&s)
        .await;
    let id = queue_one(&r, f).await;
    // give the import a server session, as if an upload had started
    r.desktop.with_db(|d| d.set_import_session("user-1", &id, Some(SID), 0)).unwrap();
    r.desktop.cancel_import("org-1", &id).await.unwrap();
    let row = r.desktop.list_imports(Some(CO)).unwrap().remove(0);
    assert_eq!(row.status, CANCELLED);
    let res = r.desktop.run_import(&id).await.unwrap();
    assert_eq!(res.status, CANCELLED, "a cancelled import is never picked up again");
}

#[tokio::test]
async fn a_restart_turns_in_flight_uploads_back_into_waiting_and_keeps_every_detail() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let user_dir = tempfile::tempdir().unwrap(); // the user's own folder, outside the app's data
    let f = file(user_dir.path(), "r.txt", b"restart me");
    let id = queue_one(&r, f).await;
    r.desktop
        .with_db(|d| {
            d.set_import_hash("user-1", &id, "ab", 10)?;
            d.claim_import("user-1", &id)?;
            d.set_import_progress("user-1", &id, 4)
        })
        .unwrap();
    assert_eq!(r.desktop.list_imports(Some(CO)).unwrap()[0].status, "uploading");
    assert_eq!(r.desktop.recover_imports().unwrap(), 1);
    let row = r.desktop.list_imports(Some(CO)).unwrap().remove(0);
    assert_eq!((row.status.as_str(), row.node_id.as_str(), row.file_name.as_str()), ("waiting", NODE, "r.txt"));
    // the local file path and names are sealed in the database, not stored in the clear
    assert!(!all_bytes(r.dir.path()).contains("restart me") && !all_bytes(&r.dir.path().join("data")).contains("r.txt"));
}

#[tokio::test]
async fn unsupported_empty_oversized_and_unknown_files_are_rejected_before_queueing() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let ok = file(r.dir.path(), "fine.pdf", b"%PDF");
    let exe = file(r.dir.path(), "tool.exe", b"MZ....");
    let empty = file(r.dir.path(), "empty.txt", b"");
    let big = file(r.dir.path(), "big.txt", &vec![b'x'; 1_000_001]);
    let staged = r.desktop.stage_paths(vec![ok, exe, big]);
    assert_eq!(staged.len(), 3);
    assert!(
        r.desktop.stage_paths(vec![r.dir.path().to_path_buf(), PathBuf::from("/definitely/not/here.pdf")]).is_empty(),
        "folders and missing files are not stageable"
    );
    let empty_staged = r.desktop.stage_paths(vec![empty]);
    let mut items: Vec<QueueItem> = staged.iter().map(|x| item(&x.token)).collect();
    items.push(item(&empty_staged[0].token));
    items.push(item("forged-token-from-the-webview"));
    let res = r.desktop.queue_imports("org-1", CO, items).await.unwrap();
    let codes: Vec<&str> = res.rejected.iter().map(|x| x.code.as_str()).collect();
    assert_eq!(res.queued.len(), 1, "{:?}", res.rejected);
    assert_eq!(codes, vec!["unsupported_type", "file_too_large", "empty_file", "unknown_file"]);
}

#[tokio::test]
async fn a_staged_file_can_be_queued_only_once() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    let f = file(r.dir.path(), "once.txt", b"once");
    let staged = r.desktop.stage_paths(vec![f]);
    let first = r.desktop.queue_imports("org-1", CO, vec![item(&staged[0].token)]).await.unwrap();
    let second = r.desktop.queue_imports("org-1", CO, vec![item(&staged[0].token)]).await.unwrap();
    assert_eq!((first.queued.len(), second.queued.len(), second.rejected.len()), (1, 0, 1));
}

#[tokio::test]
async fn process_due_runs_waiting_uploads_in_parallel_and_skips_ones_not_yet_due() {
    let s = MockServer::start().await;
    let rig = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    Mock::given(method("POST")).and(path(p("/uploads"))).respond_with(problem(409, "duplicate_detected", "dup")).mount(&s).await;
    let dir = rig.dir.path().to_path_buf();
    let desk = Arc::new(rig.desktop);
    let mut ids = Vec::new();
    for i in 0..3 {
        let staged = desk.stage_paths(vec![file(&dir, &format!("f{i}.txt"), format!("file {i}").as_bytes())]);
        ids.push(desk.queue_imports("org-1", CO, vec![item(&staged[0].token)]).await.unwrap().queued[0].id.clone());
    }
    let later = (chrono::Utc::now() + chrono::Duration::hours(1)).to_rfc3339();
    desk.with_db(|d| {
        d.set_import_hash("user-1", &ids[2], "ab", 6)?;
        d.finish_import("user-1", &ids[2], WAITING, None, Some(&later), true, None)
    })
    .unwrap();
    let n = desk.process_due(3).await.unwrap();
    assert_eq!(n, 2, "the third waits for its retry time");
    let rows = desk.list_imports(Some(CO)).unwrap();
    assert_eq!(rows.iter().filter(|x| x.status == FAILED).count(), 2);
    assert_eq!(rows.iter().filter(|x| x.status == WAITING).count(), 1);
}

// ------------------------------------------------------------------ operations, download and mirror
fn tree_ws() -> Value {
    json!({"company_id": CO, "version": 3, "workspace_state": "configured", "access_mode": "open", "categories": [], "fields": [],
           "nodes": [{"id": NODE, "parent_id": null, "node_type": "section", "key": "fin", "name": "Finance", "display_name": null, "description": "", "position": 0, "depth": 1, "is_active": true, "archived_at": null}]})
}
async fn with_storage(r: &Rig, s: &MockServer) {
    Mock::given(method("GET"))
        .and(path(p("/workspace")))
        .respond_with(ResponseTemplate::new(200).insert_header("etag", "W/\"1\"").set_body_json(tree_ws()))
        .mount(s)
        .await;
    r.desktop.companies("org-1").await.unwrap();
    r.desktop.refresh_workspace("org-1", CO).await.unwrap();
    let root = r.dir.path().join("Artify");
    std::fs::create_dir_all(&root).unwrap();
    r.desktop.set_storage_root(&root).unwrap();
}
fn doc_json(sha: &str) -> Value {
    json!({"id": DOC, "node_id": NODE, "display_name": "Supplier Invoice 1025.pdf", "extension": "pdf", "size_bytes": 11, "sha256": sha, "current_version_id": "v1", "versions": [{"version_number": 1, "sha256": sha}]})
}

#[tokio::test]
async fn download_places_the_file_by_its_cloud_folder_verifies_it_and_never_downloads_twice() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(p("/workspace")))
        .respond_with(ResponseTemplate::new(200).insert_header("etag", "W/\"1\"").set_body_json(tree_ws()))
        .mount(&s)
        .await;
    mount_limits(&s, 65536).await;
    let body = b"hello world";
    Mock::given(method("GET"))
        .and(path(p(&format!("/documents/{DOC}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(doc_json(&sha(body))))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path_regex(format!(r"/documents/{DOC}/content$")))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("x-content-sha256", sha(body).as_str())
                .insert_header("content-type", "application/pdf")
                .set_body_bytes(body.to_vec()),
        )
        .expect(1)
        .mount(&s)
        .await;
    with_storage(&r, &s).await;
    let copy = r.desktop.download_document("org-1", CO, DOC, None).await.unwrap();
    assert!(!copy.already_present && copy.rel_path.ends_with("Finance/Supplier Invoice 1025.pdf"), "{}", copy.rel_path);
    assert_eq!(std::fs::read(&copy.path).unwrap(), body);
    let again = r.desktop.download_document("org-1", CO, DOC, None).await.unwrap();
    assert!(again.already_present && again.path == copy.path, "same content: no second download, no (2) copy");
    let status = r.desktop.mirror_status(CO).unwrap();
    assert_eq!((status["files"].as_i64(), status["missing"].as_i64(), status["available"].as_bool()), (Some(1), Some(0), Some(true)));
    // the user's file disappears (or the drive is unplugged): reported, never silently recreated
    std::fs::remove_file(&copy.path).unwrap();
    assert_eq!(r.desktop.mirror_status(CO).unwrap()["missing"], 1);
    std::fs::remove_dir_all(r.dir.path().join("Artify")).unwrap();
    let st = r.desktop.mirror_status(CO).unwrap();
    assert_eq!((st["available"].as_bool(), st["missing"].as_i64()), (Some(false), Some(0)));
    assert!(r.desktop.download_document("org-1", CO, DOC, None).await.is_err_and(|e| e.code == "storage_unavailable"));
}

#[tokio::test]
async fn a_corrupted_download_is_discarded_and_never_recorded() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_limits(&s, 65536).await;
    Mock::given(method("GET"))
        .and(path(p(&format!("/documents/{DOC}"))))
        .respond_with(ResponseTemplate::new(200).set_body_json(doc_json("aa")))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path_regex(format!(r"/documents/{DOC}/content$")))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("x-content-sha256", sha(b"the real bytes").as_str())
                .set_body_bytes(b"tampered in transit".to_vec()),
        )
        .mount(&s)
        .await;
    with_storage(&r, &s).await;
    let err = r.desktop.download_document("org-1", CO, DOC, None).await.unwrap_err();
    assert_eq!(err.code, "integrity_error");
    assert_eq!(r.desktop.mirror_status(CO).unwrap()["files"], 0);
    let leftovers = std::fs::read_dir(r.dir.path().join("Artify")).map(|d| d.count()).unwrap_or(0);
    assert_eq!(leftovers, 0, "no half-written or corrupt file was left behind");
}

#[tokio::test]
async fn document_operations_use_only_the_listed_routes_and_validated_ids() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path(p("/documents")))
        .and(query_param("q", "inv oice&x=1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"items": [], "total": 0})))
        .expect(1)
        .mount(&s)
        .await;
    let q = vec![("q".to_string(), "inv oice&x=1".to_string()), ("evil".to_string(), "param".to_string())];
    r.desktop.doc_op("org-1", CO, DocOp::List(q)).await.unwrap(); // the unknown parameter is dropped, the value is encoded
    assert!(r.desktop.doc_op("org-1", CO, DocOp::Get("../../orgs".into())).await.is_err());
    assert!(r.desktop.doc_op("org-1", "not-a-uuid", DocOp::Counts).await.is_err());
}

#[test]
fn the_ui_can_only_name_known_operations_with_complete_arguments() {
    assert!(matches!(DocOp::parse("counts", &Value::Null).unwrap(), DocOp::Counts));
    assert!(matches!(DocOp::parse("get", &json!({"id": DOC})).unwrap(), DocOp::Get(id) if id == DOC));
    for bad in ["", "drop_table", "../x", "purge", "get "] {
        assert_eq!(DocOp::parse(bad, &json!({"id": DOC})).unwrap_err().code, "invalid_request", "{bad}");
    }
    // missing arguments are refused here, before any request is made
    assert_eq!(DocOp::parse("get", &json!({})).unwrap_err().code, "invalid_request");
    assert_eq!(DocOp::parse("delete", &json!({"id": DOC})).unwrap_err().code, "invalid_request");
    assert_eq!(DocOp::parse("restore_version", &json!({"id": DOC})).unwrap_err().code, "invalid_request");
    // only the listed fields of a patch are forwarded
    match DocOp::parse("patch", &json!({"id": DOC, "display_name": "x", "organization_id": "evil", "row_version": 3})).unwrap() {
        DocOp::Patch(_, body) => {
            assert_eq!(body["display_name"], "x");
            assert_eq!(body["row_version"], 3);
            assert!(body.get("organization_id").is_none());
        }
        other => panic!("{other:?}"),
    }
}
