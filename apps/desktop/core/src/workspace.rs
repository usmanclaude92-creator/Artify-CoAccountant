//! Reading and validating the company workspace delivered by the Phase 4 API, and detecting what changed between two
//! versions BY STABLE ID (never by name). Nothing here knows any category or folder name: structure is whatever the
//! company configured and the server allowed this user to see.

use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{AppError, Result};

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct Node {
    pub id: String,
    pub parent_id: Option<String>,
    pub node_type: String,
    pub key: String,
    pub name: String,
    #[serde(default)]
    pub display_name: Option<String>,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub position: i64,
    #[serde(default)]
    pub depth: i64,
    #[serde(default = "yes")]
    pub is_active: bool,
    #[serde(default)]
    pub archived_at: Option<String>,
}
fn yes() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Workspace {
    pub company_id: String,
    pub version: i64,
    #[serde(default)]
    pub workspace_state: String,
    pub nodes: Vec<Node>,
    #[serde(default)]
    pub categories: Vec<Value>,
}

/// Parse and sanity-check a workspace response. A structurally impossible tree (duplicate ids, dangling parent, cycle) is
/// REJECTED so it can never replace a good cache.
pub fn parse(v: &Value, expected_company: &str) -> Result<Workspace> {
    let ws: Workspace = serde_json::from_value(v.clone()).map_err(|_| AppError::malformed("workspace"))?;
    if ws.company_id != expected_company {
        return Err(AppError::new("workspace_mismatch", "The server answered with another company's workspace; it was discarded."));
    }
    if ws.version < 1 {
        return Err(AppError::new("invalid_workspace", "The workspace has an invalid version."));
    }
    let mut ids = HashSet::new();
    for n in &ws.nodes {
        if n.id.is_empty() || !ids.insert(n.id.as_str()) {
            return Err(AppError::new("invalid_workspace", "The workspace contains a duplicate or empty item id."));
        }
        if n.name.trim().is_empty() {
            return Err(AppError::new("invalid_workspace", "The workspace contains an unnamed item."));
        }
    }
    // The server re-roots items whose parent the user may not see (parent_id null). Any other parent must be present.
    let parent: HashMap<&str, Option<&str>> = ws.nodes.iter().map(|n| (n.id.as_str(), n.parent_id.as_deref())).collect();
    for n in &ws.nodes {
        if let Some(p) = n.parent_id.as_deref() {
            if !parent.contains_key(p) {
                return Err(AppError::new("invalid_workspace", "The workspace refers to an item that was not delivered."));
            }
        }
        let (mut cur, mut hops) = (n.parent_id.as_deref(), 0);
        while let Some(c) = cur {
            hops += 1;
            if hops > ws.nodes.len() || c == n.id {
                return Err(AppError::new("invalid_workspace", "The workspace structure contains a cycle."));
            }
            cur = parent.get(c).copied().flatten();
        }
    }
    Ok(ws)
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Change {
    Added { id: String, name: String },
    Renamed { id: String, from: String, to: String },
    Moved { id: String, name: String },
    Archived { id: String, name: String },
    Restored { id: String, name: String },
    Removed { id: String, name: String },
}

/// What changed between the cached and the fresh workspace, matched by stable node id.
pub fn diff(old: &Workspace, new: &Workspace) -> Vec<Change> {
    let before: HashMap<&str, &Node> = old.nodes.iter().map(|n| (n.id.as_str(), n)).collect();
    let after: HashSet<&str> = new.nodes.iter().map(|n| n.id.as_str()).collect();
    let mut out = Vec::new();
    for n in &new.nodes {
        match before.get(n.id.as_str()) {
            None => out.push(Change::Added { id: n.id.clone(), name: n.name.clone() }),
            Some(o) => {
                if o.name != n.name {
                    out.push(Change::Renamed { id: n.id.clone(), from: o.name.clone(), to: n.name.clone() });
                }
                if o.parent_id != n.parent_id {
                    out.push(Change::Moved { id: n.id.clone(), name: n.name.clone() });
                }
                match (o.archived_at.is_some(), n.archived_at.is_some()) {
                    (false, true) => out.push(Change::Archived { id: n.id.clone(), name: n.name.clone() }),
                    (true, false) => out.push(Change::Restored { id: n.id.clone(), name: n.name.clone() }),
                    _ => {}
                }
            }
        }
    }
    for o in &old.nodes {
        if !after.contains(o.id.as_str()) {
            out.push(Change::Removed { id: o.id.clone(), name: o.name.clone() });
        }
    }
    out
}

pub fn summarize(changes: &[Change]) -> String {
    let n = |f: fn(&Change) -> bool| changes.iter().filter(|c| f(c)).count();
    let parts: Vec<String> = [
        (n(|c| matches!(c, Change::Added { .. })), "added"),
        (n(|c| matches!(c, Change::Renamed { .. })), "renamed"),
        (n(|c| matches!(c, Change::Moved { .. })), "moved"),
        (n(|c| matches!(c, Change::Archived { .. })), "archived"),
        (n(|c| matches!(c, Change::Restored { .. })), "restored"),
        (n(|c| matches!(c, Change::Removed { .. })), "no longer visible"),
    ]
    .into_iter()
    .filter(|(c, _)| *c > 0)
    .map(|(c, l)| format!("{c} {l}"))
    .collect();
    if parts.is_empty() {
        "Workspace updated.".into()
    } else {
        format!("Workspace updated: {}.", parts.join(", "))
    }
}
