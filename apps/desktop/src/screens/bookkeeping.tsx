import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Panel, StatusChip, type Tone } from "@artify/ui";
import type { BkCashBank, BkChecklist, BkClosing, BkClosingExport, BkDay, BkDayReport, BkException, BkOp, BkReadiness, BkResult, BkSeverity, BkTask, BkTaskStatus, BkValidationPreview, XlTemplate } from "../bridge/types";
import { fmtDateTime } from "../lib/format";
import { useApp } from "../state/app";
import { money } from "./accounting";

type Call = <T,>(op: BkOp, args?: { ids?: string[]; query?: Record<string, string>; body?: unknown }) => Promise<T>;

const RESULT: Record<BkResult, { label: string; tone: Tone }> = {
  passed: { label: "Passed", tone: "success" }, passed_with_warnings: { label: "Passed with warnings", tone: "warning" },
  failed: { label: "Failed", tone: "danger" }, awaiting_review: { label: "Awaiting review", tone: "warning" },
};
const SEVERITY: Record<BkSeverity, { label: string; tone: Tone }> = { info: { label: "Note", tone: "info" }, warning: { label: "Warning", tone: "warning" }, error: { label: "Error", tone: "danger" }, critical: { label: "Critical", tone: "danger" } };
const CLOSING: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" }, in_progress: { label: "In progress", tone: "info" }, ready_for_review: { label: "Ready for review", tone: "warning" },
  closed: { label: "Closed", tone: "success" }, reopened: { label: "Reopened", tone: "warning" }, cancelled: { label: "Cancelled", tone: "neutral" },
};
const TASK_LABEL: Record<BkTaskStatus, string> = { new: "New", assigned: "Assigned", in_progress: "In progress", awaiting_information: "Awaiting information", awaiting_review: "Awaiting review", approved: "Approved", rejected: "Rejected", completed: "Completed", cancelled: "Cancelled" };
const STAGES = ["Prepare", "Validate", "Review exceptions", "Closing summary", "Submit for review", "Approve and close"];
const TABS: [string, string][] = [["today", "Today"], ["tasks", "Tasks"], ["checklists", "Checklists"], ["closing", "Closing"], ["reports", "Daily report"], ["readiness", "Period readiness"]];

/** The browser's calendar day; the company's own "today" (its time zone) comes back from the server and is shown beside it. */
function localToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function useBk() {
  const { bridge, org, company, fail } = useApp();
  const orgId = org?.id ?? "";
  const companyId = company?.id ?? "";
  const call: Call = useCallback((op, args) => bridge.bk(orgId, companyId, op, args), [bridge, orgId, companyId]);
  return { companyId, fail, call };
}

export function ResultChip({ result }: { result: BkResult | null }) {
  if (!result) return <StatusChip tone="neutral">Not validated</StatusChip>;
  return <StatusChip tone={RESULT[result].tone}>{RESULT[result].label}</StatusChip>;
}

// ------------------------------------------------------------------ page
export function BookkeepingHome() {
  const { companyId, call, fail } = useBk();
  const [tab, setTab] = useState("today");
  const [date, setDate] = useState(localToday());
  const [msg, setMsg] = useState<string | null>(null);
  const [version, setVersion] = useState(0); // bump to reload after a change
  const { bridge } = useApp();
  useEffect(() => { let off: (() => void) | undefined; void bridge.on("bookkeeping-changed", () => setVersion((v) => v + 1)).then((u) => { off = u; }); return () => off?.(); }, [bridge]);
  if (!companyId) return <section><h1>Daily Bookkeeping</h1><p>Choose a company first.</p></section>;
  const run = async <T,>(f: () => Promise<T>, ok?: string): Promise<T | null> => {
    try { const r = await f(); setMsg(ok ?? null); setVersion((v) => v + 1); return r; } catch (e) { fail(e); return null; } // errors show in the app-wide notice
  };
  return (
    <section>
      <h1>Daily Bookkeeping</h1>
      <p className="a-hint">Work through the day, validate it, and close it. A closed day can only change after someone with permission reopens it, with a reason. Nothing here posts to a tax authority or reconciles a bank.</p>
      <div className="d-inline">
        <label>Business day <input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} aria-label="Business day" /></label>
      </div>
      <div role="tablist" aria-label="Daily bookkeeping sections" className="d-tabs">
        {TABS.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className="d-tab" onClick={() => { setTab(k); setMsg(null); }}>{label}</button>)}
      </div>
      {msg ? <Alert tone="info">{msg}</Alert> : null}
      {tab === "today" ? <Today key={`${date}-${version}`} date={date} call={call} /> : tab === "tasks" ? <Tasks key={version} date={date} call={call} run={run} /> : tab === "checklists" ? <Checklists key={version} date={date} call={call} run={run} /> : tab === "closing" ? <Closing key={`${date}`} date={date} call={call} /> : tab === "reports" ? <DailyReport key={`${date}-${version}`} date={date} call={call} /> : <Readiness call={call} date={date} />}
    </section>
  );
}

