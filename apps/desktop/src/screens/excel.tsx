import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Panel, StatusChip, type Tone } from "@artify/ui";
import type { XlAnalysis, XlCatalogue, XlColumn, XlCustomReport, XlDataset, XlExport, XlFixedCell, XlIssue, XlMapping, XlMappingDef, XlOp, XlPreview, XlReportResult, XlSheetMap, XlStandardReport, XlTemplate, XlValidation } from "../bridge/types";
import { fmtDateTime } from "../lib/format";
import { useApp } from "../state/app";
import { money } from "./accounting";

const TEMPLATE_STATUS: Record<string, { label: string; tone: Tone }> = { draft: { label: "Draft", tone: "neutral" }, active: { label: "Active", tone: "success" }, inactive: { label: "Inactive", tone: "warning" }, archived: { label: "Archived", tone: "neutral" } };
const EXPORT_STATUS: Record<string, { label: string; tone: Tone }> = { queued: { label: "Queued", tone: "info" }, running: { label: "Running", tone: "info" }, completed: { label: "Completed", tone: "success" }, failed: { label: "Failed", tone: "danger" } };
const RISK: Record<string, { label: string; tone: Tone }> = { none: { label: "No compatibility warnings", tone: "success" }, warned: { label: "Some features may not survive", tone: "warning" }, unsupported: { label: "Unsupported features present", tone: "danger" } };
const TABS: [string, string][] = [["templates", "Templates"], ["generate", "Generate export"], ["history", "Export history"], ["standard", "Standard reports"], ["custom", "Custom reports"]];
const SEVERITY: Record<string, Tone> = { error: "danger", warning: "warning", info: "info" };

function useXl() {
  const { bridge, org, company, fail } = useApp();
  const orgId = org?.id ?? "";
  const companyId = company?.id ?? "";
  const call = useCallback(<T,>(op: XlOp, args?: { ids?: string[]; query?: Record<string, string>; body?: unknown }) => bridge.xl<T>(orgId, companyId, op, args), [bridge, orgId, companyId]);
  return { bridge, orgId, companyId, fail, call };
}

function Issues({ issues, label }: { issues: XlIssue[]; label: string }) {
  if (!issues.length) return null;
  return (
    <ul className="d-list" aria-label={label}>
      {issues.map((i, n) => <li key={n}><StatusChip tone={SEVERITY[i.severity] ?? "neutral"}>{i.severity === "error" ? "Error" : i.severity === "warning" ? "Warning" : "Note"}</StatusChip> {i.message}</li>)}
    </ul>
  );
}

// ------------------------------------------------------------------ page
export function ExcelHome() {
  const { companyId, call } = useXl();
  const [tab, setTab] = useState("templates");
  const [builder, setBuilder] = useState<string | null>(null);
  if (!companyId) return <section><h1>Excel &amp; Reports</h1><p>Choose a company first.</p></section>;
  if (builder) return <MappingBuilder key={builder} templateId={builder} onClose={() => setBuilder(null)} />;
  return (
    <section>
      <h1>Excel &amp; Reports</h1>
      <p className="a-hint">Use your own Excel templates. Exports contain approved accounting data only unless you explicitly include unapproved items. Formulas in your template are kept; Excel recalculates them when the file is opened. Macros are never run.</p>
      <div role="tablist" aria-label="Excel sections" className="d-tabs">
        {TABS.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className="d-tab" onClick={() => setTab(k)}>{label}</button>)}
      </div>
      {tab === "templates" ? <Templates openBuilder={setBuilder} /> : tab === "generate" ? <Generate call={call} onDone={() => setTab("history")} /> : tab === "history" ? <History /> : tab === "standard" ? <Standard /> : <Custom />}
    </section>
  );
}

