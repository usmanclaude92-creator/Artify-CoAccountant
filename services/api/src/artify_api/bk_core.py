"""Daily bookkeeping core (Phase 10): the deterministic, side-effect-free part of daily validation and closing.

Like `acct_core`, this module has no I/O, no framework and no clock. The service layer (`bookkeeping.py`) loads the day's facts
from the database, calls these functions and stores the outcome. Rules that hold throughout:

* Validation never repairs or guesses. A problem is reported with an exact code, a severity and the objects concerned.
* No country tax rule is assumed. Tax checks compare what the company configured with what was recorded; whether a rate is
  *right* is the accountant's call (Phase 8 marks the Oman tax pack as unverified, and this module adds nothing to that).
* Severity is policy: a company may raise or lower the severity of a check, except the checks in `HARD_CODES`, which protect
  the integrity of the books and can neither be lowered nor overridden.
* Money is `Decimal`; this module never sees a float.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from artify_api.acct_core import D

# ------------------------------------------------------------------ vocabulary
SEVERITIES: tuple[str, ...] = ("info", "warning", "error", "critical")
RESULTS: tuple[str, ...] = ("passed", "passed_with_warnings", "failed", "awaiting_review")
TASK_STATUSES: tuple[str, ...] = (
    "new",
    "assigned",
    "in_progress",
    "awaiting_information",
    "awaiting_review",
    "approved",
    "rejected",
    "completed",
    "cancelled",
)
TASK_PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")
ACTIONS: tuple[str, ...] = ("prepare", "review", "approve", "reopen", "override")
STAGES: tuple[str, ...] = (
    "prepare",
    "validate",
    "review_exceptions",
    "closing_summary",
    "submit_for_review",
    "approve_and_close",
)

TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"assigned", "in_progress", "cancelled"}),
    "assigned": frozenset({"in_progress", "awaiting_information", "cancelled", "new"}),
    "in_progress": frozenset({"awaiting_information", "awaiting_review", "completed", "cancelled"}),
    "awaiting_information": frozenset({"in_progress", "cancelled"}),
    "awaiting_review": frozenset({"approved", "rejected"}),
    "rejected": frozenset({"in_progress", "cancelled"}),
    "approved": frozenset({"completed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


class RuleError(ValueError):
    """A business rule refuses the requested step; `code` is stable for clients and tests."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ------------------------------------------------------------------ validation codes
@dataclass(frozen=True)
class CodeSpec:
    severity: str
    title: str
    hard: bool = False  # integrity checks: cannot be lowered by policy and cannot be overridden


