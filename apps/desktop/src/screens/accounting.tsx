import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Panel, StatusChip, type Tone } from "@artify/ui";
import type { AcctAccount, AcctContact, AcctDuplicate, AcctIssue, AcctOp, AcctTaxCode, AcctTxn, AcctTxnRow, TrialBalance } from "../bridge/types";
import { fmtDateTime } from "../lib/format";
import { useApp } from "../state/app";

/** Display only: trailing zeros beyond two decimals are hidden ("105.000000" -> "105.00", "2.899000" -> "2.899"). No arithmetic. */
export function money(v: string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  if (!v.includes(".")) return `${v}.00`;
  const [i, f = ""] = v.split(".");
  const trimmed = f.replace(/0+$/, "");
  return `${i}.${trimmed.padEnd(2, "0")}`;
}

const STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" }, awaiting_review: { label: "Awaiting review", tone: "warning" }, awaiting_approval: { label: "Awaiting approval", tone: "warning" },
  approved: { label: "Approved", tone: "success" }, rejected: { label: "Rejected", tone: "danger" }, posted: { label: "Posted", tone: "success" }, reversed: { label: "Reversed", tone: "neutral" },
};
const ACTION_LABEL: Record<string, string> = { create: "Created", submit: "Submitted", review_approve: "Reviewed", approve: "Approved", reject: "Rejected", request_correction: "Correction requested", revise: "Reopened for correction", reverse: "Reversed", amend: "Amended", override: "Differences accepted" };
const TYPES: [string, string][] = [
  ["sales_invoice", "Sales invoice"], ["sales_credit_note", "Sales credit note"], ["customer_receipt", "Customer receipt"], ["customer_refund", "Customer refund"],
  ["purchase_invoice", "Purchase invoice"], ["purchase_credit_note", "Purchase credit note"], ["supplier_payment", "Supplier payment"], ["supplier_refund", "Supplier refund"],
  ["cash_expense", "Cash expense"], ["bank_expense", "Bank expense"], ["employee_expense", "Employee expense"], ["general_expense", "General expense"],
  ["bank_deposit", "Bank deposit"], ["bank_withdrawal", "Bank withdrawal"], ["bank_charges", "Bank charges"], ["bank_transfer", "Bank transfer"],
];
const TABS: [string, string][] = [["awaiting_approval", "Awaiting approval"], ["awaiting_review", "Awaiting review"], ["draft", "Drafts"], ["approved", "Approved"], ["rejected", "Rejected"]];
const SOURCE_LABEL: Record<string, string> = { generated: "Generated", manual: "Edited by you", rounding: "Rounding difference" };
const MATCH_LABEL: Record<string, string> = { tax_number: "Same tax registration number", name_exact: "Same name", name_similar: "Similar name — check carefully" };

function useCo() {
  const { bridge, org, company, fail, go, acctTxn, openAcct } = useApp();
  return { bridge, orgId: org?.id ?? "", companyId: company?.id ?? "", fail, go, acctTxn, openAcct };
}

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <StatusChip tone={s.tone}>{s.label}</StatusChip>;
}

