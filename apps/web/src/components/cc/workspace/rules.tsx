"use client";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { DataTable, Field, Status } from "@/components/kit";
import { errorText, tapi, useTenantApi } from "@/lib/tenant";
import { FormDialog, KIND_LABEL, NodeSelect, readOnlyNote, useChange, type CField, type Category, type Ctx, type FilingRule, type Kinds, type NamingRule } from "./common";

const base = (c: Ctx) => `/companies/${c.companyId}/workspace`;
const archivedChip = (a: string | null, active = true) => <Status value={a ? "retired" : active ? "active" : "inactive"} text={a ? "Archived" : active ? "Active" : "Inactive"} />;

/* ---------------------------------------------------------------- categories */
export function CategoriesTab({ ctx, kinds }: { ctx: Ctx; kinds: Kinds | null }) {
  const change = useChange(ctx);
  const [edit, setEdit] = useState<Category | "new" | null>(null);
  const can = ctx.editable && ctx.ws.capabilities.configure;
  return (
    <div className="grid gap-4">
      {readOnlyNote(can)}
      <Panel className="grid gap-3">
        <div className="a-actions" style={{ justifyContent: "space-between" }}>
          <h2>Document categories</h2>
          <Button disabled={!can} onClick={() => setEdit("new")}>Add category</Button>
        </div>
        <p className="a-hint">A category is YOUR name for a kind of document. Each one is mapped to an Artify document kind, which is what later phases use to read it. Folder names never decide accounting treatment.</p>
        <DataTable<Category> caption="Document categories" rows={ctx.ws.categories} loading={false} error={null} rowKey={(c) => c.id} empty="No categories yet."
          columns={[
            { key: "n", header: "Category", render: (c) => c.name },
            { key: "k", header: "Maps to (Artify document kind)", render: (c) => `${KIND_LABEL(kinds, c.document_kind)} · ${c.accounting_domain} · ${c.processing_type.replace(/_/g, " ")}` },
            { key: "f", header: "Files here", render: (c) => ctx.ws.nodes.find((n) => n.id === c.node_id)?.label ?? "—" },
            { key: "t", header: "File types", render: (c) => c.allowed_file_types.join(", ") || "any" },
            { key: "s", header: "Status", render: (c) => archivedChip(c.archived_at, c.is_active) },
            { key: "a", header: "Actions", render: (c) => (
              <span className="a-actions">
                <Button variant="secondary" className="a-btn--sm" disabled={!can || !!c.archived_at} onClick={() => setEdit(c)}>Edit</Button>
                <Button variant="secondary" className="a-btn--sm" disabled={!can} onClick={() => void change("POST", `/categories/${c.id}/${c.archived_at ? "restore" : "archive"}`, {}, c.archived_at ? "Restored." : "Archived.").catch(() => undefined)}>{c.archived_at ? "Restore" : "Archive"}</Button>
              </span>) },
          ]} />
      </Panel>
      {edit ? <CategoryDialog ctx={ctx} kinds={kinds} cat={edit === "new" ? null : edit} onClose={() => setEdit(null)} /> : null}
    </div>
  );
}