CODES: dict[str, CodeSpec] = {
    "FUTURE_DATE": CodeSpec("critical", "The day has not ended yet", hard=True),
    "ACCOUNTING_NOT_SET_UP": CodeSpec("error", "Accounting has not been set up for this company"),
    "TXN_UNBALANCED": CodeSpec("critical", "An approved journal does not balance", hard=True),
    "TXN_NO_JOURNAL": CodeSpec("critical", "An approved transaction has no journal", hard=True),
    "TXN_TAX_MISMATCH": CodeSpec("error", "A transaction's tax total differs from the sum of its lines"),
    "TXN_BLOCKING_VALIDATION": CodeSpec("error", "A transaction has an unresolved blocking validation issue"),
    "TXN_AWAITING_APPROVAL": CodeSpec("error", "A transaction is waiting for review or approval"),
    "TXN_DRAFT_OPEN": CodeSpec("warning", "A draft transaction is still open"),
    "PERIOD_CLOSED": CodeSpec("critical", "The accounting period containing the day is closed", hard=True),
    "PERIOD_MISSING": CodeSpec("critical", "No accounting period covers the day", hard=True),
    "ACCOUNT_INACTIVE": CodeSpec("error", "A transaction uses an inactive account"),
    "TXN_NO_SOURCE_DOCUMENT": CodeSpec("warning", "A transaction that normally has a source document has none"),
    "TXN_DUPLICATE_EXACT": CodeSpec("error", "A transaction exactly matches another one"),
    "TXN_DUPLICATE_POSSIBLE": CodeSpec("warning", "A transaction may duplicate another one"),
    "TAX_CODE_INACTIVE": CodeSpec("error", "A transaction uses a tax code that is no longer active"),
    "TAX_CONFIG_MISSING": CodeSpec("warning", "Tax was recorded but the company has no tax configuration"),
    "TAX_CODE_UNVERIFIED": CodeSpec("warning", "Tax was recorded with a tax code nobody has verified"),
    "DOC_INFECTED": CodeSpec("critical", "A document failed the malware scan", hard=True),
    "DOC_QUARANTINED": CodeSpec("error", "A document is quarantined"),
    "DOC_UNPROCESSED": CodeSpec("warning", "Documents have not been processed yet"),
    "DOC_PROCESSING_FAILED": CodeSpec("error", "Document processing failed"),
    "DOC_NEEDS_MANUAL": CodeSpec("error", "Documents need manual data entry"),
    "DOC_AWAITING_REVIEW": CodeSpec("warning", "Extracted document data is waiting for review"),
    "SYNC_CONFLICTS": CodeSpec("error", "A device reports unresolved synchronisation conflicts"),
    "SYNC_FAILED": CodeSpec("error", "A device reports failed uploads"),
    "SYNC_PENDING": CodeSpec("warning", "A device reports work that has not synchronised yet"),
    "SYNC_NOT_REPORTED": CodeSpec("info", "No device has reported its synchronisation state for this day"),
    "CHECKLIST_MANDATORY_OPEN": CodeSpec("error", "A mandatory checklist item is not done"),
    "TASK_BLOCKING_OPEN": CodeSpec("error", "A task that blocks closing is still open"),
    "TASK_OVERDUE": CodeSpec("warning", "A task is overdue"),
    "DAY_EMPTY": CodeSpec("info", "No activity was recorded for this day"),
}
HARD_CODES: frozenset[str] = frozenset(k for k, v in CODES.items() if v.hard)

# automatic checklist rules: deterministic, evaluated from the day's facts, never from a name
AUTO_RULES: dict[str, str] = {
    "documents_processed": "Every document received today has been processed",
    "transactions_decided": "No transaction dated today is waiting for review or approval",
    "no_open_critical": "The day has no critical validation problem",
    "sync_clean": "No device reports failed uploads or conflicts for today",
}


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    message: str
    subject_type: str = "day"
    subject_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return f"{self.code}:{self.subject_type}:{self.subject_id or '-'}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "fingerprint": self.fingerprint,
            "data": self.data,
        }


def effective_severity(code: str, overrides: Mapping[str, str] | None) -> str:
    """The company's chosen severity for a check; integrity checks ignore the policy."""
    spec = CODES[code]
    if spec.hard:
        return spec.severity
    chosen = (overrides or {}).get(code)
    return chosen if chosen in SEVERITIES else spec.severity


def validate_severity_overrides(overrides: Mapping[str, Any]) -> dict[str, str]:
    """Reject unknown codes, unknown severities and any attempt to weaken an integrity check."""
    out: dict[str, str] = {}
    for code, sev in overrides.items():
        if code not in CODES:
            raise RuleError("unknown_check", f"Unknown validation check {code}.")
        if sev not in SEVERITIES:
            raise RuleError("unknown_severity", f"Unknown severity {sev}.")
        if code in HARD_CODES and sev != CODES[code].severity:
            raise RuleError("hard_check", f"{code} protects the books and its severity cannot be changed.")
        out[code] = sev
    return out


