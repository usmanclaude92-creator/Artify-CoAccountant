//! Document intelligence (Phase 7) for the desktop: thin, validated calls to the processing API. The server decides what
//! is allowed and computes everything; this layer only checks that identifiers are well-formed, fixes the set of routes
//! the interface may call, and fetches page images. No OCR/AI runs on the desktop and no provider secret ever reaches it.

use base64::Engine;
use serde::Serialize;
use serde_json::{json, Value};

use crate::desktop::Desktop;
use crate::error::{AppError, Result};

fn id(s: &str) -> Result<String> {
    uuid::Uuid::parse_str(s).map(|u| u.hyphenated().to_string()).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

#[derive(Debug, Clone, Serialize)]
pub struct PageImage {
    pub page: u32,
    pub data_base64: String,
}

const REVIEW_ACTIONS: [&str; 5] = ["correct", "confirm", "reject", "missing", "unreadable"];

impl Desktop {
    fn ip(company: &str, tail: &str) -> Result<String> {
        Ok(format!("/companies/{}{tail}", id(company)?))
    }

    pub async fn intel_start(
        &self,
        org: &str,
        company: &str,
        doc: &str,
        kind: &str,
        languages: &[String],
        reason: Option<&str>,
    ) -> Result<Value> {
        if !matches!(kind, "full" | "ocr" | "classify" | "extract") {
            return Err(AppError::new("invalid_kind", "Unknown processing kind."));
        }
        let body = json!({ "idempotency_key": format!("dp-{}", uuid::Uuid::new_v4().simple()), "kind": kind, "languages": languages, "reason": reason });
        let r = self.api.post(&Self::ip(company, &format!("/documents/{}/process", id(doc)?))?, Some(org), Some(body)).await;
        let r = r.map_err(|e| self.classify(e))?;
        self.emit("processing-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    pub async fn intel_latest(&self, org: &str, company: &str, doc: &str) -> Result<Value> {
        Ok(self
            .api
            .get(&Self::ip(company, &format!("/documents/{}/intelligence", id(doc)?))?, Some(org))
            .await
            .map_err(|e| self.classify(e))?
            .body)
    }
    pub async fn intel_queue(&self, org: &str, company: &str) -> Result<Value> {
        Ok(self.api.get(&Self::ip(company, "/processing/review-queue?limit=100")?, Some(org)).await.map_err(|e| self.classify(e))?.body)
    }
    pub async fn intel_jobs(&self, org: &str, company: &str, status: Option<&str>) -> Result<Value> {
        let tail = match status {
            Some(s) if s.chars().all(|c| c.is_ascii_lowercase() || c == '_') => format!("/processing/jobs?limit=100&status={s}"),
            _ => "/processing/jobs?limit=100".into(),
        };
        Ok(self.api.get(&Self::ip(company, &tail)?, Some(org)).await.map_err(|e| self.classify(e))?.body)
    }
    pub async fn intel_job_action(&self, org: &str, company: &str, job: &str, action: &str) -> Result<Value> {
        if !matches!(action, "cancel" | "retry") {
            return Err(AppError::new("unknown_action", "Unknown action."));
        }
        let r = self.api.post(&Self::ip(company, &format!("/processing/jobs/{}/{action}", id(job)?))?, Some(org), None).await;
        let r = r.map_err(|e| self.classify(e))?;
        self.emit("processing-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    pub async fn intel_extraction(&self, org: &str, company: &str, extraction: &str) -> Result<Value> {
        Ok(self
            .api
            .get(&Self::ip(company, &format!("/extractions/{}", id(extraction)?))?, Some(org))
            .await
            .map_err(|e| self.classify(e))?
            .body)
    }
    /// Recognised text with boxes for a few pages at a time (the server caps the window).
    pub async fn intel_ocr(&self, org: &str, company: &str, doc: &str, page_from: u32, page_to: u32) -> Result<Value> {
        let (a, b) = (page_from.max(1), page_to.max(page_from.max(1)));
        Ok(self
            .api
            .get(&Self::ip(company, &format!("/documents/{}/ocr?page_from={a}&page_to={b}", id(doc)?))?, Some(org))
            .await
            .map_err(|e| self.classify(e))?
            .body)
    }
    pub async fn intel_extractions(&self, org: &str, company: &str, doc: &str) -> Result<Value> {
        Ok(self
            .api
            .get(&Self::ip(company, &format!("/documents/{}/extractions", id(doc)?))?, Some(org))
            .await
            .map_err(|e| self.classify(e))?
            .body)
    }

    /// kind: field | line-items | transactions. `action` must be one of the review actions; `value`/`patch` carry the correction.
    pub async fn intel_review(
        &self,
        org: &str,
        company: &str,
        extraction: &str,
        kind: &str,
        target: &str,
        action: &str,
        payload: Value,
        reason: Option<&str>,
    ) -> Result<Value> {
        if !REVIEW_ACTIONS.contains(&action) {
            return Err(AppError::new("unknown_action", "Unknown review action."));
        }
        let ex = id(extraction)?;
        let (path, body) = match kind {
            "field" => {
                if !target.chars().all(|c| c.is_ascii_lowercase() || c == '_') || target.is_empty() || target.len() > 48 {
                    return Err(AppError::new("invalid_id", "That field is not valid."));
                }
                (format!("/extractions/{ex}/fields/{target}"), json!({ "action": action, "value": payload.get("value"), "reason": reason }))
            }
            "line-items" => (
                format!("/extractions/{ex}/line-items/{}", id(target)?),
                json!({ "action": action, "values": payload.get("values"), "reason": reason }),
            ),
            "transactions" => (
                format!("/extractions/{ex}/transactions/{}", id(target)?),
                json!({ "action": action, "patch": payload.get("patch"), "reason": reason }),
            ),
            _ => return Err(AppError::new("unknown_action", "Unknown review target.")),
        };
        let r = self.api.patch(&Self::ip(company, &path)?, Some(org), body).await.map_err(|e| self.classify(e))?;
        Ok(r.body)
    }
    pub async fn intel_add_transaction(&self, org: &str, company: &str, extraction: &str, data: Value) -> Result<Value> {
        let allowed: serde_json::Map<String, Value> = data
            .as_object()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|(k, _)| matches!(k.as_str(), "tx_date" | "description" | "reference" | "debit" | "credit" | "balance" | "reason"))
            .collect();
        let r = self
            .api
            .post(&Self::ip(company, &format!("/extractions/{}/transactions", id(extraction)?))?, Some(org), Some(Value::Object(allowed)))
            .await;
        Ok(r.map_err(|e| self.classify(e))?.body)
    }
    pub async fn intel_statement(&self, org: &str, company: &str, extraction: &str, patch: Value) -> Result<Value> {
        let allowed: serde_json::Map<String, Value> = patch
            .as_object()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|(k, _)| matches!(k.as_str(), "opening_balance" | "closing_balance" | "reason"))
            .collect();
        let r = self
            .api
            .patch(&Self::ip(company, &format!("/extractions/{}/statement", id(extraction)?))?, Some(org), Value::Object(allowed))
            .await;
        Ok(r.map_err(|e| self.classify(e))?.body)
    }
    pub async fn intel_classification(&self, org: &str, company: &str, doc: &str, action: &str, type_key: Option<&str>) -> Result<Value> {
        let r = self
            .api
            .post(
                &Self::ip(company, &format!("/documents/{}/classification", id(doc)?))?,
                Some(org),
                Some(json!({ "action": action, "type_key": type_key })),
            )
            .await;
        Ok(r.map_err(|e| self.classify(e))?.body)
    }
    pub async fn intel_decide(
        &self,
        org: &str,
        company: &str,
        extraction: &str,
        action: &str,
        acknowledge: bool,
        note: Option<&str>,
    ) -> Result<Value> {
        let ex = id(extraction)?;
        let (path, body) = match action {
            "approve" => (format!("/extractions/{ex}/approve"), json!({ "acknowledge_validation_errors": acknowledge, "note": note })),
            "reject" => (format!("/extractions/{ex}/reject"), json!({ "reason": note })),
            _ => return Err(AppError::new("unknown_action", "Unknown action.")),
        };
        let r = self.api.post(&Self::ip(company, &path)?, Some(org), Some(body)).await.map_err(|e| self.classify(e))?;
        self.emit("processing-changed", json!({ "company_id": company }));
        self.emit("documents-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    /// kind: workspace | metadata
    pub async fn intel_accept(
        &self,
        org: &str,
        company: &str,
        extraction: &str,
        kind: &str,
        suggestion: &str,
        value: Option<Value>,
    ) -> Result<Value> {
        let ex = id(extraction)?;
        let sg = id(suggestion)?;
        let (path, body) = match kind {
            "workspace" => (format!("/extractions/{ex}/workspace-suggestions/{sg}/accept"), json!({ "move": true })),
            "metadata" => (format!("/extractions/{ex}/metadata-suggestions/{sg}/accept"), json!({ "value": value })),
            _ => return Err(AppError::new("unknown_action", "Unknown suggestion kind.")),
        };
        let r = self.api.post(&Self::ip(company, &path)?, Some(org), Some(body)).await.map_err(|e| self.classify(e))?;
        self.emit("documents-changed", json!({ "company_id": company }));
        Ok(r.body)
    }
    pub async fn intel_split(&self, org: &str, company: &str, extraction: &str, approve: bool) -> Result<Value> {
        let r = self
            .api
            .post(&Self::ip(company, &format!("/extractions/{}/split", id(extraction)?))?, Some(org), Some(json!({ "approve": approve })))
            .await;
        Ok(r.map_err(|e| self.classify(e))?.body)
    }

    /// The page as OCR read it (rotation/deskew applied): the surface that the field boxes refer to.
    pub async fn intel_page_image(&self, org: &str, company: &str, doc: &str, page: u32) -> Result<PageImage> {
        let tmp = self.data_dir.join("tmp");
        std::fs::create_dir_all(&tmp)?;
        let part = tmp.join(format!("page-{}.part", uuid::Uuid::new_v4().simple()));
        let path = Self::ip(company, &format!("/documents/{}/pages/{}/image", id(doc)?, page.max(1)))?;
        let got = self
            .api
            .download_to(
                &path,
                Some(org),
                &part,
                std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
                std::sync::Arc::new(|_| {}),
            )
            .await;
        let got = got.map_err(|e| {
            let _ = std::fs::remove_file(&part);
            self.classify(e)
        })?;
        let _ = got;
        let bytes = std::fs::read(&part)?;
        let _ = std::fs::remove_file(&part);
        Ok(PageImage { page, data_base64: base64::engine::general_purpose::STANDARD.encode(bytes) })
    }
}
