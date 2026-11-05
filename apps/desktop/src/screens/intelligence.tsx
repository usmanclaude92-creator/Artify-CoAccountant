import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Panel, StatusChip, type Tone } from "@artify/ui";
import type { Box, ExtractedField, ExtractionView, IntelLatest, ProcessingJob, ReviewKind, ReviewQueueItem, ReviewStatus, StatementTx } from "../bridge/types";
import { fmtDateTime } from "../lib/format";
import { useApp } from "../state/app";

const ACTIVE_JOB = ["queued", "preparing_pages", "ocr_processing", "classifying", "extracting", "validating", "suggesting"];
const JOB_LABEL: Record<string, string> = {
  queued: "Queued", preparing_pages: "Preparing pages", ocr_processing: "Reading text (OCR)", classifying: "Identifying document type",
  extracting: "Extracting data", validating: "Validating", suggesting: "Suggesting filing", awaiting_review: "Awaiting accountant review", approved: "Approved",
  failed: "Failed", cancelled: "Cancelled", needs_manual: "Needs manual processing",
};
const STATUS_LABEL: Record<ReviewStatus, string> = { extracted: "Suggested", needs_review: "Needs review", confirmed: "Confirmed", corrected: "Corrected by you", missing: "Marked missing", unreadable: "Marked unreadable", rejected: "Rejected" };
const STATUS_TONE: Record<ReviewStatus, Tone> = { extracted: "info", needs_review: "warning", confirmed: "success", corrected: "success", missing: "neutral", unreadable: "danger", rejected: "neutral" };
const DOC_TYPES: [string, string][] = [
  ["sales_invoice", "Sales invoice"], ["purchase_invoice", "Purchase invoice"], ["tax_invoice", "Tax invoice"], ["simplified_tax_invoice", "Simplified tax invoice"], ["receipt", "Receipt"],
  ["credit_note", "Credit note"], ["debit_note", "Debit note"], ["bank_statement", "Bank statement"], ["payment_voucher", "Payment voucher"], ["receipt_voucher", "Receipt voucher"],
  ["purchase_order", "Purchase order"], ["sales_order", "Sales order"], ["delivery_note", "Delivery note"], ["payroll", "Payroll"], ["expense", "Expense"], ["contract", "Contract"],
  ["correspondence", "Correspondence"], ["other", "Other"],
];
const pct = (c: number | null) => (c === null ? "—" : `${Math.round(c * 100)}%`);

function useCtx() {
  const { bridge, org, company, fail, go, reviewDoc, openReview } = useApp();
  return { bridge, orgId: org?.id ?? "", companyId: company?.id ?? "", fail, go, reviewDoc, openReview };
}

