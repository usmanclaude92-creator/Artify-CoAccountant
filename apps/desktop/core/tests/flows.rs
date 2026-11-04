mod common;
use artify_core::api::LoginOutcome;
use common::*;
use serde_json::{json, Value};
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn node(id: &str, parent: Option<&str>, name: &str, archived: bool) -> Value {
    json!({"id": id, "parent_id": parent, "node_type": if parent.is_some() {"folder"} else {"section"}, "key": id, "name": name, "display_name": null,
           "description": "", "position": 0, "depth": 1, "is_active": true, "archived_at": if archived { Some("2026-01-01T00:00:00Z") } else { None }})
}
fn ws(company: &str, version: i64, nodes: Vec<Value>) -> Value {
    json!({"company_id": company, "version": version, "workspace_state": "configured", "access_mode": "open", "nodes": nodes, "categories": [], "fields": []})
}
fn company(id: &str, name: &str, status: &str) -> Value {
    json!({"id": id, "organization_id": "org-1", "name": name, "code": id.to_uppercase(), "country": "OM", "base_currency": "OMR", "status": status})
}

async fn signed_in(s: &MockServer) -> Rig {
    mount_json(s, "POST", "/auth/login", ok_login("ACCESS", "REFRESH-TOKEN-VALUE")).await;
    mount_json(s, "GET", "/me", me("user-1")).await;
    let r = rig(s);
    assert_eq!(r.desktop.login("accountant@example.com", "pw-PLAINTEXT-9").await.unwrap(), LoginOutcome::Authenticated);
    r
}
async fn mount_ws(s: &MockServer, company: &str, version: i64, etag: &str, nodes: Vec<Value>, prio: u8) {
    Mock::given(method("GET"))
        .and(path(format!("/api/v1/companies/{company}/workspace")))
        .respond_with(ResponseTemplate::new(200).insert_header("etag", etag).set_body_json(ws(company, version, nodes)))
        .with_priority(prio)
        .mount(s)
        .await;
}

#[tokio::test]
async fn sign_in_caches_the_identity_and_tracks_the_user() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    assert_eq!(r.desktop.user_id().unwrap(), "user-1");
    let acc = r.desktop.with_db(|d| d.get_account("user-1")).unwrap().unwrap();
    assert_eq!(acc["email"], "accountant@example.com");
}

#[tokio::test]
async fn mfa_sign_in_completes_the_session() {
    let s = MockServer::start().await;
    mount_json(
        &s,
        "POST",
        "/auth/login",
        json!({"status": "mfa_required", "tokens": null, "mfa_token": "CH", "mfa_enrollment_required": false}),
    )
    .await;
    mount_json(&s, "POST", "/auth/mfa/verify", tokens("A", "R")).await;
    mount_json(&s, "GET", "/me", me("user-1")).await;
    let r = rig(&s);
    let LoginOutcome::MfaRequired { mfa_token } = r.desktop.login("a@example.com", "pw").await.unwrap() else {
        panic!("expected challenge")
    };
    assert!(r.desktop.user_id().is_err(), "no user until the second factor is verified");
    let id = r.desktop.verify_mfa(&mfa_token, "123456").await.unwrap();
    assert_eq!(id.user_id, "user-1");
}

#[tokio::test]
async fn startup_restores_a_stored_session_or_asks_for_sign_in() {
    let s = MockServer::start().await;
    mount_json(&s, "POST", "/auth/login", ok_login("A1", "R1")).await;
    mount_json(&s, "GET", "/me", me("user-1")).await;
    mount_json(&s, "POST", "/auth/refresh", tokens("A2", "R2")).await;
    let first = rig(&s);
    first.desktop.login("a@example.com", "pw").await.unwrap();
    // the app is restarted: new process, same OS vault
    let second = rig_with_store(&s, first.store.clone());
    assert!(second.desktop.user_id().is_err(), "nothing is signed in until the session is restored");
    let id = second.desktop.restore().await.unwrap().expect("session restored without typing a password");
    assert_eq!(id.user_id, "user-1");
    assert_eq!(second.store.snapshot().values().filter(|v| *v == "R2").count(), 1, "the rotated refresh token replaced the old one");
    // a machine with an empty vault must sign in
    assert!(rig(&s).desktop.restore().await.unwrap().is_none());
}

