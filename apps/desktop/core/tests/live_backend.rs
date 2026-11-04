//! Live test: the real Rust core against a REAL running Artify API (see infrastructure/dev/desktop_live.sh).
//! Skipped by default (`#[ignore]`); run with `ARTIFY_LIVE_FIXTURE=<json> cargo test --test live_backend -- --ignored`.
use std::sync::Arc;

use artify_core::api::{ApiClient, ApiConfig, LoginOutcome};
use artify_core::db::Db;
use artify_core::desktop::Desktop;
use artify_core::logs::Logger;
use artify_core::seal::Sealer;
use artify_core::vault::MemoryStore;
use hmac::{Hmac, Mac};
use serde_json::{json, Value};
use sha1::Sha1;

struct Fx(Value);
impl Fx {
    fn load() -> Fx {
        let p = std::env::var("ARTIFY_LIVE_FIXTURE").expect("ARTIFY_LIVE_FIXTURE");
        Fx(serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap())
    }
    fn s(&self, path: &[&str]) -> String {
        let mut v = &self.0;
        for p in path {
            v = &v[*p];
        }
        v.as_str().unwrap().to_string()
    }
}

fn totp(secret: &str, step_offset: i64) -> String {
    let key = data_encoding::BASE32_NOPAD.decode(secret.trim_end_matches('=').to_ascii_uppercase().as_bytes()).unwrap();
    let counter = (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs() / 30) as i64 + step_offset;
    let mut mac = Hmac::<Sha1>::new_from_slice(&key).unwrap();
    mac.update(&(counter as u64).to_be_bytes());
    let h = mac.finalize().into_bytes();
    let o = (h[19] & 0xf) as usize;
    let n = (u32::from_be_bytes([h[o], h[o + 1], h[o + 2], h[o + 3]]) & 0x7fff_ffff) % 1_000_000;
    format!("{n:06}")
}

