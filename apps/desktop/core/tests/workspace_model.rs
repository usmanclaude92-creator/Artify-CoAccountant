use artify_core::workspace::{diff, parse, summarize, Change};
use serde_json::{json, Value};

fn node(id: &str, parent: Option<&str>, name: &str, archived: bool) -> Value {
    json!({"id": id, "parent_id": parent, "node_type": if parent.is_some() {"folder"} else {"section"}, "key": id, "name": name,
           "display_name": null, "description": "", "position": 0, "depth": 1, "is_active": true,
           "archived_at": if archived { Some("2026-01-01T00:00:00Z") } else { None }})
}
fn ws(company: &str, version: i64, nodes: Vec<Value>) -> Value {
    json!({"company_id": company, "version": version, "workspace_state": "configured", "nodes": nodes, "categories": []})
}

#[test]
fn four_and_ten_section_companies_parse_to_their_own_structure() {
    let a = ws(
        "A",
        5,
        ["Bank Statements", "Receivables", "Payables", "Expenses"]
            .iter()
            .enumerate()
            .map(|(i, n)| node(&format!("a{i}"), None, n, false))
            .collect(),
    );
    let mut nodes: Vec<Value> = (0..10).map(|i| node(&format!("b{i}"), None, &format!("Section {i}"), false)).collect();
    nodes.push(node("b-bank", Some("b0"), "Banking", false));
    nodes.push(node("b-rec", Some("b-bank"), "Reconciliation", false));
    let b = ws("B", 13, nodes);
    let (pa, pb) = (parse(&a, "A").unwrap(), parse(&b, "B").unwrap());
    assert_eq!(pa.nodes.len(), 4);
    assert_eq!(pb.nodes.iter().filter(|n| n.parent_id.is_none()).count(), 10);
    assert_eq!(pb.nodes.len(), 12);
    assert!(pa.nodes.iter().all(|n| n.parent_id.is_none()));
}

#[test]
fn an_empty_workspace_is_valid() {
    assert!(parse(&ws("A", 1, vec![]), "A").unwrap().nodes.is_empty());
}

#[test]
fn another_companys_workspace_is_rejected() {
    assert_eq!(parse(&ws("B", 2, vec![]), "A").unwrap_err().code, "workspace_mismatch");
}

#[test]
fn structurally_impossible_workspaces_are_rejected() {
    let dup = ws("A", 2, vec![node("x", None, "One", false), node("x", None, "Two", false)]);
    assert_eq!(parse(&dup, "A").unwrap_err().code, "invalid_workspace");
    let dangling = ws("A", 2, vec![node("x", Some("ghost"), "One", false)]);
    assert_eq!(parse(&dangling, "A").unwrap_err().code, "invalid_workspace");
    let cycle = ws("A", 2, vec![node("x", Some("y"), "One", false), node("y", Some("x"), "Two", false)]);
    assert_eq!(parse(&cycle, "A").unwrap_err().code, "invalid_workspace");
    let selfp = ws("A", 2, vec![node("x", Some("x"), "One", false)]);
    assert_eq!(parse(&selfp, "A").unwrap_err().code, "invalid_workspace");
    let unnamed = ws("A", 2, vec![node("x", None, "  ", false)]);
    assert_eq!(parse(&unnamed, "A").unwrap_err().code, "invalid_workspace");
    assert_eq!(parse(&ws("A", 0, vec![]), "A").unwrap_err().code, "invalid_workspace");
    assert_eq!(parse(&json!({"company_id": "A"}), "A").unwrap_err().code, "malformed_response");
    assert_eq!(parse(&json!("nonsense"), "A").unwrap_err().code, "malformed_response");
}

#[test]
fn nodes_re_rooted_by_the_server_are_accepted() {
    // the server returns a node whose hidden parent it may not disclose as a root (parent_id null)
    let w = ws("A", 3, vec![node("child", None, "Visible child", false)]);
    assert_eq!(parse(&w, "A").unwrap().nodes.len(), 1);
}

#[test]
fn changes_are_detected_by_stable_id_not_by_name() {
    let old = parse(
        &ws(
            "A",
            2,
            vec![
                node("s1", None, "Finance", false),
                node("f1", Some("s1"), "Banks", false),
                node("s2", None, "Sales", false),
                node("gone", None, "Hidden", false),
            ],
        ),
        "A",
    )
    .unwrap();
    let new = parse(
        &ws(
            "A",
            7,
            vec![
                node("s1", None, "Money", false),       // renamed
                node("f1", Some("s2"), "Banks", false), // moved
                node("s2", None, "Sales", true),        // archived
                node("n1", None, "Payroll", false),     // added
            ],
        ),
        "A",
    )
    .unwrap();
    let ch = diff(&old, &new);
    assert!(ch.contains(&Change::Renamed { id: "s1".into(), from: "Finance".into(), to: "Money".into() }));
    assert!(ch.contains(&Change::Moved { id: "f1".into(), name: "Banks".into() }));
    assert!(ch.contains(&Change::Archived { id: "s2".into(), name: "Sales".into() }));
    assert!(ch.contains(&Change::Added { id: "n1".into(), name: "Payroll".into() }));
    assert!(ch.contains(&Change::Removed { id: "gone".into(), name: "Hidden".into() }));
    assert_eq!(ch.len(), 5);
    let restored = diff(&new, &old);
    assert!(restored.contains(&Change::Restored { id: "s2".into(), name: "Sales".into() }));
    assert_eq!(summarize(&ch), "Workspace updated: 1 added, 1 renamed, 1 moved, 1 archived, 1 no longer visible.");
    assert!(diff(&old, &old).is_empty() && summarize(&[]) == "Workspace updated.");
}
