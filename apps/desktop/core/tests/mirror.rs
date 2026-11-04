use std::fs;

use artify_core::imports::LocalFile;
use artify_core::mirror::{apply_moves, node_segments, place_file, plan_moves, resolve_under, safe_file_name, unique_in_dir, TreeNode};

fn n(id: &str, parent: Option<&str>, name: &str, pos: i64) -> TreeNode {
    TreeNode { id: id.into(), parent_id: parent.map(str::to_string), name: name.into(), position: pos }
}

#[test]
fn folders_come_from_the_cloud_tree_for_any_shape() {
    // company A: four flat sections; company B: nested - the same code serves both, nothing is hard-coded
    let a: Vec<TreeNode> = ["Bank Statements", "Receivables", "Payables", "Expenses"]
        .iter()
        .enumerate()
        .map(|(i, s)| n(&format!("a{i}"), None, s, i as i64))
        .collect();
    assert_eq!(node_segments(&a, "a2").unwrap(), vec!["Payables"]);
    let b = vec![
        n("f", None, "Finance", 0),
        n("bk", Some("f"), "Banking", 0),
        n("rc", Some("bk"), "Reconciliation", 0),
        n("s", None, "Sales", 1),
    ];
    assert_eq!(node_segments(&b, "rc").unwrap(), vec!["Finance", "Banking", "Reconciliation"]);
    assert_eq!(node_segments(&b, "s").unwrap(), vec!["Sales"]);
    assert!(node_segments(&b, "unknown").is_none());
}

#[test]
fn unsafe_and_colliding_folder_names_stay_safe_and_distinct() {
    let nodes = vec![
        n("r", None, "Docs", 0),
        n("x1", Some("r"), "A/B", 0),
        n("x2", Some("r"), "A-B", 1),
        n("x3", Some("r"), "a-b", 2),
        n("c", None, "CON", 1),
        n("t", None, "..", 2),
    ];
    let s1 = node_segments(&nodes, "x1").unwrap();
    let s2 = node_segments(&nodes, "x2").unwrap();
    let s3 = node_segments(&nodes, "x3").unwrap();
    assert_eq!(s1, vec!["Docs", "A-B"]);
    assert!(s2[1].starts_with("A-B (") && s3[1].starts_with("a-b ("), "{s2:?} {s3:?}");
    assert_ne!(s2[1].to_lowercase(), s3[1].to_lowercase());
    assert_eq!(node_segments(&nodes, "c").unwrap(), vec!["CON_"]);
    let dots = node_segments(&nodes, "t").unwrap();
    assert!(dots[0] != ".." && !dots[0].contains(".."), "{dots:?}");
}

#[test]
fn file_names_keep_the_extension_and_lose_every_unsafe_character() {
    assert_eq!(safe_file_name("Supplier_INV-1.PDF"), "Supplier_INV-1.pdf");
    assert_eq!(safe_file_name("a/b\\c:d*e?.pdf"), "a-b-c-d-e.pdf");
    assert_eq!(safe_file_name("../../evil.pdf"), "evil.pdf");
    assert_eq!(safe_file_name("LPT1.txt"), "LPT1_.txt");
    assert!(safe_file_name(&format!("{}.pdf", "x".repeat(500))).len() <= 105);
    assert_eq!(safe_file_name("   "), "Document");
}

#[test]
fn paths_cannot_leave_the_storage_root() {
    let root = tempfile::tempdir().unwrap();
    for bad in ["", "/etc/passwd", "../x", "a/../../x", "C:/x", "a/./b", "..", "/"] {
        assert!(resolve_under(root.path(), bad).is_err(), "{bad}");
    }
    assert!(resolve_under(root.path(), "Company/Finance/file.pdf").unwrap().starts_with(root.path()));
}

