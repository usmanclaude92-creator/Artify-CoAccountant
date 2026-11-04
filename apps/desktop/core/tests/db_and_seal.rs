use std::fs;

use artify_core::db::{Db, LocalDevice, MIGRATIONS};
use artify_core::seal::Sealer;
use artify_core::vault::{MemoryStore, SecretStore};
use serde_json::json;

fn sealer() -> Sealer {
    Sealer::from_key(&[7u8; 32]).unwrap()
}
fn open(dir: &std::path::Path) -> Db {
    Db::open(&dir.join("app.db"), sealer()).unwrap().0
}

#[test]
fn a_new_database_is_created_and_migrated() {
    let d = tempfile::tempdir().unwrap();
    let (db, report) = Db::open(&d.path().join("sub/app.db"), sealer()).unwrap();
    assert_eq!(report.schema_version, MIGRATIONS.last().unwrap().0);
    assert!(report.recovered_from.is_none() && report.migrated_from.is_none());
    assert_eq!(db.schema_version().unwrap(), report.schema_version);
    for t in [
        "settings",
        "accounts",
        "organizations",
        "companies",
        "workspace_cache",
        "sync_state",
        "device",
        "local_dirs",
        "pending_ops",
        "events",
    ] {
        let n: i64 = db.conn().query_row("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1", [t], |r| r.get(0)).unwrap();
        assert_eq!(n, 1, "{t}");
    }
    let idx: i64 = db
        .conn()
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name IN ('companies_by_org','pending_ops_due','events_recent')",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(idx, 3);
}

#[test]
fn reopening_is_idempotent_and_the_cache_persists() {
    let d = tempfile::tempdir().unwrap();
    {
        let db = open(d.path());
        db.set_setting("u1", "theme", "dark").unwrap();
        db.put_workspace("u1", "c1", 4, Some("W/\"x\""), &json!({"nodes": []})).unwrap();
    }
    let (db, report) = Db::open(&d.path().join("app.db"), sealer()).unwrap();
    assert!(report.migrated_from.is_none() && report.recovered_from.is_none());
    assert_eq!(db.get_setting("u1", "theme").unwrap().as_deref(), Some("dark"));
    let ws = db.get_workspace("u1", "c1").unwrap().unwrap();
    assert_eq!((ws.version, ws.etag.as_deref()), (4, Some("W/\"x\"")));
}

static V1_ONLY: &[(i64, &str)] = &[(1, "CREATE TABLE a (x INTEGER);")];
static V1_V2: &[(i64, &str)] =
    &[(1, "CREATE TABLE a (x INTEGER);"), (2, "ALTER TABLE a ADD COLUMN y INTEGER DEFAULT 5; CREATE TABLE b (z TEXT);")];
static V2_BROKEN: &[(i64, &str)] = &[(1, "CREATE TABLE a (x INTEGER);"), (2, "CREATE TABLE c (z TEXT); THIS IS NOT SQL;")];