#[tokio::test]
async fn organizations_single_and_multiple_and_offline_fallback() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let one = json!([{"id": "org-1", "display_name": "Solo Accountant", "type": "independent_accountant"}]);
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs"))
        .respond_with(ResponseTemplate::new(200).set_body_json(one))
        .up_to_n_times(1)
        .with_priority(1)
        .mount(&s)
        .await;
    let l = r.desktop.organizations().await.unwrap();
    assert_eq!((l.items.len(), l.stale), (1, false));
    let two = json!([{"id": "org-1", "display_name": "Firm One"}, {"id": "org-2", "display_name": "Firm Two"}]);
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs"))
        .respond_with(ResponseTemplate::new(200).set_body_json(two))
        .up_to_n_times(1)
        .with_priority(2)
        .mount(&s)
        .await;
    let l = r.desktop.organizations().await.unwrap();
    assert_eq!((l.items.len(), l.stale), (2, false));
    // server becomes unavailable: the saved copy is shown and marked stale
    Mock::given(method("GET")).and(path("/api/v1/orgs")).respond_with(ResponseTemplate::new(503)).with_priority(3).mount(&s).await;
    let l = r.desktop.organizations().await.unwrap();
    assert_eq!((l.items.len(), l.stale), (2, true));
    assert_eq!(l.items[0]["display_name"], "Firm One");
}

#[tokio::test]
async fn multiple_companies_are_listed_cached_and_revoked_ones_disappear() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let all = json!([
        company("c1", "Alpha Trading", "active"),
        company("c2", "Beta Contracting", "active"),
        company("c3", "Gamma Archived", "archived")
    ]);
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs/org-1/companies"))
        .respond_with(ResponseTemplate::new(200).set_body_json(all))
        .up_to_n_times(1)
        .with_priority(1)
        .mount(&s)
        .await;
    let l = r.desktop.companies("org-1").await.unwrap();
    assert_eq!(l.items.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(), ["Alpha Trading", "Beta Contracting", "Gamma Archived"]);
    assert_eq!(l.items[2].status, "archived");
    r.desktop.set_favorite("c2", true).unwrap();
    assert!(r.desktop.set_favorite("not-mine", true).is_err());
    // membership of c2 and c3 revoked
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs/org-1/companies"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([company("c1", "Alpha Trading", "active")])))
        .up_to_n_times(1)
        .with_priority(2)
        .mount(&s)
        .await;
    let l = r.desktop.companies("org-1").await.unwrap();
    assert_eq!(l.items.len(), 1, "revoked companies leave the list at the next refresh");
    // offline: only what the server last allowed
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs/org-1/companies"))
        .respond_with(ResponseTemplate::new(503))
        .with_priority(3)
        .mount(&s)
        .await;
    let l = r.desktop.companies("org-1").await.unwrap();
    assert_eq!((l.items.len(), l.stale), (1, true));
}

#[tokio::test]
async fn every_company_selection_is_verified_by_the_backend() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs/org-1/companies"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([company("c1", "Alpha", "active"), company("c2", "Beta", "active")])))
        .mount(&s)
        .await;
    r.desktop.companies("org-1").await.unwrap();
    mount_json(&s, "GET", "/companies/c1", company("c1", "Alpha", "active")).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/c2"))
        .respond_with(problem(404, "not_found", "Company not found."))
        .mount(&s)
        .await;
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/c-guessed"))
        .respond_with(problem(404, "not_found", "Company not found."))
        .mount(&s)
        .await;
    assert_eq!(r.desktop.select_company("org-1", "c1").await.unwrap()["id"], "c1");
    // switching works and remembers the last company
    assert_eq!(r.desktop.with_db(|d| d.get_setting("user-1", "last_company_id")).unwrap().as_deref(), Some("c1"));
    // c2 is in the local cache, but the backend says no (membership revoked meanwhile): refused and purged
    let e = r.desktop.select_company("org-1", "c2").await.unwrap_err();
    assert_eq!(e.code, "company_unavailable");
    assert!(r.desktop.with_db(|d| d.list_companies("user-1", "org-1")).unwrap().iter().all(|c| c.id != "c2"));
    // a manually typed id is just as unauthorized
    assert_eq!(r.desktop.select_company("org-1", "c-guessed").await.unwrap_err().code, "company_unavailable");
    assert_eq!(
        r.desktop.with_db(|d| d.get_setting("user-1", "last_company_id")).unwrap().as_deref(),
        Some("c1"),
        "a refused selection changes nothing"
    );
}

#[tokio::test]
async fn a_selection_answered_with_another_companys_data_is_refused() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_json(&s, "GET", "/companies/c1", company("c-other", "Not what I asked for", "active")).await;
    assert_eq!(r.desktop.select_company("org-1", "c1").await.unwrap_err().code, "malformed_response");
}