function useLoad<T>(call: Call, op: BkOp, ids: string[] | undefined, query?: Record<string, string>): { data: T | null; error: string | null } {
  const { fail } = useBk();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = JSON.stringify([op, ids, query]);
  useEffect(() => {
    let live = true;
    call<T>(op, { ids, query }).then((r) => live && setData(r)).catch((e) => { const m = fail(e); if (live && m) setError(m); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [call, key]);
  return { data, error };
}

// ------------------------------------------------------------------ today: the workbench
function Today({ date, call }: { date: string; call: Call }) {
  const day = useLoad<BkDay>(call, "day", [date]);
  const preview = useLoad<BkValidationPreview>(call, "day_validation", [date]);
  const docs = useLoad<{ id: string; display_name: string; processing_status: string | null; scan_status: string | null }[]>(call, "day_documents", [date]);
  const txns = useLoad<{ id: string; number: string; txn_type: string; status: string; currency: string; gross_amount: string }[]>(call, "day_transactions", [date]);
  const d = day.data;
  if (day.error) return <Alert tone="danger">{day.error}</Alert>;
  if (!d) return <p role="status">Loading the day…</p>;
  const docTotal = d.documents.total ?? 0;
  return (
    <div>
      <Panel>
        <h2>{d.business_date}</h2>
        <p className="a-hint">Days are counted in the company's time zone ({d.timezone}). Today there is {d.today}. {d.is_future ? "This day has not happened yet." : ""}</p>
        <ul className="d-list" aria-label="Day overview">
          <li>Closing: {d.closing ? <StatusChip tone={CLOSING[d.closing.status]?.tone ?? "neutral"}>{CLOSING[d.closing.status]?.label ?? d.closing.status}</StatusChip> : <StatusChip tone="neutral">Not started</StatusChip>} {d.closing ? <ResultChip result={d.closing.validation_status} /> : null}</li>
          <li>Approved transactions not yet in an Excel export: <strong>{d.approved_not_exported}</strong></li>
          <li>Documents received: <strong>{docTotal}</strong>{d.documents.unprocessed ? <> — {d.documents.unprocessed} not processed yet</> : null}</li>
          <li>Transactions: {Object.keys(d.transactions).length ? Object.entries(d.transactions).map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`).join(", ") : "none"}</li>
          <li>Validation now: <ResultChip result={d.validation.result} /> ({d.validation.issue_count} finding{d.validation.issue_count === 1 ? "" : "s"})</li>
          <li>Checklist: {d.checklist.done} of {d.checklist.total} done{d.checklist.mandatory_open ? `, ${d.checklist.mandatory_open} required item(s) open` : ""}</li>
          <li>Your open tasks: {d.tasks_mine?.open ?? 0}{d.tasks_mine?.overdue ? `, ${d.tasks_mine.overdue} overdue` : ""}</li>
          <li>Devices that reported their synchronisation state: {d.sync_reports}</li>
        </ul>
        <p className="a-hint">{d.scope_note}</p>
        {!d.has_activity ? <p>Nothing was recorded for this day.</p> : null}
      </Panel>
      <Panel>
        <h2>What validation finds today</h2>
        {preview.data?.issues.length ? (
          <ul className="d-list" aria-label="Findings">{preview.data.issues.map((i) => <li key={i.fingerprint}><StatusChip tone={SEVERITY[i.severity].tone}>{SEVERITY[i.severity].label}</StatusChip> {i.message} {i.status && i.status !== "open" ? <em>({i.status})</em> : null}</li>)}</ul>
        ) : <p>No findings.</p>}
      </Panel>
      <Panel>
        <h2>Document queue</h2>
        {docs.data?.length ? (
          <table className="d-table"><thead><tr><th>Document</th><th>Processing</th><th>Scan</th></tr></thead>
            <tbody>{docs.data.map((x) => <tr key={x.id}><td>{x.display_name}</td><td>{(x.processing_status ?? "not started").replace(/_/g, " ")}</td><td>{(x.scan_status ?? "not scanned").replace(/_/g, " ")}</td></tr>)}</tbody></table>
        ) : <p>No documents in the folders you can see for this day.</p>}
      </Panel>
      <Panel>
        <h2>Transactions of the day</h2>
        {txns.data?.length ? (
          <table className="d-table"><thead><tr><th>Number</th><th>Type</th><th>Status</th><th>Gross</th></tr></thead>
            <tbody>{txns.data.map((t) => <tr key={t.id}><td>{t.number}</td><td>{t.txn_type.replace(/_/g, " ")}</td><td>{t.status.replace(/_/g, " ")}</td><td>{money(t.gross_amount)} {t.currency}</td></tr>)}</tbody></table>
        ) : <p>No transactions are dated on this day.</p>}
      </Panel>
    </div>
  );
}

// ------------------------------------------------------------------ tasks
type Run = <T>(f: () => Promise<T>, ok?: string) => Promise<T | null>;

function Tasks({ date, call, run }: { date: string; call: Call; run: Run }) {
  const { data } = useLoad<BkTask[]>(call, "tasks", undefined, { open_only: "true" });
  const [title, setTitle] = useState("");
  const [blocks, setBlocks] = useState(false);
  const [review, setReview] = useState(false);
  const create = () => run(() => call<BkTask>("task_create", { body: { title, business_date: date, blocks_closing: blocks, requires_review: review } }), "Task created.").then(() => setTitle(""));
  const move = (t: BkTask, status: BkTaskStatus) => {
    const needsReason = ["rejected", "cancelled", "awaiting_information"].includes(status);
    const reason = needsReason ? window.prompt(`Reason for “${TASK_LABEL[status]}”?`) : null;
    if (needsReason && !reason) return;
    void run(() => call<BkTask>("task_transition", { ids: [t.id], body: { status, reason } }), `Task is now ${TASK_LABEL[status].toLowerCase()}.`);
  };
  return (
    <div>
      <Panel>
        <h2>New task</h2>
        <label className="d-field">Title <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} /></label>
        <label><input type="checkbox" checked={blocks} onChange={(e) => setBlocks(e.target.checked)} /> Blocks closing the day</label>{" "}
        <label><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> Needs a review by someone else</label>{" "}
        <Button onClick={() => void create()} disabled={!title.trim()}>Create task</Button>
      </Panel>
      <Panel>
        <h2>Open tasks</h2>
        {!data ? <p role="status">Loading tasks…</p> : data.length === 0 ? <p>No open tasks.</p> : (
          <table className="d-table"><thead><tr><th>Task</th><th>Status</th><th>Assigned to</th><th>Due</th><th>Actions</th></tr></thead>
            <tbody>{data.map((t) => (
              <tr key={t.id}>
                <td>{t.title}{t.blocks_closing ? <> <StatusChip tone="warning">Blocks closing</StatusChip></> : null}</td>
                <td>{TASK_LABEL[t.status]}{t.overdue ? <> <StatusChip tone="danger">Overdue</StatusChip></> : null}</td>
                <td>{t.assignee_name ?? "Nobody yet"}</td><td>{t.due_date ?? "—"}</td>
                <td>
                  {t.status === "new" || t.status === "assigned" || t.status === "rejected" || t.status === "awaiting_information" ? <Button variant="secondary" className="a-btn--sm" onClick={() => move(t, "in_progress")}>Start</Button> : null}
                  {t.status === "in_progress" ? (t.requires_review ? <Button variant="secondary" className="a-btn--sm" onClick={() => move(t, "awaiting_review")}>Ask for review</Button> : <Button variant="secondary" className="a-btn--sm" onClick={() => move(t, "completed")}>Complete</Button>) : null}
                  {t.status === "awaiting_review" ? <><Button variant="secondary" className="a-btn--sm" onClick={() => move(t, "approved")}>Approve</Button> <Button variant="secondary" className="a-btn--sm" onClick={() => move(t, "rejected")}>Reject</Button></> : null}
                  {t.status === "approved" ? <Button variant="secondary" className="a-btn--sm" onClick={() => move(t, "completed")}>Complete</Button> : null}
                </td>
              </tr>))}</tbody></table>
        )}
      </Panel>
    </div>
  );
}

// ------------------------------------------------------------------ checklists
function Checklists({ date, call, run }: { date: string; call: Call; run: Run }) {
  const v = useLoad<BkValidationPreview>(call, "day_validation", [date]);
  const lists = v.data?.checklists ?? [];
  const copy = () => run(() => call<BkChecklist>("checklist_copy", { ids: ["daily_bookkeeping"] }), "Checklist copied. It is now this company's own and can be changed freely.");
  const tick = (id: string, done: boolean) => run(() => call(done ? "item_uncomplete" : "item_complete", { ids: [id], query: { business_date: date }, body: {} }));
  return (
    <div>
      <p className="a-hint">Checklists belong to this company. Copying a default template creates an independent list: changing it never affects another company, and changing the template never reaches it.</p>
      <Button variant="secondary" onClick={() => void copy()}>Copy the default “Daily bookkeeping” checklist</Button>
      {!v.data ? <p role="status">Loading checklists…</p> : lists.length === 0 ? <p>This company has no checklists yet.</p> : lists.map((l) => (
        <Panel key={l.id}>
          <h2>{l.name}</h2>
          <ul className="d-list" aria-label={l.name}>
            {l.items.map((i) => (
              <li key={i.id}>
                <label>
                  <input type="checkbox" checked={!!i.done} disabled={!!i.auto_rule} onChange={() => void tick(i.id, !!i.done)} /> {i.title}
                </label>{" "}
                {i.mandatory ? <StatusChip tone="warning">Required</StatusChip> : null}{" "}
                {i.auto_rule ? <StatusChip tone="info">Automatic{i.done ? " — met" : " — not met"}</StatusChip> : null}{" "}
                {i.assigned_role ? <span className="a-hint">for {i.assigned_role.replace(/_/g, " ")}</span> : null}
              </li>))}
          </ul>
        </Panel>))}
    </div>
  );
}

// ------------------------------------------------------------------ closing wizard
function Closing({ date, call }: { date: string; call: Call }) {
  const { fail } = useBk();
  const [closing, setClosing] = useState<BkClosing | null | undefined>(undefined);
  const [note, setNote] = useState("");
  const [summary, setSummary] = useState<{ validation: { result: BkResult } } | null>(null);
  const [reload, setReload] = useState(0);
  const fetchClosing = useCallback(async (): Promise<BkClosing | null> => {
    const all = await call<{ id: string; business_date: string; status: string }[]>("closings", { query: { date_from: date, date_to: date } });
    const live = all.find((c) => c.status !== "reopened" && c.status !== "cancelled");
    return live ? await call<BkClosing>("closing", { ids: [live.id] }) : null;
  }, [call, date]);
  useEffect(() => {
    let alive = true;
    fetchClosing().then((c) => alive && setClosing(c)).catch((e) => { fail(e); if (alive) setClosing(null); });
    return () => { alive = false; };
  }, [fetchClosing, fail, reload]);
  const load = async () => setReload((n) => n + 1);
  const act = async (op: BkOp, body?: unknown, ids?: string[]) => {
    try { await call(op, { ids: ids ?? (closing ? [closing.id] : undefined), body: body ?? {} }); setNote(""); await load(); } catch (e) { fail(e); }
  };
  if (closing === undefined) return <p role="status">Loading the closing…</p>;
  const stage = closing?.stage ?? 0;
  return (
    <div>
      <ol className="d-list" aria-label="Closing stages">
        {STAGES.map((s, n) => <li key={s} aria-current={stage === n + 1 ? "step" : undefined}>{n + 1}. {s}{stage === n + 1 ? " (current)" : stage > n + 1 ? " — done" : ""}</li>)}
      </ol>
      {!closing ? (
        <Panel><p>No closing has been started for {date}.</p><Button onClick={() => void act("closing_create", { business_date: date }, [])}>Start closing for {date}</Button></Panel>
      ) : (
        <Panel>
          <h2>Closing for {closing.business_date} <StatusChip tone={CLOSING[closing.status]?.tone ?? "neutral"}>{CLOSING[closing.status]?.label ?? closing.status}</StatusChip> <ResultChip result={closing.validation_status} /></h2>
          {closing.stale ? <Alert tone="warning">The day changed after it was last validated. Validate it again before submitting.</Alert> : null}
          {closing.review_note ? <Alert tone="warning">Sent back: {closing.review_note}</Alert> : null}
          {closing.reopened_from_id ? <p className="a-hint">This closing replaces an earlier one that was reopened. The earlier closing and its snapshot are kept.</p> : null}
          <Exceptions list={closing.exceptions} editable={closing.status === "draft" || closing.status === "in_progress"} act={act} />
          {closing.status === "draft" || closing.status === "in_progress" ? (
            <div>
              <Button onClick={() => void act("closing_validate")}>Validate day</Button>{" "}
              <Button variant="secondary" disabled={closing.validation_status === null} onClick={() => void call<{ validation: { result: BkResult } }>("closing_summary", { ids: [closing.id] }).then(setSummary).then(() => load())}>Show closing summary</Button>{" "}
              <Button disabled={closing.validation_status === null} onClick={() => void act("closing_submit")}>Submit for review</Button>
              {summary ? <p role="status">Summary prepared: validation result is {RESULT[summary.validation.result].label.toLowerCase()}.</p> : null}
            </div>
          ) : null}
          {closing.status === "ready_for_review" ? (
            <div>
              <p>Submitted by {closing.submitted_by_name}. {closing.independence_required ? "A different person must approve and close it." : "This company allows the same person to approve."}</p>
              <label className="d-field">Reason for sending back <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
              <Button onClick={() => void act("closing_close")}>Approve and close</Button>{" "}
              <Button variant="secondary" disabled={note.trim().length < 5} onClick={() => void act("closing_send_back", { note })}>Send back</Button>
            </div>
          ) : null}
          {closing.status === "closed" ? (
            <div>
              <p>Closed by {closing.closed_by_name} {closing.closed_at ? fmtDateTime(closing.closed_at) : ""}. {closing.snapshot ? <>Snapshot #{closing.snapshot.snapshot_no}, fingerprint <code>{closing.snapshot.sha256.slice(0, 12)}…</code>.</> : null}</p>
              <label className="d-field">Reason for reopening <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
              <Button variant="secondary" disabled={note.trim().length < 10} onClick={() => void act("closing_reopen", { reason: note })}>Reopen this day</Button>
            </div>
          ) : null}
          {closing.status === "draft" || closing.status === "in_progress" ? (
            <Button variant="secondary" onClick={() => { const reason = window.prompt("Why is this closing cancelled?"); if (reason) void act("closing_cancel", { reason }); }}>Cancel closing</Button>
          ) : null}
          <ClosingReports closing={closing} act={act} />
          <h3>History</h3>
          <ul className="d-list" aria-label="Closing history">{closing.approvals.map((a, n) => <li key={n}>{a.action.replace(/_/g, " ")} — {a.actor_name ?? "system"}{a.note ? `: ${a.note}` : ""}</li>)}</ul>
        </Panel>
      )}
    </div>
  );
}

/** Daily Excel reports for the closing's day, generated by the Excel engine (Phase 9) from the company's own templates. */
function ClosingReports({ closing, act }: { closing: BkClosing; act: (op: BkOp, body?: unknown, ids?: string[]) => Promise<void> }) {
  const { bridge, org, company, fail } = useApp();
  const [templates, setTemplates] = useState<XlTemplate[] | null>(null);
  const [tpl, setTpl] = useState("");
  const [again, setAgain] = useState(false);
  const [reason, setReason] = useState("");
  useEffect(() => {
    let live = true;
    bridge.xl<XlTemplate[]>(org?.id ?? "", company?.id ?? "", "templates").then((t) => { if (live) setTemplates(t.filter((x) => x.status === "active")); }).catch((e) => { fail(e); });
    return () => { live = false; };
  }, [bridge, org, company, fail]);
  const make = () => act("closing_export_create", { template_id: tpl, idempotency_key: `${closing.id}-${tpl}-${Date.now()}`, acknowledge_repeat: again, reason: again ? reason : null });
  return (
    <div>
      <h3>Daily Excel report</h3>
      <p className="a-hint">Uses approved data only. Creating the same report again must be confirmed and explained.</p>
      {templates && templates.length === 0 ? <p>This company has no active Excel template yet. Add one in “Excel &amp; Reports”.</p> : (
        <div className="d-inline">
          <label>Template <select value={tpl} onChange={(e) => setTpl(e.target.value)}><option value="">Choose a template</option>{(templates ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>{" "}
          <label><input type="checkbox" checked={again} onChange={(e) => setAgain(e.target.checked)} /> This is a repeat</label>{" "}
          {again ? <label>Reason <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} /></label> : null}{" "}
          <Button variant="secondary" disabled={!tpl || (again && !reason.trim())} onClick={() => void make()}>Create Excel report</Button>
        </div>
      )}
      {closing.exports.length ? (
        <ul className="d-list" aria-label="Daily reports">{closing.exports.map((x: BkClosingExport) => <li key={x.id}>{x.template_name} (template v{x.template_version}, mapping {x.mapping_no}) — {x.status}{x.include_unapproved ? " — includes unapproved items" : " — approved only"}{x.repeat_acknowledged ? " — repeat" : ""}</li>)}</ul>
      ) : null}
    </div>
  );
}

function Exceptions({ list, editable, act }: { list: BkException[]; editable: boolean; act: (op: BkOp, body?: unknown, ids?: string[]) => Promise<void> }) {
  const { openAcct } = useApp();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const shown = list.filter((e) => e.status !== "resolved");
  if (!shown.length) return <p>No exceptions.</p>;
  return (
    <ul className="d-list" aria-label="Exceptions">
      {shown.map((e) => (
        <li key={e.id}>
          <StatusChip tone={SEVERITY[e.severity].tone}>{SEVERITY[e.severity].label}</StatusChip> {e.message} <em>({e.status})</em>
          {e.subject_type === "transaction" && e.subject_id ? <> <Button variant="secondary" className="a-btn--sm" onClick={() => openAcct(e.subject_id as string)}>Open transaction</Button></> : null}
          {editable && e.status === "open" && e.severity !== "info" ? (
            <div>
              <label className="d-field">Explanation for “{e.code}” <input value={notes[e.id] ?? ""} onChange={(ev) => setNotes({ ...notes, [e.id]: ev.target.value })} maxLength={500} /></label>
              {e.severity === "critical"
                ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act("exception_override", { note: notes[e.id] ?? "" }, [e.id])}>Override</Button>
                : <Button variant="secondary" className="a-btn--sm" onClick={() => void act("exception_acknowledge", { note: notes[e.id] ?? "" }, [e.id])}>Acknowledge</Button>}
            </div>
          ) : e.resolution_note ? <div className="a-hint">Explained: {e.resolution_note}</div> : null}
        </li>))}
    </ul>
  );
}

// ------------------------------------------------------------------ daily report, cash and bank
function DailyReport({ date, call }: { date: string; call: Call }) {
  const r = useLoad<BkDayReport>(call, "day_report", [date]);
  const cb = useLoad<BkCashBank>(call, "day_cash_bank", [date]);
  if (r.error) return <Alert tone="danger">{r.error}</Alert>;
  if (!r.data) return <p role="status">Loading the report…</p>;
  return (
    <div>
      <Panel>
        <h2>Approved transactions</h2>
        <p className="a-hint">{r.data.transactions.basis}</p>
        {r.data.transactions.journal_totals ? <p>Journal totals of the day (base currency): debit {money(r.data.transactions.journal_totals.debit)}, credit {money(r.data.transactions.journal_totals.credit)} — {r.data.transactions.journal_totals.journals} journal(s).</p> : null}
        {r.data.transactions.approved_totals.length ? (
          <table className="d-table"><thead><tr><th>Type</th><th>Count</th><th>Net</th><th>Tax</th><th>Gross</th></tr></thead>
            <tbody>{r.data.transactions.approved_totals.map((t) => <tr key={`${t.txn_type}-${t.currency}`}><td>{t.txn_type.replace(/_/g, " ")}</td><td>{t.n}</td><td>{money(t.net)} {t.currency}</td><td>{money(t.tax)}</td><td>{money(t.gross)}</td></tr>)}</tbody></table>
        ) : <p>No approved transactions on this day.</p>}
      </Panel>
      <Panel>
        <h2>Cash and bank</h2>
        {cb.data ? (<>
          <p className="a-hint">{cb.data.note}</p>
          {cb.data.unapproved_cash_bank_transactions ? <Alert tone="warning">{cb.data.unapproved_cash_bank_transactions} cash or bank transaction(s) are not approved yet and are not in these figures.</Alert> : null}
          {cb.data.accounts.length ? (
            <table className="d-table"><thead><tr><th>Account</th><th>Opening</th><th>Receipts</th><th>Payments</th><th>Closing (books)</th></tr></thead>
              <tbody>{cb.data.accounts.map((a) => <tr key={a.account_id}><td>{a.code} {a.name}</td><td>{money(a.opening)}</td><td>{money(a.receipts)}</td><td>{money(a.payments)}</td><td>{money(a.closing)}</td></tr>)}</tbody></table>
          ) : <p>No cash or bank accounts are set up.</p>}
        </>) : <p role="status">Loading…</p>}
      </Panel>
      <p className="a-hint">Excel versions of this report are created from the closing, using your own templates, in the “Excel &amp; Reports” section.</p>
    </div>
  );
}

// ------------------------------------------------------------------ period readiness
function Readiness({ call, date }: { call: Call; date: string }) {
  const month = date.slice(0, 7);
  const [from, setFrom] = useState(`${month}-01`);
  const [to, setTo] = useState(date);
  const r = useLoad<BkReadiness>(call, "readiness", undefined, { date_from: from, date_to: to });
  return (
    <div>
      <div className="d-inline">
        <label>From <input type="date" value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>{" "}
        <label>To <input type="date" value={to} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
      </div>
      {!r.data ? (r.error ? <Alert tone="danger">{r.error}</Alert> : <p role="status">Checking…</p>) : (
        <Panel>
          <h2>{r.data.ready ? <StatusChip tone="success">Ready for period end</StatusChip> : <StatusChip tone="warning">Not ready</StatusChip>}</h2>
          <p>{r.data.days_closed} of {r.data.days_needing_closing} day(s) with activity are closed.</p>
          <ul className="d-list" aria-label="Blockers">{r.data.blockers.map((b) => <li key={b.code}>{b.message}{b.dates.length ? ` ${b.dates.join(", ")}` : ""}</li>)}</ul>
          {r.data.attention ? <p>Worth a look before period end: {r.data.attention.approved_not_exported} approved transaction(s) not yet exported, {r.data.attention.documents_not_processed} document(s) not fully processed, {r.data.attention.without_source_document} transaction(s) without a source document.</p> : null}
          <p className="a-hint">{r.data.note}</p>
        </Panel>
      )}
    </div>
  );
}
