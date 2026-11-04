//! The local mirror: a per-company Windows folder tree derived from the company's CLOUD workspace.
//!
//! The cloud workspace node id is a document's location; the local folder is only a representation of it. Therefore:
//! - folders are computed from the current tree on every use (a rename or move in the cloud changes the computed path, the
//!   files are then moved - never copied - by `plan_moves`);
//! - a local path is never an identity and is never trusted: every path is built from sanitised segments and checked to
//!   stay inside the storage root;
//! - nothing is ever overwritten: a name that exists gets a numeric suffix;
//! - changing the storage root moves nothing (documented): stored paths are relative to the root, and files that are not
//!   found under the new root are reported as missing instead of being re-created silently.

use std::collections::HashMap;
use std::fs;
use std::path::{Component, Path, PathBuf};

use crate::error::{AppError, Result};
use crate::imports::LocalFile;
use crate::storage::sanitize_segment;

#[derive(Debug, Clone)]
pub struct TreeNode {
    pub id: String,
    pub parent_id: Option<String>,
    pub name: String,
    pub position: i64,
}

fn short_id(id: &str) -> String {
    id.chars().filter(|c| c.is_ascii_alphanumeric()).rev().take(6).collect::<Vec<_>>().into_iter().rev().collect()
}

/// Folder names from the section down to `node_id`, each a safe Windows segment. Siblings that would collide after
/// sanitising (or only differ by case) get a stable id suffix, so two different folders never share a directory.
pub fn node_segments(nodes: &[TreeNode], node_id: &str) -> Option<Vec<String>> {
    let by_id: HashMap<&str, &TreeNode> = nodes.iter().map(|n| (n.id.as_str(), n)).collect();
    let mut chain = Vec::new();
    let mut cur = by_id.get(node_id).copied()?;
    for _ in 0..40 {
        chain.push(cur);
        match cur.parent_id.as_deref().and_then(|p| by_id.get(p).copied()) {
            Some(p) => cur = p,
            None => break,
        }
    }
    chain.reverse();
    let mut out = Vec::new();
    for node in chain {
        let base = sanitize_segment(&node.name);
        let mut siblings: Vec<&TreeNode> = nodes.iter().filter(|n| n.parent_id == node.parent_id).collect();
        siblings.sort_by(|a, b| (a.position, &a.id).cmp(&(b.position, &b.id)));
        let first = siblings.iter().find(|s| sanitize_segment(&s.name).eq_ignore_ascii_case(&base));
        let seg = if first.is_some_and(|f| f.id != node.id) { format!("{base} ({})", short_id(&node.id)) } else { base };
        out.push(seg);
    }
    Some(out)
}

/// A safe single file name that keeps the real extension: `Supplier_INV-1.pdf`.
pub fn safe_file_name(name: &str) -> String {
    let (stem, ext) = match name.rsplit_once('.') {
        Some((s, e)) if !s.is_empty() && e.len() <= 10 && e.chars().all(|c| c.is_ascii_alphanumeric()) => (s, Some(e.to_ascii_lowercase())),
        _ => (name, None),
    };
    let mut stem = sanitize_segment(stem);
    if stem == "Company" && name.trim().is_empty() {
        stem = "Document".into();
    }
    let stem: String = stem.chars().take(100).collect();
    match ext {
        Some(e) => format!("{stem}.{e}"),
        None => stem,
    }
}

/// `name`, then `name (2).ext`, `name (3).ext`… the first one that does not exist in `dir` (case-insensitive, as NTFS is).
pub fn unique_in_dir(dir: &Path, name: &str) -> String {
    let taken: Vec<String> = fs::read_dir(dir)
        .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_lowercase()).collect())
        .unwrap_or_default();
    if !taken.contains(&name.to_lowercase()) {
        return name.to_string();
    }
    let (stem, ext) = match name.rsplit_once('.') {
        Some((s, e)) => (s.to_string(), format!(".{e}")),
        None => (name.to_string(), String::new()),
    };
    (2..10_000)
        .map(|n| format!("{stem} ({n}){ext}"))
        .find(|c| !taken.contains(&c.to_lowercase()))
        .unwrap_or_else(|| format!("{stem} ({}){ext}", uuid::Uuid::new_v4().simple()))
}