// ------------------------------------------------------------------ queue
export function ProcessingQueue() {
  const { bridge, orgId, companyId, fail, go, openReview } = useCtx();
  const [queue, setQueue] = useState<{ items: ReviewQueueItem[]; total: number } | null>(null);
  const [jobs, setJobs] = useState<ProcessingJob[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!companyId) return;
    bridge.intelQueue(orgId, companyId).then(setQueue).catch((e) => { const m = fail(e); if (m) setMsg(m); });
    bridge.intelJobs(orgId, companyId, null).then((r) => setJobs(r.items)).catch(() => undefined);
  }, [bridge, orgId, companyId, fail]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 5000);
    const off = bridge.on("processing-changed", load);
    return () => { clearInterval(t); void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, load]);
  if (!companyId) return <section className="d-page"><h1>Processing Queue</h1><Panel><p>Open a company first.</p><Button onClick={() => go("companies")}>Choose a company</Button></Panel></section>;
  const running = jobs.filter((j) => ACTIVE_JOB.includes(j.status) || ["failed", "needs_manual"].includes(j.status));
  const act = async (id: string, a: "cancel" | "retry") => { try { await bridge.intelJobAction(orgId, companyId, id, a); load(); } catch (e) { const m = fail(e); if (m) setMsg(m); } };
  return (
    <section className="d-page" aria-labelledby="h-proc">
      <h1 id="h-proc">Processing Queue</h1>
      <p className="a-hint">Everything here is a suggestion from automatic reading. Nothing is approved until an accountant reviews and approves it. No accounting entries are created.</p>
      {msg ? <Alert tone="danger">{msg}</Alert> : null}
      <h2>Waiting for review</h2>
      {queue === null ? <p role="status">Loading…</p> : queue.items.length === 0 ? <Panel><p>No documents are waiting for review. Open a document and choose “Read with OCR” to process it.</p></Panel> : (
        <table className="d-table">
          <caption className="a-visually-hidden">Documents waiting for review</caption>
          <thead><tr><th scope="col">Document</th><th scope="col">Type</th><th scope="col">Confidence</th><th scope="col">Read</th><th scope="col"><span className="a-visually-hidden">Actions</span></th></tr></thead>
          <tbody>{queue.items.map((q) => (
            <tr key={q.id}><th scope="row">{q.display_name}</th><td>{q.type_name}</td><td>{pct(q.confidence)}</td><td>{fmtDateTime(q.created_at)}</td>
              <td><Button className="a-btn--sm" onClick={() => openReview(q.document_id)}>Review</Button></td></tr>))}</tbody>
        </table>)}
      <h2>In progress and needing attention</h2>
      {running.length === 0 ? <p className="a-hint">Nothing is being processed.</p> : (
        <table className="d-table">
          <caption className="a-visually-hidden">Processing jobs</caption>
          <thead><tr><th scope="col">Stage</th><th scope="col">Progress</th><th scope="col">Details</th><th scope="col"><span className="a-visually-hidden">Actions</span></th></tr></thead>
          <tbody>{running.map((j) => (
            <tr key={j.id}>
              <td><StatusChip tone={j.status === "failed" ? "danger" : j.status === "needs_manual" ? "warning" : "info"}>{JOB_LABEL[j.status] ?? j.status}</StatusChip></td>
              <td>{ACTIVE_JOB.includes(j.status) ? <progress max={100} value={j.progress} aria-label="Progress">{j.progress}%</progress> : "—"}</td>
              <td>{j.error_message ?? (j.pages_total ? `Page ${j.pages_done ?? 0} of ${j.pages_total}` : "")}{j.attempt_count > 1 ? ` · attempt ${j.attempt_count}` : ""}</td>
              <td className="d-actions">
                <Button variant="secondary" className="a-btn--sm" onClick={() => openReview(j.document_id)}>Open</Button>
                {ACTIVE_JOB.includes(j.status) ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act(j.id, "cancel")}>Cancel</Button> : <Button variant="secondary" className="a-btn--sm" onClick={() => void act(j.id, "retry")}>Retry</Button>}
              </td>
            </tr>))}</tbody>
        </table>)}
    </section>
  );
}

