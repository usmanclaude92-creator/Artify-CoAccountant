//! Accounting (Phase 8) for the desktop: a fixed, validated passthrough to the accounting API. The server decides everything
//! (calculation, validation, permissions, approval); this layer only fixes WHICH routes the interface may call, checks that
//! identifiers are well-formed UUIDs, restricts query keys and values, and forwards the body unchanged. No accounting rule,
//! tax rate or amount is computed here and nothing is approved here.

use serde_json::Value;

use crate::desktop::Desktop;
use crate::error::{AppError, Result};

fn id(s: &str) -> Result<String> {
    uuid::Uuid::parse_str(s).map(|u| u.hyphenated().to_string()).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum M {
    Get,
    Post,
    Put,
    Patch,
}

/// operation -> (method, path under /companies/{company}/accounting, number of ids it needs)
const OPS: &[(&str, M, &str, u8)] = &[
    ("config", M::Get, "/config", 0),
    ("accounts", M::Get, "/accounts", 0),
    ("roles", M::Get, "/roles", 0),
    ("tax_codes", M::Get, "/tax-codes", 0),
    ("contacts", M::Get, "/contacts", 0),
    ("contact_match", M::Get, "/contacts/match", 0),
    ("contact_create", M::Post, "/contacts", 0),
    ("periods", M::Get, "/periods", 0),
    ("transactions", M::Get, "/transactions", 0),
    ("transaction", M::Get, "/transactions/{id}", 1),
    ("txn_create", M::Post, "/transactions", 0),
    ("from_extraction", M::Post, "/transactions/from-extraction/{id}", 1),
    ("txn_update", M::Patch, "/transactions/{id}", 1),
    ("txn_lines", M::Put, "/transactions/{id}/lines", 1),
    ("txn_contact", M::Put, "/transactions/{id}/contact", 1),
    ("txn_journal_lines", M::Put, "/transactions/{id}/journal-lines", 1),
    ("txn_regenerate", M::Post, "/transactions/{id}/regenerate", 1),
    ("txn_validate", M::Post, "/transactions/{id}/validate", 1),
    ("txn_submit", M::Post, "/transactions/{id}/submit", 1),
    ("txn_review", M::Post, "/transactions/{id}/review-approve", 1),
    ("txn_approve", M::Post, "/transactions/{id}/approve", 1),
    ("txn_reject", M::Post, "/transactions/{id}/reject", 1),
    ("txn_request_correction", M::Post, "/transactions/{id}/request-correction", 1),
    ("txn_revise", M::Post, "/transactions/{id}/revise", 1),
    ("txn_reverse", M::Post, "/transactions/{id}/reverse", 1),
    ("txn_amend", M::Post, "/transactions/{id}/amend", 1),
    ("txn_duplicate", M::Post, "/transactions/{id}/duplicates/{sub}", 2),
    ("trial_balance", M::Get, "/reports/trial-balance", 0),
    ("account_balances", M::Get, "/reports/account-balances", 0),
];
const QUERY_KEYS: &[&str] = &["status", "txn_type", "q", "limit", "offset", "as_of", "kind", "date_from", "date_to", "active"];

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
    /// One allow-listed accounting call. `ids` fill the `{id}` and `{sub}` placeholders in order.
    pub async fn acct_call(
        &self,
        org: &str,
        company: &str,
        op: &str,
        ids: &[String],
        query: &[(String, String)],
        body: Value,
    ) -> Result<Value> {
        let Some((_, method, tail, need)) = OPS.iter().find(|o| o.0 == op) else {
            return Err(AppError::new("unknown_action", "Unknown accounting action."));
        };
        if ids.len() != *need as usize {
            return Err(AppError::new("invalid_id", "That identifier is not valid."));
        }
        let mut path = format!("/companies/{}/accounting{tail}", id(company)?);
        if let Some(first) = ids.first() {
            path = path.replace("{id}", &id(first)?);
        }
        if let Some(second) = ids.get(1) {
            path = path.replace("{sub}", &id(second)?);
        }
        if *method != M::Get && !query.is_empty() {
            return Err(AppError::new("invalid_query", "That filter is not allowed."));
        }
        path.push_str(&query_string(query)?);
        let res = match method {
            M::Get => self.api.get(&path, Some(org)).await,
            M::Post => self.api.post(&path, Some(org), Some(body)).await,
            M::Put => self.api.put(&path, Some(org), body).await,
            M::Patch => self.api.patch(&path, Some(org), body).await,
        };
        let r = res.map_err(|e| self.classify(e))?;
        if *method != M::Get {
            self.emit("accounting-changed", serde_json::json!({ "company_id": company }));
        }
        Ok(r.body)
    }
}