function CategoryDialog({ ctx, kinds, cat, onClose }: { ctx: Ctx; kinds: Kinds | null; cat: Category | null; onClose: () => void }) {
  const change = useChange(ctx);
  const [f, setF] = useState({ name: cat?.name ?? "", description: cat?.description ?? "", kind: cat?.document_kind ?? "", node: cat?.node_id ?? "", types: (cat?.allowed_file_types ?? []).join(", "), ref: cat?.processing_type === "reference_only", active: cat?.is_active ?? true });
  const kind = kinds?.items.find((k) => k.key === f.kind);
  return (
    <FormDialog title={cat ? `Edit ${cat.name}` : "Add category"} submit="Save" onClose={onClose} disabled={!f.name.trim() || !f.kind}
      onSubmit={async () => {
        const body = { name: f.name.trim(), description: f.description, document_kind: f.kind, node_id: f.node || null, is_active: f.active,
          allowed_file_types: f.types.split(",").map((x) => x.trim()).filter(Boolean), processing_type: f.ref ? "reference_only" : kind?.processing_type };
        await change(cat ? "PATCH" : "POST", cat ? `/categories/${cat.id}` : "/categories", body, "Category saved.");
      }}>
      <Field label="Category name">{(id) => <input id={id} value={f.name} maxLength={120} required onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
      <Field label="Artify document kind (required)" hint={kind ? `${kind.description}. Accounting domain: ${kind.accounting_domain}. Processing: ${kind.processing_type.replace(/_/g, " ")}.` : "Choose what this document is, whatever you call it."}>
        {(id) => <select id={id} value={f.kind} required onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="">Choose…</option>{kinds?.items.map((k) => <option key={k.key} value={k.key}>{k.name}</option>)}</select>}
      </Field>
      <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="checkbox" checked={f.ref} onChange={(e) => setF({ ...f, ref: e.target.checked })} /> Store for reference only (no extraction or accounting processing)</label>
      <NodeSelect nodes={ctx.ws.nodes} value={f.node} onChange={(v) => setF({ ...f, node: v })} label="Files into folder (optional)" allowEmpty />
      <Field label="Allowed file types" hint="Comma separated, for example pdf, jpg, xlsx. Empty allows any.">{(id) => <input id={id} value={f.types} onChange={(e) => setF({ ...f, types: e.target.value })} />}</Field>
      <Field label="Description">{(id) => <textarea id={id} value={f.description} maxLength={1000} onChange={(e) => setF({ ...f, description: e.target.value })} />}</Field>
      <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>
    </FormDialog>
  );
}

/* ---------------------------------------------------------------- custom fields */
const TYPE_LABEL: Record<string, string> = { short_text: "Short text", long_text: "Long text", number: "Whole number", decimal: "Decimal", currency: "Currency", date: "Date", dropdown: "Dropdown", multi_select: "Multi-select", boolean: "Yes / No", reference: "Reference" };

export function FieldsTab({ ctx }: { ctx: Ctx }) {
  const change = useChange(ctx);
  const [edit, setEdit] = useState<CField | "new" | null>(null);
  const can = ctx.editable && ctx.ws.capabilities.configure;
  return (
    <div className="grid gap-4">
      {readOnlyNote(can)}
      <Panel className="grid gap-3">
        <div className="a-actions" style={{ justifyContent: "space-between" }}><h2>Custom metadata fields</h2><Button disabled={!can} onClick={() => setEdit("new")}>Add field</Button></div>
        <p className="a-hint">Extra information to capture for this company’s documents (project, branch, cost centre…). A field’s internal key and type cannot change once created, and archiving a field or removing an option never discards stored values.</p>
        <DataTable<CField> caption="Custom fields" rows={ctx.ws.fields} loading={false} error={null} rowKey={(f) => f.id} empty="No custom fields yet."
          columns={[
            { key: "l", header: "Field", render: (f) => <>{f.label}{f.required ? " *" : ""}</> },
            { key: "k", header: "Internal key", render: (f) => <span className="a-mono">{f.key}</span> },
            { key: "t", header: "Type", render: (f) => TYPE_LABEL[f.data_type] ?? f.data_type },
            { key: "o", header: "Options", render: (f) => f.options.filter((o) => !o.archived_at).map((o) => o.label).join(", ") || "—" },
            { key: "w", header: "Applies to", render: (f) => (f.category_ids.length + f.node_ids.length ? `${f.category_ids.length} categories, ${f.node_ids.length} folders` : "Everywhere") },
            { key: "s", header: "Status", render: (f) => archivedChip(f.archived_at, f.is_active) },
            { key: "a", header: "Actions", render: (f) => (
              <span className="a-actions">
                <Button variant="secondary" className="a-btn--sm" disabled={!can || !!f.archived_at} onClick={() => setEdit(f)}>Edit</Button>
                <Button variant="secondary" className="a-btn--sm" disabled={!can} onClick={() => void change("POST", `/fields/${f.id}/${f.archived_at ? "restore" : "archive"}`, {}, f.archived_at ? "Restored." : "Archived (stored values are kept).").catch(() => undefined)}>{f.archived_at ? "Restore" : "Archive"}</Button>
              </span>) },
          ]} />
      </Panel>
      {edit ? <FieldDialog ctx={ctx} field={edit === "new" ? null : edit} onClose={() => setEdit(null)} /> : null}
    </div>
  );
}

function FieldDialog({ ctx, field, onClose }: { ctx: Ctx; field: CField | null; onClose: () => void }) {
  const change = useChange(ctx);
  const v = field?.validation ?? {};
  const [f, setF] = useState({
    label: field?.label ?? "", description: field?.description ?? "", type: field?.data_type ?? "short_text", required: field?.required ?? false,
    options: (field?.options ?? []).filter((o) => !o.archived_at).map((o) => `${o.value}|${o.label}`).join("\n"),
    maxLength: String(v.max_length ?? ""), min: String(v.min ?? ""), max: String(v.max ?? ""), places: String(v.decimal_places ?? ""),
    target: field?.reference_target ?? "workspace_node", cats: new Set(field?.category_ids ?? []), active: field?.is_active ?? true,
  });
  const t = f.type;
  const hasOptions = t === "dropdown" || t === "multi_select";
  const num = (s: string) => (s.trim() === "" ? undefined : Number(s));
  const submit = async () => {
    const validation: Record<string, unknown> = {};
    if ((t === "short_text" || t === "long_text") && f.maxLength) validation.max_length = num(f.maxLength);
    if (t === "number" || t === "decimal" || t === "currency") {
      if (f.min) validation.min = t === "number" ? num(f.min) : f.min;
      if (f.max) validation.max = t === "number" ? num(f.max) : f.max;
    }
    if ((t === "decimal" || t === "currency") && f.places) validation.decimal_places = num(f.places);
    const options = hasOptions ? f.options.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const [raw = "", ...rest] = l.split("|"); return { value: raw.trim(), label: (rest.join("|") || raw).trim() }; }) : [];
    const body: Record<string, unknown> = { label: f.label.trim(), description: f.description, required: f.required, validation, options, category_ids: [...f.cats], is_active: f.active };
    if (!field) { body.data_type = t; if (t === "reference") body.reference_target = f.target; }
    await change(field ? "PATCH" : "POST", field ? `/fields/${field.id}` : "/fields", body, "Field saved.");
  };
  return (
    <FormDialog title={field ? `Edit ${field.label}` : "Add custom field"} submit="Save" onClose={onClose} disabled={!f.label.trim()} onSubmit={submit}>
      <Field label="Field name">{(id) => <input id={id} value={f.label} maxLength={120} required onChange={(e) => setF({ ...f, label: e.target.value })} />}</Field>
      <Field label="Type" hint={field ? "The type cannot be changed after creation." : undefined}>
        {(id) => <select id={id} value={t} disabled={!!field} onChange={(e) => setF({ ...f, type: e.target.value })}>{Object.entries(TYPE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>}
      </Field>
      {t === "reference" ? <Field label="Refers to">{(id) => <select id={id} value={f.target} disabled={!!field} onChange={(e) => setF({ ...f, target: e.target.value })}><option value="workspace_node">A section or folder of this company</option><option value="document_category">A document category of this company</option></select>}</Field> : null}
      {hasOptions ? <Field label="Options" hint="One per line: value|label (for example ops|Operations). Removed options are archived, not erased.">{(id) => <textarea id={id} value={f.options} required onChange={(e) => setF({ ...f, options: e.target.value })} />}</Field> : null}
      {t === "short_text" || t === "long_text" ? <Field label="Maximum length (optional)">{(id) => <input id={id} type="number" min={1} value={f.maxLength} onChange={(e) => setF({ ...f, maxLength: e.target.value })} />}</Field> : null}
      {t === "number" || t === "decimal" || t === "currency" ? (
        <div className="a-toolbar">
          <Field label="Minimum (optional)">{(id) => <input id={id} value={f.min} inputMode="decimal" onChange={(e) => setF({ ...f, min: e.target.value })} />}</Field>
          <Field label="Maximum (optional)">{(id) => <input id={id} value={f.max} inputMode="decimal" onChange={(e) => setF({ ...f, max: e.target.value })} />}</Field>
          {t !== "number" ? <Field label="Decimal places (optional)">{(id) => <input id={id} type="number" min={0} max={6} value={f.places} onChange={(e) => setF({ ...f, places: e.target.value })} />}</Field> : null}
        </div>
      ) : null}
      <Field label="Description">{(id) => <textarea id={id} value={f.description} maxLength={1000} onChange={(e) => setF({ ...f, description: e.target.value })} />}</Field>
      <fieldset className="a-field"><legend>Applies to categories (none selected = everywhere)</legend>
        <div className="a-checks">{ctx.ws.categories.filter((c) => !c.archived_at).map((c) => (
          <label key={c.id}><input type="checkbox" checked={f.cats.has(c.id)} onChange={(e) => setF((p) => { const s = new Set(p.cats); if (e.target.checked) s.add(c.id); else s.delete(c.id); return { ...p, cats: s }; })} /> {c.name}</label>
        ))}{ctx.ws.categories.length === 0 ? <span className="a-hint">No categories yet.</span> : null}</div>
      </fieldset>
      <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="checkbox" checked={f.required} onChange={(e) => setF({ ...f, required: e.target.checked })} /> Required</label>
      <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>
    </FormDialog>
  );
}

/* ---------------------------------------------------------------- naming rules */
interface Preview { generated_filename: string; original_filename: string | null; warnings: string[]; used_fallback: boolean; renamed_to_avoid_overwrite: boolean }
const swap = <T,>(a: T[], i: number, j: number): T[] => { const b = [...a]; const x = b[i]; const y = b[j]; if (x === undefined || y === undefined) return b; b[i] = y; b[j] = x; return b; };
const parseSample = (s: string) => Object.fromEntries(s.split("\n").map((l) => l.split("=")).filter((p) => p.length >= 2 && (p[0] ?? "").trim()).map(([k = "", ...r]) => [k.trim(), r.join("=").trim()]));

export function NamingTab({ ctx, kinds }: { ctx: Ctx; kinds: Kinds | null }) {
  const change = useChange(ctx);
  const [edit, setEdit] = useState<NamingRule | "new" | null>(null);
  const can = ctx.editable && ctx.ws.capabilities.configure;
  const tokens = [...Object.keys(kinds?.naming_tokens ?? {}).map((k) => k), ...ctx.ws.fields.filter((f) => !f.archived_at).map((f) => f.key)];
  return (
    <div className="grid gap-4">
      {readOnlyNote(can)}
      <Panel className="grid gap-3">
        <div className="a-actions" style={{ justifyContent: "space-between" }}><h2>Document naming rules</h2><Button disabled={!can} onClick={() => setEdit("new")}>Add naming rule</Button></div>
        <p className="a-hint">Names are generated when documents are stored in a later phase; the original file name is always kept separately. If no rule applies, a safe default name is used.</p>
        <DataTable<NamingRule> caption="Naming rules" rows={ctx.ws.naming_rules} loading={false} error={null} rowKey={(r) => r.id} empty="No naming rules yet."
          columns={[
            { key: "n", header: "Rule", render: (r) => r.name }, { key: "p", header: "Pattern", render: (r) => <span className="a-mono">{r.pattern}</span> },
            { key: "c", header: "For category", render: (r) => ctx.ws.categories.find((c) => c.id === r.category_id)?.name ?? "All documents" },
            { key: "s", header: "Status", render: (r) => archivedChip(r.archived_at, r.is_active) },
            { key: "a", header: "Actions", render: (r) => (
              <span className="a-actions">
                <Button variant="secondary" className="a-btn--sm" disabled={!can || !!r.archived_at} onClick={() => setEdit(r)}>Edit</Button>
                <Button variant="secondary" className="a-btn--sm" disabled={!can} onClick={() => void change("POST", `/naming-rules/${r.id}/${r.archived_at ? "restore" : "archive"}`, {}, "Done.").catch(() => undefined)}>{r.archived_at ? "Restore" : "Archive"}</Button>
              </span>) },
          ]} />
        <p className="a-hint">Available tokens: {tokens.map((t) => `{${t}}`).join(" ")}. Add a fallback with {"{Token|fallback}"}.</p>
      </Panel>
      {edit ? <NamingDialog ctx={ctx} rule={edit === "new" ? null : edit} onClose={() => setEdit(null)} /> : null}
    </div>
  );
}

function NamingDialog({ ctx, rule, onClose }: { ctx: Ctx; rule: NamingRule | null; onClose: () => void }) {
  const change = useChange(ctx);
  const [f, setF] = useState({ name: rule?.name ?? "", pattern: rule?.pattern ?? "", cat: rule?.category_id ?? "", sample: "Supplier=Acme LLC\nDocumentDate=2026-03-04", file: "scan 001.pdf" });
  const [prev, setPrev] = useState<Preview | null>(null);
  const [perr, setPerr] = useState<string | null>(null);
  const run = async () => {
    setPerr(null);
    try { setPrev(await tapi<Preview>(ctx.orgId, `${base(ctx)}/naming-rules/preview`, { method: "POST", body: { pattern: f.pattern, category_id: f.cat || null, values: parseSample(f.sample), original_filename: f.file } })); }
    catch (e) { setPrev(null); setPerr(errorText(e)); }
  };
  return (
    <FormDialog title={rule ? `Edit ${rule.name}` : "Add naming rule"} submit="Save" onClose={onClose} disabled={!f.name.trim() || !f.pattern.trim()}
      onSubmit={async () => { await change(rule ? "PATCH" : "POST", rule ? `/naming-rules/${rule.id}` : "/naming-rules", { name: f.name.trim(), pattern: f.pattern, category_id: f.cat || null }, "Naming rule saved."); }}>
      <Field label="Rule name">{(id) => <input id={id} value={f.name} maxLength={120} required onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
      <Field label="Pattern" hint="For example {Supplier}_{InvoiceNumber}_{DocumentDate}">{(id) => <input id={id} className="a-mono" value={f.pattern} maxLength={300} required onChange={(e) => setF({ ...f, pattern: e.target.value })} />}</Field>
      <Field label="Applies to category" hint="Only one active rule per category.">
        {(id) => <select id={id} value={f.cat} onChange={(e) => setF({ ...f, cat: e.target.value })}><option value="">All documents (company default)</option>{ctx.ws.categories.filter((c) => !c.archived_at).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
      </Field>
      <fieldset className="a-field"><legend>Preview with sample values</legend>
        <Field label="Sample values (one per line, name=value)">{(id) => <textarea id={id} className="a-mono" value={f.sample} onChange={(e) => setF({ ...f, sample: e.target.value })} />}</Field>
        <Field label="Original file name">{(id) => <input id={id} value={f.file} onChange={(e) => setF({ ...f, file: e.target.value })} />}</Field>
        <div><Button type="button" variant="secondary" className="a-btn--sm" onClick={() => void run()}>Preview</Button></div>
        {perr ? <Alert tone="danger">{perr}</Alert> : null}
        {prev ? <Alert tone="info"><strong className="a-mono">{prev.generated_filename}</strong>{prev.warnings.length ? <ul>{prev.warnings.map((w) => <li key={w}>{w}</li>)}</ul> : null}<span className="a-hint">Original name kept: {prev.original_filename ?? "—"}</span></Alert> : null}
      </fieldset>
    </FormDialog>
  );
}

/* ---------------------------------------------------------------- filing rules */
const COND_FIELDS = [["document_kind", "Document kind"], ["source", "Source"], ["filename", "File name"]] as const;
const OPS: [string, string][] = [["equals", "is"], ["not_equals", "is not"], ["in", "is one of"], ["contains", "contains"], ["starts_with", "starts with"], ["ends_with", "ends with"], ["is_present", "is filled in"], ["is_empty", "is empty"]];
interface Row { field: string; operator: string; value: string }
interface Eval { winner: { name: string; target_node_id: string } | null; matches: { name: string; priority: number }[]; ambiguous: boolean }
interface Finding { code: string; message: string }

export function FilingTab({ ctx, kinds }: { ctx: Ctx; kinds: Kinds | null }) {
  const change = useChange(ctx);
  const [edit, setEdit] = useState<FilingRule | "new" | null>(null);
  const can = ctx.editable && ctx.ws.capabilities.configure;
  const rules = ctx.ws.filing_rules;
  const findings = useTenantApi<{ findings: Finding[] }>(ctx.orgId, `${base(ctx)}/filing-rules/analysis?v=${ctx.ws.version}`);
  const live = rules.filter((r) => !r.archived_at);
  const move = (i: number, d: -1 | 1) => { const o = swap(live.map((r) => r.id), i, i + d); void change("POST", "/filing-rules/reorder", { ordered_ids: o }, "Order saved.").catch(() => undefined); };
  const nodeName = (id: string) => ctx.ws.nodes.find((n) => n.id === id)?.label ?? "(hidden or archived)";
  return (
    <div className="grid gap-4">
      {readOnlyNote(can)}
      <Panel className="grid gap-3">
        <div className="a-actions" style={{ justifyContent: "space-between" }}><h2>Automatic filing rules</h2><Button disabled={!can} onClick={() => setEdit("new")}>Add filing rule</Button></div>
        <p className="a-hint">Rules say where a document should be filed. The first matching rule (lowest priority number) wins. Rules are configuration only: nothing is moved automatically yet.</p>
        {findings.data?.findings.map((f) => <Alert key={f.message} tone="warning">{f.message}</Alert>)}
        <DataTable<FilingRule> caption="Filing rules" rows={rules} loading={false} error={null} rowKey={(r) => r.id} empty="No filing rules yet."
          columns={[
            { key: "p", header: "Priority", numeric: true, render: (r) => r.priority },
            { key: "n", header: "Rule", render: (r) => r.name },
            { key: "c", header: "When", render: (r) => r.conditions.map((c) => `${c.field.replace("custom:", "")} ${OPS.find((o) => o[0] === c.operator)?.[1] ?? c.operator} ${Array.isArray(c.value) ? c.value.join("/") : (c.value ?? "")}`).join(" AND ") },
            { key: "t", header: "File into", render: (r) => nodeName(r.target_node_id) },
            { key: "s", header: "Status", render: (r) => archivedChip(r.archived_at, r.is_active) },
            { key: "a", header: "Actions", render: (r) => {
              const i = live.findIndex((x) => x.id === r.id);
              return (
                <span className="a-actions">
                  <Button variant="secondary" className="a-btn--sm" disabled={!can || i <= 0} aria-label={`Raise priority of ${r.name}`} onClick={() => move(i, -1)}>Raise</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!can || i < 0 || i >= live.length - 1} aria-label={`Lower priority of ${r.name}`} onClick={() => move(i, 1)}>Lower</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!can || !!r.archived_at} onClick={() => setEdit(r)}>Edit</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!can} onClick={() => void change("POST", `/filing-rules/${r.id}/${r.archived_at ? "restore" : "archive"}`, {}, "Done.").catch(() => undefined)}>{r.archived_at ? "Restore" : "Archive"}</Button>
                </span>); } },
          ]} />
      </Panel>
      <RuleTester ctx={ctx} kinds={kinds} />
      {edit ? <FilingDialog ctx={ctx} kinds={kinds} rule={edit === "new" ? null : edit} onClose={() => setEdit(null)} /> : null}
    </div>
  );
}

function RuleTester({ ctx, kinds }: { ctx: Ctx; kinds: Kinds | null }) {
  const [kind, setKind] = useState("");
  const [extra, setExtra] = useState("");
  const [res, setRes] = useState<Eval | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Panel className="grid gap-3">
      <h2>Test your rules</h2>
      <p className="a-hint">See which rule would win for a sample document. Nothing is filed.</p>
      <div className="a-toolbar">
        <Field label="Document kind">{(id) => <select id={id} value={kind} onChange={(e) => setKind(e.target.value)}><option value="">Any</option>{kinds?.items.map((k) => <option key={k.key} value={k.key}>{k.name}</option>)}</select>}</Field>
        <Field label="Other values (name=value per line)">{(id) => <textarea id={id} className="a-mono" value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="custom:bank=Bank of Muscat" />}</Field>
        <Button variant="secondary" onClick={async () => { setErr(null); try { setRes(await tapi<Eval>(ctx.orgId, `${base(ctx)}/filing-rules/evaluate`, { method: "POST", body: { attributes: { ...(kind ? { document_kind: kind } : {}), ...parseSample(extra) } } })); } catch (e) { setRes(null); setErr(errorText(e)); } }}>Test</Button>
      </div>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {res ? <Alert tone={res.winner ? "success" : "info"}>{res.winner ? <>Winner: <strong>{res.winner.name}</strong> → {ctx.ws.nodes.find((n) => n.id === res.winner!.target_node_id)?.label ?? "folder"}{res.ambiguous ? ". Other matching rules would file elsewhere; the highest priority decides." : ""}</> : "No rule matches."}</Alert> : null}
    </Panel>
  );
}