#[test]
fn nothing_is_ever_overwritten() {
    let root = tempfile::tempdir().unwrap();
    let work = tempfile::tempdir().unwrap();
    let mut placed = Vec::new();
    for i in 0..3 {
        let src = work.path().join(format!("src{i}.part"));
        fs::write(&src, format!("content {i}")).unwrap();
        placed.push(place_file(root.path(), "Gulf Trading", &["Finance".into(), "Banking".into()], "stmt.pdf", &src).unwrap());
    }
    assert_eq!(placed[0].rel_path, "Gulf Trading/Finance/Banking/stmt.pdf");
    assert_eq!(placed[1].rel_path, "Gulf Trading/Finance/Banking/stmt (2).pdf");
    assert_eq!(placed[2].rel_path, "Gulf Trading/Finance/Banking/stmt (3).pdf");
    for (i, p) in placed.iter().enumerate() {
        assert_eq!(fs::read_to_string(&p.abs_path).unwrap(), format!("content {i}"));
    }
    assert_eq!(unique_in_dir(placed[0].abs_path.parent().unwrap(), "STMT.PDF"), "STMT (4).PDF");
    // NTFS is case-insensitive
}

#[test]
fn renamed_and_moved_cloud_folders_move_local_files_without_copies() {
    let root = tempfile::tempdir().unwrap();
    let work = tempfile::tempdir().unwrap();
    let src = work.path().join("a.part");
    fs::write(&src, "payload").unwrap();
    let before = vec![n("f", None, "Finance", 0), n("bk", Some("f"), "Banking", 0)];
    let segs = node_segments(&before, "bk").unwrap();
    let placed = place_file(root.path(), "Co", &segs, "stmt.pdf", &src).unwrap();
    let files = vec![LocalFile {
        document_id: "d1".into(),
        version_id: "v1".into(),
        node_id: "bk".into(),
        rel_path: placed.rel_path.clone(),
        sha256: "x".into(),
        size: 7,
    }];
    // nothing changed: nothing to do
    assert!(plan_moves(&files, &before, "Co").is_empty());
    // the cloud folder was renamed AND moved under another section
    let after = vec![n("f", None, "Finance", 0), n("o", None, "Operations", 1), n("bk", Some("o"), "Bank Accounts", 0)];
    let moves = plan_moves(&files, &after, "Co");
    assert_eq!(moves.len(), 1);
    assert_eq!(moves[0].to_rel, "Co/Operations/Bank Accounts/stmt.pdf");
    let (report, new_paths) = apply_moves(root.path(), &moves).unwrap();
    assert_eq!(report.moved.len(), 1);
    assert_eq!(new_paths[0].1, "Co/Operations/Bank Accounts/stmt.pdf");
    assert!(!placed.abs_path.exists(), "the file left its old folder (moved, not copied)");
    assert_eq!(fs::read_to_string(root.path().join("Co/Operations/Bank Accounts/stmt.pdf")).unwrap(), "payload");
}

#[test]
fn a_missing_file_is_reported_and_an_occupied_destination_gets_a_new_name() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("Co/New")).unwrap();
    fs::write(root.path().join("Co/New/x.pdf"), "someone else's").unwrap();
    fs::create_dir_all(root.path().join("Co/Old")).unwrap();
    fs::write(root.path().join("Co/Old/x.pdf"), "mine").unwrap();
    let moves = vec![
        artify_core::mirror::Move { document_id: "d1".into(), from_rel: "Co/Old/x.pdf".into(), to_rel: "Co/New/x.pdf".into() },
        artify_core::mirror::Move { document_id: "d2".into(), from_rel: "Co/Old/gone.pdf".into(), to_rel: "Co/New/gone.pdf".into() },
    ];
    let (report, paths) = apply_moves(root.path(), &moves).unwrap();
    assert_eq!(report.missing, vec!["d2"]);
    assert_eq!(report.conflicts_renamed, 1);
    assert_eq!(paths[0].1, "Co/New/x (2).pdf");
    assert_eq!(fs::read_to_string(root.path().join("Co/New/x.pdf")).unwrap(), "someone else's"); // untouched
    assert!(!root.path().join("Co/New/gone.pdf").exists(), "a file that is gone is never recreated");
}

#[test]
fn a_move_request_that_escapes_the_root_is_refused() {
    let root = tempfile::tempdir().unwrap();
    let bad = vec![artify_core::mirror::Move { document_id: "d".into(), from_rel: "../outside.txt".into(), to_rel: "Co/x.txt".into() }];
    assert!(apply_moves(root.path(), &bad).is_err());
}