// ------------------------------------------------------------------ templates
function Templates({ openBuilder }: { openBuilder: (id: string) => void }) {
  const { bridge, orgId, companyId, fail, call } = useXl();
  const [rows, setRows] = useState<XlTemplate[] | null>(null);
  const [cat, setCat] = useState<XlCatalogue | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("custom_report");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { call<XlTemplate[]>("templates").then(setRows).catch((e) => { const m = fail(e); if (m) setMsg(m); }); }, [call, fail]);
  useEffect(() => { load(); call<XlCatalogue>("catalogue").then(setCat).catch(() => undefined); }, [load, call]);

  const run = async (done: string, fn: () => Promise<unknown>) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg(done); load(); } catch (e) { const m = fail(e); if (m) setMsg(m); } finally { setBusy(false); }
  };
  const upload = (templateId?: string) => run(templateId ? "New version uploaded. Review its mapping before using it." : "Template uploaded. Set up its mapping to start using it.", async () => {
    const r = await bridge.xlUploadPick(orgId, companyId, templateId ? { template_id: templateId } : { name: name.trim(), category });
    if (!r) throw { code: "cancelled", message: "No file was chosen.", status: null, retryable: false };
    if (!templateId) setName("");
  });

  return (
    <div>
      {msg ? <Alert tone={/uploaded|archived|Done|saved/i.test(msg) ? "success" : "info"}>{msg}</Alert> : null}
      <Panel>
        <h2>Add a template</h2>
        <p className="a-hint">Upload your own workbook (.xlsx; .xlsm only if your plan allows it). Macros are never executed. The file is stored privately for this company only.</p>
        <div className="d-actions">
          <label className="d-inline">Template name<input aria-label="Template name" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="d-inline">Category<input aria-label="Template category" list="xl-cats" value={category} onChange={(e) => setCategory(e.target.value)} /></label>
          <datalist id="xl-cats">{(cat?.categories ?? []).map((c) => <option key={c} value={c} />)}</datalist>
          <Button disabled={busy || !name.trim()} onClick={() => void upload()}>Choose Excel file…</Button>
        </div>
      </Panel>
      <h2>Templates</h2>
      {rows === null ? <p>Loading…</p> : rows.length === 0 ? <p>No templates yet. Upload your first workbook above; nothing is created for you.</p> : (
        <table className="d-table">
          <thead><tr><th>Name</th><th>Category</th><th>Status</th><th>Version</th><th>Mapping</th><th>Compatibility</th><th>Actions</th></tr></thead>
          <tbody>{rows.map((t) => (
            <tr key={t.id}>
              <td>{t.name}<span className="a-hint" style={{ display: "block" }}>{t.filename}</span></td>
              <td>{t.category.replaceAll("_", " ")}</td>
              <td><StatusChip tone={TEMPLATE_STATUS[t.status]?.tone ?? "neutral"}>{TEMPLATE_STATUS[t.status]?.label ?? t.status}</StatusChip></td>
              <td>v{t.current_version_no}</td>
              <td>{t.active_mapping_no ? `#${t.active_mapping_no} active` : "None yet"}</td>
              <td><StatusChip tone={RISK[t.risk_level]?.tone ?? "neutral"}>{RISK[t.risk_level]?.label ?? t.risk_level}</StatusChip></td>
              <td className="d-actions">
                <Button variant="secondary" className="a-btn--sm" aria-label={`Mapping for ${t.name}`} onClick={() => openBuilder(t.id)}>Mapping</Button>
                <Button variant="secondary" className="a-btn--sm" aria-label={`Replace file of ${t.name}`} disabled={busy} onClick={() => void upload(t.id)}>New version</Button>
                {t.status === "active" ? <Button variant="secondary" className="a-btn--sm" aria-label={`Deactivate ${t.name}`} disabled={busy} onClick={() => void run("Deactivated.", () => call("template_status", { ids: [t.id], body: { action: "deactivate" } }))}>Deactivate</Button> : t.status === "inactive" ? <Button variant="secondary" className="a-btn--sm" aria-label={`Activate ${t.name}`} disabled={busy} onClick={() => void run("Activated.", () => call("template_status", { ids: [t.id], body: { action: "activate" } }))}>Activate</Button> : null}
                <Button variant="secondary" className="a-btn--sm" aria-label={`Archive ${t.name}`} disabled={busy} onClick={() => { if (window.confirm(`Archive “${t.name}”? Its export history is kept.`)) void run("Archived. Its history is kept.", () => call("template_status", { ids: [t.id], body: { action: "archive" } })); }}>Archive</Button>
              </td>
            </tr>))}</tbody>
        </table>)}
    </div>
  );
}

// ------------------------------------------------------------------ mapping builder
const letter = /^[A-Za-z]{1,3}$/;

function suggest(analysis: XlAnalysis | null, sheet: string, dataset: XlDataset | undefined): XlColumn[] {
  const names = analysis?.sheets.find((s) => s.name === sheet)?.column_names ?? {};
  const out: XlColumn[] = [];
  for (const [col, header] of Object.entries(names)) {
    const h = header.trim().toLowerCase();
    const f = dataset?.fields.find((x) => x.label.toLowerCase() === h || x.key.replaceAll("_", " ") === h);
    if (f) out.push({ column: col, kind: "field", field: f.key });
  }
  return out;
}

