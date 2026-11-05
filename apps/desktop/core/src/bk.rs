//! Daily bookkeeping and closing (Phase 10) for the desktop: a fixed, validated passthrough to the bookkeeping API. The server
//! decides everything (validation results, who may prepare/review/approve/close/reopen/override, independence, snapshots); this
//! layer only fixes WHICH routes the interface may call, checks identifiers, business dates and template keys, restricts query
//! keys and values, and forwards the body unchanged. Nothing is validated, approved, closed or overridden here, and a
//! synchronisation state is only ever *declared* to the server by this app, never proven.

use serde_json::Value;

use crate::desktop::Desktop;
use crate::error::{AppError, Result};

/// What a path placeholder may hold. Each is validated before it is placed in a URL.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum A {
    /// a UUID
    Id,
    /// a calendar day, `YYYY-MM-DD`
    Date,
    /// an Artify-defined template key such as `daily_bookkeeping`
    Key,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum M {
    Get,
    Post,
    Put,
    Patch,
    Delete,
}

use A::{Date, Id, Key};

/// operation -> (method, path under /companies/{company}/bookkeeping, the placeholders it needs in order)
const OPS: &[(&str, M, &str, &[A])] = &[
    ("policy", M::Get, "/policy", &[]),
    ("policy_update", M::Put, "/policy", &[]),
    ("checklist_templates", M::Get, "/checklist-templates", &[]),
    ("checklists", M::Get, "/checklists", &[]),
    ("checklist_create", M::Post, "/checklists", &[]),
    ("checklist_copy", M::Post, "/checklists/from-template/{0}", &[Key]),
    ("checklist_update", M::Patch, "/checklists/{0}", &[Id]),
    ("item_add", M::Post, "/checklists/{0}/items", &[Id]),
    ("item_order", M::Put, "/checklists/{0}/order", &[Id]),
    ("item_update", M::Patch, "/checklist-items/{0}", &[Id]),
    ("item_complete", M::Post, "/checklist-items/{0}/complete", &[Id]),
    ("item_uncomplete", M::Delete, "/checklist-items/{0}/complete", &[Id]),
    ("tasks", M::Get, "/tasks", &[]),
    ("task_create", M::Post, "/tasks", &[]),
    ("task", M::Get, "/tasks/{0}", &[Id]),
    ("task_update", M::Patch, "/tasks/{0}", &[Id]),
    ("task_assign", M::Post, "/tasks/{0}/assign", &[Id]),
    ("task_transition", M::Post, "/tasks/{0}/transition", &[Id]),
    ("task_comment", M::Post, "/tasks/{0}/comments", &[Id]),
    ("day", M::Get, "/days/{0}", &[Date]),
    ("day_validation", M::Get, "/days/{0}/validation", &[Date]),
    ("day_documents", M::Get, "/days/{0}/documents", &[Date]),
    ("day_transactions", M::Get, "/days/{0}/transactions", &[Date]),
    ("day_exceptions", M::Get, "/days/{0}/exceptions", &[Date]),
    ("day_report", M::Get, "/days/{0}/report", &[Date]),
    ("day_cash_bank", M::Get, "/days/{0}/cash-bank", &[Date]),
    ("day_snapshots", M::Get, "/days/{0}/snapshots", &[Date]),
    ("sync_report", M::Post, "/sync-reports", &[]),
    ("exception_acknowledge", M::Post, "/exceptions/{0}/acknowledge", &[Id]),
    ("exception_override", M::Post, "/exceptions/{0}/override", &[Id]),
    ("closings", M::Get, "/closings", &[]),
    ("closing_create", M::Post, "/closings", &[]),
    ("closing", M::Get, "/closings/{0}", &[Id]),
    ("closing_validate", M::Post, "/closings/{0}/validate", &[Id]),
    ("closing_stage", M::Post, "/closings/{0}/stage", &[Id]),
    ("closing_summary", M::Get, "/closings/{0}/summary", &[Id]),
    ("closing_submit", M::Post, "/closings/{0}/submit", &[Id]),
    ("closing_send_back", M::Post, "/closings/{0}/send-back", &[Id]),
    ("closing_close", M::Post, "/closings/{0}/close", &[Id]),
    ("closing_reopen", M::Post, "/closings/{0}/reopen", &[Id]),
    ("closing_cancel", M::Post, "/closings/{0}/cancel", &[Id]),
    ("closing_snapshot", M::Get, "/closings/{0}/snapshot", &[Id]),
    ("closing_exports", M::Get, "/closings/{0}/exports", &[Id]),
    ("closing_export_create", M::Post, "/closings/{0}/exports", &[Id]),
    ("closing_export_regenerate", M::Post, "/closings/{0}/exports/{1}/regenerate", &[Id, Id]),
    ("readiness", M::Get, "/readiness", &[]),
];
const QUERY_KEYS: &[&str] =
    &["status", "mine", "business_date", "open_only", "limit", "offset", "date_from", "date_to", "period_id", "include_archived"];