# ------------------------------------------------------------------ facts about one day
@dataclass
class DayFacts:
    business_date: date
    today: date  # "today" in the company's time zone
    accounting_configured: bool = True
    tax_configured: bool = False  # company is tax registered or has a jurisdiction
    txn_counts: dict[str, int] = field(default_factory=dict)  # status -> count (reversal entries excluded)
    awaiting: list[dict[str, Any]] = field(default_factory=list)  # [{id, number, status}]
    drafts: list[dict[str, Any]] = field(default_factory=list)
    blocking_validation: list[dict[str, Any]] = field(default_factory=list)  # [{id, number, codes}]
    duplicates: list[dict[str, Any]] = field(default_factory=list)  # [{id, number, level}]
    unbalanced: list[dict[str, Any]] = field(default_factory=list)  # [{id, number, debit, credit}]
    no_journal: list[dict[str, Any]] = field(default_factory=list)
    tax_mismatch: list[dict[str, Any]] = field(default_factory=list)  # [{id, number, header, lines}]
    inactive_tax: list[dict[str, Any]] = field(default_factory=list)
    unverified_tax: list[dict[str, Any]] = field(default_factory=list)
    tax_recorded: bool = False
    inactive_account: list[dict[str, Any]] = field(default_factory=list)  # [{id, number, account}]
    no_source_document: list[dict[str, Any]] = field(default_factory=list)  # [{id, number}]
    period_name: str | None = None
    period_closed: bool = False
    period_required: bool = False  # the company requires an open accounting period (Phase 8 `require_open_period`)
    has_period: bool = True
    docs: dict[str, int] = field(
        default_factory=dict
    )  # total/infected/quarantined/unprocessed/failed/needs_manual/awaiting_review
    sync_reports: list[dict[str, Any]] = field(default_factory=list)
    checklist_open: list[dict[str, Any]] = field(default_factory=list)  # mandatory items not done [{id, title, list}]
    blocking_tasks: list[dict[str, Any]] = field(default_factory=list)
    overdue_tasks: list[dict[str, Any]] = field(default_factory=list)

    def has_activity(self) -> bool:
        return bool(sum(self.txn_counts.values()) or self.docs.get("total", 0))


CAP = 50  # per-object issues are capped; the count is still reported


