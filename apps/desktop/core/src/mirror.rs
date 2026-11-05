//! The local Windows folder mirror: a directory tree DERIVED from the company's workspace, keyed by stable node id.
//!
//! Rules: nothing is ever deleted or overwritten. A renamed or moved workspace folder renames or moves its local folder
//! (children and saved files follow); an archived or vanished node leaves its local folder untouched; a name clash is
//! reported and resolved with a suffix; an unavailable drive stops everything before a single directory is created.
//! The tree comes only from the workspace delivered by the server: there are no built-in folder names.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;

use serde::Serialize;

use crate::storage::{sanitize_segment, MAX_COMPANY_DIR_PATH};

#[derive(Debug, Clone)]
pub struct MirrorNode {
    pub id: String,
    pub parent_id: Option<String>,
    pub name: String,
    pub position: i64,
    pub archived: bool,
}

/// Desired relative path (forward slashes) per live node. Siblings get unique, Windows-safe names (case-insensitive);
/// a clash is resolved by appending the end of the node id, so the result is deterministic and independent of ordering
/// accidents. Archived nodes and everything below them are left out.
pub fn plan(nodes: &[MirrorNode]) -> HashMap<String, String> {
    let ids: HashSet<&str> = nodes.iter().map(|n| n.id.as_str()).collect();
    let mut children: HashMap<Option<&str>, Vec<&MirrorNode>> = HashMap::new();
    for n in nodes.iter().filter(|n| !n.archived) {
        let parent = n.parent_id.as_deref().filter(|p| ids.contains(p));
        children.entry(parent).or_default().push(n);
    }
    let mut out = HashMap::new();
    let mut stack: Vec<(Option<&str>, String)> = vec![(None, String::new())];
    while let Some((parent, prefix)) = stack.pop() {
        let mut kids = children.remove(&parent).unwrap_or_default();
        kids.sort_by(|a, b| (a.position, a.name.to_lowercase(), &a.id).cmp(&(b.position, b.name.to_lowercase(), &b.id)));
        let mut taken: HashSet<String> = HashSet::new();
        for k in kids {
            let base = sanitize_segment(&k.name);
            let mut seg = base.clone();
            if taken.contains(&seg.to_lowercase()) {
                let tail: String =
                    k.id.chars().filter(|c| c.is_ascii_alphanumeric()).rev().take(6).collect::<Vec<_>>().into_iter().rev().collect();
                seg = format!("{base} ({tail})");
            }
            taken.insert(seg.to_lowercase());
            let rel = if prefix.is_empty() { seg } else { format!("{prefix}/{seg}") };
            out.insert(k.id.clone(), rel.clone());
            stack.push((Some(k.id.as_str()), rel));
        }
    }
    out
}

#[derive(Debug, Clone, Serialize, Default, PartialEq, Eq)]
pub struct MirrorReport {
    pub created: usize,
    pub renamed: usize,
    pub unchanged: usize,
    /// folders whose desired new name/location was taken by something else: left where they are, nothing overwritten
    pub conflicts: Vec<String>,
    /// folders that would exceed the Windows path-length budget: not created
    pub too_long: Vec<String>,
    /// local folders of nodes that are archived or no longer exist: kept untouched
    pub orphaned: usize,
}

#[derive(Debug, Clone)]
pub struct Applied {
    pub report: MirrorReport,
    /// the real relative path of every node after this run (persist it)
    pub mapping: HashMap<String, String>,
    /// (old_prefix, new_prefix) renames performed, so saved-file records can follow their folder
    pub renames: Vec<(String, String)>,
}

fn rewrite(path: &str, renames: &[(String, String)]) -> String {
    let mut p = path.to_string();
    for (old, new) in renames {
        if p == *old || p.starts_with(&format!("{old}/")) {
            p = format!("{new}{}", &p[old.len()..]);
        }
    }
    p
}

