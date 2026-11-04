import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { DocLimits, DuplicateCheck, ImportRow, QueueResult, Staged, Suggestion, WsCategory, WsField } from "../bridge/types";
import { describe } from "../lib/errors";
import { applicableFields, fmtPct, importProblem, IMPORT_STATUS_LABEL, isActiveImport, isFinishedImport, needsDecision, nodeChain, uploadTargets } from "../lib/documents";
import { toPayload, type Raw, type RawValues } from "../lib/fields";
import { fmtBytes, fmtDateTime } from "../lib/format";
import { labelOf } from "../lib/tree";
import { useApp } from "../state/app";
import { useWs } from "../state/useWs";

interface Entry { file: Staged; nodeId: string; categoryId: string; name: string; raw: RawValues; policy: string; suggestion?: Suggestion | null; note?: string }

export function ImportScreen() {
  const { bridge, org, company, go, fail, pendingFiles, addPendingFiles, removePendingFile, clearPendingFiles } = useApp();
  const { data: ws, stale } = useWs();
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [limits, setLimits] = useState<DocLimits | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueueResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const orgId = org?.id;
  const companyId = company?.id;

  useEffect(() => {
    if (!orgId || !companyId) return;
    bridge.documentsOp<DocLimits>(orgId, companyId, "limits").then(setLimits).catch(() => undefined);
  }, [bridge, orgId, companyId]);

  const targets = useMemo(() => uploadTargets(ws?.nodes ?? []), [ws]);
  const categories = useMemo(() => (ws?.categories ?? []).filter((c) => c.is_active && !c.archived_at), [ws]);

  const entryFor = (f: Staged): Entry => entries[f.token] ?? { file: f, nodeId: targets.length === 1 ? (targets[0]?.id ?? "") : "", categoryId: "", name: "", raw: {}, policy: "ask" };
  const patch = (f: Staged, p: Partial<Entry>) => setEntries((cur) => ({ ...cur, [f.token]: { ...entryFor(f), ...p } }));

  const choose = async () => {
    setProblem(null);
    try { addPendingFiles(await bridge.filesPick()); } catch (e) { const m = fail(e); if (m) setProblem(m); }
  };

  if (!company) return <section className="d-page"><h1>Import Documents</h1><Panel><p>Choose a company first. Documents are always imported into one company.</p><Button onClick={() => go("companies")}>Go to My Companies</Button></Panel></section>;

  const catsFor = (nodeId: string): WsCategory[] => categories.filter((c) => c.node_id === null || c.node_id === nodeId);
  const fieldsFor = (e: Entry): WsField[] => applicableFields(ws?.fields, e.categoryId || null, nodeChain(ws?.nodes ?? [], e.nodeId));
  const ready = pendingFiles.every((f) => entryFor(f).nodeId);
  const tooMany = limits && pendingFiles.length > limits.batch_max_files;

  const suggest = async (f: Staged) => {
    const e = entryFor(f);
    if (!orgId || !companyId) return;
    try {
      const s = await bridge.documentsOp<Suggestion>(orgId, companyId, "suggest", { filename: f.name, category_id: e.categoryId || null, node_id: e.nodeId || null, field_values: toPayload(fieldsFor(e), e.raw).values });
      const next: Partial<Entry> = { suggestion: s };
      if (s.filing.target_node_id && targets.some((n) => n.id === s.filing.target_node_id)) { next.nodeId = s.filing.target_node_id; if (s.filing.category_id) next.categoryId = s.filing.category_id; }
      if (s.naming.suggested_name) next.name = s.naming.suggested_name;
      next.note = s.filing.rule ? `Filing rule “${s.filing.rule.name}” suggests ${s.filing.path?.join(" / ") ?? "a folder"}.` : "No filing rule matched; choose the folder yourself.";
      patch(f, next);
    } catch (err) { const m = fail(err); if (m) patch(f, { note: m }); }
  };

  const queue = async () => {
    if (!orgId || !companyId) return;
    setProblem(null);
    const items = [];
    for (const f of pendingFiles) {
      const e = entryFor(f);
      const p = toPayload(fieldsFor(e), e.raw);
      if (p.missing.length || p.invalid.length) {
        setProblem(`${f.name}: ${p.missing.length ? `please fill in ${p.missing.join(", ")}` : `check ${p.invalid.join(", ")}`}.`);
        return;
      }
      items.push({ token: f.token, node_id: e.nodeId, category_id: e.categoryId || null, display_name: e.name.trim() || null, field_values: Object.keys(p.values).length ? p.values : null, duplicate_policy: e.policy, filing_rule_id: e.suggestion?.filing.rule?.id ?? null });
    }
    setBusy(true);
    try {
      const r = await bridge.importsQueue(orgId, companyId, items);
      setResult(r);
      clearPendingFiles();
      setEntries({});
      void bridge.importsProcess().catch(() => undefined);
    } catch (e) { const m = fail(e); if (m) setProblem(m); } finally { setBusy(false); }
  };

  return (
    <section className="d-page" aria-labelledby="h-imp">
      <header className="d-page__head"><div><h1 id="h-imp">Import Documents</h1><p className="a-hint">{company.name} · files are checked, sent to the private cloud store and filed in the folder you choose.</p></div>
        <Button onClick={() => void choose()}>Choose files…</Button></header>
      {stale ? <Alert tone="warning">The saved folder structure is shown; the latest could not be confirmed. Folders may have changed.</Alert> : null}
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {limits ? <p className="a-hint">Up to {limits.batch_max_files} files at a time, {fmtBytes(limits.max_file_size)} each. Allowed: {limits.allowed_extensions.join(", ").toUpperCase()}.</p> : null}
      {result ? (
        <Panel>
          <h2>{result.queued.length} file{result.queued.length === 1 ? "" : "s"} added to the upload queue</h2>
          {result.rejected.length ? <Alert tone="warning">Not added: <ul>{result.rejected.map((r, i) => <li key={i}><strong>{r.name}</strong>: {importProblem(r.code, r.message)}</li>)}</ul></Alert> : null}
          <div className="d-actions"><Button onClick={() => go("queue")}>Open the upload queue</Button><Button variant="secondary" onClick={() => setResult(null)}>Import more</Button></div>
        </Panel>
      ) : null}
      {pendingFiles.length === 0 && !result ? (
        <Panel className="d-drop"><p><strong>Drag files here</strong> from File Explorer, or use “Choose files…”.</p><p className="a-hint">Nothing is sent until you choose a destination for each file and add them to the queue. Images, PDFs and spreadsheets are supported.</p></Panel>
      ) : null}
      {targets.length === 0 && pendingFiles.length > 0 ? <Alert tone="warning">You do not have permission to add documents to any folder of this company, or the company has no folders yet. Ask an administrator.</Alert> : null}
      {tooMany ? <Alert tone="danger">Too many files for one batch (limit {limits?.batch_max_files}). Remove some or import in several steps.</Alert> : null}
      {pendingFiles.map((f) => {
        const e = entryFor(f);
        const fields = fieldsFor(e);
        return (
          <Panel key={f.token} className="d-import">
            <header className="d-page__head"><div><h2>{f.name}</h2><p className="a-hint">{fmtBytes(f.size)}</p></div><Button variant="secondary" className="a-btn--sm" onClick={() => removePendingFile(f.token)}>Remove from list</Button></header>
            <div className="d-form-grid">
              <div className="a-field"><label htmlFor={`n-${f.token}`}>Destination folder</label>
                <select id={`n-${f.token}`} value={e.nodeId} onChange={(ev) => patch(f, { nodeId: ev.target.value, categoryId: "" })}><option value="">Choose a folder…</option>{targets.map((n) => <option key={n.id} value={n.id}>{"— ".repeat(Math.max(0, n.depth - 1))}{labelOf(n)}</option>)}</select></div>
              <div className="a-field"><label htmlFor={`c-${f.token}`}>Category</label>
                <select id={`c-${f.token}`} value={e.categoryId} disabled={!e.nodeId} onChange={(ev) => patch(f, { categoryId: ev.target.value })}><option value="">No category</option>{catsFor(e.nodeId).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
              <div className="a-field"><label htmlFor={`d-${f.token}`}>Document name</label>
                <input id={`d-${f.token}`} value={e.name} placeholder="Leave blank to keep the file name" maxLength={255} onChange={(ev) => patch(f, { name: ev.target.value })} /></div>
              <div className="a-field"><label htmlFor={`p-${f.token}`}>If identical content already exists</label>
                <select id={`p-${f.token}`} value={e.policy} onChange={(ev) => patch(f, { policy: ev.target.value })}><option value="ask">Ask me</option><option value="skip">Skip this file</option><option value="keep_both">Keep both</option></select></div>
            </div>
            {fields.length ? (
              <fieldset className="d-fieldset"><legend>Details for this folder and category</legend>
                {fields.map((fl) => <FieldInput key={fl.id} field={fl} idPrefix={f.token} raw={e.raw[fl.key] ?? {}} nodes={ws?.nodes ?? []} categories={categories} onChange={(r) => patch(f, { raw: { ...e.raw, [fl.key]: r } })} />)}
              </fieldset>
            ) : null}
            <div className="d-actions">
              <Button variant="secondary" className="a-btn--sm" onClick={() => void suggest(f)}>Suggest folder and name</Button>
            </div>
            {e.note ? <p className="a-hint" role="status">{e.note}</p> : null}
            {e.suggestion?.naming.missing?.length ? <p className="a-hint">A name could not be suggested yet; missing: {e.suggestion.naming.missing.join(", ")}.</p> : null}
          </Panel>
        );
      })}
      {pendingFiles.length > 0 ? (
        <div className="d-actions">
          <Button disabled={busy || !ready || !!tooMany} onClick={() => void queue()}>{busy ? "Adding…" : `Add ${pendingFiles.length} file${pendingFiles.length === 1 ? "" : "s"} to the upload queue`}</Button>
          {!ready ? <span className="a-hint">Choose a folder for every file.</span> : null}
        </div>
      ) : null}
    </section>
  );
}

function FieldInput({ field, idPrefix, raw, onChange, nodes, categories }: { field: WsField; idPrefix: string; raw: Raw; onChange: (r: Raw) => void; nodes: { id: string; name: string; display_name: string | null }[]; categories: WsCategory[] }) {
  const id = `${idPrefix}-f-${field.key}`;
  const label = `${field.label}${field.required ? " (required)" : ""}`;
  const t = field.data_type;
  const options = field.options.filter((o) => !o.archived_at);
  if (t === "multi_select") {
    return <fieldset className="d-fieldset"><legend>{label}</legend>{options.map((o) => <label key={o.value} className="d-check"><input type="checkbox" checked={(raw.picked ?? []).includes(o.value)} onChange={(e) => onChange({ ...raw, picked: e.target.checked ? [...(raw.picked ?? []), o.value] : (raw.picked ?? []).filter((x) => x !== o.value) })} /> {o.label}</label>)}</fieldset>;
  }
  return (
    <div className="a-field"><label htmlFor={id}>{label}</label>
      {t === "dropdown" ? <select id={id} value={raw.text ?? ""} onChange={(e) => onChange({ text: e.target.value })}><option value="">Not set</option>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
        : t === "boolean" ? <select id={id} value={raw.bool ?? ""} onChange={(e) => onChange({ bool: e.target.value as Raw["bool"] })}><option value="">Not set</option><option value="yes">Yes</option><option value="no">No</option></select>
        : t === "reference" ? <select id={id} value={raw.text ?? ""} onChange={(e) => onChange({ text: e.target.value })}><option value="">Not set</option>{(field.reference_target === "document_category" ? categories.map((c) => ({ id: c.id, label: c.name })) : nodes.map((n) => ({ id: n.id, label: n.display_name || n.name }))).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
        : t === "date" ? <input id={id} type="date" value={raw.text ?? ""} onChange={(e) => onChange({ text: e.target.value })} />
        : t === "long_text" ? <textarea id={id} value={raw.text ?? ""} rows={3} onChange={(e) => onChange({ text: e.target.value })} />
        : t === "currency" ? <span className="d-inline-form"><input id={id} inputMode="decimal" value={raw.text ?? ""} placeholder="Amount" onChange={(e) => onChange({ ...raw, text: e.target.value })} /><input aria-label={`${field.label} currency`} value={raw.currency ?? ""} maxLength={3} placeholder="OMR" onChange={(e) => onChange({ ...raw, currency: e.target.value })} /></span>
        : <input id={id} inputMode={t === "number" || t === "decimal" ? "decimal" : undefined} value={raw.text ?? ""} onChange={(e) => onChange({ text: e.target.value })} />}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ upload queue
export function QueueScreen() {
  const { bridge, org, company, go, fail } = useApp();
  const { data: ws } = useWs();
  const [rows, setRows] = useState<ImportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [dupFor, setDupFor] = useState<Record<string, DuplicateCheck | null>>({});
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const companyId = company?.id ?? null;

  useEffect(() => {
    let live = true;
    bridge.importsList(companyId).then((r) => { if (live) { setRows(r); setError(null); } }).catch((e) => { if (live) { const m = fail(e); if (m) setError(m); } });
    return () => { live = false; };
  }, [bridge, companyId, tick, fail]);
  useEffect(() => {
    const off = bridge.on("imports-updated", reload);
    return () => { void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, reload]);
  const active = rows?.some((r) => isActiveImport(r.status)) ?? false;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(reload, 2000);
    return () => clearInterval(t);
  }, [active, reload]);

  const nodeName = useMemo(() => new Map((ws?.nodes ?? []).map((n) => [n.id, labelOf(n)])), [ws]);
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); } catch (e) { const m = fail(e); if (m) setError(describe(e)); } reload(); };
  const counts = useMemo(() => {
    const c = { active: 0, failed: 0, done: 0 };
    for (const r of rows ?? []) { if (isActiveImport(r.status)) c.active++; else if (r.status === "completed") c.done++; else if (r.status === "failed" || r.status === "quarantined") c.failed++; }
    return c;
  }, [rows]);
  const visible = (rows ?? []).filter((r) => showDone || !isFinishedImport(r.status));

  const dupInfo = async (r: ImportRow) => {
    if (!org || !company || !r.sha256) return;
    try { const d = await bridge.documentsOp<DuplicateCheck>(org.id, company.id, "check_duplicate", { sha256: r.sha256 }); setDupFor((c) => ({ ...c, [r.id]: d })); } catch (e) { fail(e); }
  };

  return (
    <section className="d-page" aria-labelledby="h-q">
      <header className="d-page__head"><div><h1 id="h-q">Upload Queue</h1><p className="a-hint">{counts.active} in progress · {counts.failed} need attention · {counts.done} stored</p></div>
        <div className="d-actions"><label className="d-check"><input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> Show finished</label><Button variant="secondary" onClick={() => void act(() => bridge.importsProcess())} disabled={!org}>Process now</Button></div></header>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {rows === null ? <p role="status">Loading the queue…</p> : visible.length === 0 ? <Panel><p>{rows.length ? "Nothing is waiting. Finished uploads are hidden." : "The upload queue is empty."}</p><Button onClick={() => go("import")}>Import documents</Button></Panel> : (
        <ul className="d-list" aria-label="Uploads">
          {visible.map((r) => (
            <li key={r.id} className="d-queue">
              <div className="d-queue__head">
                <strong>{r.display_name || r.file_name}</strong>
                <StatusChip tone={r.status === "completed" ? "success" : r.status === "failed" || r.status === "quarantined" ? "danger" : r.status === "cancelled" ? "neutral" : "info"}>{IMPORT_STATUS_LABEL[r.status]}</StatusChip>
                <span className="a-hint">{fmtBytes(r.size)} · {nodeName.get(r.node_id) ?? "folder"} · {fmtDateTime(r.created_at)}</span>
              </div>
              {isActiveImport(r.status) ? <progress value={fmtPct(r.bytes_sent, r.size)} max={100} aria-label={`${r.file_name} progress`} /> : null}
              {r.status === "completed" ? <p className="a-hint">Confirmed by the cloud. The original is unchanged on this computer.</p> : null}
              {r.error_code || r.status === "failed" || r.status === "quarantined" ? <p role="alert" className="d-queue__err">{importProblem(r.error_code, r.error_message)}{r.next_retry_at && r.status === "waiting" ? ` Next attempt: ${fmtDateTime(r.next_retry_at)}.` : ""}</p> : null}
              {r.status === "waiting" && r.error_code ? <p className="a-hint">Attempt {r.attempts}; the file is kept and will be retried automatically.</p> : null}
              <div className="d-actions">
                {r.error_code === "duplicate_detected" ? (
                  <>
                    <Button variant="secondary" className="a-btn--sm" onClick={() => void dupInfo(r)}>Show the existing document</Button>
                    <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.importRetry(r.id, { duplicatePolicy: "keep_both" }))}>Keep both</Button>
                    <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.importRetry(r.id, { duplicatePolicy: "skip" }))}>Skip this file</Button>
                  </>
                ) : null}
                {(r.status === "failed") && !needsDecision(r) ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.importRetry(r.id))}>Retry</Button> : null}
                {r.status === "failed" && r.error_code === "target_unavailable" ? <span className="a-hint">Re-import this file into another folder.</span> : null}
                {isActiveImport(r.status) && org ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.importCancel(org.id, r.id))}>Cancel</Button> : null}
                {!isActiveImport(r.status) ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.importRemove(r.id))}>Remove from list</Button> : null}
              </div>
              {dupFor[r.id] ? <p className="a-hint" role="status">{dupFor[r.id]?.document ? `Already stored as “${dupFor[r.id]?.document?.display_name}” in ${dupFor[r.id]?.document?.path.join(" / ")}.` : "An identical document exists in a folder you cannot see."}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