def validate_day(facts: DayFacts, overrides: Mapping[str, str] | None = None) -> list[Issue]:
    """All validation issues for one day, in a stable order. Pure: the same facts always give the same issues."""
    out: list[Issue] = []

    def add(code: str, message: str, subject_type: str = "day", subject_id: str | None = None, **data: Any) -> None:
        out.append(Issue(code, effective_severity(code, overrides), message, subject_type, subject_id, data))

    if facts.business_date > facts.today:
        add(
            "FUTURE_DATE",
            f"{facts.business_date.isoformat()} is after today ({facts.today.isoformat()}) in the company's time zone.",
        )
    if facts.period_closed:
        add(
            "PERIOD_CLOSED",
            f"The accounting period {facts.period_name or ''} that contains this day is closed. Reopen the period before closing the day.".replace(
                "  ", " "
            ),
        )
    if facts.period_required and not facts.has_period:
        add(
            "PERIOD_MISSING",
            "No accounting period covers this day and the company requires one. Create the period first.",
        )
    if not facts.accounting_configured:
        add(
            "ACCOUNTING_NOT_SET_UP",
            "Accounting has not been set up for this company, so transactions cannot be validated.",
        )

    for t in facts.unbalanced[:CAP]:
        add(
            "TXN_UNBALANCED",
            f"Journal of {t['number']} does not balance: debit {t['debit']} against credit {t['credit']}.",
            "transaction",
            t["id"],
            debit=t["debit"],
            credit=t["credit"],
        )
    for t in facts.no_journal[:CAP]:
        add("TXN_NO_JOURNAL", f"{t['number']} is approved but has no journal.", "transaction", t["id"])
    for t in facts.tax_mismatch[:CAP]:
        add(
            "TXN_TAX_MISMATCH",
            f"Tax on {t['number']} is {t['header']} but its lines add up to {t['lines']}.",
            "transaction",
            t["id"],
            header=t["header"],
            lines=t["lines"],
        )
    for t in facts.blocking_validation[:CAP]:
        add(
            "TXN_BLOCKING_VALIDATION",
            f"{t['number']} has unresolved blocking issues: {', '.join(t['codes'])}.",
            "transaction",
            t["id"],
            codes=t["codes"],
        )
    for t in facts.awaiting[:CAP]:
        add("TXN_AWAITING_APPROVAL", f"{t['number']} is {t['status'].replace('_', ' ')}.", "transaction", t["id"])
    for t in facts.drafts[:CAP]:
        add("TXN_DRAFT_OPEN", f"{t['number']} is still a draft.", "transaction", t["id"])
    for t in facts.inactive_account[:CAP]:
        add(
            "ACCOUNT_INACTIVE",
            f"{t['number']} uses the inactive account {t['account']}.",
            "transaction",
            t["id"],
            account=t["account"],
        )
    for t in facts.no_source_document[:CAP]:
        add("TXN_NO_SOURCE_DOCUMENT", f"{t['number']} has no source document linked.", "transaction", t["id"])
    for t in facts.duplicates[:CAP]:
        code = "TXN_DUPLICATE_EXACT" if t["level"] == "exact" else "TXN_DUPLICATE_POSSIBLE"
        add(code, f"{t['number']} has a {t['level']} duplicate match that nobody has decided.", "transaction", t["id"])
    for t in facts.inactive_tax[:CAP]:
        add("TAX_CODE_INACTIVE", f"{t['number']} uses the inactive tax code {t['tax_code']}.", "transaction", t["id"])
    if facts.tax_recorded and not facts.tax_configured:
        add(
            "TAX_CONFIG_MISSING",
            "Tax amounts were recorded but the company has no tax registration or jurisdiction set.",
        )
    for t in facts.unverified_tax[:CAP]:
        add(
            "TAX_CODE_UNVERIFIED",
            f"Tax code {t['tax_code']} is unverified and was used on {t['count']} transaction(s) today.",
            "tax_code",
            t["id"],
            count=t["count"],
        )

    d = facts.docs
    if d.get("infected"):
        add(
            "DOC_INFECTED",
            f"{d['infected']} document(s) failed the malware scan.",
            count=d["infected"],
        )
    if d.get("quarantined"):
        add(
            "DOC_QUARANTINED",
            f"{d['quarantined']} document(s) are quarantined.",
            count=d["quarantined"],
        )
    if d.get("unprocessed"):
        add(
            "DOC_UNPROCESSED",
            f"{d['unprocessed']} document(s) have not finished processing.",
            count=d["unprocessed"],
        )
    if d.get("failed"):
        add(
            "DOC_PROCESSING_FAILED",
            f"Processing failed for {d['failed']} document(s).",
            count=d["failed"],
        )
    if d.get("needs_manual"):
        add(
            "DOC_NEEDS_MANUAL",
            f"{d['needs_manual']} document(s) need manual data entry.",
            count=d["needs_manual"],
        )
    if d.get("awaiting_review"):
        add(
            "DOC_AWAITING_REVIEW",
            f"Extracted data of {d['awaiting_review']} document(s) is waiting for review.",
            count=d["awaiting_review"],
        )

    conflicts = sum(r.get("conflicts", 0) for r in facts.sync_reports)
    failed = sum(r.get("failed_uploads", 0) for r in facts.sync_reports)
    pending = sum(r.get("pending_uploads", 0) + r.get("pending_changes", 0) for r in facts.sync_reports)
    if conflicts:
        add(
            "SYNC_CONFLICTS",
            f"Devices report {conflicts} unresolved conflict(s). (Declared by the device.)",
            count=conflicts,
        )
    if failed:
        add("SYNC_FAILED", f"Devices report {failed} failed upload(s). (Declared by the device.)", count=failed)
    if pending:
        add(
            "SYNC_PENDING",
            f"Devices report {pending} item(s) not yet synchronised. (Declared by the device.)",
            count=pending,
        )
    if not facts.sync_reports:
        add("SYNC_NOT_REPORTED", "No device has reported its synchronisation state for this day.")

    for it in facts.checklist_open[:CAP]:
        add(
            "CHECKLIST_MANDATORY_OPEN",
            f"Checklist item '{it['title']}' ({it['list']}) is not done.",
            "checklist_item",
            it["id"],
        )
    for tk in facts.blocking_tasks[:CAP]:
        add(
            "TASK_BLOCKING_OPEN",
            f"Task '{tk['title']}' blocks closing and is {tk['status'].replace('_', ' ')}.",
            "task",
            tk["id"],
        )
    for tk in facts.overdue_tasks[:CAP]:
        add("TASK_OVERDUE", f"Task '{tk['title']}' was due {tk['due_date']}.", "task", tk["id"])

    if not facts.has_activity() and all(i.severity == "info" for i in out):
        add("DAY_EMPTY", "No transactions or documents were recorded for this day.")
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    return sorted(out, key=lambda i: (order[i.severity], i.code, i.subject_id or ""))