struct Live {
    desk: Desktop,
    store: Arc<MemoryStore>,
    _dir: tempfile::TempDir,
}
fn rig(fx: &Fx, store: Arc<MemoryStore>) -> Live {
    let dir = tempfile::tempdir().unwrap();
    let api = Arc::new(ApiClient::new(ApiConfig::new(&fx.s(&["api_base"]), "0.1.0-live"), store.clone()).unwrap());
    let (db, rep) = Db::open(&dir.path().join("app.db"), Sealer::from_vault(store.as_ref()).unwrap()).unwrap();
    let desk = Desktop::new(api, db, rep, Logger::new(&dir.path().join("logs")).unwrap(), dir.path(), "0.1.0-live");
    Live { desk, store, _dir: dir }
}
async fn sign_in(l: &Live, who: &Fx, key: &str) {
    match l.desk.login(&who.s(&[key, "email"]), &who.s(&[key, "password"])).await.unwrap() {
        LoginOutcome::Authenticated => {}
        LoginOutcome::MfaRequired { mfa_token } => {
            let secret = who.s(&[key, "totp_secret"]);
            // the seeding step used the current time step; use the next one (the server accepts +-1 step)
            l.desk.verify_mfa(&mfa_token, &totp(&secret, 1)).await.unwrap();
        }
        LoginOutcome::MfaEnrollmentRequired => panic!("enrolment unexpected"),
    }
}
fn names(v: &Value) -> Vec<String> {
    let mut n: Vec<(i64, String)> = v["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|x| x["parent_id"].is_null())
        .map(|x| (x["position"].as_i64().unwrap(), x["name"].as_str().unwrap().to_string()))
        .collect();
    n.sort();
    n.into_iter().map(|x| x.1).collect()
}

#[tokio::test]
#[ignore = "needs a running API (infrastructure/dev/desktop_live.sh)"]
async fn owner_sees_each_companys_own_workspace_and_a_remote_change_is_detected_by_id() {
    let fx = Fx::load();
    let org = fx.s(&["org"]);
    let l = rig(&fx, Arc::new(MemoryStore::new()));
    sign_in(&l, &fx, "owner").await;
    let orgs = l.desk.organizations().await.unwrap();
    assert_eq!(orgs.items.len(), 1);
    let cos = l.desk.companies(&org).await.unwrap();
    assert_eq!(cos.items.len(), 3);
    for c in &cos.items {
        assert_eq!(l.desk.select_company(&org, &c.id).await.unwrap()["id"], c.id.as_str());
    }
    let (a, b, g) = (fx.s(&["companies", "alpha"]), fx.s(&["companies", "beta"]), fx.s(&["companies", "gamma"]));
    let wa = l.desk.workspace(&org, &a).await.unwrap();
    let wb = l.desk.workspace(&org, &b).await.unwrap();
    let wg = l.desk.workspace(&org, &g).await.unwrap();
    assert_eq!(names(&wa.data), ["Bank Statements", "Receivables", "Payables", "Expenses"]);
    assert_eq!(wb.data["nodes"].as_array().unwrap().iter().filter(|n| n["parent_id"].is_null()).count(), 10);
    assert!(
        wb.data["nodes"].as_array().unwrap().iter().any(|n| n["name"] == "Bank of Muscat" && n["depth"] == 3),
        "nested folders arrive with their hierarchy"
    );
    assert!(
        wb.data["nodes"].as_array().unwrap().iter().all(|n| n["name"] != "Old Folder" || n["archived_at"].is_string()),
        "archived nodes are delivered as archived"
    );
    assert!(wg.data["nodes"].as_array().unwrap().is_empty(), "an unconfigured company has no invented structure");
    assert!(!wa.stale && wa.version >= 5);

    // the Control Center renames a section of Beta (through the real API, as the Builder does)
    let section = fx.0["beta_section_ids"][0].as_str().unwrap().to_string();
    let before = l.desk.refresh_workspace(&org, &b).await.unwrap();
    assert!(!before.changed, "ETag revalidation: nothing changed yet");
    l.desk.api.patch(&format!("/companies/{b}/workspace/nodes/{section}"), Some(&org), json!({"name": "Renamed Section"})).await.unwrap();
    let upd = l.desk.refresh_workspace(&org, &b).await.unwrap();
    assert!(upd.changed && upd.version > wb.version);
    assert_eq!(upd.summary.as_deref(), Some("Workspace updated: 1 renamed."));
    let view = l.desk.workspace(&org, &b).await.unwrap();
    let renamed = view.data["nodes"].as_array().unwrap().iter().find(|n| n["id"] == section.as_str()).unwrap();
    assert_eq!(renamed["name"], "Renamed Section", "same stable id, new name");
    // Alpha was not touched by a change to Beta
    assert_eq!(l.desk.workspace(&org, &a).await.unwrap().version, wa.version);
}

#[tokio::test]
#[ignore = "needs a running API"]
async fn an_assigned_accountant_cannot_reach_any_other_company_even_by_typing_its_id() {
    let fx = Fx::load();
    let org = fx.s(&["org"]);
    let l = rig(&fx, Arc::new(MemoryStore::new()));
    sign_in(&l, &fx, "accountant").await;
    let cos = l.desk.companies(&org).await.unwrap();
    let (a, b, g) = (fx.s(&["companies", "alpha"]), fx.s(&["companies", "beta"]), fx.s(&["companies", "gamma"]));
    assert_eq!(cos.items.iter().map(|c| c.id.clone()).collect::<Vec<_>>(), vec![a.clone()], "the server lists only the assigned company");
    assert!(l.desk.select_company(&org, &a).await.is_ok());
    for forbidden in [b.as_str(), g.as_str(), "00000000-0000-0000-0000-000000000000", "not-a-uuid"] {
        let e = l.desk.select_company(&org, forbidden).await.unwrap_err();
        assert_eq!(e.code, "company_unavailable", "{forbidden}");
        assert!(l.desk.workspace(&org, forbidden).await.is_err(), "no workspace for {forbidden}");
        assert!(l.desk.ensure_company_dir(&org, forbidden).is_err());
    }
    let wa = l.desk.workspace(&org, &a).await.unwrap();
    assert_eq!(names(&wa.data), ["Bank Statements", "Receivables", "Payables", "Expenses"]);
    // the organization claim cannot be forged either
    assert!(l.desk.companies("00000000-0000-0000-0000-000000000000").await.is_err());
    // nothing of Beta ever reached the local cache
    assert!(l.desk.with_db(|d| d.get_workspace(&l.desk.user_id()?, &b)).unwrap().is_none());
}

#[tokio::test]
#[ignore = "needs a running API"]
async fn wrong_credentials_never_produce_a_session() {
    let fx = Fx::load();
    let l = rig(&fx, Arc::new(MemoryStore::new()));
    let e = l.desk.login(&fx.s(&["accountant", "email"]), "definitely-wrong-password").await.unwrap_err();
    assert_eq!(e.status, Some(401));
    assert!(l.store.snapshot().keys().all(|k| !k.starts_with("refresh-token")));
    assert!(l.desk.user_id().is_err());
}

#[tokio::test]
#[ignore = "needs a running API"]
async fn session_restore_and_logout_work_against_the_real_server() {
    let fx = Fx::load();
    let first = rig(&fx, Arc::new(MemoryStore::new()));
    sign_in(&first, &fx, "accountant").await;
    // "restart the app": new process, same OS vault
    let second = rig(&fx, first.store.clone());
    let id = second.desk.restore().await.unwrap().expect("restored without a password");
    assert_eq!(id.email, fx.s(&["accountant", "email"]));
    second.desk.logout().await;
    assert!(second.store.snapshot().keys().all(|k| !k.starts_with("refresh-token")));
    assert!(second.desk.user_id().is_err());
    // the old refresh token was revoked server-side by the sign-out: it cannot be used again
    let third = rig(&fx, first.store.clone());
    assert!(third.desk.restore().await.unwrap().is_none(), "a signed-out session cannot be resumed");
}

#[tokio::test]
#[ignore = "needs a running API"]
async fn revoking_the_device_ends_that_installations_session_and_wipes_its_cache() {
    let fx = Fx::load();
    let org = fx.s(&["org"]);
    let a = fx.s(&["companies", "alpha"]);
    // installation 1 registers (its session becomes device-bound) and caches the workspace
    let pc1 = rig(&fx, Arc::new(MemoryStore::new()));
    sign_in(&pc1, &fx, "accountant").await;
    let dev = pc1.desk.ensure_device(&org, "LIVE-PC-1").await.unwrap();
    assert_eq!(dev.status, "active");
    pc1.desk.companies(&org).await.unwrap();
    pc1.desk.workspace(&org, &a).await.unwrap();
    assert!(pc1.desk.ensure_device(&org, "LIVE-PC-1").await.is_ok(), "heartbeat on the bound device");
    // the same user, from another computer, revokes installation 1 (an administrator could do the same)
    let pc2 = rig(&fx, Arc::new(MemoryStore::new()));
    sign_in(&pc2, &fx, "accountant").await;
    let listed = pc2.desk.api.get("/devices", Some(&org)).await.unwrap().body;
    assert!(listed
        .as_array()
        .unwrap()
        .iter()
        .any(|d| d["id"] == dev.device_id.as_str() && d["status"] == "active" && d["last_seen_at"].is_string()));
    pc2.desk.api.delete(&format!("/devices/{}", dev.device_id), Some(&org)).await.unwrap();
    // installation 1: its access token is dead and its refresh token cannot be rotated
    let e = pc1.desk.workspace(&org, &a).await.unwrap_err();
    assert!(e.is_session_expired(), "got {e:?}");
    assert!(pc1.desk.user_id().is_err(), "signed out");
    assert!(pc1.store.snapshot().keys().all(|k| !k.starts_with("refresh-token")), "no token left in the vault");
    // and the cache of the revoked installation was wiped, so nothing stays readable offline
    assert!(
        pc1.desk.with_db(|d| Ok(d.conn().query_row("SELECT COUNT(*) FROM workspace_cache", [], |r| r.get::<_, i64>(0))?)).unwrap() == 0,
        "cache wiped"
    );
    // the revoked device id cannot be re-bound to any new session
    let rebind = pc2.desk.api.post(&format!("/devices/{}/session", dev.device_id), Some(&org), None).await.unwrap_err();
    assert_eq!(rebind.status, Some(404));
    // a restore attempt with the old vault content fails (the refresh token was revoked with the device)
    let pc1_restart = rig(&fx, pc1.store.clone());
    assert!(pc1_restart.desk.restore().await.unwrap().is_none());
}

#[tokio::test]
#[ignore = "needs a running API"]
async fn documents_are_imported_stored_verified_listed_and_saved_locally_against_the_real_server() {
    use artify_core::queue::QueueAdd;
    let fx = Fx::load();
    let org = fx.s(&["org"]);
    let (a, b) = (fx.s(&["companies", "alpha"]), fx.s(&["companies", "beta"]));
    let l = rig(&fx, Arc::new(MemoryStore::new()));
    sign_in(&l, &fx, "accountant").await; // (an assigned accountant: documents.upload/read/manage, not delete)
    l.desk.companies(&org).await.unwrap();
    let ws = l.desk.workspace(&org, &a).await.unwrap();
    let node =
        ws.data["nodes"].as_array().unwrap().iter().find(|n| n["name"] == "Receivables").unwrap()["id"].as_str().unwrap().to_string();
    let root = l._dir.path().join("Artify Data");
    std::fs::create_dir_all(&root).unwrap();
    l.desk.set_storage_root(&root).unwrap();

    let content = b"%PDF-1.4\n% synthetic live-test document\ntrailer\n%%EOF\n".to_vec();
    let file = l._dir.path().join("Invoice 1.pdf");
    std::fs::write(&file, &content).unwrap();
    let staged = l.desk.stage_paths(vec![file.clone()]).await.unwrap();
    let add = |staged_id: &str, policy: &str| QueueAdd {
        staged_id: staged_id.into(),
        node_id: Some(node.clone()),
        category_id: None,
        display_name: None,
        metadata: json!({}),
        filing_rule_id: None,
        duplicate_policy: policy.into(),
        target_document_id: None,
        change_reason: None,
        batch_local: Some("live-1".into()),
    };
    let q = l.desk.queue_add(&org, &a, vec![add(&staged[0].id, "block")]).unwrap();
    let run = l.desk.queue_run().await.unwrap();
    assert_eq!((run.completed, run.failed), (1, 0), "{run:?}");
    let item = l.desk.queue_list(Some(&a)).unwrap().into_iter().find(|i| i.id == q[0].id).unwrap();
    assert_eq!(item.status, "completed");
    let doc = item.result_document_id.clone().expect("document id");

    // the server really holds it: listed, versioned, byte-identical when downloaded
    let list = l.desk.docs_list(&org, &a, &json!({"node_id": node, "limit": 10})).await.unwrap();
    assert_eq!(list["total"], 1);
    let detail = l.desk.docs_get(&org, &a, &doc).await.unwrap();
    assert_eq!((detail["display_name"].as_str(), detail["current_version_no"].as_i64()), (Some("Invoice 1.pdf"), Some(1)));
    let saved = l.desk.docs_save_copy(&org, &a, &doc, None).await.unwrap();
    assert!(saved.created && saved.path.contains("Receivables"), "{}", saved.path);
    assert_eq!(std::fs::read(&saved.path).unwrap(), content);

    // an identical file is refused (duplicate policy "block") and reported with the existing document
    let again = l.desk.stage_paths(vec![file.clone()]).await.unwrap();
    let q2 = l.desk.queue_add(&org, &a, vec![add(&again[0].id, "block")]).unwrap();
    l.desk.queue_run().await.unwrap();
    let dup = l.desk.queue_list(Some(&a)).unwrap().into_iter().find(|i| i.id == q2[0].id).unwrap();
    assert_eq!((dup.status.as_str(), dup.error_code.as_deref()), ("failed", Some("duplicate_document")));
    assert_eq!(dup.duplicates[0]["display_name"], "Invoice 1.pdf");

    // a file that lies about its type is rejected by the SERVER (the client only checked the extension)
    let fake = l._dir.path().join("fake.pdf");
    std::fs::write(&fake, b"\x89PNG\r\n\x1a\n not a pdf at all").unwrap();
    let st = l.desk.stage_paths(vec![fake]).await.unwrap();
    let q3 = l.desk.queue_add(&org, &a, vec![add(&st[0].id, "block")]).unwrap();
    l.desk.queue_run().await.unwrap();
    let bad = l.desk.queue_list(Some(&a)).unwrap().into_iter().find(|i| i.id == q3[0].id).unwrap();
    assert_eq!(bad.status, "failed");
    assert!(bad.error_code.as_deref().is_some_and(|c| c == "content_mismatch" || c == "corrupt_file"), "{:?}", bad.error_code);

    assert!(l.desk.docs_list(&org, &b, &json!({})).await.is_err(), "Beta is not reachable for this accountant");
    assert!(l.desk.docs_get(&org, &b, &doc).await.is_err());
    assert!(l.desk.docs_get(&org, &a, "00000000-0000-4000-8000-000000000000").await.is_err());

    // an assigned accountant can read and import but not delete, and cannot touch Beta
    assert_eq!(l.desk.docs_list(&org, &a, &json!({})).await.unwrap()["total"], 1);
    assert!(l.desk.docs_list(&org, &b, &json!({})).await.is_err());
    assert_eq!(l.desk.docs_action(&org, &a, &doc, "delete", None).await.unwrap_err().status, Some(403));
}