/// Join a stored relative path to the root, refusing anything that could leave it.
pub fn resolve_under(root: &Path, rel: &str) -> Result<PathBuf> {
    let p = Path::new(rel);
    let bad_segment = |s: &str| s.is_empty() || s == "." || s == ".." || s.contains(['\\', ':']);
    if rel.is_empty() || p.is_absolute() || rel.split('/').any(bad_segment) || p.components().any(|c| !matches!(c, Component::Normal(_))) {
        return Err(AppError::new("invalid_path", "That local path is not inside the storage folder."));
    }
    Ok(root.join(p))
}

fn rel_string(parts: &[String]) -> String {
    parts.join("/")
}

#[derive(Debug, Clone)]
pub struct Placed {
    pub rel_path: String,
    pub abs_path: PathBuf,
}

/// Move a finished download into the company folder tree (creating folders). Never overwrites.
pub fn place_file(root: &Path, company_dir: &str, segments: &[String], file_name: &str, finished: &Path) -> Result<Placed> {
    let mut rel: Vec<String> = vec![company_dir.to_string()];
    rel.extend(segments.iter().cloned());
    let dir = resolve_under(root, &rel_string(&rel))?;
    fs::create_dir_all(&dir).map_err(|_| AppError::new("local_io", "The local folder could not be created."))?;
    let name = unique_in_dir(&dir, &safe_file_name(file_name));
    let dest = dir.join(&name);
    if fs::rename(finished, &dest).is_err() {
        fs::copy(finished, &dest).map_err(|_| AppError::new("local_io", "The file could not be saved to the local folder."))?;
        let _ = fs::remove_file(finished);
    }
    rel.push(name);
    Ok(Placed { rel_path: rel_string(&rel), abs_path: dest })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Move {
    pub document_id: String,
    pub from_rel: String,
    pub to_rel: String,
}

/// Which local files are in the wrong folder for the CURRENT cloud tree (after a rename, move or restructure)?
pub fn plan_moves(files: &[LocalFile], nodes: &[TreeNode], company_dir: &str) -> Vec<Move> {
    files
        .iter()
        .filter_map(|f| {
            let segs = node_segments(nodes, &f.node_id)?;
            let file_name = f.rel_path.rsplit('/').next()?;
            let mut rel = vec![company_dir.to_string()];
            rel.extend(segs);
            rel.push(file_name.to_string());
            let to_rel = rel_string(&rel);
            (to_rel != f.rel_path).then(|| Move { document_id: f.document_id.clone(), from_rel: f.rel_path.clone(), to_rel })
        })
        .collect()
}

#[derive(Debug, Clone, PartialEq, Eq, Default, serde::Serialize)]
pub struct MoveReport {
    pub moved: Vec<(String, String)>,
    pub missing: Vec<String>,
    pub conflicts_renamed: usize,
}

/// Execute planned moves with `fs::rename` (same volume: no copy, no duplicate). A missing source is reported, never
/// recreated; an occupied destination gets a numeric suffix.
pub fn apply_moves(root: &Path, moves: &[Move]) -> Result<(MoveReport, Vec<(String, String)>)> {
    let mut report = MoveReport::default();
    let mut new_paths = Vec::new();
    for m in moves {
        let from = resolve_under(root, &m.from_rel)?;
        if !from.is_file() {
            report.missing.push(m.document_id.clone());
            continue;
        }
        let to = resolve_under(root, &m.to_rel)?;
        let dir = to.parent().ok_or_else(|| AppError::new("invalid_path", "Bad destination."))?;
        fs::create_dir_all(dir).map_err(|_| AppError::new("local_io", "The local folder could not be created."))?;
        let wanted = to.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let name = unique_in_dir(dir, &wanted);
        if name != wanted {
            report.conflicts_renamed += 1;
        }
        fs::rename(&from, dir.join(&name)).map_err(|_| AppError::new("local_io", "A file could not be moved to its new folder."))?;
        let rel = format!("{}/{}", m.to_rel.rsplit_once('/').map(|x| x.0).unwrap_or(""), name);
        report.moved.push((m.from_rel.clone(), rel.clone()));
        new_paths.push((m.document_id.clone(), rel));
    }
    Ok((report, new_paths))
}