function FilingDialog({ ctx, kinds, rule, onClose }: { ctx: Ctx; kinds: Kinds | null; rule: FilingRule | null; onClose: () => void }) {
  const change = useChange(ctx);
  const [name, setName] = useState(rule?.name ?? "");
  const [rows, setRows] = useState<Row[]>(rule?.conditions.map((c) => ({ field: c.field, operator: c.operator, value: Array.isArray(c.value) ? c.value.join(", ") : String(c.value ?? "") })) ?? [{ field: "document_kind", operator: "equals", value: "" }]);
  const [target, setTarget] = useState(rule?.target_node_id ?? "");
  const [tcat, setTcat] = useState(rule?.target_category_id ?? "");
  const fields = [...COND_FIELDS.map(([v, l]) => [v, l] as const), ...ctx.ws.fields.filter((f) => !f.archived_at).map((f) => [`custom:${f.key}`, f.label] as const)];
  const upd = (i: number, p: Partial<Row>) => setRows((r) => r.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const ready = name.trim() && target && rows.length && rows.every((r) => r.operator === "is_present" || r.operator === "is_empty" || r.value.trim());
  return (
    <FormDialog title={rule ? `Edit ${rule.name}` : "Add filing rule"} submit="Save" onClose={onClose} disabled={!ready}
      onSubmit={async () => {
        const conditions = rows.map((r) => (r.operator === "is_present" || r.operator === "is_empty" ? { field: r.field, operator: r.operator } : { field: r.field, operator: r.operator, value: r.operator === "in" ? r.value.split(",").map((x) => x.trim()).filter(Boolean) : r.value.trim() }));
        await change(rule ? "PATCH" : "POST", rule ? `/filing-rules/${rule.id}` : "/filing-rules", { name: name.trim(), conditions, target_node_id: target, target_category_id: tcat || null }, "Filing rule saved.");
      }}>
      <Field label="Rule name">{(id) => <input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}</Field>
      <fieldset className="a-field"><legend>Conditions (all must match)</legend>
        {rows.map((r, i) => (
          <div key={i} className="a-toolbar">
            <Field label={`Condition ${i + 1} field`}>{(id) => <select id={id} value={r.field} onChange={(e) => upd(i, { field: e.target.value })}>{fields.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
            <Field label="Operator">{(id) => <select id={id} value={r.operator} onChange={(e) => upd(i, { operator: e.target.value })}>{OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
            {r.operator === "is_present" || r.operator === "is_empty" ? null : r.field === "document_kind" && r.operator !== "in" ? (
              <Field label="Value">{(id) => <select id={id} value={r.value} onChange={(e) => upd(i, { value: e.target.value })}><option value="">Choose…</option>{kinds?.items.map((k) => <option key={k.key} value={k.key}>{k.name}</option>)}</select>}</Field>
            ) : <Field label={r.operator === "in" ? "Values (comma separated)" : "Value"}>{(id) => <input id={id} value={r.value} onChange={(e) => upd(i, { value: e.target.value })} />}</Field>}
            <Button type="button" variant="secondary" className="a-btn--sm" disabled={rows.length === 1} onClick={() => setRows((x) => x.filter((_, j) => j !== i))}>Remove</Button>
          </div>
        ))}
        <div><Button type="button" variant="secondary" className="a-btn--sm" disabled={rows.length >= 20} onClick={() => setRows((x) => [...x, { field: "document_kind", operator: "equals", value: "" }])}>Add condition</Button></div>
      </fieldset>
      <NodeSelect nodes={ctx.ws.nodes} value={target} onChange={setTarget} label="File into" allowEmpty emptyLabel="Choose a folder…" />
      <Field label="Category to assign (optional)">{(id) => <select id={id} value={tcat} onChange={(e) => setTcat(e.target.value)}><option value="">None</option>{ctx.ws.categories.filter((c) => !c.archived_at).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}</Field>
    </FormDialog>
  );
}