#[tokio::test]
async fn four_and_ten_section_companies_show_exactly_their_own_structure() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    let four: Vec<Value> = ["Bank Statements", "Receivables", "Payables", "Expenses"]
        .iter()
        .enumerate()
        .map(|(i, n)| node(&format!("a{i}"), None, n, false))
        .collect();
    let mut ten: Vec<Value> = (0..10).map(|i| node(&format!("b{i}"), None, &format!("Dept {i}"), false)).collect();
    ten.push(node("b-bank", Some("b0"), "Banking", false));
    ten.push(node("b-rec", Some("b-bank"), "Reconciliation", false));
    mount_ws(&s, "cA", 5, "W/\"5\"", four, 1).await;
    mount_ws(&s, "cB", 13, "W/\"13\"", ten, 1).await;
    let a = r.desktop.workspace("org-1", "cA").await.unwrap();
    let b = r.desktop.workspace("org-1", "cB").await.unwrap();
    assert_eq!((a.version, a.data["nodes"].as_array().unwrap().len(), a.stale), (5, 4, false));
    assert_eq!((b.version, b.data["nodes"].as_array().unwrap().len()), (13, 12));
    let names = |v: &Value| v["nodes"].as_array().unwrap().iter().map(|n| n["name"].as_str().unwrap().to_string()).collect::<Vec<_>>();
    assert!(names(&a.data).contains(&"Receivables".to_string()) && !names(&b.data).contains(&"Receivables".to_string()));
    assert!(names(&b.data).contains(&"Reconciliation".to_string()) && !names(&a.data).contains(&"Reconciliation".to_string()));
}

#[tokio::test]
async fn a_new_workspace_version_is_detected_cached_and_described_by_stable_ids() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 2, "W/\"2\"", vec![node("s1", None, "Finance", false), node("s2", None, "Sales", false)], 3).await;
    let first = r.desktop.refresh_workspace("org-1", "cA").await.unwrap();
    assert!(first.changed && first.version == 2 && first.summary.is_none(), "the first load has nothing to compare against");
    // unchanged: ETag revalidation
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/cA/workspace"))
        .and(header("if-none-match", "W/\"2\""))
        .respond_with(ResponseTemplate::new(304))
        .with_priority(1)
        .up_to_n_times(1)
        .mount(&s)
        .await;
    let same = r.desktop.refresh_workspace("org-1", "cA").await.unwrap();
    assert!(!same.changed && same.version == 2);
    // the Control Center renamed Finance, archived Sales and added Payroll
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/cA/workspace"))
        .respond_with(ResponseTemplate::new(200).insert_header("etag", "W/\"5\"").set_body_json(ws(
            "cA",
            5,
            vec![node("s1", None, "Money", false), node("s2", None, "Sales", true), node("s3", None, "Payroll", false)],
        )))
        .with_priority(1)
        .mount(&s)
        .await;
    let upd = r.desktop.refresh_workspace("org-1", "cA").await.unwrap();
    assert!(upd.changed && upd.version == 5);
    assert_eq!(upd.summary.as_deref(), Some("Workspace updated: 1 added, 1 renamed, 1 archived."));
    let view = r.desktop.workspace("org-1", "cA").await.unwrap();
    assert_eq!(view.version, 5);
    let nodes = view.data["nodes"].as_array().unwrap();
    assert_eq!(nodes.iter().find(|n| n["id"] == "s1").unwrap()["name"], "Money", "same id, new name");
    assert!(nodes.iter().find(|n| n["id"] == "s2").unwrap()["archived_at"].is_string(), "archived items stay visible as archived");
    let ev = r.desktop.with_db(|d| d.list_events("user-1", 10)).unwrap();
    assert!(ev.iter().any(|e| e.kind == "workspace_updated" && e.message.contains("1 renamed")));
}

#[tokio::test]
async fn an_older_response_never_overwrites_a_newer_cache() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 9, "W/\"9\"", vec![node("s1", None, "Current", false)], 2).await;
    r.desktop.refresh_workspace("org-1", "cA").await.unwrap();
    mount_ws(&s, "cA", 4, "W/\"4\"", vec![node("s1", None, "Stale", false)], 1).await;
    let e = r.desktop.refresh_workspace("org-1", "cA").await.unwrap_err();
    assert_eq!(e.code, "stale_response");
    let cached = r.desktop.with_db(|d| d.get_workspace("user-1", "cA")).unwrap().unwrap();
    assert_eq!(cached.version, 9);
    assert_eq!(cached.data["nodes"][0]["name"], "Current");
}