// ------------------------------------------------------------------ list
export function AccountingHome() {
  const { bridge, orgId, companyId, fail, go, openAcct } = useCo();
  const [tab, setTab] = useState("awaiting_approval");
  const [rows, setRows] = useState<AcctTxnRow[] | null>(null);
  const [tb, setTb] = useState<TrialBalance | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [setup, setSetup] = useState<boolean | null>(null);

  useEffect(() => {
    if (!companyId) return;
    bridge.acct<{ initialized: boolean }>(orgId, companyId, "config").then((c) => setSetup(c.initialized)).catch((e) => { const m = fail(e); if (m) setMsg(m); });
  }, [bridge, orgId, companyId, fail]);
  const load = useCallback(() => {
    if (!companyId || setup === false) return;
    if (tab === "trial") {
      bridge.acct<TrialBalance>(orgId, companyId, "trial_balance").then(setTb).catch((e) => { const m = fail(e); if (m) setMsg(m); });
      return;
    }
    bridge.acct<{ items: AcctTxnRow[]; total: number }>(orgId, companyId, "transactions", { query: { status: tab } }).then((r) => { setRows(r.items); setCounts((c) => ({ ...c, [tab]: r.total })); }).catch((e) => { const m = fail(e); if (m) setMsg(m); });
  }, [bridge, orgId, companyId, tab, setup, fail]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const off = bridge.on("accounting-changed", load);
    return () => { void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, load]);

  if (!companyId) return <section className="d-page"><h1>Accounting</h1><Panel><p>Open a company first.</p><Button onClick={() => go("companies")}>Choose a company</Button></Panel></section>;
  return (
    <section className="d-page" aria-labelledby="h-acct">
      <h1 id="h-acct">Accounting</h1>
      <p className="a-hint">Transactions prepared from approved documents. Amounts, tax and journals are calculated and checked by the server; nothing is approved until an authorised person approves it. Posting and exports arrive in a later release.</p>
      {msg ? <Alert tone="danger">{msg}</Alert> : null}
      {setup === false ? <Alert tone="warning">Accounting has not been set up for this company. Ask an administrator to set up its accounting profile and chart of accounts in the Control Center or through the API.</Alert> : null}
      <div className="d-tabs" role="tablist" aria-label="Transaction status">
        {TABS.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className="d-tab" onClick={() => setTab(k)}>{label}{counts[k] !== undefined ? ` (${counts[k]})` : ""}</button>)}
        <button type="button" role="tab" aria-selected={tab === "trial"} className="d-tab" onClick={() => setTab("trial")}>Trial balance</button>
      </div>
      {tab === "trial" ? (
        tb === null ? <p role="status">Loading…</p> : tb.rows.length === 0 ? <Panel><p>No approved journals yet, so there are no balances to show.</p></Panel> : (
          <table className="d-table">
            <caption className="a-visually-hidden">Trial balance of approved journals</caption>
            <thead><tr><th scope="col">Account</th><th scope="col">Debit</th><th scope="col">Credit</th></tr></thead>
            <tbody>{tb.rows.map((r) => <tr key={r.account_id}><th scope="row">{r.code} · {r.name}</th><td>{Number(r.debit) ? money(r.debit) : ""}</td><td>{Number(r.credit) ? money(r.credit) : ""}</td></tr>)}</tbody>
            <tfoot><tr><th scope="row">Total {tb.balanced ? "(balanced)" : "(NOT balanced)"}</th><td>{money(tb.debit)}</td><td>{money(tb.credit)}</td></tr></tfoot>
          </table>)
      ) : rows === null ? <p role="status">Loading…</p> : rows.length === 0 ? <Panel><p>No transactions here. Approve a document’s extracted data, then choose “Prepare accounting transaction” on the review screen.</p></Panel> : (
        <table className="d-table">
          <caption className="a-visually-hidden">Accounting transactions</caption>
          <thead><tr><th scope="col">Number</th><th scope="col">Type</th><th scope="col">Date</th><th scope="col">Party / document</th><th scope="col">Amount</th><th scope="col">Status</th><th scope="col">Checks</th><th scope="col"><span className="a-visually-hidden">Open</span></th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}><th scope="row">{r.number}</th><td>{r.type_label}</td><td>{r.transaction_date}</td><td>{r.contact_name ?? r.document_name ?? r.doc_number ?? "—"}</td><td>{money(r.gross_amount)} {r.currency}</td>
              <td><StatusBadge status={r.status} /></td>
              <td>{r.blocking_count > 0 ? <StatusChip tone="danger">{r.blocking_count} blocking</StatusChip> : r.duplicate_status !== "none" ? <StatusChip tone="warning">Possible duplicate</StatusChip> : <span className="a-hint">None</span>}</td>
              <td><Button className="a-btn--sm" onClick={() => openAcct(r.id)}>Review</Button></td></tr>))}</tbody>
        </table>)}
    </section>
  );
}