fn id(s: &str) -> Result<String> {
    uuid::Uuid::parse_str(s).map(|u| u.hyphenated().to_string()).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

/// `YYYY-MM-DD` with plausible month/day numbers. The server owns the real calendar rules (and time zones).
fn date(s: &str) -> Result<String> {
    let b = s.as_bytes();
    let ok = b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b.iter().enumerate().all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
        && matches!(s[5..7].parse::<u8>(), Ok(1..=12))
        && matches!(s[8..10].parse::<u8>(), Ok(1..=31));
    if ok {
        Ok(s.to_string())
    } else {
        Err(AppError::new("invalid_date", "That date is not valid."))
    }
}

fn key(s: &str) -> Result<String> {
    let ok = (2..=40).contains(&s.len())
        && s.starts_with(|c: char| c.is_ascii_lowercase())
        && s.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_');
    if ok {
        Ok(s.to_string())
    } else {
        Err(AppError::new("invalid_key", "That template key is not valid."))
    }
}

fn query_string(query: &[(String, String)]) -> Result<String> {
    let mut parts = Vec::new();
    for (k, v) in query {
        if !QUERY_KEYS.contains(&k.as_str()) || v.len() > 100 || !v.chars().all(|c| c.is_ascii_alphanumeric() || " _-.".contains(c)) {
            return Err(AppError::new("invalid_query", "That filter is not allowed."));
        }
        parts.push(format!("{k}={}", v.replace(' ', "%20")));
    }
    Ok(if parts.is_empty() { String::new() } else { format!("?{}", parts.join("&")) })
}

impl Desktop {
    /// One allow-listed bookkeeping call. `args` fill the numbered placeholders in order.
    pub async fn bk_call(
        &self,
        org: &str,
        company: &str,
        op: &str,
        args: &[String],
        query: &[(String, String)],
        body: Value,
    ) -> Result<Value> {
        let Some((_, method, tail, kinds)) = OPS.iter().find(|o| o.0 == op) else {
            return Err(AppError::new("unknown_action", "Unknown bookkeeping action."));
        };
        if args.len() != kinds.len() {
            return Err(AppError::new("invalid_id", "That identifier is not valid."));
        }
        let mut path = format!("/companies/{}/bookkeeping{tail}", id(company)?);
        for (n, (kind, value)) in kinds.iter().zip(args).enumerate() {
            let checked = match kind {
                A::Id => id(value)?,
                A::Date => date(value)?,
                A::Key => key(value)?,
            };
            path = path.replace(&format!("{{{n}}}"), &checked);
        }
        // writes take no filters, except ticking a checklist item, which names the day it is ticked for
        let day_tick = matches!(op, "item_complete" | "item_uncomplete") && query.len() == 1 && query[0].0 == "business_date";
        if *method != M::Get && !query.is_empty() && !day_tick {
            return Err(AppError::new("invalid_query", "That filter is not allowed."));
        }
        if day_tick {
            date(&query[0].1)?;
        }
        path.push_str(&query_string(query)?);
        let res = match method {
            M::Get => self.api.get(&path, Some(org)).await,
            M::Post => self.api.post(&path, Some(org), Some(body)).await,
            M::Put => self.api.put(&path, Some(org), body).await,
            M::Patch => self.api.patch(&path, Some(org), body).await,
            M::Delete => self.api.delete(&path, Some(org)).await,
        };
        let r = res.map_err(|e| self.classify(e))?;
        if *method != M::Get {
            self.emit("bookkeeping-changed", serde_json::json!({ "company_id": company }));
        }
        Ok(r.body)
    }
}
