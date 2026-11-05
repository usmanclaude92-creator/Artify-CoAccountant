//! Cloud synchronization for the desktop (Phase 12): pull the server's change feed into a local checkpoint, report an honest
//! status, and reach the conflict endpoints. The server stays the authority: the feed is read-only here, conflicts are
//! *verified and decided* by the server, and the app never shows "synchronized" unless the server confirmed it.
//!
//! * `sync_pull`   - pages through `GET /companies/{id}/sync/changes` from the saved cursor. The cursor and the items it covers
//!   are stored in one transaction, so a crash re-reads a page but never skips one. A rejected cursor restarts from the beginning.
//! * `sync_status` - local queue + checkpoint + the server's own count of open conflicts, folded into one state. "synchronized"
//!   requires: nothing queued, nothing failed, a completed pull that reached the end of the feed, and the server reporting zero
//!   open conflicts. Anything else is pending / uploading / failed / conflict_detected, with the reason.
//! * `sync_call`   - an allow-listed passthrough to the conflict routes (list, report, read, resolve). Resolving records a decision;
//!   it does not write the record (the normal endpoint does, with the new base revision the server returns).

use serde::Serialize;
use serde_json::{json, Value};

use crate::db::FeedItem;
use crate::desktop::Desktop;
use crate::error::{AppError, Result};

const PAGE: usize = 200;
const MAX_PAGES_PER_PULL: usize = 50;
/// Re-read a few seconds before the saved cursor so a row whose transaction committed late is not missed (idempotent by id + revision).
const OVERLAP_SECONDS: u32 = 5;

#[derive(Debug, Clone, Serialize, Default, PartialEq, Eq)]
pub struct PullSummary {
    pub pages: usize,
    pub items: usize,
    pub caught_up: bool,
    pub restarted: bool,
    pub server_time: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SyncStatus {
    /// pending | uploading | failed | conflict_detected | synchronized
    pub state: String,
    pub label: String,
    pub waiting_uploads: i64,
    pub uploading: i64,
    pub failed_uploads: i64,
    pub open_conflicts: Option<i64>,
    pub last_pull_at: Option<String>,
    pub last_error: Option<String>,
    pub caught_up: bool,
    pub known_items: Vec<(String, i64)>,
}

/// The single place that decides what the user is told. Pure, so it can be tested without a server.
#[allow(clippy::too_many_arguments)]
pub fn fold_state(
    waiting: i64,
    uploading: i64,
    failed: i64,
    open_conflicts: Option<i64>,
    pulled_ok: bool,
    caught_up: bool,
    last_error: Option<&str>,
) -> (&'static str, &'static str) {
    if open_conflicts.unwrap_or(0) > 0 {
        return ("conflict_detected", "Some changes conflict with newer versions on the server. Review them.");
    }
    if failed > 0 {
        return ("failed", "Some files could not be uploaded.");
    }
    if uploading > 0 {
        return ("uploading", "Uploading…");
    }
    if waiting > 0 {
        return ("pending", "Waiting to upload.");
    }
    if last_error.is_some() {
        return ("failed", "The last check with the server failed.");
    }
    if !pulled_ok || !caught_up {
        return ("pending", "Not yet checked against the server.");
    }
    if open_conflicts.is_none() {
        return ("pending", "The server could not confirm the state.");
    }
    ("synchronized", "Everything is confirmed by the server.")
}

const CONFLICT_OPS: &[(&str, &str, &str, bool)] = &[
    // operation, method, path under /companies/{company}/sync, takes an id
    ("status", "GET", "/status", false),
    ("conflicts", "GET", "/conflicts", false),
    ("conflict_report", "POST", "/conflicts", false),
    ("conflict", "GET", "/conflicts/{0}", true),
    ("conflict_resolve", "POST", "/conflicts/{0}/resolve", true),
    ("check", "POST", "/check", false),
];

fn uuid_ok(s: &str) -> Result<String> {
    uuid::Uuid::parse_str(s).map(|u| u.hyphenated().to_string()).map_err(|_| AppError::new("invalid_id", "That identifier is not valid."))
}

impl Desktop {
    /// Pull the change feed for one company up to its end (or `MAX_PAGES_PER_PULL`). Safe to call repeatedly.
    pub async fn sync_pull(&self, org: &str, company: &str) -> Result<PullSummary> {
        let user = self.user_id()?;
        let company = uuid_ok(company)?;
        let mut out = PullSummary::default();
        let mut restarted = false;
        for _ in 0..MAX_PAGES_PER_PULL {
            let state = self.db()?.feed_get(&user, &company)?;
            let mut path = format!("/companies/{company}/sync/changes?limit={PAGE}");
            if let Some(c) = &state.cursor {
                path.push_str(&format!("&cursor={}&overlap_seconds={OVERLAP_SECONDS}", urlenc(c)));
            }
            let page = match self.api.get(&path, Some(org)).await {
                Ok(r) => r.body,
                Err(e) if e.code == "invalid_cursor" && !restarted => {
                    self.db()?.feed_reset(&user, &company)?;
                    restarted = true;
                    out.restarted = true;
                    continue;
                }
                Err(e) => {
                    let e = self.classify(e);
                    let _ = self.db()?.feed_record_error(&user, &company, &e.code);
                    return Err(e);
                }
            };
            let items = parse_items(&page)?;
            let has_more = page.get("has_more").and_then(Value::as_bool).unwrap_or(false);
            let next = page.get("next_cursor").and_then(Value::as_str).map(str::to_string);
            self.db()?.feed_save_page(&user, &company, next.as_deref(), &items, !has_more)?;
            out.pages += 1;
            out.items += items.len();
            out.server_time = page.get("server_time").and_then(Value::as_str).map(str::to_string);
            if !has_more {
                out.caught_up = true;
                self.emit("sync-changed", json!({ "company_id": company, "items": out.items }));
                return Ok(out);
            }
        }
        Ok(out) // more pages remain: the checkpoint is saved, the next call continues
    }