// ------------------------------------------------------------------ review
interface LineDraft { description: string; amount: string; tax_code_id: string; account_id: string; tax_amount: string | null }
interface JDraft { account_id: string; debit: string; credit: string }

function severityLabel(i: AcctIssue): string {
  return i.blocking ? "Blocks approval" : i.severity === "error" ? "Needs your acknowledgement" : i.severity === "warning" ? "Warning" : "Note";
}

export function AccountingReview() {
  const { bridge, orgId, companyId, fail, go, acctTxn } = useCo();
  const [t, setT] = useState<AcctTxn | null>(null);
  const [accounts, setAccounts] = useState<AcctAccount[]>([]);
  const [taxCodes, setTaxCodes] = useState<AcctTaxCode[]>([]);
  const [contacts, setContacts] = useState<AcctContact[]>([]);
  const [msg, setMsg] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<LineDraft[] | null>(null);
  const [jEdit, setJEdit] = useState<JDraft[] | null>(null);
  const [header, setHeader] = useState<{ txn_type: string; transaction_date: string; doc_number: string; narration: string; exchange_rate: string; settlement_account_id: string } | null>(null);
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);
  const [revDate, setRevDate] = useState("");
  const [image, setImage] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [dupNote, setDupNote] = useState("");

  const apply = useCallback((x: AcctTxn) => {
    setT(x);
    setLines(x.lines.map((l) => {
      const input = ((l.calc as { input?: { amount?: string; tax_amount?: string } }).input) ?? {};
      return { description: l.description ?? "", amount: input.amount ?? l.net_amount, tax_code_id: l.tax_code_id ?? "", account_id: l.account_id ?? "", tax_amount: input.tax_amount ?? null };
    }));
    setHeader({ txn_type: x.txn_type, transaction_date: x.transaction_date, doc_number: x.doc_number ?? "", narration: x.narration ?? "", exchange_rate: x.exchange_rate ?? "", settlement_account_id: x.settlement_account_id ?? "" });
    setJEdit(null);
  }, []);
  const call = useCallback(<R,>(op: AcctOp, args?: { ids?: string[]; query?: Record<string, string>; body?: unknown }) => bridge.acct<R>(orgId, companyId, op, args), [bridge, orgId, companyId]);

  const load = useCallback(() => {
    if (!acctTxn) return Promise.resolve();
    return call<AcctTxn>("transaction", { ids: [acctTxn] }).then(apply).catch((e) => { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); });
  }, [acctTxn, call, apply, fail]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!companyId) return;
    call<{ items: AcctAccount[] }>("accounts").then((r) => setAccounts(r.items)).catch(() => undefined);
    call<{ items: AcctTaxCode[] }>("tax_codes").then((r) => setTaxCodes(r.items)).catch(() => undefined);
    call<{ items: AcctContact[] }>("contacts").then((r) => setContacts(r.items)).catch(() => undefined);
  }, [companyId, call]);
  const docId = t?.source_document_id ?? null;
  useEffect(() => {
    if (!docId) return;
    bridge.intelPageImage(orgId, companyId, docId, page).then((im) => setImage(`data:image/png;base64,${im.data_base64}`)).catch(() => setImage(null));
  }, [bridge, orgId, companyId, docId, page]);

  const run = async (okText: string, op: AcctOp, body?: unknown) => {
    if (!t) return;
    setBusy(true); setMsg(null);
    try { apply(await call<AcctTxn>(op, { ids: [t.id], body })); if (okText) setMsg({ tone: "success", text: okText }); }
    catch (e) { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); }
    finally { setBusy(false); }
  };

  if (!acctTxn) return <section className="d-page"><h1>Review transaction</h1><Panel><p>Choose a transaction from Accounting.</p><Button onClick={() => go("accounting")}>Accounting</Button></Panel></section>;
  if (!t || !lines || !header) return <section className="d-page"><p role="status">{msg ? msg.text : "Loading…"}</p></section>;

  const editable = t.can.edit;
  const soft = t.issues.filter((i) => i.severity === "error" && !i.blocking);
  const bankLike = accounts.filter((a) => a.is_active && (a.account_type === "bank" || a.account_type === "cash"));
  const postable = accounts.filter((a) => a.is_active && a.is_postable);
  const kind = t.contact?.kind ?? (["sales_invoice", "sales_credit_note", "customer_receipt", "customer_refund"].includes(t.txn_type) ? "customer" : "supplier");
  const partyOptions = contacts.filter((c) => c.is_active && c.kind === kind);
  const stateOf = (i: number, k: keyof LineDraft, v: string) => setLines(lines.map((l, n) => (n === i ? { ...l, [k]: v } : l)));
  const saveLines = () => run("Lines saved. The server recalculated the amounts and the journal.", "txn_lines", {
    lines: lines.map((l) => ({ description: l.description || undefined, amount: l.amount || "0", tax_code_id: l.tax_code_id || undefined, account_id: l.account_id || undefined, tax_amount: l.tax_amount ?? undefined })),
  });
  const saveHeader = () => run("Details saved.", "txn_update", {
    txn_type: header.txn_type, transaction_date: header.transaction_date, doc_number: header.doc_number || null, narration: header.narration || null,
    ...(t.currency !== "OMR" && header.exchange_rate ? { exchange_rate: header.exchange_rate } : {}), settlement_account_id: header.settlement_account_id || null, base_row_version: t.row_version,
  });
  const decideDup = (d: AcctDuplicate, decision: "not_duplicate" | "duplicate") => {
    setBusy(true);
    call<AcctTxn>("txn_duplicate", { ids: [t.id, d.id], body: { decision, note: dupNote || undefined } }).then((x) => { apply(x); setDupNote(""); }).catch((e) => { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); }).finally(() => setBusy(false));
  };

  return (
    <section className="d-page" aria-labelledby="h-txn">
      <div className="d-actions"><Button variant="secondary" className="a-btn--sm" onClick={() => go("accounting")}>← Accounting</Button></div>
      <h1 id="h-txn">{t.number} · {t.type_label}</h1>
      <p>
        <StatusBadge status={t.status} />{" "}
        <StatusChip tone={t.balanced ? "success" : "danger"}>{t.balanced ? "Journal balanced" : "Journal NOT balanced"}</StatusChip>{" "}
        {t.blocking_count > 0 ? <StatusChip tone="danger">{t.blocking_count} item{t.blocking_count === 1 ? "" : "s"} block approval</StatusChip> : <StatusChip tone="success">No blocking items</StatusChip>}
        {t.journal_mode === "manual" ? <> <StatusChip tone="info">Journal edited by hand</StatusChip></> : null}
      </p>
      {msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}
      {t.correction_note ? <Alert tone="warning">Correction requested: {t.correction_note}</Alert> : null}
      {t.status === "rejected" ? <Alert tone="danger">Rejected: {t.rejection_reason}</Alert> : null}
      {t.status === "approved" ? <Alert tone="success">Approved{t.people.approved_by ? ` by ${t.people.approved_by}` : ""}. It can no longer be edited; corrections are made by reversal or amendment.</Alert> : null}
      <p className="a-hint">Suggested accounts and amounts come from the company’s configuration and the approved document. Review every line; the approval is yours.</p>

      <div className="d-review">
        <div className="d-viewer" role="region" aria-label="Source document">
          <div className="d-viewer__bar">
            <strong>{t.document ? t.document.display_name : "No source document"}</strong>
            {t.document ? <>
              <Button variant="secondary" className="a-btn--sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Previous page">‹</Button><span>Page {page}</span>
              <Button variant="secondary" className="a-btn--sm" onClick={() => setPage((p) => p + 1)} aria-label="Next page">›</Button></> : null}
          </div>
          <div className="d-viewer__stage">{t.document ? (image ? <img src={image} alt={`Page ${page} of the source document`} style={{ maxInlineSize: "100%" }} /> : <p className="a-hint">The page image is not available for this document.</p>) : <p className="a-hint">This transaction was prepared by hand.</p>}</div>
        </div>

        <div className="d-review__side">
          <h2>Details</h2>
          <div className="d-form">
            <label className="d-inline">Type<select aria-label="Transaction type" value={header.txn_type} disabled={!editable} onChange={(e) => setHeader({ ...header, txn_type: e.target.value })}>
              {TYPES.some(([k]) => k === header.txn_type) ? null : <option value={header.txn_type}>{t.type_label}</option>}{TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="d-inline">Date<input aria-label="Transaction date" value={header.transaction_date} disabled={!editable} onChange={(e) => setHeader({ ...header, transaction_date: e.target.value })} /></label>
            <label className="d-inline">Document number<input aria-label="Document number" value={header.doc_number} disabled={!editable} onChange={(e) => setHeader({ ...header, doc_number: e.target.value })} /></label>
            <label className="d-inline">Narration<input aria-label="Narration" dir="auto" value={header.narration} disabled={!editable} onChange={(e) => setHeader({ ...header, narration: e.target.value })} /></label>
            <label className="d-inline">Bank / cash account<select aria-label="Bank or cash account" value={header.settlement_account_id} disabled={!editable} onChange={(e) => setHeader({ ...header, settlement_account_id: e.target.value })}>
              <option value="">Company default</option>{bankLike.map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}</option>)}</select></label>
            {t.currency !== "OMR" ? <label className="d-inline">Exchange rate ({t.currency})<input aria-label="Exchange rate" inputMode="decimal" value={header.exchange_rate} disabled={!editable} onChange={(e) => setHeader({ ...header, exchange_rate: e.target.value })} /></label> : null}
            <p className="a-hint">Currency {t.currency}{t.exchange_rate ? ` · rate ${t.exchange_rate} (${t.rate_source ?? "source not recorded"}${t.rate_date ? `, ${t.rate_date}` : ""})` : ""}</p>
            {editable ? <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void saveHeader()}>Save details</Button> : null}
          </div>

          <h2>{kind === "customer" ? "Customer" : "Supplier"}</h2>
          {t.contact ? <p><strong>{t.contact.display_name}</strong>{t.contact.tax_number ? ` · ${t.contact.tax_number}` : ""} {editable ? <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run("", "txn_contact", { contact_id: null })}>Clear</Button> : null}</p> : <p className="a-hint">Not chosen yet. Nothing is linked automatically.</p>}
          {editable && !t.contact ? (
            <>
              {t.contact_suggestions.length ? <ul className="d-list" aria-label="Suggested matches">{t.contact_suggestions.map((s) => (
                <li key={s.contact_id}>{s.display_name} <span className="a-hint">— {MATCH_LABEL[s.strength] ?? s.strength}</span> <Button variant="secondary" className="a-btn--sm" aria-label={`Use ${s.display_name}`} disabled={busy} onClick={() => void run("", "txn_contact", { contact_id: s.contact_id })}>Use this {kind}</Button></li>))}</ul> : null}
              <label className="d-inline">Choose existing<select aria-label={`Choose ${kind}`} value="" onChange={(e) => e.target.value && void run("", "txn_contact", { contact_id: e.target.value })}>
                <option value="">Select…</option>{partyOptions.map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}</select></label>
            </>) : null}

          <h2>Lines</h2>
          <table className="d-table d-table--tx">
            <caption className="a-visually-hidden">Transaction lines</caption>
            <thead><tr><th scope="col">Description</th><th scope="col">Amount{t.prices_include_tax ? " (incl. tax)" : ""}</th><th scope="col">Tax code</th><th scope="col">Account</th><th scope="col">Net</th><th scope="col">Tax</th><th scope="col">Gross</th>{editable ? <th scope="col"><span className="a-visually-hidden">Remove</span></th> : null}</tr></thead>
            <tbody>{lines.map((l, i) => {
              const sv = t.lines[i];
              return (
                <tr key={i}>
                  <td>{editable ? <input aria-label={`Description of line ${i + 1}`} dir="auto" value={l.description} onChange={(e) => stateOf(i, "description", e.target.value)} /> : l.description || "—"}</td>
                  <td>{editable ? <input aria-label={`Amount of line ${i + 1}`} inputMode="decimal" value={l.amount} onChange={(e) => stateOf(i, "amount", e.target.value)} /> : money(l.amount)}</td>
                  <td>{editable ? <select aria-label={`Tax code of line ${i + 1}`} value={l.tax_code_id} onChange={(e) => stateOf(i, "tax_code_id", e.target.value)}><option value="">No tax code</option>{taxCodes.filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.code}</option>)}</select> : sv?.tax_code ?? "—"}</td>
                  <td>{editable ? <select aria-label={`Account of line ${i + 1}`} value={l.account_id} onChange={(e) => stateOf(i, "account_id", e.target.value)}><option value="">Choose…</option>{postable.map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}</option>)}</select> : sv?.account_code ? `${sv.account_code} · ${sv.account_name}` : <StatusChip tone="danger">Missing</StatusChip>}
                    {sv?.account_source === "role_default" ? <span className="a-hint" style={{ display: "block" }}>Company default account</span> : sv?.account_source === "rule" ? <span className="a-hint" style={{ display: "block" }}>Chosen by a company rule</span> : null}</td>
                  <td>{money(sv?.net_amount)}</td><td>{money(sv?.tax_amount)}</td><td>{money(sv?.gross_amount)}</td>
                  {editable ? <td><Button variant="secondary" className="a-btn--sm" aria-label={`Remove line ${i + 1}`} onClick={() => setLines(lines.filter((_, n) => n !== i))}>Remove</Button></td> : null}
                </tr>);
            })}</tbody>
            <tfoot><tr><th scope="row" colSpan={4}>Totals as calculated by the server</th><td>{money(t.net_amount)}</td><td>{money(t.tax_amount)}</td><td>{money(t.gross_amount)}</td>{editable ? <td /> : null}</tr></tfoot>
          </table>
          {editable ? <div className="d-actions"><Button variant="secondary" className="a-btn--sm" onClick={() => setLines([...lines, { description: "", amount: "", tax_code_id: "", account_id: "", tax_amount: null }])}>Add line</Button><Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void saveLines()}>Save lines</Button></div> : null}
          <details><summary>Calculation details</summary>
            <ul className="d-list">{t.lines.map((l) => { const c = l.calc as { tax_rate?: string | null; treatment?: string | null }; return <li key={l.id}>Line {l.line_no}: {c.treatment ? `${c.treatment.replace("_", " ")} at ${c.tax_rate ?? "0"}%` : "no tax code"}; net {money(l.net_amount)}, tax {money(l.tax_amount)}, gross {money(l.gross_amount)}.</li>; })}</ul>
            <p className="a-hint">{t.prices_include_tax ? "Line amounts include tax." : "Line amounts exclude tax."} All figures are calculated by the server with exact decimal arithmetic.</p></details>

          <h2>Proposed journal</h2>
          {t.journal ? (
            <>
              {jEdit === null ? (
                <table className="d-table">
                  <caption className="a-visually-hidden">Proposed journal lines</caption>
                  <thead><tr><th scope="col">Account</th><th scope="col">Debit</th><th scope="col">Credit</th><th scope="col">Origin</th></tr></thead>
                  <tbody>{t.journal.lines.map((l) => <tr key={l.id}><th scope="row">{l.account_code} · {l.account_name}</th><td>{Number(l.debit) ? money(l.debit) : ""}</td><td>{Number(l.credit) ? money(l.credit) : ""}</td><td className="a-hint">{SOURCE_LABEL[l.source] ?? l.source}</td></tr>)}</tbody>
                  <tfoot><tr><th scope="row">Total {t.balanced ? "— balanced" : "— NOT balanced"}</th><td>{money(t.debit_total)}</td><td>{money(t.credit_total)}</td><td /></tr></tfoot>
                </table>
              ) : (
                <>
                  <table className="d-table d-table--tx">
                    <caption className="a-visually-hidden">Edit journal lines</caption>
                    <thead><tr><th scope="col">Account</th><th scope="col">Debit</th><th scope="col">Credit</th><th scope="col"><span className="a-visually-hidden">Remove</span></th></tr></thead>
                    <tbody>{jEdit.map((l, i) => (
                      <tr key={i}>
                        <td><select aria-label={`Journal account ${i + 1}`} value={l.account_id} onChange={(e) => setJEdit(jEdit.map((x, n) => (n === i ? { ...x, account_id: e.target.value } : x)))}><option value="">Choose…</option>{postable.map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}</option>)}</select></td>
                        <td><input aria-label={`Journal debit ${i + 1}`} inputMode="decimal" value={l.debit} onChange={(e) => setJEdit(jEdit.map((x, n) => (n === i ? { ...x, debit: e.target.value, credit: e.target.value ? "" : x.credit } : x)))} /></td>
                        <td><input aria-label={`Journal credit ${i + 1}`} inputMode="decimal" value={l.credit} onChange={(e) => setJEdit(jEdit.map((x, n) => (n === i ? { ...x, credit: e.target.value, debit: e.target.value ? "" : x.debit } : x)))} /></td>
                        <td><Button variant="secondary" className="a-btn--sm" aria-label={`Remove journal line ${i + 1}`} onClick={() => setJEdit(jEdit.filter((_, n) => n !== i))}>Remove</Button></td>
                      </tr>))}</tbody>
                  </table>
                  <div className="d-actions">
                    <Button variant="secondary" className="a-btn--sm" onClick={() => setJEdit([...jEdit, { account_id: "", debit: "", credit: "" }])}>Add journal line</Button>
                    <Button className="a-btn--sm" disabled={busy || jEdit.some((l) => !l.account_id)} onClick={() => void run("Journal saved. The server checked it.", "txn_journal_lines", { lines: jEdit.map((l) => ({ account_id: l.account_id, debit: l.debit || undefined, credit: l.credit || undefined })) })}>Save journal</Button>
                    <Button variant="secondary" className="a-btn--sm" onClick={() => setJEdit(null)}>Cancel</Button>
                  </div>
                </>)}
              {editable && jEdit === null ? (
                <div className="d-actions">
                  <Button variant="secondary" className="a-btn--sm" onClick={() => setJEdit(t.journal?.lines.map((l) => ({ account_id: l.account_id, debit: Number(l.debit) ? l.debit : "", credit: Number(l.credit) ? l.credit : "" })) ?? [])}>Edit journal lines</Button>
                  {t.journal_mode === "manual" ? <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run("Journal regenerated from the lines.", "txn_regenerate")}>Regenerate from lines</Button> : null}
                </div>) : null}
            </>) : <p className="a-hint">No journal has been proposed yet.</p>}

          <h2>Checks</h2>
          {t.issues.length === 0 ? <p><StatusChip tone="success">All checks passed</StatusChip></p> : (
            <ul className="d-list" aria-label="Validation results">{t.issues.map((i, n) => (
              <li key={n}><StatusChip tone={i.blocking ? "danger" : i.severity === "error" ? "warning" : i.severity === "warning" ? "warning" : "info"}>{severityLabel(i)}</StatusChip> {i.message}</li>))}</ul>)}

          {t.duplicates.length ? (
            <>
              <h2>Possible duplicates</h2>
              <ul className="d-list">{t.duplicates.map((d) => (
                <li key={d.id}>
                  <strong>{d.level === "exact" ? "Looks identical to" : "Similar to"} {d.matched_number}</strong> ({d.matched_status.replace("_", " ")}, {money(d.matched_gross)}, {d.matched_date}{d.matched_doc_number ? `, no. ${d.matched_doc_number}` : ""})
                  <span className="a-hint" style={{ display: "block" }}>Evidence: {Object.entries(d.signals).filter(([, v]) => v === true).map(([k]) => k.replaceAll("_", " ")).join(", ") || "date and amount"}</span>
                  {d.decision ? <StatusChip tone="neutral">Decided: {d.decision.replace("_", " ")}</StatusChip> : editable ? (
                    <span className="d-actions">
                      <label className="d-inline">Reason<input aria-label="Reason for the duplicate decision" value={dupNote} onChange={(e) => setDupNote(e.target.value)} /></label>
                      <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => decideDup(d, "not_duplicate")}>Not a duplicate</Button>
                      <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => decideDup(d, "duplicate")}>It is a duplicate</Button>
                    </span>) : null}
                </li>))}</ul>
            </>) : null}
        </div>
      </div>

      <div className="d-actions d-review__foot" role="group" aria-label="Workflow actions">
        {t.can.submit ? <Button disabled={busy} onClick={() => void run("Submitted for approval.", "txn_submit", { note: note || undefined })}>Submit for approval</Button> : null}
        {t.status === "draft" && !t.can.submit && editable ? <span className="a-hint">Resolve the blocking items above to submit.</span> : null}
        {t.can.review ? <Button disabled={busy} onClick={() => void run("Passed to final approval.", "txn_review", { note: note || undefined })}>Pass to final approval</Button> : null}
        {t.can.approve ? (
          <>
            {soft.length ? <label className="d-inline"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have reviewed the differences listed above</label> : null}
            <Button disabled={busy || (soft.length > 0 && (!ack || !note.trim()))} onClick={() => { if (window.confirm("Approve this transaction? Approval is recorded under your name and the journal becomes part of the books.")) void run("Approved.", "txn_approve", { acknowledge: ack, note: note || undefined }); }}>Approve</Button>
          </>) : null}
        {t.status === "awaiting_approval" && !t.can.approve ? <span className="a-hint">You cannot approve this transaction (for example because you prepared it, or you lack approval rights). The server decides.</span> : null}
        {t.can.request_correction ? <Button variant="secondary" disabled={busy || note.trim().length < 3} onClick={() => void run("Sent back for correction.", "txn_request_correction", { reason: note })}>Request correction</Button> : null}
        {t.can.reject ? <Button variant="secondary" disabled={busy || note.trim().length < 3} onClick={() => void run("Rejected.", "txn_reject", { reason: note })}>Reject</Button> : null}
        {t.can.revise ? <Button disabled={busy} onClick={() => void run("Reopened for correction.", "txn_revise", { note: note || undefined })}>Reopen for correction</Button> : null}
        {t.can.reverse ? (
          <>
            <label className="d-inline">Reversal date<input aria-label="Reversal date" placeholder="YYYY-MM-DD" value={revDate} onChange={(e) => setRevDate(e.target.value)} /></label>
            <Button variant="secondary" disabled={busy || !revDate || note.trim().length < 3} onClick={() => { if (window.confirm("Reverse this approved transaction? A mirror journal is created; the original stays in the history.")) void run("Reversed. The original and the reversal both stay in the history.", "txn_reverse", { reversal_date: revDate, reason: note }); }}>Reverse</Button>
          </>) : null}
        {(t.can.approve || t.can.reject || t.can.request_correction || t.can.reverse || t.can.submit || t.can.review) ? <label className="d-inline">Note / reason<input aria-label="Note or reason" dir="auto" value={note} onChange={(e) => setNote(e.target.value)} /></label> : null}
      </div>

      <h2>History</h2>
      <ul className="d-list" aria-label="Approval history">{t.trail.map((e, n) => <li key={n}>{ACTION_LABEL[e.action] ?? e.action} · {e.actor ?? "system"} · {fmtDateTime(e.created_at)}{e.note ? ` — ${e.note}` : ""}</li>)}</ul>
    </section>
  );
}