// ------------------------------------------------------------------ review workspace
function Overlay({ boxes, active }: { boxes: { box: Box; id: string }[]; active: string | null }) {
  return (
    <>{boxes.map(({ box, id }) => (
      <span key={id} data-testid={`box-${id}`} className={id === active ? "d-box d-box--on" : "d-box"} style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%` }} />))}</>
  );
}

export function Review() {
  const { bridge, orgId, companyId, fail, go, reviewDoc } = useCtx();
  const [latest, setLatest] = useState<IntelLatest | null>(null);
  const [ex, setEx] = useState<ExtractionView | null>(null);
  const [msg, setMsg] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [rot, setRot] = useState(0);
  const [images, setImages] = useState<Record<number, string>>({});
  const [active, setActive] = useState<string | null>(null);
  const [ack, setAck] = useState(false);
  const [note, setNote] = useState("");
  const requested = useRef(new Set<number>());

  const load = useCallback((): Promise<void> => {
    if (!reviewDoc) return Promise.resolve();
    return bridge.intelLatest(orgId, companyId, reviewDoc)
      .then(async (l) => { setLatest(l); setEx(l.extraction_id ? await bridge.intelExtraction(orgId, companyId, l.extraction_id) : null); })
      .catch((e) => { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); });
  }, [bridge, orgId, companyId, reviewDoc, fail]);
  useEffect(() => { void load(); }, [load]);
  const running = !!latest?.job && ACTIVE_JOB.includes(latest.job.status);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 2000);
    return () => clearInterval(t);
  }, [running, load]);

  const pagesTotal = latest?.job?.pages_total ?? Math.max(1, ...[...(ex?.fields ?? []).map((f) => f.page_no ?? 1), ...(ex?.transactions ?? []).map((t) => t.page_no ?? 1)]);
  const wantPage = useCallback((n: number) => {
    if (!reviewDoc || requested.current.has(n) || !ex) return;
    requested.current.add(n);
    bridge.intelPageImage(orgId, companyId, reviewDoc, n).then((im) => setImages((s) => ({ ...s, [n]: `data:image/png;base64,${im.data_base64}` }))).catch(() => requested.current.delete(n));
  }, [bridge, orgId, companyId, reviewDoc, ex]);
  useEffect(() => { wantPage(page); }, [page, wantPage]);
  useEffect(() => { for (let n = 1; n <= Math.min(pagesTotal, 12); n++) wantPage(n); }, [pagesTotal, wantPage]);

  const run = async (okText: string, f: () => Promise<unknown>) => {
    setBusy(true); setMsg(null);
    try { await f(); if (okText) setMsg({ tone: "success", text: okText }); await load(); }
    catch (e) { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); }
    finally { setBusy(false); }
  };
  const review = (kind: ReviewKind, target: string, action: "correct" | "confirm" | "reject" | "missing" | "unreadable", payload: Record<string, unknown> = {}) =>
    ex ? run("", () => bridge.intelReview(orgId, companyId, ex.id, kind, target, action, payload, null)) : Promise.resolve();
  const start = (kind: "full" | "ocr" | "classify" | "extract") => reviewDoc ? run(`Started: ${kind === "full" ? "full read" : kind}. Earlier results are kept.`, () => bridge.intelStart(orgId, companyId, reviewDoc, kind, [], "Re-run from the review screen")) : Promise.resolve();

  const boxes = useMemo(() => {
    if (!ex) return [] as { box: Box; id: string; page: number }[];
    return [
      ...ex.fields.filter((f) => f.bbox && f.page_no === page).map((f) => ({ box: f.bbox as Box, id: f.field_key, page })),
      ...(ex.transactions ?? []).filter((t) => t.bbox && t.page_no === page).map((t) => ({ box: t.bbox as Box, id: t.id, page })),
      ...ex.line_items.filter((l) => l.bbox && l.page_no === page).map((l) => ({ box: l.bbox as Box, id: l.id, page })),
    ];
  }, [ex, page]);
  const focus = (id: string, p: number | null) => { setActive(id); if (p) setPage(p); };

  if (!reviewDoc) return <section className="d-page"><h1>Review</h1><Panel><p>Choose a document from the Processing Queue.</p><Button onClick={() => go("processing")}>Processing Queue</Button></Panel></section>;
  const decided = ex ? ex.status !== "draft" : false;
  const open = ex ? ex.unresolved.fields + ex.unresolved.line_items + ex.unresolved.transactions : 0;
  const issues = ex?.statement?.validation ?? ex?.validation ?? [];
  const errors = issues.filter((i) => i.severity === "error");
  return (
    <section className="d-page" aria-labelledby="h-review">
      <div className="d-actions"><Button variant="secondary" className="a-btn--sm" onClick={() => go("processing")}>← Processing Queue</Button></div>
      <h1 id="h-review">Review extracted data</h1>
      {msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}
      {latest?.job ? (
        <p role="status">
          <StatusChip tone={latest.job.status === "failed" ? "danger" : running ? "info" : "neutral"}>{JOB_LABEL[latest.job.status] ?? latest.job.status}</StatusChip>
          {running ? <> <progress max={100} value={latest.job.progress} aria-label="Progress" /> {latest.job.progress}%</> : null}
          {latest.job.error_message ? ` ${latest.job.error_message}` : ""}
        </p>) : null}
      {!ex ? (
        <Panel>
          <p>{running ? "Reading this document…" : "This document has no extracted data yet."}</p>
          <div className="d-actions"><Button disabled={busy || running} onClick={() => void start("full")}>Read with OCR</Button></div>
        </Panel>) : (
        <>
          <Alert tone="info">{decided ? `This extraction is ${ex.status}.` : "These values were suggested by automatic reading. Review each one; only you can approve."}</Alert>
          {ex.classification ? (
            <p>
              <strong>Type:</strong> {DOC_TYPES.find(([k]) => k === ex.type_key)?.[1] ?? ex.type_key} <StatusChip tone={ex.classification.needs_manual ? "warning" : "info"}>{pct(ex.classification.confidence)} · {ex.classification.review_status}</StatusChip>
              {ex.classification.needs_manual ? <span className="a-hint"> Low confidence — please choose the type.</span> : null}
              {!decided ? (
                <> <label className="d-inline">Change type<select aria-label="Document type" value={ex.type_key} onChange={(e) => void run("Type changed. Re-run extraction to read it as this type.", () => bridge.intelClassification(orgId, companyId, ex.document_id, "correct", e.target.value))}>
                  {DOC_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
                  <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run("Type confirmed.", () => bridge.intelClassification(orgId, companyId, ex.document_id, "confirm", null))}>Confirm type</Button></>) : null}
            </p>) : null}
          <div className="d-review">
            <div className="d-viewer" role="region" aria-label="Page view">
              <div className="d-viewer__bar">
                <Button variant="secondary" className="a-btn--sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Previous page">‹</Button>
                <span>Page {page} of {pagesTotal}</span>
                <Button variant="secondary" className="a-btn--sm" disabled={page >= pagesTotal} onClick={() => setPage((p) => p + 1)} aria-label="Next page">›</Button>
                <Button variant="secondary" className="a-btn--sm" onClick={() => setZoom((z) => Math.min(z * 1.25, 6))} aria-label="Zoom in">+</Button>
                <Button variant="secondary" className="a-btn--sm" onClick={() => setZoom((z) => Math.max(z / 1.25, 0.25))} aria-label="Zoom out">−</Button>
                <Button variant="secondary" className="a-btn--sm" onClick={() => setZoom(1)}>Fit</Button>
                <Button variant="secondary" className="a-btn--sm" onClick={() => setRot((r) => (r + 90) % 360)}>Rotate view</Button>
              </div>
              <div className="d-review__thumbs" role="list" aria-label="Pages">
                {Array.from({ length: Math.min(pagesTotal, 60) }, (_, i) => i + 1).map((n) => (
                  <button key={n} type="button" role="listitem" className="d-thumb" aria-current={n === page ? "page" : undefined} aria-label={`Page ${n}`} onClick={() => setPage(n)}>
                    {images[n] ? <img src={images[n]} alt="" /> : <span>{n}</span>}</button>))}
              </div>
              <div className="d-viewer__stage">
                {images[page] ? (
                  <div className="d-review__page" style={{ width: `${zoom * 100}%`, transform: `rotate(${rot}deg)` }}>
                    <img src={images[page]} alt={`Page ${page}`} />
                    <Overlay boxes={boxes} active={active} />
                  </div>) : <p role="status">Loading page…</p>}
              </div>
            </div>
            <div className="d-review__side">
              <h2>Fields</h2>
              <ul className="d-fields">{ex.fields.map((f) => (
                <FieldRow key={f.id + f.review_status + (f.value ?? "")} f={f} active={active === f.field_key} disabled={busy || decided} onFocus={() => focus(f.field_key, f.page_no)} onAct={(a, v) => void review("field", f.field_key, a, a === "correct" ? { value: v } : {})} />))}</ul>
              {ex.line_items.length ? <LineItems ex={ex} disabled={busy || decided} active={active} onFocus={focus} onAct={(id, a, values) => void review("line-items", id, a, values ? { values } : {})} /> : null}
              {ex.statement ? <Statement ex={ex} disabled={busy || decided} active={active} onFocus={focus} onAct={(id, a, patch) => void review("transactions", id, a, patch ? { patch } : {})}
                onAdd={(d) => run("Row added. It is marked as manually entered.", () => bridge.intelAddTransaction(orgId, companyId, ex.id, d))}
                onBalances={(p) => run("Statement balances updated.", () => bridge.intelStatement(orgId, companyId, ex.id, p))} /> : null}
              {issues.length ? (
                <><h2>Checks</h2><ul className="d-list">{issues.map((i, n) => <li key={n}><StatusChip tone={i.severity === "error" ? "danger" : i.severity === "warning" ? "warning" : "info"}>{i.severity}</StatusChip> {i.message}{i.data?.["difference"] ? ` (difference ${String(i.data["difference"])})` : ""}</li>)}</ul></>) : null}
              {ex.workspace_suggestions.length ? (
                <><h2>Suggested location</h2><ul className="d-list">{ex.workspace_suggestions.map((s) => (
                  <li key={s.id}>{s.node_label ?? "—"}{s.category_name ? ` · ${s.category_name}` : ""} <span className="a-hint">{pct(s.confidence)}</span>{" "}
                    {s.status === "pending" && s.you_can_file_here && !decided ? <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run("Document moved to the suggested location.", () => bridge.intelAccept(orgId, companyId, ex.id, "workspace", s.id, null))}>Move here</Button> : <StatusChip tone="neutral">{s.status}</StatusChip>}</li>))}</ul></>) : null}
              {ex.metadata_suggestions.length ? (
                <><h2>Suggested details</h2><ul className="d-list">{ex.metadata_suggestions.map((m) => (
                  <li key={m.id}>{m.label}: <strong>{String(m.value ?? "")}</strong> {m.problems?.length ? <span className="a-hint">({m.problems.join(", ")})</span> : null}{" "}
                    {m.status === "pending" && !m.problems?.length && !decided ? <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run("Detail saved.", () => bridge.intelAccept(orgId, companyId, ex.id, "metadata", m.id, m.value))}>Use</Button> : <StatusChip tone="neutral">{m.status}</StatusChip>}</li>))}</ul></>) : null}
              {ex.split_proposal ? (
                <><h2>Proposed split</h2><p className="a-hint">This file seems to contain several documents. Nothing is split unless you approve.</p>
                  <ul className="d-list">{ex.split_proposal.segments.map((s, i) => <li key={i}>Pages {s.from_page}–{s.to_page}{s.type_key ? ` · ${s.type_key}` : ""}</li>)}</ul>
                  {ex.split_proposal.status === "proposed" && !decided ? <div className="d-actions"><Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run("Split recorded.", () => bridge.intelSplit(orgId, companyId, ex.id, true))}>Approve split</Button><Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run("Split declined.", () => bridge.intelSplit(orgId, companyId, ex.id, false))}>Decline</Button></div> : null}</>) : null}
            </div>
          </div>
          {!decided ? (
            <div className="d-actions d-review__foot">
              <Button disabled={busy || open > 0 || (errors.length > 0 && (!ack || !note.trim()))} onClick={() => { if (window.confirm("Approve this data? Approval is recorded under your name. No accounting entries are created.")) void run("Approved.", () => bridge.intelDecide(orgId, companyId, ex.id, "approve", ack, note.trim() || null)); }}>Approve</Button>
              <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm("Reject this extraction? The document and earlier results are kept.")) void run("Rejected.", () => bridge.intelDecide(orgId, companyId, ex.id, "reject", false, "Rejected in review")); }}>Reject</Button>
              {open > 0 ? <span className="a-hint">{open} item{open === 1 ? "" : "s"} still need{open === 1 ? "s" : ""} review.</span> : null}
              {errors.length > 0 ? <><label className="d-inline"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have reviewed the validation errors above</label>
                <label className="d-inline">Why approve anyway?<input value={note} onChange={(e) => setNote(e.target.value)} /></label></> : null}
            </div>) : null}
          <div className="d-actions">
            <span className="a-hint">Run again (earlier results are kept):</span>
            <Button variant="secondary" className="a-btn--sm" disabled={busy || running} onClick={() => void start("ocr")}>Re-run OCR</Button>
            <Button variant="secondary" className="a-btn--sm" disabled={busy || running} onClick={() => void start("classify")}>Re-run classification</Button>
            <Button variant="secondary" className="a-btn--sm" disabled={busy || running} onClick={() => void start("extract")}>Re-run extraction</Button>
          </div>
        </>)}
    </section>
  );
}

function FieldRow({ f, active, disabled, onFocus, onAct }: { f: ExtractedField; active: boolean; disabled: boolean; onFocus: () => void; onAct: (a: "correct" | "confirm" | "reject" | "missing" | "unreadable", v?: string) => void }) {
  const [v, setV] = useState(f.value ?? "");
  const origin = f.review_status === "corrected" ? "Corrected by you" : f.review_status === "confirmed" ? "Confirmed by you" : "Suggested by the system";
  return (
    <li className={active ? "d-field d-field--on" : "d-field"} onClick={onFocus}>
      <label htmlFor={`f-${f.field_key}`}>{f.label}{f.required ? " *" : ""}</label>
      <input id={`f-${f.field_key}`} value={v} disabled={disabled} onFocus={onFocus} onChange={(e) => setV(e.target.value)} dir="auto" />
      <span className="a-hint">{origin} · {pct(f.confidence)}{f.flags?.length ? ` · ${f.flags.join(", ")}` : ""}</span>
      <StatusChip tone={STATUS_TONE[f.review_status]}>{STATUS_LABEL[f.review_status]}</StatusChip>
      {f.original_value && f.original_value !== f.value ? <span className="a-hint">Originally read as: {f.original_value}</span> : null}
      {!disabled ? (
        <span className="d-actions">
          <Button variant="secondary" className="a-btn--sm" aria-label={`Save ${f.label}`} onClick={() => onAct("correct", v)} disabled={v === (f.value ?? "")}>Save</Button>
          <Button variant="secondary" className="a-btn--sm" aria-label={`Confirm ${f.label}`} onClick={() => onAct("confirm")}>Confirm</Button>
          <Button variant="secondary" className="a-btn--sm" aria-label={`Mark ${f.label} missing`} onClick={() => onAct("missing")}>Missing</Button>
          <Button variant="secondary" className="a-btn--sm" aria-label={`Mark ${f.label} unreadable`} onClick={() => onAct("unreadable")}>Unreadable</Button>
          <Button variant="secondary" className="a-btn--sm" aria-label={`Reject ${f.label}`} onClick={() => onAct("reject")}>Reject</Button>
        </span>) : null}
    </li>
  );
}

function LineItems({ ex, disabled, active, onFocus, onAct }: { ex: ExtractionView; disabled: boolean; active: string | null; onFocus: (id: string, p: number | null) => void; onAct: (id: string, a: "confirm" | "reject" | "correct", values?: Record<string, string | null>) => void }) {
  return (
    <>
      <h2>Line items</h2>
      <table className="d-table">
        <thead><tr>{ex.line_fields.map((c) => <th key={c.key} scope="col">{c.label}</th>)}<th scope="col">Status</th><th scope="col"><span className="a-visually-hidden">Actions</span></th></tr></thead>
        <tbody>{ex.line_items.map((l) => (
          <tr key={l.id} className={active === l.id ? "d-row--on" : undefined} onClick={() => onFocus(l.id, l.page_no)}>
            {ex.line_fields.map((c) => <td key={c.key} dir="auto">{l.values[c.key] ?? "—"}</td>)}
            <td><StatusChip tone={STATUS_TONE[l.review_status]}>{STATUS_LABEL[l.review_status]}</StatusChip></td>
            <td>{!disabled ? <><Button variant="secondary" className="a-btn--sm" aria-label={`Confirm line ${l.line_no}`} onClick={() => onAct(l.id, "confirm")}>Confirm</Button><Button variant="secondary" className="a-btn--sm" aria-label={`Reject line ${l.line_no}`} onClick={() => onAct(l.id, "reject")}>Reject</Button></> : null}</td>
          </tr>))}</tbody>
      </table>
    </>
  );
}

function Statement({ ex, disabled, active, onFocus, onAct, onAdd, onBalances }: {
  ex: ExtractionView; disabled: boolean; active: string | null; onFocus: (id: string, p: number | null) => void;
  onAct: (id: string, a: "confirm" | "reject" | "correct", patch?: Record<string, unknown>) => void; onAdd: (d: Record<string, unknown>) => Promise<unknown>; onBalances: (p: Record<string, unknown>) => Promise<unknown>;
}) {
  const st = ex.statement;
  const [edit, setEdit] = useState<string | null>(null);
  const [draft, setDraft] = useState<Partial<StatementTx>>({});
  const [row, setRow] = useState({ tx_date: "", description: "", debit: "", credit: "", balance: "" });
  const [bal, setBal] = useState({ opening_balance: st?.opening_balance ?? "", closing_balance: st?.closing_balance ?? "" });
  if (!st) return null;
  const txs = ex.transactions ?? [];
  return (
    <>
      <h2>Bank statement</h2>
      <dl className="a-dl">
        <dt>Bank</dt><dd>{st.bank_name ?? "—"}</dd><dt>Account</dt><dd>{st.account_number_masked ?? st.iban_masked ?? "—"}</dd>
        <dt>Period</dt><dd>{st.period_start ?? "—"} to {st.period_end ?? "—"}</dd><dt>Currency</dt><dd>{st.currency ?? "—"}</dd>
      </dl>
      {!disabled ? (
        <form className="d-actions" onSubmit={(e) => { e.preventDefault(); void onBalances({ opening_balance: bal.opening_balance || null, closing_balance: bal.closing_balance || null }); }}>
          <label className="d-inline">Opening balance<input value={bal.opening_balance} onChange={(e) => setBal({ ...bal, opening_balance: e.target.value })} inputMode="decimal" /></label>
          <label className="d-inline">Closing balance<input value={bal.closing_balance} onChange={(e) => setBal({ ...bal, closing_balance: e.target.value })} inputMode="decimal" /></label>
          <Button type="submit" variant="secondary" className="a-btn--sm">Save balances</Button>
        </form>) : <p>Opening {st.opening_balance ?? "—"} · Closing {st.closing_balance ?? "—"}</p>}
      <table className="d-table d-table--tx">
        <caption className="a-visually-hidden">Transactions</caption>
        <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">Description</th><th scope="col">Debit</th><th scope="col">Credit</th><th scope="col">Balance</th><th scope="col">Status</th><th scope="col"><span className="a-visually-hidden">Actions</span></th></tr></thead>
        <tbody>{txs.map((t) => (
          <tr key={t.id} className={active === t.id ? "d-row--on" : undefined} onClick={() => onFocus(t.id, t.page_no)}>
            <td>{t.row_no}</td>
            {edit === t.id ? (
              <>
                <td><input aria-label="Date" value={draft.tx_date ?? t.tx_date ?? ""} onChange={(e) => setDraft({ ...draft, tx_date: e.target.value })} /></td>
                <td><input aria-label="Description" dir="auto" value={draft.description ?? t.description ?? ""} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></td>
                <td><input aria-label="Debit" inputMode="decimal" value={draft.debit ?? t.debit ?? ""} onChange={(e) => setDraft({ ...draft, debit: e.target.value })} /></td>
                <td><input aria-label="Credit" inputMode="decimal" value={draft.credit ?? t.credit ?? ""} onChange={(e) => setDraft({ ...draft, credit: e.target.value })} /></td>
                <td><input aria-label="Balance" inputMode="decimal" value={draft.balance ?? t.balance ?? ""} onChange={(e) => setDraft({ ...draft, balance: e.target.value })} /></td>
              </>) : (<><td>{t.tx_date ?? "—"}</td><td dir="auto">{t.description ?? "—"}{t.manual ? " (added by hand)" : ""}</td><td>{t.debit ?? ""}</td><td>{t.credit ?? ""}</td><td>{t.balance ?? ""}</td></>)}
            <td><StatusChip tone={STATUS_TONE[t.review_status]}>{STATUS_LABEL[t.review_status]}</StatusChip>{t.flags?.length ? <span className="a-hint"> {t.flags.join(", ")}</span> : null}</td>
            <td>{!disabled ? (edit === t.id
              ? <><Button className="a-btn--sm" onClick={() => { onAct(t.id, "correct", draft); setEdit(null); setDraft({}); }}>Save row</Button><Button variant="secondary" className="a-btn--sm" onClick={() => { setEdit(null); setDraft({}); }}>Cancel</Button></>
              : <><Button variant="secondary" className="a-btn--sm" aria-label={`Edit row ${t.row_no}`} onClick={() => setEdit(t.id)}>Edit</Button><Button variant="secondary" className="a-btn--sm" aria-label={`Confirm row ${t.row_no}`} onClick={() => onAct(t.id, "confirm")}>Confirm</Button><Button variant="secondary" className="a-btn--sm" aria-label={`Reject row ${t.row_no}`} onClick={() => onAct(t.id, "reject")}>Reject</Button></>) : null}</td>
          </tr>))}</tbody>
      </table>
      {!disabled ? (
        <form className="d-actions" aria-label="Add a missing row" onSubmit={(e) => { e.preventDefault(); void onAdd({ tx_date: row.tx_date || null, description: row.description || null, debit: row.debit || null, credit: row.credit || null, balance: row.balance || null }).then(() => setRow({ tx_date: "", description: "", debit: "", credit: "", balance: "" })); }}>
          <label className="d-inline">Date<input value={row.tx_date} onChange={(e) => setRow({ ...row, tx_date: e.target.value })} placeholder="YYYY-MM-DD" /></label>
          <label className="d-inline">Description<input dir="auto" value={row.description} onChange={(e) => setRow({ ...row, description: e.target.value })} /></label>
          <label className="d-inline">Debit<input inputMode="decimal" value={row.debit} onChange={(e) => setRow({ ...row, debit: e.target.value })} /></label>
          <label className="d-inline">Credit<input inputMode="decimal" value={row.credit} onChange={(e) => setRow({ ...row, credit: e.target.value })} /></label>
          <label className="d-inline">Balance<input inputMode="decimal" value={row.balance} onChange={(e) => setRow({ ...row, balance: e.target.value })} /></label>
          <Button type="submit" variant="secondary" className="a-btn--sm">Add row</Button>
        </form>) : null}
    </>
  );
}