    /// What to tell the user right now. Asks the server for its open-conflict count; if that fails the answer is "pending",
    /// never "synchronized".
    pub async fn sync_status(&self, org: &str, company: &str) -> Result<SyncStatus> {
        let user = self.user_id()?;
        let company = uuid_ok(company)?;
        let (rows, feed, counts) = {
            let d = self.db()?;
            (d.queue_list(&user, Some(&company))?, d.feed_get(&user, &company)?, d.feed_counts(&user, &company)?)
        };
        let n = |f: &dyn Fn(&str) -> bool| rows.iter().filter(|r| f(&r.status)).count() as i64;
        let waiting = n(&|s| matches!(s, "waiting" | "preparing" | "verification_pending"));
        let uploading = n(&|s| s == "uploading");
        let failed = n(&|s| matches!(s, "failed" | "quarantined"));
        let open = match self.api.get(&format!("/companies/{company}/sync/status"), Some(org)).await {
            Ok(r) => r.body.get("open_conflicts").and_then(Value::as_i64),
            Err(e) => {
                self.classify(e);
                None
            }
        };
        let pulled_ok = feed.last_pull_at.is_some();
        let (state, label) = fold_state(waiting, uploading, failed, open, pulled_ok, feed.caught_up, feed.last_error.as_deref());
        Ok(SyncStatus {
            state: state.into(),
            label: label.into(),
            waiting_uploads: waiting,
            uploading,
            failed_uploads: failed,
            open_conflicts: open,
            last_pull_at: feed.last_pull_at,
            last_error: feed.last_error,
            caught_up: feed.caught_up,
            known_items: counts,
        })
    }

    /// One allow-listed conflict/preflight call. The server verifies every claim and decides every resolution.
    pub async fn sync_call(
        &self,
        org: &str,
        company: &str,
        op: &str,
        arg: Option<&str>,
        query: &[(String, String)],
        body: Value,
    ) -> Result<Value> {
        let Some((_, method, tail, takes_id)) = CONFLICT_OPS.iter().find(|o| o.0 == op) else {
            return Err(AppError::new("unknown_action", "Unknown synchronization action."));
        };
        let mut path = format!("/companies/{}/sync{tail}", uuid_ok(company)?);
        if *takes_id {
            path = path.replace("{0}", &uuid_ok(arg.ok_or_else(|| AppError::new("invalid_id", "That identifier is not valid."))?)?);
        } else if arg.is_some() {
            return Err(AppError::new("invalid_id", "That identifier is not valid."));
        }
        let mut q = Vec::new();
        for (k, v) in query {
            let ok_key = matches!(k.as_str(), "state" | "kind" | "limit");
            if !ok_key || v.len() > 40 || !v.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
                return Err(AppError::new("invalid_query", "That filter is not allowed."));
            }
            q.push(format!("{k}={v}"));
        }
        if !q.is_empty() {
            if *method != "GET" {
                return Err(AppError::new("invalid_query", "That filter is not allowed."));
            }
            path.push('?');
            path.push_str(&q.join("&"));
        }
        let res = if *method == "GET" { self.api.get(&path, Some(org)).await } else { self.api.post(&path, Some(org), Some(body)).await };
        let r = res.map_err(|e| self.classify(e))?;
        if *method != "GET" {
            self.emit("sync-changed", json!({ "company_id": company }));
        }
        Ok(r.body)
    }
}

fn parse_items(page: &Value) -> Result<Vec<FeedItem>> {
    let arr = page.get("items").and_then(Value::as_array).ok_or_else(|| AppError::malformed("sync feed"))?;
    arr.iter()
        .map(|i| {
            Ok(FeedItem {
                kind: i.get("kind").and_then(Value::as_str).ok_or_else(|| AppError::malformed("sync feed"))?.to_string(),
                id: i.get("id").and_then(Value::as_str).ok_or_else(|| AppError::malformed("sync feed"))?.to_string(),
                revision: i.get("revision").and_then(Value::as_i64).ok_or_else(|| AppError::malformed("sync feed"))?,
                status: i.get("status").and_then(Value::as_str).map(str::to_string),
                changed_at: i.get("changed_at").and_then(Value::as_str).map(str::to_string),
            })
        })
        .collect()
}

/// The cursor is URL-safe base64 already; encode defensively anyway.
fn urlenc(s: &str) -> String {
    s.bytes()
        .map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::fold_state;

    #[test]
    fn synchronized_needs_every_confirmation() {
        assert_eq!(fold_state(0, 0, 0, Some(0), true, true, None).0, "synchronized");
        assert_eq!(fold_state(0, 0, 0, None, true, true, None).0, "pending", "no answer from the server is not a confirmation");
        assert_eq!(fold_state(0, 0, 0, Some(0), false, false, None).0, "pending", "never pulled");
        assert_eq!(fold_state(0, 0, 0, Some(0), true, false, None).0, "pending", "feed not read to its end");
        assert_eq!(fold_state(1, 0, 0, Some(0), true, true, None).0, "pending");
        assert_eq!(fold_state(0, 1, 0, Some(0), true, true, None).0, "uploading");
        assert_eq!(fold_state(0, 0, 1, Some(0), true, true, None).0, "failed");
        assert_eq!(fold_state(0, 0, 0, Some(0), true, true, Some("network_unavailable")).0, "failed");
        assert_eq!(fold_state(0, 0, 1, Some(2), true, true, None).0, "conflict_detected", "conflicts outrank everything");
    }
}