# ------------------------------------------------------------------ result
OPEN = "open"


def evaluate(issues: Iterable[Issue], statuses: Mapping[str, str] | None = None) -> tuple[str, dict[str, int]]:
    """(result, counts). `statuses` maps an issue fingerprint to its exception status.

    * an open critical issue            -> failed (nothing but fixing it, or an authorised override, helps)
    * an open error                     -> awaiting_review (a reviewer must fix, acknowledge or reject it)
    * an open warning, or any issue a person acknowledged or overrode -> passed_with_warnings
    * nothing else than info            -> passed
    """
    statuses = statuses or {}
    counts = {"critical": 0, "error": 0, "warning": 0, "info": 0, "acknowledged": 0, "overridden": 0}
    result = "passed"
    for i in issues:
        st = statuses.get(i.fingerprint, OPEN)
        if st == "overridden":
            counts["overridden"] += 1
            result = _worse(result, "passed_with_warnings")
            continue
        if st == "acknowledged" and i.severity in ("warning", "error"):
            counts["acknowledged"] += 1
            if i.severity == "error":
                result = _worse(result, "passed_with_warnings")
            continue
        counts[i.severity] += 1
        if i.severity == "critical":
            result = _worse(result, "failed")
        elif i.severity == "error":
            result = _worse(result, "awaiting_review")
        elif i.severity == "warning":
            result = _worse(result, "passed_with_warnings")
    return result, counts


_RANK = {"passed": 0, "passed_with_warnings": 1, "awaiting_review": 2, "failed": 3}