function MappingBuilder({ templateId, onClose }: { templateId: string; onClose: () => void }) {
  const { companyId, fail, call } = useXl();
  const [t, setT] = useState<XlTemplate | null>(null);
  const [analysis, setAnalysis] = useState<XlAnalysis | null>(null);
  const [cat, setCat] = useState<XlCatalogue | null>(null);
  const [def, setDef] = useState<XlMappingDef>({ sheets: [], fixed_cells: [] });
  const [saved, setSaved] = useState<XlMapping | null>(null);
  const [issues, setIssues] = useState<XlIssue[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [ack, setAck] = useState(false);
  const [preview, setPreview] = useState<XlPreview["preview"] | null>(null);
  const [range, setRange] = useState({ from: "", to: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!companyId) return;
    const guard = (e: unknown) => { const m = fail(e); if (m) setMsg(m); };
    call<XlTemplate>("template", { ids: [templateId] }).then(setT).catch(guard);
    call<XlAnalysis>("template_analysis", { ids: [templateId] }).then(setAnalysis).catch(guard);
    call<XlCatalogue>("catalogue").then(setCat).catch(guard);
    call<XlMapping[]>("mappings", { ids: [templateId] }).then((ms) => {
      const m = ms.find((x) => x.status === "draft") ?? ms.find((x) => x.status === "active") ?? null;
      if (m) { setSaved(m); setDef(structuredClone(m.definition)); setIssues(m.validation); }
    }).catch(guard);
  }, [call, fail, companyId, templateId]);

  const dataset = (key: string) => cat?.datasets.find((d) => d.key === key);
  const patchSheet = (i: number, p: Partial<XlSheetMap>) => setDef((d) => ({ ...d, sheets: d.sheets.map((s, n) => (n === i ? { ...s, ...p } : s)) }));
  const patchCol = (i: number, j: number, p: Partial<XlColumn>) => setDef((d) => ({ ...d, sheets: d.sheets.map((s, n) => (n === i ? { ...s, columns: s.columns.map((c, m) => (m === j ? { ...c, ...p } : c)) } : s)) }));
  const addSheet = () => {
    const first = analysis?.sheets.find((s) => s.visibility === "visible") ?? analysis?.sheets[0];
    const ds = cat?.datasets[0];
    if (!first || !ds) return;
    setDef((d) => ({ ...d, sheets: [...d.sheets, { sheet: first.name, dataset: ds.key, header_row: first.suggested_header_row ?? 1, start_row: first.suggested_start_row ?? 2, template_rows: 1, columns: [] }] }));
  };
  const badLetters = def.sheets.some((s) => s.columns.some((c) => !letter.test(c.column)));

  const save = async () => {
    setBusy(true); setMsg(null); setPreview(null);
    try {
      const m = await call<XlMapping>("mapping_save", { ids: [templateId], body: { definition: def, mapping_id: saved?.status === "draft" ? saved.id : undefined } });
      setSaved(m); setIssues(m.validation);
      setMsg(m.ready ? "Draft saved and valid." : "Draft saved. Fix the errors below before activating; a draft may be incomplete.");
    } catch (e) { const m = fail(e); if (m) setMsg(m); } finally { setBusy(false); }
  };
  const activate = async () => {
    if (!saved) return;
    setBusy(true); setMsg(null);
    try { const m = await call<XlMapping>("mapping_activate", { ids: [saved.id], body: { acknowledge_risks: ack } }); setSaved(m); setMsg("Mapping activated. The template can now be used for exports."); }
    catch (e) { const m = fail(e); if (m) setMsg(m); } finally { setBusy(false); }
  };
  const runPreview = async () => {
    if (!saved) return;
    setBusy(true); setMsg(null);
    try { setPreview(await call<XlPreview["preview"]>("mapping_preview", { ids: [saved.id], body: { params: { date_from: range.from || undefined, date_to: range.to || undefined } } })); }
    catch (e) { const m = fail(e); if (m) setMsg(m); } finally { setBusy(false); }
  };

  if (!t || !cat) return <section><Button variant="secondary" onClick={onClose}>Back</Button><p>{msg ?? "Loading…"}</p></section>;
  const risky = (analysis?.risk_level ?? "none") !== "none";
  return (
    <section>
      <Button variant="secondary" onClick={onClose}>Back to templates</Button>
      <h1>Mapping · {t.name}</h1>
      <p className="a-hint">Version {t.current_version_no} · {saved ? `${saved.status} mapping #${saved.mapping_no}` : "no mapping yet"}. Choose which accounting field fills which column. Formulas in your template stay as they are; a column that holds a formula is only overwritten if you explicitly allow it.</p>
      {msg ? <Alert tone={/saved|activated/i.test(msg) ? "success" : "info"}>{msg}</Alert> : null}
      {analysis?.fidelity.filter((f) => f.status !== "preserved").length ? (
        <Panel><h2>Compatibility</h2><ul className="d-list" aria-label="Compatibility notes">{analysis.fidelity.filter((f) => f.status !== "preserved").map((f) => <li key={f.feature}><StatusChip tone={f.status === "unsupported" ? "danger" : "warning"}>{f.status === "unsupported" ? "Unsupported" : f.status === "not_recalculated" ? "Note" : "May change"}</StatusChip> {f.detail}</li>)}</ul></Panel>) : null}

      {def.sheets.map((s, i) => {
        const ds = dataset(s.dataset);
        return (
          <Panel key={i}>
            <h2>Worksheet {i + 1}</h2>
            <div className="d-actions">
              <label className="d-inline">Worksheet<select aria-label={`Worksheet ${i + 1} name`} value={s.sheet} onChange={(e) => patchSheet(i, { sheet: e.target.value })}>{(analysis?.sheets ?? []).map((x) => <option key={x.name} value={x.name}>{x.name}{x.visibility !== "visible" ? " (hidden)" : ""}</option>)}</select></label>
              <label className="d-inline">Data<select aria-label={`Data source ${i + 1}`} value={s.dataset} onChange={(e) => patchSheet(i, { dataset: e.target.value, columns: [] })}>{cat.datasets.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}</select></label>
              <label className="d-inline">Header row<input aria-label={`Header row ${i + 1}`} type="number" min={0} value={s.header_row} onChange={(e) => patchSheet(i, { header_row: Number(e.target.value) })} /></label>
              <label className="d-inline">First data row<input aria-label={`First data row ${i + 1}`} type="number" min={1} value={s.start_row} onChange={(e) => patchSheet(i, { start_row: Number(e.target.value) })} /></label>
              <Button variant="secondary" className="a-btn--sm" onClick={() => patchSheet(i, { columns: suggest(analysis, s.sheet, ds) })}>Suggest from headers</Button>
              <Button variant="secondary" className="a-btn--sm" onClick={() => setDef((d) => ({ ...d, sheets: d.sheets.filter((_, n) => n !== i) }))}>Remove worksheet</Button>
            </div>
            {ds?.note ? <p className="a-hint">{ds.note}</p> : null}
            <table className="d-table">
              <thead><tr><th>Column</th><th>Heading in your file</th><th>Fills with</th><th>Format</th><th>Required</th><th /></tr></thead>
              <tbody>{s.columns.map((c, j) => (
                <tr key={j}>
                  <td><input aria-label={`Column letter ${i + 1}.${j + 1}`} size={3} value={c.column} onChange={(e) => patchCol(i, j, { column: e.target.value.toUpperCase() })} /></td>
                  <td>{analysis?.sheets.find((x) => x.name === s.sheet)?.column_names[c.column.toUpperCase()] ?? "—"}</td>
                  <td>
                    <select aria-label={`Field for column ${c.column || j + 1}`} value={c.kind === "static" ? "__static" : (c.field ?? "")} onChange={(e) => (e.target.value === "__static" ? patchCol(i, j, { kind: "static", field: undefined, value: "" }) : patchCol(i, j, { kind: "field", field: e.target.value, value: undefined }))}>
                      <option value="">— choose —</option>
                      {ds?.fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                      <option value="__static">Fixed text…</option>
                    </select>
                    {c.kind === "static" ? <input aria-label={`Fixed text for column ${c.column}`} dir="auto" value={c.value ?? ""} onChange={(e) => patchCol(i, j, { value: e.target.value })} /> : null}
                  </td>
                  <td><input aria-label={`Format for column ${c.column}`} size={12} placeholder="e.g. #,##0.000" value={c.format ?? ""} onChange={(e) => patchCol(i, j, { format: e.target.value || undefined })} /></td>
                  <td><input type="checkbox" aria-label={`Required column ${c.column}`} checked={!!c.required} onChange={(e) => patchCol(i, j, { required: e.target.checked })} /></td>
                  <td><Button variant="secondary" className="a-btn--sm" aria-label={`Remove column ${c.column || j + 1}`} onClick={() => setDef((d) => ({ ...d, sheets: d.sheets.map((x, n) => (n === i ? { ...x, columns: x.columns.filter((_, m) => m !== j) } : x)) }))}>Remove</Button></td>
                </tr>))}</tbody>
            </table>
            <Button variant="secondary" className="a-btn--sm" onClick={() => patchSheet(i, { columns: [...s.columns, { column: "", kind: "field", field: "" }] })}>Add column</Button>
          </Panel>);
      })}
      <div className="d-actions">
        <Button variant="secondary" onClick={addSheet} disabled={!analysis}>Add worksheet mapping</Button>
        <Button variant="secondary" onClick={() => setDef((d) => ({ ...d, fixed_cells: [...d.fixed_cells, { sheet: d.sheets[0]?.sheet ?? analysis?.sheets[0]?.name ?? "", cell: "", kind: "field", field: "company_name" } as XlFixedCell] }))}>Add header cell</Button>
      </div>
      {def.fixed_cells.length ? (
        <Panel>
          <h2>Header cells</h2>
          <ul className="d-list">{def.fixed_cells.map((f, k) => (
            <li key={k} className="d-actions">
              <select aria-label={`Header cell ${k + 1} worksheet`} value={f.sheet} onChange={(e) => setDef((d) => ({ ...d, fixed_cells: d.fixed_cells.map((x, n) => (n === k ? { ...x, sheet: e.target.value } : x)) }))}>{(analysis?.sheets ?? []).map((x) => <option key={x.name}>{x.name}</option>)}</select>
              <input aria-label={`Header cell ${k + 1} address`} size={5} placeholder="B3" value={f.cell} onChange={(e) => setDef((d) => ({ ...d, fixed_cells: d.fixed_cells.map((x, n) => (n === k ? { ...x, cell: e.target.value.toUpperCase() } : x)) }))} />
              <select aria-label={`Header cell ${k + 1} value`} value={f.field ?? ""} onChange={(e) => setDef((d) => ({ ...d, fixed_cells: d.fixed_cells.map((x, n) => (n === k ? { ...x, kind: "field", field: e.target.value } : x)) }))}>{cat.header_fields.map((h) => <option key={h.key} value={h.key}>{h.label}</option>)}</select>
              <Button variant="secondary" className="a-btn--sm" onClick={() => setDef((d) => ({ ...d, fixed_cells: d.fixed_cells.filter((_, n) => n !== k) }))}>Remove</Button>
            </li>))}</ul>
        </Panel>) : null}

      <Issues issues={issues} label="Mapping validation" />
      <div className="d-actions" role="group" aria-label="Mapping actions">
        <Button disabled={busy || !def.sheets.length || badLetters} onClick={() => void save()}>Save draft &amp; validate</Button>
        <label className="d-inline">Preview from<input aria-label="Preview from" placeholder="YYYY-MM-DD" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></label>
        <label className="d-inline">to<input aria-label="Preview to" placeholder="YYYY-MM-DD" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></label>
        <Button variant="secondary" disabled={busy || !saved?.ready} onClick={() => void runPreview()}>Preview with real data</Button>
        {risky ? <label className="d-inline"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have read the compatibility notes</label> : null}
        <Button disabled={busy || !saved || saved.status !== "draft" || !saved.ready || (risky && !ack)} onClick={() => void activate()}>Activate mapping</Button>
      </div>
      {badLetters ? <p className="a-hint">Column letters must be 1 to 3 letters (A, B, AA…).</p> : null}
      {preview ? <PreviewTable p={preview} /> : null}
    </section>
  );
}

function PreviewTable({ p }: { p: XlPreview["preview"] }) {
  return (
    <div aria-label="Preview">
      <h2>Preview (the first rows of the real output)</h2>
      {p.fixed_cells.length ? <p>{p.fixed_cells.map((f) => `${f.sheet}!${f.cell} = ${f.value || "(empty)"}`).join(" · ")}</p> : null}
      {p.sheets.map((s) => (
        <div key={s.sheet}>
          <h3>{s.sheet} — {s.rows} row(s) will be written from row {s.start_row}</h3>
          {s.sample.length === 0 ? <p>No approved rows match.</p> : (
            <table className="d-table"><thead><tr><th>Row</th>{s.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{s.sample.map((r) => <tr key={r.row}><td>{r.row}</td>{s.columns.map((c) => <td key={c}>{r.cells[c] ?? ""}</td>)}</tr>)}</tbody></table>)}
        </div>))}
    </div>
  );
}

// ------------------------------------------------------------------ generate
function Generate({ call, onDone }: { call: ReturnType<typeof useXl>["call"]; onDone: () => void }) {
  const { bridge, orgId, companyId, fail } = useXl();
  const [templates, setTemplates] = useState<XlTemplate[]>([]);
  const [tid, setTid] = useState("");
  const [range, setRange] = useState({ from: "", to: "" });
  const [unapproved, setUnapproved] = useState(false);
  const [val, setVal] = useState<XlValidation | null>(null);
  const [preview, setPreview] = useState<XlPreview["preview"] | null>(null);
  const [repeat, setRepeat] = useState({ ack: false, reason: "" });
  const [result, setResult] = useState<XlExport | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());

  useEffect(() => { call<XlTemplate[]>("templates", { query: { status: "active" } }).then((r) => { setTemplates(r); setTid((c) => c || r[0]?.id || ""); }).catch((e) => { const m = fail(e); if (m) setMsg(m); }); }, [call, fail]);
  const params = useMemo(() => ({ date_from: range.from || undefined, date_to: range.to || undefined, include_unapproved: unapproved || undefined }), [range, unapproved]);
  const reset = () => { setVal(null); setPreview(null); setResult(null); key.current = crypto.randomUUID(); setRepeat({ ack: false, reason: "" }); };

  const check = async () => {
    setBusy(true); setMsg(null); setResult(null);
    try { const r = await call<XlPreview>("export_preview", { body: { template_id: tid, params } }); setVal(r.validation); setPreview(r.preview); }
    catch (e) { const m = fail(e); if (m) setMsg(m); } finally { setBusy(false); }
  };
  const generate = async () => {
    setBusy(true); setMsg(null);
    try {
      let e = await call<XlExport>("export_create", { body: { template_id: tid, params, idempotency_key: key.current, acknowledge_repeat: repeat.ack, reason: repeat.reason || undefined } });
      for (let n = 0; n < 120 && (e.status === "queued" || e.status === "running"); n++) { await new Promise((r) => setTimeout(r, 1000)); e = await call<XlExport>("export", { ids: [e.id] }); }
      setResult(e);
    } catch (e) { const m = fail(e); if (m) setMsg(m); } finally { setBusy(false); }
  };
  const save = async () => {
    if (!result) return;
    try { const p = await bridge.xlExportSave(orgId, companyId, result.id, result.filename ?? "export.xlsx"); setMsg(p ? `Saved to ${p}. Its checksum was verified.` : "Saving was cancelled."); }
    catch (e) { const m = fail(e); if (m) setMsg(m); }
  };
  const needsRepeat = !!val?.repeat.needs_acknowledgement;
  const canGo = !!val?.can_generate && (!needsRepeat || (repeat.ack && repeat.reason.trim().length >= 3));

  return (
    <div>
      {msg ? <Alert tone={/saved/i.test(msg) ? "success" : "info"}>{msg}</Alert> : null}
      {templates.length === 0 ? <p>No active templates. Upload a template and activate a mapping first.</p> : (
        <Panel>
          <div className="d-actions">
            <label className="d-inline">Template<select aria-label="Template" value={tid} onChange={(e) => { setTid(e.target.value); reset(); }}>{templates.map((t) => <option key={t.id} value={t.id}>{t.name} (v{t.current_version_no})</option>)}</select></label>
            <label className="d-inline">From<input aria-label="Export from" placeholder="YYYY-MM-DD" value={range.from} onChange={(e) => { setRange({ ...range, from: e.target.value }); reset(); }} /></label>
            <label className="d-inline">To<input aria-label="Export to" placeholder="YYYY-MM-DD" value={range.to} onChange={(e) => { setRange({ ...range, to: e.target.value }); reset(); }} /></label>
            <label className="d-inline"><input type="checkbox" checked={unapproved} onChange={(e) => { setUnapproved(e.target.checked); reset(); }} /> Include unapproved transactions (the file is marked as unapproved; needs permission)</label>
            <Button disabled={busy || !tid} onClick={() => void check()}>Validate &amp; preview</Button>
          </div>
        </Panel>)}
      {val ? (
        <div>
          <h2>Before you generate</h2>
          <p>{val.total_rows} row(s) · {val.transactions} transaction(s) · template “{val.template.name}” v{val.template.version_no}, mapping #{val.template.mapping_no}{val.include_unapproved ? " · INCLUDES UNAPPROVED DATA" : " · approved data only"}</p>
          <Issues issues={val.issues} label="Export checks" />
          {needsRepeat ? (
            <Alert tone="warning">
              {val.repeat.transactions} of these transaction(s) were already exported with this template: {val.repeat.exports.slice(0, 3).map((x) => `${x.filename ?? "export"} (${fmtDateTime(x.created_at)})`).join("; ")}.
              <span className="d-actions">
                <label className="d-inline"><input type="checkbox" checked={repeat.ack} onChange={(e) => setRepeat({ ...repeat, ack: e.target.checked })} /> I want a repeat export</label>
                <label className="d-inline">Reason<input aria-label="Reason for the repeat export" dir="auto" value={repeat.reason} onChange={(e) => setRepeat({ ...repeat, reason: e.target.value })} /></label>
              </span>
            </Alert>) : null}
          {preview ? <PreviewTable p={preview} /> : null}
          <div className="d-actions"><Button disabled={busy || !canGo} onClick={() => void generate()}>Generate Excel file</Button></div>
        </div>) : null}
      {result ? (
        <Panel>
          <h2>Result</h2>
          <p><StatusChip tone={EXPORT_STATUS[result.status]?.tone ?? "neutral"}>{EXPORT_STATUS[result.status]?.label ?? result.status}</StatusChip> {result.status === "completed" ? `${result.filename} · ${Object.values(result.row_counts ?? {}).reduce((a, b) => a + b, 0)} row(s)` : result.error_message}</p>
          {result.status === "completed" ? <div className="d-actions"><Button onClick={() => void save()}>Save as…</Button><Button variant="secondary" onClick={onDone}>Open export history</Button></div> : <p className="a-hint">Nothing was produced; no partial file exists. You can correct the problem and generate again.</p>}
        </Panel>) : null}
    </div>
  );
}

// ------------------------------------------------------------------ history
function History() {
  const { bridge, orgId, companyId, fail, call } = useXl();
  const [rows, setRows] = useState<XlExport[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const load = useCallback(() => { call<{ items: XlExport[] }>("exports", { query: { limit: "100" } }).then((r) => setRows(r.items)).catch((e) => { const m = fail(e); if (m) setMsg(m); }); }, [call, fail]);
  useEffect(() => { load(); }, [load]);
  const guard = async (fn: () => Promise<string | null>) => { try { const m = await fn(); if (m) setMsg(m); load(); } catch (e) { const m = fail(e); if (m) setMsg(m); } };
  return (
    <div>
      {msg ? <Alert tone={/saved|matches|created/i.test(msg) ? "success" : "info"}>{msg}</Alert> : null}
      <p className="a-hint">Every export is kept as a permanent record with the template version, mapping, filters and checksum it was made with. A re-export creates a new record; it never replaces the first.</p>
      <label className="d-inline">Reason for a re-export<input aria-label="Reason for re-export" dir="auto" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      {rows === null ? <p>Loading…</p> : rows.length === 0 ? <p>No exports yet.</p> : (
        <table className="d-table">
          <thead><tr><th>Created</th><th>Template</th><th>Status</th><th>Rows</th><th>Data</th><th>By</th><th>Actions</th></tr></thead>
          <tbody>{rows.map((e) => (
            <tr key={e.id}>
              <td>{fmtDateTime(e.created_at)}</td>
              <td>{e.template_name}<span className="a-hint" style={{ display: "block" }}>v{e.template_version} · mapping #{e.mapping_no}{e.source_export_id ? " · re-export" : ""}</span></td>
              <td><StatusChip tone={EXPORT_STATUS[e.status]?.tone ?? "neutral"}>{EXPORT_STATUS[e.status]?.label ?? e.status}</StatusChip>{e.error_message ? <span className="a-hint" style={{ display: "block" }}>{e.error_message}</span> : null}</td>
              <td>{Object.values(e.row_counts ?? {}).reduce((a, b) => a + b, 0)}</td>
              <td>{e.include_unapproved ? <StatusChip tone="warning">Includes unapproved</StatusChip> : "Approved only"}</td>
              <td>{e.created_by_name ?? "—"}</td>
              <td className="d-actions">
                {e.status === "completed" ? (
                  <>
                    <Button variant="secondary" className="a-btn--sm" aria-label={`Save ${e.filename}`} onClick={() => void guard(async () => { const p = await bridge.xlExportSave(orgId, companyId, e.id, e.filename ?? "export.xlsx"); return p ? `Saved to ${p}. Its checksum was verified.` : null; })}>Save as…</Button>
                    <Button variant="secondary" className="a-btn--sm" aria-label={`Verify ${e.filename}`} onClick={() => void guard(async () => { const v = await call<{ ok: boolean }>("export_verify", { ids: [e.id] }); return v.ok ? "The stored file matches its recorded checksum." : "WARNING: the stored file does not match its recorded checksum."; })}>Verify</Button>
                  </>) : null}
                <Button variant="secondary" className="a-btn--sm" aria-label={`Re-export ${e.template_name} ${e.id}`} disabled={reason.trim().length < 3} onClick={() => void guard(async () => { await call("export_regenerate", { ids: [e.id], body: { idempotency_key: crypto.randomUUID(), reason } }); return "Re-export created as a new record."; })}>Re-export</Button>
              </td>
            </tr>))}</tbody>
        </table>)}
    </div>
  );
}

// ------------------------------------------------------------------ reports
function ReportTable({ r }: { r: XlReportResult }) {
  return (
    <div aria-label="Report result">
      <p className="a-hint">{r.note || "Management information from approved data."} This is not a statutory filing. {r.truncated ? "The result was cut at the maximum number of rows." : ""}</p>
      {r.rows.length === 0 ? <p>No rows for these filters.</p> : (
        <table className="d-table"><thead><tr>{r.columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
          <tbody>{r.rows.map((row, n) => <tr key={n}>{r.columns.map((c) => <td key={c.key}>{c.kind === "money" ? money(row[c.key]) : (row[c.key] ?? "")}</td>)}</tr>)}</tbody></table>)}
      <p>{r.total} row(s)</p>
    </div>
  );
}

function Standard() {
  const { fail, call } = useXl();
  const [list, setList] = useState<XlStandardReport[]>([]);
  const [key, setKey] = useState("");
  const [p, setP] = useState({ from: "", to: "", asOf: "" });
  const [res, setRes] = useState<XlReportResult | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { call<XlStandardReport[]>("reports_standard").then((r) => { setList(r); setKey((k) => k || r[0]?.key || ""); }).catch((e) => { const m = fail(e); if (m) setMsg(m); }); }, [call, fail]);
  const cur = list.find((r) => r.key === key);
  const run = async () => {
    setMsg(null);
    const params: Record<string, string> = {};
    if (cur?.filters.includes("date_from") && p.from) params.date_from = p.from;
    if (cur?.filters.includes("date_to") && p.to) params.date_to = p.to;
    if (cur?.filters.includes("as_of") && p.asOf) params.as_of = p.asOf;
    try { setRes(await call<XlReportResult>("report_run", { ids: [key], query: { limit: "100" }, body: { params } })); } catch (e) { const m = fail(e); if (m) setMsg(m); }
  };
  return (
    <div>
      {msg ? <Alert tone="info">{msg}</Alert> : null}
      <div className="d-actions">
        <label className="d-inline">Report<select aria-label="Standard report" value={key} onChange={(e) => { setKey(e.target.value); setRes(null); }}>{list.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select></label>
        {cur?.filters.includes("date_from") ? <label className="d-inline">From<input aria-label="Report from" placeholder="YYYY-MM-DD" value={p.from} onChange={(e) => setP({ ...p, from: e.target.value })} /></label> : null}
        {cur?.filters.includes("date_to") ? <label className="d-inline">To<input aria-label="Report to" placeholder="YYYY-MM-DD" value={p.to} onChange={(e) => setP({ ...p, to: e.target.value })} /></label> : null}
        {cur?.filters.includes("as_of") ? <label className="d-inline">As of<input aria-label="Report as of" placeholder="YYYY-MM-DD" value={p.asOf} onChange={(e) => setP({ ...p, asOf: e.target.value })} /></label> : null}
        <Button disabled={!key} onClick={() => void run()}>Run report</Button>
      </div>
      {cur ? <p className="a-hint">{cur.description} {cur.note}</p> : null}
      {res ? <ReportTable r={res} /> : null}
    </div>
  );
}

function Custom() {
  const { fail, call } = useXl();
  const [cat, setCat] = useState<XlCatalogue | null>(null);
  const [list, setList] = useState<XlCustomReport[]>([]);
  const [templates, setTemplates] = useState<XlTemplate[]>([]);
  const [name, setName] = useState("");
  const [dataset, setDataset] = useState("");
  const [fields, setFields] = useState<string[]>([]);
  const [groupBy, setGroupBy] = useState("");
  const [sortField, setSortField] = useState("");
  const [tpl, setTpl] = useState("");
  const [issues, setIssues] = useState<XlIssue[]>([]);
  const [res, setRes] = useState<XlReportResult | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const guard = useCallback((e: unknown) => { const m = fail(e); if (m) setMsg(m); }, [fail]);
  const load = useCallback(() => { call<XlCustomReport[]>("reports_custom").then(setList).catch(guard); }, [call, guard]);
  useEffect(() => { load(); call<XlCatalogue>("catalogue").then((c) => { setCat(c); setDataset((d) => d || c.datasets.find((x) => x.custom)?.key || ""); }).catch(guard); call<XlTemplate[]>("templates", { query: { status: "active" } }).then(setTemplates).catch(() => undefined); }, [load, call, guard]);
  const ds = cat?.datasets.find((d) => d.key === dataset);
  const definition = () => ({ dataset, fields, group_by: groupBy || null, sort: sortField ? [{ field: sortField, direction: "asc" }] : [] });
  const validate = async () => { try { const r = await call<{ issues: XlIssue[]; ready: boolean }>("custom_validate", { body: { definition: definition(), template_id: tpl || null } }); setIssues(r.issues); setMsg(r.ready ? "The report definition is valid." : null); } catch (e) { guard(e); } };
  const create = async () => { try { await call("custom_create", { body: { name: name.trim(), definition: definition(), template_id: tpl || null } }); setMsg("Report saved."); setName(""); setFields([]); load(); } catch (e) { guard(e); } };
  const run = async (id: string) => { try { setRes(await call<XlReportResult>("custom_run", { ids: [id], query: { limit: "100" }, body: { params: {} } })); } catch (e) { guard(e); } };
  return (
    <div>
      {msg ? <Alert tone={/saved|valid/i.test(msg) ? "success" : "info"}>{msg}</Alert> : null}
      <p className="a-hint">Build a report from governed data sources only: choose fields, one grouping and a sort. There is no free-form query.</p>
      <Panel>
        <h2>New report</h2>
        <div className="d-actions">
          <label className="d-inline">Name<input aria-label="Report name" dir="auto" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="d-inline">Data<select aria-label="Report data source" value={dataset} onChange={(e) => { setDataset(e.target.value); setFields([]); setGroupBy(""); setSortField(""); }}>{(cat?.datasets ?? []).filter((d) => d.custom).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}</select></label>
          <label className="d-inline">Group by<select aria-label="Group by" value={groupBy} onChange={(e) => setGroupBy(e.target.value)}><option value="">No grouping</option>{(ds?.groupable ?? []).map((g) => <option key={g} value={g}>{g.replaceAll("_", " ")}</option>)}</select></label>
          <label className="d-inline">Sort by<select aria-label="Sort by" value={sortField} onChange={(e) => setSortField(e.target.value)}><option value="">Default</option>{(ds?.fields ?? []).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}</select></label>
          <label className="d-inline">Excel template<select aria-label="Report template" value={tpl} onChange={(e) => setTpl(e.target.value)}><option value="">None</option>{templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
        </div>
        <fieldset><legend>Fields</legend>
          <div className="d-actions">{(ds?.fields ?? []).map((f) => <label key={f.key} className="d-inline"><input type="checkbox" checked={fields.includes(f.key)} onChange={(e) => setFields(e.target.checked ? [...fields, f.key] : fields.filter((x) => x !== f.key))} /> {f.label}</label>)}</div>
        </fieldset>
        <Issues issues={issues} label="Report checks" />
        <div className="d-actions"><Button variant="secondary" disabled={!fields.length} onClick={() => void validate()}>Validate</Button><Button disabled={!fields.length || !name.trim()} onClick={() => void create()}>Save report</Button></div>
      </Panel>
      <h2>Saved reports</h2>
      {list.length === 0 ? <p>No custom reports yet.</p> : (
        <ul className="d-list">{list.map((r) => (
          <li key={r.id} className="d-actions">
            <strong>{r.name}</strong> <span className="a-hint">{r.dataset.replaceAll("_", " ")} · {r.definition.fields.length} field(s)</span>
            <Button variant="secondary" className="a-btn--sm" aria-label={`Run ${r.name}`} onClick={() => void run(r.id)}>Run</Button>
            <Button variant="secondary" className="a-btn--sm" aria-label={`Archive report ${r.name}`} onClick={() => { if (window.confirm(`Archive “${r.name}”?`)) void call("custom_archive", { ids: [r.id] }).then(load).catch(guard); }}>Archive</Button>
          </li>))}</ul>)}
      {res ? <ReportTable r={res} /> : null}
    </div>
  );
}