#[tokio::test]
async fn an_invalid_workspace_is_rejected_and_the_previous_one_stays() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 2, "W/\"2\"", vec![node("s1", None, "Good", false)], 2).await;
    r.desktop.refresh_workspace("org-1", "cA").await.unwrap();
    mount_ws(&s, "cA", 3, "W/\"3\"", vec![node("x", Some("y"), "A", false), node("y", Some("x"), "B", false)], 1).await;
    assert_eq!(r.desktop.refresh_workspace("org-1", "cA").await.unwrap_err().code, "invalid_workspace");
    let view = r.desktop.workspace("org-1", "cA").await.unwrap();
    assert_eq!(view.version, 2, "the good cache is still what is shown");
    assert!(view.stale, "but it is marked as not confirmed current, because the refresh failed");
    assert_eq!(view.last_error.as_deref(), Some("invalid_workspace"));
    assert!(r.desktop.with_db(|d| d.list_events("user-1", 10)).unwrap().iter().any(|e| e.kind == "sync_rejected"));
}

#[tokio::test]
async fn another_companys_workspace_in_the_answer_is_discarded() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 2, "W/\"2\"", vec![node("s1", None, "Mine", false)], 2).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/cB/workspace"))
        .respond_with(ResponseTemplate::new(200).set_body_json(ws("cA", 2, vec![node("s1", None, "Mine", false)])))
        .mount(&s)
        .await;
    r.desktop.refresh_workspace("org-1", "cA").await.unwrap();
    assert_eq!(r.desktop.refresh_workspace("org-1", "cB").await.unwrap_err().code, "workspace_mismatch");
    assert!(r.desktop.with_db(|d| d.get_workspace("user-1", "cB")).unwrap().is_none(), "nothing cached under the wrong company");
}

#[tokio::test]
async fn permission_restricted_workspaces_show_only_what_the_server_delivers() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    // the server hides Payroll from this member and re-roots the one visible subfolder
    mount_ws(
        &s,
        "cA",
        6,
        "W/\"6\"",
        vec![
            node("sales", None, "Sales", false),
            node("inv", Some("sales"), "Invoices", false),
            node("loans", None, "Loans (visible child of a hidden parent)", false),
        ],
        1,
    )
    .await;
    let v = r.desktop.workspace("org-1", "cA").await.unwrap();
    let names: Vec<&str> = v.data["nodes"].as_array().unwrap().iter().map(|n| n["name"].as_str().unwrap()).collect();
    assert_eq!(names.len(), 3);
    assert!(!names.iter().any(|n| n.contains("Payroll")));
}

#[tokio::test]
async fn offline_workspace_comes_from_the_encrypted_cache_and_failures_are_recorded() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 3, "W/\"3\"", vec![node("s1", None, "Finance", false)], 2).await;
    r.desktop.workspace("org-1", "cA").await.unwrap();
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/cA/workspace"))
        .respond_with(ResponseTemplate::new(503))
        .with_priority(1)
        .mount(&s)
        .await;
    let off = r.desktop.workspace("org-1", "cA").await.unwrap();
    assert!(off.stale && off.version == 3 && off.data["nodes"][0]["name"] == "Finance");
    assert_eq!(off.last_error.as_deref(), Some("server_error"));
    assert!(off.last_success_at.is_some(), "the last SUCCESS time is kept");
    // never loaded and offline: an honest error, not an empty/fake workspace
    let e = r.desktop.workspace("org-1", "cNever").await.unwrap_err();
    assert!(e.retryable || e.status.is_some());
}

#[tokio::test]
async fn revoked_access_to_a_company_is_not_papered_over_by_the_cache() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 3, "W/\"3\"", vec![node("s1", None, "Finance", false)], 2).await;
    r.desktop.workspace("org-1", "cA").await.unwrap();
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/cA/workspace"))
        .respond_with(problem(404, "not_found", "Company not found."))
        .with_priority(1)
        .mount(&s)
        .await;
    assert_eq!(r.desktop.workspace("org-1", "cA").await.unwrap_err().status, Some(404), "a 404 is NOT answered from the cache");
}