def _worse(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def can_resolve(issue_severity: str, code: str, action: str, *, note: str | None, min_chars: int) -> None:
    """Whether an exception may be `acknowledge`d or `override`n; raises RuleError otherwise."""
    text = (note or "").strip()
    if code in HARD_CODES:
        raise RuleError(
            "hard_check", f"{code} protects the books and cannot be acknowledged or overridden. Fix the cause."
        )
    if action == "acknowledge":
        if issue_severity not in ("warning", "error"):
            raise RuleError(
                "not_acknowledgeable", "Only warnings and errors can be acknowledged; critical issues need an override."
            )
        if issue_severity == "error" and len(text) < min_chars:
            raise RuleError(
                "explanation_required",
                f"Acknowledging an error needs an explanation of at least {min_chars} characters.",
            )
        return
    if action == "override":
        if issue_severity != "critical":
            raise RuleError(
                "not_overridable", "Only critical issues are overridden; acknowledge warnings and errors instead."
            )
        if len(text) < min_chars:
            raise RuleError(
                "explanation_required", f"An override needs an explanation of at least {min_chars} characters."
            )
        return
    raise RuleError("unknown_action", f"Unknown action {action}.")


# ------------------------------------------------------------------ automatic checklist rules
def auto_rule_met(rule: str, facts: DayFacts, issues: Sequence[Issue]) -> bool:
    if rule == "documents_processed":
        d = facts.docs
        return not (d.get("unprocessed") or d.get("failed") or d.get("needs_manual") or d.get("awaiting_review"))
    if rule == "transactions_decided":
        return not facts.awaiting and not facts.drafts
    if rule == "no_open_critical":
        return not any(i.severity == "critical" for i in issues)
    if rule == "sync_clean":
        return bool(facts.sync_reports) and not any(
            r.get("failed_uploads") or r.get("conflicts") for r in facts.sync_reports
        )
    raise RuleError("unknown_rule", f"Unknown automatic rule {rule}.")


def checklist_state(
    items: Sequence[Mapping[str, Any]], done_ids: set[str], auto: Callable[[str], bool]
) -> list[dict[str, Any]]:
    """Per-item state for a day: manual completion OR an automatic rule that is currently met."""
    out = []
    for it in items:
        manual = it["id"] in done_ids
        auto_ok = bool(it.get("auto_rule")) and auto(it["auto_rule"])
        out.append({**it, "done": manual or auto_ok, "done_by": "manual" if manual else ("auto" if auto_ok else None)})
    return out


# ------------------------------------------------------------------ tasks
def check_task_transition(
    current: str,
    target: str,
    *,
    requires_review: bool,
    actor: str,
    review_requested_by: str | None,
    independence_required: bool,
    has_review_perm: bool,
    is_assignee: bool,
    can_manage: bool,
) -> None:
    """Raises RuleError when this user may not move the task between these statuses."""
    if target not in TASK_TRANSITIONS.get(current, frozenset()):
        raise RuleError(
            "invalid_transition", f"A task cannot go from {current.replace('_', ' ')} to {target.replace('_', ' ')}."
        )
    if target == "completed" and current == "in_progress" and requires_review:
        raise RuleError("review_required", "This task needs a review before it can be completed.")
    if target == "awaiting_review" and not requires_review:
        raise RuleError("review_not_required", "This task does not need a review; complete it instead.")
    if target in ("approved", "rejected"):
        if not has_review_perm:
            raise RuleError("review_forbidden", "You do not have permission to review tasks.")
        if independence_required and review_requested_by == actor:
            raise RuleError("independence_required", "The person who asked for the review cannot approve or reject it.")
    elif target == "cancelled":
        if not can_manage:
            raise RuleError("cancel_forbidden", "Only a task manager can cancel a task.")
    elif target in ("assigned", "new"):
        if not can_manage:
            raise RuleError("assign_forbidden", "Only a task manager can assign a task.")
    elif not (is_assignee or can_manage):
        raise RuleError("not_your_task", "Only the person the task is assigned to, or a task manager, can work on it.")


# ------------------------------------------------------------------ closing
def check_submit(*, status: str, result: str, stale: bool) -> None:
    if status not in ("in_progress", "draft"):
        raise RuleError("invalid_status", f"A closing that is {status.replace('_', ' ')} cannot be submitted.")
    if stale:
        raise RuleError(
            "validation_stale", "The day changed after it was last validated. Validate it again before submitting."
        )
    if result == "failed":
        raise RuleError("critical_failure", "The day has an unresolved critical problem and cannot be submitted.")
    if result == "awaiting_review":
        raise RuleError("errors_open", "The day has errors that nobody has fixed or acknowledged yet.")


def check_close(
    *, status: str, result: str, submitted_by: str | None, closer: str, independence_required: bool
) -> None:
    if status != "ready_for_review":
        raise RuleError("invalid_status", "Only a closing that was submitted for review can be approved and closed.")
    if result == "failed":
        raise RuleError("critical_failure", "A critical validation failure blocks closing.")
    if result == "awaiting_review":
        raise RuleError("errors_open", "Errors are still open. Fix or acknowledge them before closing.")
    if independence_required and submitted_by == closer:
        raise RuleError("independence_required", "The person who submitted the closing cannot approve and close it.")


def check_reopen(*, status: str, reason: str | None, period_closed: bool) -> None:
    if status != "closed":
        raise RuleError("invalid_status", "Only a closed day can be reopened.")
    if len((reason or "").strip()) < 10:
        raise RuleError("reason_required", "Reopening needs a reason of at least 10 characters.")
    if period_closed:
        raise RuleError(
            "period_closed", "The accounting period containing this day is closed. Reopen the period first."
        )


def role_allows(action_roles: Mapping[str, Sequence[str]], action: str, user_roles: Iterable[str]) -> bool:
    """Per-company restriction on top of the permission: an empty or missing list means the permission alone decides."""
    allowed = action_roles.get(action) or []
    return not allowed or bool(set(allowed) & set(user_roles))


# ------------------------------------------------------------------ dates and time zones
def company_today(now_utc: datetime, tz: str) -> date:
    return business_date_of(now_utc, tz)


def business_date_of(moment: datetime, tz: str) -> date:
    """The calendar day of an instant in the company's own time zone (an invoice at 23:30 UTC may be tomorrow in Muscat)."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(zone).date()


# ------------------------------------------------------------------ cash and bank
@dataclass(frozen=True)
class CashRow:
    account_id: str
    code: str
    name: str
    account_type: str
    opening: Decimal
    receipts: Decimal
    payments: Decimal
    lines: int

    @property
    def closing(self) -> Decimal:
        return self.opening + self.receipts - self.payments


def cash_bank_summary(rows: Iterable[CashRow]) -> dict[str, Any]:
    """Daily movement per cash/bank account from approved journals. It is NOT a reconciliation: nothing is compared with a
    bank statement and the closing balance is the books' balance, not the bank's."""
    accounts = []
    tot = {"opening": Decimal(0), "receipts": Decimal(0), "payments": Decimal(0), "closing": Decimal(0)}
    for r in rows:
        accounts.append(
            {
                "account_id": r.account_id,
                "code": r.code,
                "name": r.name,
                "account_type": r.account_type,
                "opening": r.opening,
                "receipts": r.receipts,
                "payments": r.payments,
                "closing": r.closing,
                "lines": r.lines,
            }
        )
        tot["opening"] += r.opening
        tot["receipts"] += r.receipts
        tot["payments"] += r.payments
        tot["closing"] += r.closing
    return {"accounts": accounts, "totals": tot, "reconciled": False}


# ------------------------------------------------------------------ snapshots
def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return format(o, "f")
    if isinstance(o, date | datetime):
        return o.isoformat()
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False)


def payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ period readiness
def period_readiness(days: Sequence[Mapping[str, Any]], *, today: date, period_status: str | None) -> dict[str, Any]:
    """Whether every day that needs a closing has one. A day needs a closing when it has activity or a started closing.

    This is an overview, not a gate: it does not lock the period, prepare a VAT return or approve anything."""
    needing = [
        d
        for d in days
        if d["has_activity"] or d["closing_status"] in ("draft", "in_progress", "ready_for_review", "closed")
    ]
    unclosed = [d for d in needing if d["closing_status"] != "closed" and d["date"] <= today]
    future = [d for d in days if d["date"] > today]
    blockers = []
    if unclosed:
        blockers.append(
            {
                "code": "DAYS_NOT_CLOSED",
                "message": f"{len(unclosed)} day(s) with activity are not closed.",
                "dates": [str(d["date"]) for d in unclosed],
            }
        )
    open_ex = sum(d.get("open_exceptions", 0) for d in days)
    if open_ex:
        blockers.append(
            {"code": "OPEN_EXCEPTIONS", "message": f"{open_ex} validation exception(s) are still open.", "dates": []}
        )
    open_txn = sum(d.get("open_transactions", 0) for d in days)
    if open_txn:
        blockers.append(
            {"code": "OPEN_TRANSACTIONS", "message": f"{open_txn} transaction(s) are not approved.", "dates": []}
        )
    if future:
        blockers.append(
            {
                "code": "PERIOD_NOT_ENDED",
                "message": f"{len(future)} day(s) of the range have not happened yet.",
                "dates": [],
            }
        )
    return {
        "days": len(days),
        "days_needing_closing": len(needing),
        "days_closed": sum(1 for d in needing if d["closing_status"] == "closed"),
        "ready": not blockers,
        "blockers": blockers,
        "period_status": period_status,
        "note": "An overview only. It does not lock the period, prepare a VAT return or approve anything.",
    }


def money(value: Any) -> Decimal:
    return D(value if value is not None else 0)
