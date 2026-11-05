//! Document operations for the desktop: thin, validated calls to the document API, previews, and saving copies into the
//! local folder mirror. The server stays the authority for permissions, workspace access and limits; this layer only
//! checks that identifiers are well-formed (so they can never alter a request path), whitelists query parameters,
//! streams to disk, and refuses damaged downloads.

use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use base64::Engine;
use serde::Serialize;
use serde_json::{json, Value};

use crate::desktop::Desktop;
use crate::error::{AppError, Result};
use crate::mirror::{self, MirrorNode, MirrorReport};
use crate::workspace;

const LIST_KEYS: [&str; 19] = [
    "node_id",
    "include_subfolders",
    "category_id",
    "status",
    "q",
    "extension",
    "source",
    "created_by",
    "min_size",
    "max_size",
    "created_from",
    "created_to",
    "sha256",
    "import_batch_id",
    "field_key",
    "field_value",
    "sort",
    "order",
    "limit",
];
pub const PREVIEW_MAX_BYTES: u64 = 20 * 1024 * 1024;

fn id(s: &str) -> Result<String> {
    uuid::Uuid::parse_str(s).map(|u| u.hyphenated().to_string()).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

#[derive(Debug, Clone, Serialize)]
pub struct Preview {
    pub mime: String,
    pub size: u64,
    pub data_base64: String,
    pub name: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct SavedCopy {
    pub path: String,
    /// false when an identical copy of this version was already saved and reused
    pub created: bool,
    pub name: String,
}

impl Desktop {
    fn docs_path(company: &str, tail: &str) -> Result<String> {
        Ok(format!("/companies/{}/documents{tail}", id(company)?))
    }

    pub async fn upload_config(&self, org: &str, company: &str) -> Result<Value> {
        let r =
            self.api.get(&format!("/companies/{}/workspace/upload-config", id(company)?), Some(org)).await.map_err(|e| self.classify(e))?;
        Ok(r.body)
    }

    pub async fn docs_list(&self, org: &str, company: &str, query: &Value) -> Result<Value> {
        let mut url = reqwest::Url::parse("http://x/").map_err(|_| AppError::new("internal", "bad url"))?;
        {
            let mut q = url.query_pairs_mut();
            if let Some(obj) = query.as_object() {
                for (k, v) in obj {
                    if !LIST_KEYS.contains(&k.as_str()) && k != "offset" {
                        continue;
                    }
                    let text = match v {
                        Value::String(s) => s.clone(),
                        Value::Number(n) => n.to_string(),
                        Value::Bool(b) => b.to_string(),
                        _ => continue,
                    };
                    if matches!(k.as_str(), "node_id" | "category_id" | "created_by" | "import_batch_id") {
                        q.append_pair(k, &id(&text)?);
                    } else {
                        q.append_pair(k, &text);
                    }
                }
            }
        }
        let tail = url.query().map(|q| format!("?{q}")).unwrap_or_default();
        let r = self.api.get(&Self::docs_path(company, &tail)?, Some(org)).await.map_err(|e| self.classify(e))?;
        Ok(r.body)
    }
    pub async fn docs_counts(&self, org: &str, company: &str) -> Result<Value> {
        Ok(self.api.get(&Self::docs_path(company, "/counts")?, Some(org)).await.map_err(|e| self.classify(e))?.body)
    }
    pub async fn docs_get(&self, org: &str, company: &str, doc: &str) -> Result<Value> {
        Ok(self.api.get(&Self::docs_path(company, &format!("/{}", id(doc)?))?, Some(org)).await.map_err(|e| self.classify(e))?.body)
    }
    pub async fn docs_events(&self, org: &str, company: &str, doc: Option<&str>) -> Result<Value> {
        let tail = match doc {
            Some(d) => format!("/{}/events?limit=100", id(d)?),
            None => "/events?limit=100".to_string(),
        };
        Ok(self.api.get(&Self::docs_path(company, &tail)?, Some(org)).await.map_err(|e| self.classify(e))?.body)
    }
    pub async fn docs_patch(&self, org: &str, company: &str, doc: &str, patch: Value) -> Result<Value> {
        let allowed: serde_json::Map<String, Value> = patch
            .as_object()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|(k, _)| matches!(k.as_str(), "display_name" | "category_id" | "metadata" | "base_row_version"))
            .collect();
        let r = self
            .api
            .patch(&Self::docs_path(company, &format!("/{}", id(doc)?))?, Some(org), Value::Object(allowed))
            .await
            .map_err(|e| self.classify(e))?;
        self.emit("documents-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    pub async fn docs_move(
        &self,
        org: &str,
        company: &str,
        doc: &str,
        node: &str,
        category: Option<&str>,
        base_row_version: Option<i64>,
    ) -> Result<Value> {
        let mut body = json!({ "node_id": id(node)?, "base_row_version": base_row_version });
        if let Some(c) = category {
            body["category_id"] = json!(id(c)?);
        }
        let r = self
            .api
            .post(&Self::docs_path(company, &format!("/{}/move", id(doc)?))?, Some(org), Some(body))
            .await
            .map_err(|e| self.classify(e))?;
        self.emit("documents-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    /// archive | restore | delete | undelete | duplicate | purge
    pub async fn docs_action(&self, org: &str, company: &str, doc: &str, action: &str, body: Option<Value>) -> Result<Value> {
        let doc = id(doc)?;
        let r = match action {
            "archive" | "restore" | "delete" | "undelete" => {
                self.api.post(&Self::docs_path(company, &format!("/{doc}/{action}"))?, Some(org), body).await
            }
            "duplicate" => {
                self.api.post(&Self::docs_path(company, &format!("/{doc}/duplicate"))?, Some(org), Some(body.unwrap_or(json!({})))).await
            }
            "purge" => self.api.delete(&Self::docs_path(company, &format!("/{doc}?confirm=true"))?, Some(org)).await,
            _ => return Err(AppError::new("unknown_action", "Unknown document action.")),
        }
        .map_err(|e| self.classify(e))?;
        self.emit("documents-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    pub async fn docs_restore_version(&self, org: &str, company: &str, doc: &str, version: i64) -> Result<Value> {
        let r = self
            .api
            .post(&Self::docs_path(company, &format!("/{}/versions/{}/restore", id(doc)?, version.max(1)))?, Some(org), Some(json!({})))
            .await
            .map_err(|e| self.classify(e))?;
        self.emit("documents-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    pub async fn docs_suggest(&self, org: &str, company: &str, body: Value) -> Result<Value> {
        Ok(self.api.post(&Self::docs_path(company, "/suggest")?, Some(org), Some(body)).await.map_err(|e| self.classify(e))?.body)
    }
    pub async fn docs_duplicates(&self, org: &str, company: &str, sha256: &str) -> Result<Value> {
        let r = self.api.post(&Self::docs_path(company, "/duplicates/check")?, Some(org), Some(json!({ "sha256": sha256 }))).await;
        Ok(r.map_err(|e| self.classify(e))?.body)
    }

    // ------------------------------------------------------------------ preview and local copies
    fn tmp_dir(&self) -> Result<PathBuf> {
        let d = self.data_dir.join("tmp");
        std::fs::create_dir_all(&d)?;
        Ok(d)
    }

    /// Bytes of a previewable document for the viewer (PDF and common images, size-capped). Nothing is kept on disk.
    pub async fn docs_preview(&self, org: &str, company: &str, doc: &str, version: Option<i64>) -> Result<Preview> {
        let detail = self.docs_get(org, company, doc).await?;
        let size = detail.get("size_bytes").and_then(Value::as_u64).unwrap_or(0);
        let mime = detail.get("mime_type").and_then(Value::as_str).unwrap_or("").to_string();
        let name = detail.get("display_name").and_then(Value::as_str).unwrap_or("document").to_string();
        if !matches!(mime.as_str(), "application/pdf" | "image/png" | "image/jpeg" | "image/bmp") {
            return Err(AppError::new("not_previewable", "This file type cannot be previewed here. Download it to open it."));
        }
        if size > PREVIEW_MAX_BYTES {
            return Err(AppError::new("too_large_to_preview", "This file is too large to preview. Download it to open it."));
        }
        let tmp = self.tmp_dir()?.join(format!("preview-{}.part", uuid::Uuid::new_v4().simple()));
        let q = version.map(|v| format!("&version={}", v.max(1))).unwrap_or_default();
        let path = Self::docs_path(company, &format!("/{}/content?disposition=inline{q}", id(doc)?))?;
        let got = self.api.download_to(&path, Some(org), &tmp, Arc::new(AtomicBool::new(false)), Arc::new(|_| {})).await;
        let got = got.map_err(|e| {
            let _ = std::fs::remove_file(&tmp);
            self.classify(e)
        })?;
        verify(&got.sha256, got.announced_sha256.as_deref(), &tmp)?;
        let bytes = std::fs::read(&tmp)?;
        let _ = std::fs::remove_file(&tmp);
        Ok(Preview { mime, size: got.size, data_base64: base64::engine::general_purpose::STANDARD.encode(bytes), name })
    }

    /// Download a document into its workspace folder in the local mirror (never overwriting). An identical, already saved
    /// copy of the same version is reused instead of downloaded again.
    pub async fn docs_save_copy(&self, org: &str, company: &str, doc: &str, version: Option<i64>) -> Result<SavedCopy> {
        let user = self.user_id()?;
        let (doc, company_id) = (id(doc)?, id(company)?);
        let detail = self.docs_get(org, &company_id, &doc).await?;
        let node = detail.get("node_id").and_then(Value::as_str).ok_or_else(|| AppError::malformed("document"))?.to_string();
        let name = detail.get("display_name").and_then(Value::as_str).unwrap_or("document").to_string();
        let current = detail.get("current_version_no").and_then(Value::as_i64).unwrap_or(1);
        let ver = version.unwrap_or(current).max(1);
        let base = self.ensure_company_dir(org, &company_id)?;
        self.mirror_sync_inner(&user, &company_id, &base)?;
        let rel_dir = self.db()?.mirror_nodes(&user, &company_id)?.get(&node).cloned();
        let dir = match rel_dir {
            Some(r) => mirror::join_rel(&base, &r),
            None => base.clone(), // the node is archived or not part of the mirror: keep the file at the company level
        };
        std::fs::create_dir_all(&dir)?;
        if let Some((rel, sha)) = self.db()?.mirror_get_file(&user, &company_id, &doc, ver)? {
            let existing = mirror::join_rel(&base, &rel);
            if existing.is_file() && file_sha(&existing).map(|h| h == sha).unwrap_or(false) {
                return Ok(SavedCopy {
                    path: existing.display().to_string(),
                    created: false,
                    name: rel.rsplit('/').next().unwrap_or(&name).to_string(),
                });
            }
        }
        let file_name = mirror::unique_file_name(&dir, &name);
        let part = dir.join(format!(".{file_name}.{}.part", uuid::Uuid::new_v4().simple()));
        let path = Self::docs_path(&company_id, &format!("/{doc}/content?version={ver}"))?;
        let got = self.api.download_to(&path, Some(org), &part, Arc::new(AtomicBool::new(false)), Arc::new(|_| {})).await;
        let got = got.map_err(|e| {
            let _ = std::fs::remove_file(&part);
            self.classify(e)
        })?;
        verify(&got.sha256, got.announced_sha256.as_deref(), &part)?;
        let final_path = dir.join(&file_name);
        if final_path.exists() {
            let _ = std::fs::remove_file(&part);
            return Err(AppError::filesystem(
                "already_exists",
                "A file with that name appeared meanwhile; nothing was overwritten. Try again.",
            ));
        }
        std::fs::rename(&part, &final_path)?;
        let rel = final_path.strip_prefix(&base).map(|p| p.to_string_lossy().replace('\\', "/")).unwrap_or(file_name.clone());
        self.db()?.mirror_put_file(&user, &company_id, &doc, ver, &rel, &got.sha256)?;
        self.log.info("document_saved", "copy written to the local folder");
        Ok(SavedCopy { path: final_path.display().to_string(), created: true, name: file_name })
    }

    // ------------------------------------------------------------------ mirror of the workspace tree
    pub fn mirror_sync(&self, org: &str, company: &str) -> Result<MirrorReport> {
        let user = self.user_id()?;
        let company = id(company)?;
        let base = self.ensure_company_dir(org, &company)?;
        self.mirror_sync_inner(&user, &company, &base)
    }

    fn mirror_sync_inner(&self, user: &str, company: &str, base: &std::path::Path) -> Result<MirrorReport> {
        let cached = self
            .db()?
            .get_workspace(user, company)?
            .ok_or_else(|| AppError::new("workspace_unavailable", "Open the workspace first so the folders can be created."))?;
        let ws = workspace::parse(&cached.data, company)?;
        let nodes: Vec<MirrorNode> = ws
            .nodes
            .iter()
            .map(|n| MirrorNode {
                id: n.id.clone(),
                parent_id: n.parent_id.clone(),
                name: n.display_name.clone().filter(|d| !d.trim().is_empty()).unwrap_or_else(|| n.name.clone()),
                position: n.position,
                archived: n.archived_at.is_some() || !n.is_active,
            })
            .collect();
        let desired = mirror::plan(&nodes);
        let previous = self.db()?.mirror_nodes(user, company)?;
        let applied = mirror::apply(base, &desired, &previous)?;
        let d = self.db()?;
        for (node, rel) in &applied.mapping {
            d.mirror_put_node(user, company, node, rel)?;
        }
        for (old, new) in &applied.renames {
            d.mirror_rewrite_prefix(user, company, old, new)?;
        }
        Ok(applied.report)
    }

    /// Absolute folder of a workspace node inside the mirror (after a sync), for "show in folder".
    pub fn mirror_folder(&self, org: &str, company: &str, node: Option<&str>) -> Result<PathBuf> {
        let user = self.user_id()?;
        let company = id(company)?;
        let base = self.ensure_company_dir(org, &company)?;
        match node {
            None => Ok(base),
            Some(n) => {
                let rel = self.db()?.mirror_nodes(&user, &company)?.get(&id(n)?).cloned();
                match rel {
                    Some(r) if mirror::join_rel(&base, &r).is_dir() => Ok(mirror::join_rel(&base, &r)),
                    _ => Ok(base),
                }
            }
        }
    }
}

fn file_sha(path: &std::path::Path) -> std::io::Result<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            return Ok(crate::api::hex(&h.finalize()));
        }
        h.update(&buf[..n]);
    }
}

/// A download whose checksum differs from the one the server announced is damaged: refuse it and delete it.
fn verify(actual: &str, announced: Option<&str>, tmp: &std::path::Path) -> Result<()> {
    if let Some(a) = announced {
        if !a.eq_ignore_ascii_case(actual) {
            let _ = std::fs::remove_file(tmp);
            return Err(AppError::new(
                "download_corrupted",
                "The downloaded file did not match its checksum and was discarded. Try again.",
            )
            .retryable());
        }
    }
    Ok(())
}