#[tokio::test]
async fn caches_do_not_leak_between_users_of_the_same_install() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 3, "W/\"3\"", vec![node("s1", None, "Finance", false)], 2).await;
    r.desktop.workspace("org-1", "cA").await.unwrap();
    // a different person signs in on the same install: their cache view is empty and cannot open user-1's rows
    r.desktop.logout().await;
    assert!(r.desktop.with_db(|d| d.get_workspace("user-2", "cA")).unwrap().is_none());
    assert!(r.desktop.with_db(|d| d.get_workspace("user-1", "cA")).unwrap().is_none(), "and user-1's was wiped on logout");
}

#[tokio::test]
async fn a_session_that_ends_during_sync_wipes_the_cache_and_signs_out() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_ws(&s, "cA", 3, "W/\"3\"", vec![node("s1", None, "Finance", false)], 2).await;
    r.desktop.workspace("org-1", "cA").await.unwrap();
    r.desktop.with_db(|d| d.enqueue_op("user-1", Some("cA"), "upload", &json!({"x": 1}))).unwrap();
    Mock::given(method("GET"))
        .and(path("/api/v1/companies/cA/workspace"))
        .respond_with(problem(401, "unauthenticated", "Session is no longer valid."))
        .with_priority(1)
        .mount(&s)
        .await;
    Mock::given(method("POST"))
        .and(path("/api/v1/auth/refresh"))
        .respond_with(problem(401, "unauthenticated", "Refresh token expired or revoked."))
        .mount(&s)
        .await;
    let e = r.desktop.workspace("org-1", "cA").await.unwrap_err();
    assert!(e.is_session_expired());
    assert!(r.desktop.user_id().is_err(), "the app is signed out");
    assert!(r.store.snapshot().iter().all(|(k, _)| !k.starts_with("refresh-token")), "the session token is gone from the vault");
    r.desktop
        .with_db(|d| {
            assert!(d.get_workspace("user-1", "cA").unwrap().is_none());
            assert_eq!(d.pending_summary("user-1").unwrap().pending, 1, "unsent work was kept");
            Ok(())
        })
        .unwrap();
}

#[tokio::test]
async fn the_storage_root_is_validated_and_company_folders_are_stable_and_separate() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs/org-1/companies"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([
            company("c1", "Acme", "active"),
            company("c2", "ACME", "active"),
            company("c3", "Bad/Name:*", "active")
        ])))
        .up_to_n_times(1)
        .with_priority(1)
        .mount(&s)
        .await;
    r.desktop.companies("org-1").await.unwrap();
    assert_eq!(r.desktop.ensure_company_dir("org-1", "c1").unwrap_err().code, "storage_not_configured");
    let root = r.dir.path().join("Artify Data");
    std::fs::create_dir(&root).unwrap();
    assert_eq!(r.desktop.set_storage_root(&root).unwrap().status, "ok");
    assert_eq!(r.desktop.storage_status().unwrap().unwrap().status, "ok");
    let (d1, d2, d3) = (
        r.desktop.ensure_company_dir("org-1", "c1").unwrap(),
        r.desktop.ensure_company_dir("org-1", "c2").unwrap(),
        r.desktop.ensure_company_dir("org-1", "c3").unwrap(),
    );
    assert!(d1 != d2 && d2 != d3 && d1.starts_with(&root) && d2.starts_with(&root) && d3.starts_with(&root));
    assert_eq!(d1.file_name().unwrap(), "Acme");
    assert!(d2.file_name().unwrap().to_string_lossy().starts_with("ACME ("), "case-insensitive clash gets a distinguishing suffix");
    // the name is kept even if the company is renamed in the Control Center
    Mock::given(method("GET"))
        .and(path("/api/v1/orgs/org-1/companies"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([
            company("c1", "Acme Renamed", "active"),
            company("c2", "ACME", "active"),
            company("c3", "Bad/Name:*", "active")
        ])))
        .with_priority(1)
        .mount(&s)
        .await;
    r.desktop.companies("org-1").await.unwrap();
    assert_eq!(r.desktop.ensure_company_dir("org-1", "c1").unwrap(), d1);
    assert_eq!(r.desktop.ensure_company_dir("org-1", "nope").unwrap_err().code, "not_found");
    // changing location: overlap refused, existing data untouched
    std::fs::write(d1.join("keep.txt"), "x").unwrap();
    assert_eq!(r.desktop.set_storage_root(&d1).unwrap_err().code, "overlapping_root");
    let other = r.dir.path().join("Other");
    std::fs::create_dir(&other).unwrap();
    r.desktop.set_storage_root(&other).unwrap();
    assert!(d1.join("keep.txt").exists(), "changing the root never moves or deletes files");
    // the drive disappears
    std::fs::remove_dir_all(&other).unwrap();
    assert_eq!(r.desktop.storage_status().unwrap().unwrap().status, "missing");
    assert_eq!(r.desktop.ensure_company_dir("org-1", "c1").unwrap_err().code, "storage_unavailable");
}