#[test]
fn upgrades_keep_existing_data() {
    let d = tempfile::tempdir().unwrap();
    let path = d.path().join("m.db");
    {
        let (db, _) = Db::open_with(&path, sealer(), V1_ONLY).unwrap();
        db.conn().execute("INSERT INTO a (x) VALUES (42)", []).unwrap();
    }
    let (db, report) = Db::open_with(&path, sealer(), V1_V2).unwrap();
    assert_eq!((report.schema_version, report.migrated_from), (2, Some(1)));
    let (x, y): (i64, i64) = db.conn().query_row("SELECT x, y FROM a", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
    assert_eq!((x, y), (42, 5));
}

#[test]
fn a_failing_migration_changes_nothing() {
    let d = tempfile::tempdir().unwrap();
    let path = d.path().join("m.db");
    drop(Db::open_with(&path, sealer(), V1_ONLY).unwrap());
    assert!(Db::open_with(&path, sealer(), V2_BROKEN).is_err());
    let (db, report) = Db::open_with(&path, sealer(), V1_ONLY).unwrap();
    assert_eq!(report.schema_version, 1);
    let n: i64 = db.conn().query_row("SELECT COUNT(*) FROM sqlite_master WHERE name='c'", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 0, "the half-applied migration was rolled back");
}

#[test]
fn a_database_from_a_newer_app_is_refused_untouched() {
    let d = tempfile::tempdir().unwrap();
    let path = d.path().join("m.db");
    drop(Db::open_with(&path, sealer(), V1_V2).unwrap());
    let before = fs::read(&path).unwrap();
    let err = Db::open_with(&path, sealer(), V1_ONLY).err().unwrap();
    assert_eq!(err.code, "database_too_new");
    assert_eq!(fs::read(&path).unwrap(), before, "no downgrade, no changes");
}

#[test]
fn a_corrupt_database_is_quarantined_and_replaced_by_a_fresh_cache() {
    let d = tempfile::tempdir().unwrap();
    let path = d.path().join("app.db");
    fs::write(&path, b"this is definitely not an sqlite database, just garbage bytes ".repeat(40)).unwrap();
    let (db, report) = Db::open(&path, sealer()).unwrap();
    let q = report.recovered_from.expect("recovery must be reported");
    assert!(std::path::Path::new(&q).exists(), "the damaged file is kept for support");
    assert_eq!(db.schema_version().unwrap(), MIGRATIONS.last().unwrap().0);
    db.set_setting("u", "theme", "light").unwrap();
    assert_eq!(db.get_setting("u", "theme").unwrap().as_deref(), Some("light"));
}

#[test]
fn cached_payloads_are_encrypted_at_rest() {
    let d = tempfile::tempdir().unwrap();
    {
        let mut db = open(d.path());
        db.put_account("u1", &json!({"email": "partner@example.com", "display_name": "ZXQ-Marker-Name"})).unwrap();
        db.replace_companies("u1", "o1", &[("c1".into(), "active".into(), json!({"name": "ZXQ-Secret-Company"}))]).unwrap();
        db.put_workspace("u1", "c1", 1, None, &json!({"nodes": [{"name": "ZXQ-Confidential-Folder"}]})).unwrap();
        db.enqueue_op("u1", Some("c1"), "upload", &json!({"file": "ZXQ-invoice.pdf"})).unwrap();
        db.conn().execute_batch("PRAGMA wal_checkpoint(TRUNCATE);").unwrap();
    }
    let mut raw = Vec::new();
    for e in fs::read_dir(d.path()).unwrap() {
        raw.extend(fs::read(e.unwrap().path()).unwrap());
    }
    let hay = String::from_utf8_lossy(&raw);
    for needle in ["ZXQ-Marker-Name", "partner@example.com", "ZXQ-Secret-Company", "ZXQ-Confidential-Folder", "ZXQ-invoice"] {
        assert!(!hay.contains(needle), "{needle} is readable in the database file");
    }
}

#[test]
fn a_blob_cannot_be_replayed_into_another_users_or_companys_row() {
    let d = tempfile::tempdir().unwrap();
    let db = open(d.path());
    db.put_workspace("userA", "cA", 3, None, &json!({"secret": "A"})).unwrap();
    db.conn().execute("INSERT INTO workspace_cache (user_id, company_id, version, etag, payload, fetched_at) SELECT 'userB', company_id, version, etag, payload, fetched_at FROM workspace_cache WHERE user_id='userA'", []).unwrap();
    assert!(db.get_workspace("userB", "cA").is_err(), "AAD binds the blob to user + table + company");
    db.conn().execute("INSERT INTO workspace_cache (user_id, company_id, version, etag, payload, fetched_at) SELECT user_id, 'cOther', version, etag, payload, fetched_at FROM workspace_cache WHERE company_id='cA' AND user_id='userA'", []).unwrap();
    assert!(db.get_workspace("userA", "cOther").is_err());
    assert!(db.get_workspace("userA", "cA").unwrap().is_some());
}

#[test]
fn a_different_key_cannot_read_the_cache() {
    let d = tempfile::tempdir().unwrap();
    let path = d.path().join("app.db");
    drop(Db::open(&path, sealer()).unwrap().0.put_account("u", &json!({"a": 1})));
    let (other, _) = Db::open(&path, Sealer::from_key(&[9u8; 32]).unwrap()).unwrap();
    assert!(other.get_account("u").is_err());
}

#[test]
fn seal_roundtrip_tamper_and_vault_key_creation() {
    let s = sealer();
    let ct = s.seal("aad", b"hello").unwrap();
    assert_eq!(s.open("aad", &ct).unwrap(), b"hello");
    assert!(s.open("other-aad", &ct).is_err());
    let mut bad = ct.clone();
    *bad.last_mut().unwrap() ^= 1;
    assert!(s.open("aad", &bad).is_err());
    assert!(s.open("aad", &ct[..10]).is_err());
    assert_ne!(s.seal("aad", b"hello").unwrap(), ct, "fresh nonce every time");
    let vault = MemoryStore::new();
    let a = Sealer::from_vault(&vault).unwrap();
    let b = Sealer::from_vault(&vault).unwrap();
    assert!(vault.get("cache-key-v1").unwrap().is_some());
    assert_eq!(b.open("x", &a.seal("x", b"same key").unwrap()).unwrap(), b"same key");
}

#[test]
fn caches_are_per_user_and_companies_the_server_stops_returning_disappear() {
    let d = tempfile::tempdir().unwrap();
    let mut db = open(d.path());
    let co = |id: &str, name: &str| (id.to_string(), "active".to_string(), json!({"id": id, "name": name}));
    db.replace_companies("u1", "o1", &[co("c1", "One"), co("c2", "Two")]).unwrap();
    db.replace_companies("u2", "o1", &[co("c9", "Nine")]).unwrap();
    db.put_workspace("u1", "c2", 2, None, &json!({})).unwrap();
    db.set_favorite("u1", "c1", true).unwrap();
    assert_eq!(db.list_companies("u2", "o1").unwrap().len(), 1);
    db.replace_companies("u1", "o1", &[co("c1", "One renamed")]).unwrap(); // membership of c2 revoked
    let after = db.list_companies("u1", "o1").unwrap();
    assert_eq!(after.len(), 1);
    assert!(after[0].favorite, "favorites survive a refresh");
    assert!(db.get_workspace("u1", "c2").unwrap().is_none(), "its cached workspace goes with it");
    assert!(!db.set_favorite("u1", "c2", true).unwrap());
    assert_eq!(db.list_companies("u2", "o1").unwrap().len(), 1, "other users untouched");
}

#[test]
fn logout_wipe_removes_cached_data_but_never_silently_drops_pending_operations() {
    let d = tempfile::tempdir().unwrap();
    let mut db = open(d.path());
    db.put_account("u1", &json!({})).unwrap();
    db.put_workspace("u1", "c1", 1, None, &json!({})).unwrap();
    db.set_setting("u1", "theme", "dark").unwrap();
    db.enqueue_op("u1", Some("c1"), "upload", &json!({"x": 1})).unwrap();
    db.put_device(
        "u1",
        "o1",
        &LocalDevice {
            device_id: "d".into(),
            name: "PC".into(),
            app_version: "0.1.0".into(),
            registered_at: "t".into(),
            last_seen_at: None,
            status: "active".into(),
        },
    )
    .unwrap();
    db.wipe_user_cache("u1", false).unwrap();
    assert!(
        db.get_account("u1").unwrap().is_none()
            && db.get_workspace("u1", "c1").unwrap().is_none()
            && db.get_device("u1", "o1").unwrap().is_none()
    );
    assert_eq!(db.pending_summary("u1").unwrap().pending, 1, "unsent work survives a wipe");
    assert_eq!(db.get_setting("u1", "theme").unwrap().as_deref(), Some("dark"), "preferences are not cache");
    db.wipe_user_cache("u1", true).unwrap();
    assert_eq!(db.pending_summary("u1").unwrap().pending, 0);
}

#[test]
fn events_keep_a_bounded_history() {
    let d = tempfile::tempdir().unwrap();
    let db = open(d.path());
    for i in 0..230 {
        db.add_event("u1", "info", "k", &format!("m{i}")).unwrap();
    }
    let ev = db.list_events("u1", 500).unwrap();
    assert!(ev.len() <= 201 && ev[0].message == "m229");
    db.mark_events_read("u1").unwrap();
    assert!(db.list_events("u1", 5).unwrap().iter().all(|e| e.read));
}