/// Make `base` (the company's local folder, which must already exist) match `desired`, using `previous` (node id -> the
/// relative path used last time) to rename instead of re-create. Parents are handled before children.
pub fn apply(base: &Path, desired: &HashMap<String, String>, previous: &HashMap<String, String>) -> std::io::Result<Applied> {
    let mut report = MirrorReport::default();
    let mut mapping = previous.clone();
    let mut renames: Vec<(String, String)> = Vec::new();
    let mut order: Vec<(&String, &String)> = desired.iter().collect();
    order.sort_by_key(|(id, rel)| (rel.matches('/').count(), (*rel).clone(), (*id).clone()));
    for (id, rel) in order {
        let target = join_rel(base, rel);
        if base.display().to_string().chars().count() + rel.chars().count() + 1 > MAX_COMPANY_DIR_PATH + 80 {
            report.too_long.push(rel.clone());
            continue;
        }
        let prev = previous.get(id).map(|p| rewrite(p, &renames));
        match prev {
            Some(old) if old == *rel => {
                if target.is_dir() {
                    report.unchanged += 1;
                } else {
                    fs::create_dir_all(&target)?;
                    report.created += 1;
                }
                mapping.insert(id.clone(), rel.clone());
            }
            Some(old) if join_rel(base, &old).is_dir() => {
                if target.exists() {
                    report.conflicts.push(rel.clone());
                    mapping.insert(id.clone(), old);
                    continue;
                }
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::rename(join_rel(base, &old), &target)?;
                renames.push((old, rel.clone()));
                mapping.insert(id.clone(), rel.clone());
                report.renamed += 1;
            }
            _ => {
                if target.exists() && !target.is_dir() {
                    report.conflicts.push(rel.clone());
                    continue;
                }
                let existed = target.is_dir();
                fs::create_dir_all(&target)?;
                if existed {
                    report.unchanged += 1;
                } else {
                    report.created += 1;
                }
                mapping.insert(id.clone(), rel.clone());
            }
        }
    }
    report.orphaned = mapping.keys().filter(|id| !desired.contains_key(*id)).count();
    Ok(Applied { report, mapping, renames })
}

/// Join a `/`-separated relative path onto `base` component by component (pure `\\` paths on Windows).
pub fn join_rel(base: &Path, rel: &str) -> std::path::PathBuf {
    rel.split('/').filter(|c| !c.is_empty()).fold(base.to_path_buf(), |p, c| p.join(c))
}

/// A Windows-safe file name that keeps its extension (the folder-name sanitizer truncates, which would cut it off).
pub fn safe_file_name(name: &str) -> String {
    let base = name.rsplit(['/', '\\']).next().unwrap_or(name);
    let (stem, ext) = match base.rsplit_once('.') {
        Some((s, e)) if !s.is_empty() && e.len() <= 10 && e.chars().all(|c| c.is_ascii_alphanumeric()) => {
            (s, format!(".{}", e.to_ascii_lowercase()))
        }
        _ => (base, String::new()),
    };
    let stem = sanitize_segment(stem);
    let stem: String = stem.chars().take(100).collect();
    format!("{}{ext}", stem.trim_end_matches(['.', ' ']))
}

/// A file name that does not exist yet in `dir`: `name.ext`, then `name (2).ext`, `name (3).ext` ... Never overwrites.
pub fn unique_file_name(dir: &Path, name: &str) -> String {
    let name = safe_file_name(name);
    if !dir.join(&name).exists() {
        return name;
    }
    let (stem, ext) = match name.rsplit_once('.') {
        Some((s, e)) if !s.is_empty() => (s.to_string(), format!(".{e}")),
        _ => (name.clone(), String::new()),
    };
    (2..10_000)
        .map(|n| format!("{stem} ({n}){ext}"))
        .find(|c| !dir.join(c).exists())
        .unwrap_or_else(|| format!("{stem} ({}){ext}", uuid::Uuid::new_v4().simple()))
}