#[tokio::test]
async fn device_registration_heartbeat_revocation_and_limits() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST")).and(path("/api/v1/devices")).and(header("x-organization-id", "org-1")).respond_with(ResponseTemplate::new(201).set_body_json(json!({"id": "dev-1", "platform": "windows", "name": "PC-01", "app_version": "0.1.0-test", "status": "active", "registered_at": "2026-11-04T10:00:00Z", "last_seen_at": "2026-11-04T10:00:00Z"}))).up_to_n_times(1).mount(&s).await;
    let d = r.desktop.ensure_device("org-1", "PC-01").await.unwrap();
    assert_eq!((d.device_id.as_str(), d.status.as_str()), ("dev-1", "active"));
    let body =
        String::from_utf8(s.received_requests().await.unwrap().into_iter().find(|q| q.url.path().ends_with("/devices")).unwrap().body)
            .unwrap();
    assert!(body.contains("\"windows\"") && !body.contains("password"));
    // later sessions bind to the same device and record activity
    mount_json(&s, "POST", "/devices/dev-1/session", json!({"id": "dev-1", "platform": "windows", "name": "PC-01", "app_version": "x", "status": "active", "registered_at": "2026-11-04T10:00:00Z", "last_seen_at": "2026-11-04T12:00:00Z"})).await;
    let d2 = r.desktop.ensure_device("org-1", "PC-01").await.unwrap();
    assert_eq!(d2.last_seen_at.as_deref(), Some("2026-11-04T12:00:00Z"));
    // revoked by an administrator: the server no longer knows it as active
    Mock::given(method("POST"))
        .and(path("/api/v1/devices/dev-1/session"))
        .respond_with(problem(404, "not_found", "Device not found."))
        .with_priority(1)
        .mount(&s)
        .await;
    let e = r.desktop.ensure_device("org-1", "PC-01").await.unwrap_err();
    assert_eq!(e.code, "device_revoked");
    assert_eq!(r.desktop.local_device("org-1").unwrap().unwrap().status, "revoked");
}

#[tokio::test]
async fn the_subscription_device_limit_is_reported_not_bypassed() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    Mock::given(method("POST")).and(path("/api/v1/devices")).respond_with(ResponseTemplate::new(402).set_body_json(json!({"code": "entitlement_exceeded", "detail": "Limit limit.devices reached (1 of 1 in use).", "key": "limit.devices", "limit": 1, "current": 1, "requested": 1}))).mount(&s).await;
    let e = r.desktop.ensure_device("org-1", "PC-02").await.unwrap_err();
    assert_eq!((e.status, e.code.as_str()), (Some(402), "entitlement_exceeded"));
    assert!(r.desktop.local_device("org-1").unwrap().is_none(), "no local device record without a server registration");
}

#[tokio::test]
async fn logout_clears_session_and_cache_and_no_credential_is_ever_written_to_disk() {
    let s = MockServer::start().await;
    let r = signed_in(&s).await;
    mount_json(&s, "POST", "/auth/logout", json!(null)).await;
    mount_ws(&s, "cA", 3, "W/\"3\"", vec![node("s1", None, "Finance", false)], 2).await;
    r.desktop.workspace("org-1", "cA").await.unwrap();
    // while signed in: the refresh token is in the vault, and in NO file
    assert!(r.store.snapshot().values().any(|v| v == "REFRESH-TOKEN-VALUE"));
    let disk = all_bytes(r.dir.path());
    for secret in ["REFRESH-TOKEN-VALUE", "pw-PLAINTEXT-9", "ACCESS"] {
        assert!(!disk.contains(secret), "{secret} found on disk (database or logs)");
    }
    assert!(!disk.contains("Finance"), "cached workspace content is encrypted");
    r.desktop.logout().await;
    assert!(r.store.snapshot().keys().all(|k| !k.starts_with("refresh-token")));
    assert!(r.desktop.user_id().is_err());
    assert!(r.desktop.with_db(|d| d.get_account("user-1")).unwrap().is_none());
    assert!(r.desktop.workspace("org-1", "cA").await.is_err(), "no data access after logout");
    for secret in ["REFRESH-TOKEN-VALUE", "pw-PLAINTEXT-9"] {
        assert!(!all_bytes(r.dir.path()).contains(secret));
    }
}
