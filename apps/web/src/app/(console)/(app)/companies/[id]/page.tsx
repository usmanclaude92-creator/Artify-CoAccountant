"use client";
import { use, useState } from "react";
import type { Schemas } from "@artify/api-client";
import Link from "next/link";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { ActionDialog, DataTable, Empty, ErrorBox, Field, Loading, Status, Tabs, useToast } from "@/components/kit";
import { ORG_ROLE_LABEL, errorText, fmtBytes, fmtDate, fmtDateTime, fmtNumber, label, tapi, useTenantApi, type Page } from "@/lib/tenant";

type Company = Schemas["CompanyOut"];
type Assignment = Schemas["AssignmentOut"];
type History = Schemas["HistoryEvent"];
type Member = Schemas["MemberOut"];
type Config = Schemas["ConfigurationOut"];
type Accountant = Schemas["AccountantRow"];

const TABS = [
  { id: "overview", label: "Overview" }, { id: "accounting", label: "Accounting settings" }, { id: "accountants", label: "Assigned accountants" },
  { id: "users", label: "Client users" }, { id: "config", label: "Configuration" }, { id: "documents", label: "Documents" }, { id: "history", label: "History" },
];
type Verb = "activate" | "deactivate" | "archive" | "restore";
const ACTION_TEXT: Record<Verb, { title: string; submit: string; danger?: boolean; help: string }> = {
  activate: { title: "Activate company", submit: "Activate", help: "The company becomes active again and counts toward your company limit." },
  deactivate: { title: "Deactivate company", submit: "Deactivate", help: "An inactive company stays visible but is read-only until it is activated again." },
  archive: { title: "Archive company", submit: "Archive", danger: true, help: "Archiving keeps all data, frees the subscription slot and hides the company from daily lists. Nothing is deleted." },
  restore: { title: "Restore company", submit: "Restore", help: "Restoring needs a free slot in your company limit." },
};
const STATUS_ACTIONS: Record<string, Verb[]> = { active: ["deactivate", "archive"], suspended: ["activate", "archive"], archived: ["restore"] };

export default function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { orgId, href, can } = useOrg();
  const toast = useToast();
  const [tab, setTab] = useState("overview");
  const [action, setAction] = useState<Verb | null>(null);
  const c = useTenantApi<Company>(orgId, `/companies/${id}`);
  if (c.error && !c.data) return <div className="grid gap-4"><CcHeader title="Company" crumbs={[{ label: "My Companies", href: href("/companies") }]} />{/not found/i.test(c.error) ? <Alert tone="info">This company does not exist or you do not have access to it.</Alert> : <ErrorBox message={c.error} onRetry={c.reload} />}</div>;
  if (!c.data) return <Loading what="the company" />;
  const co = c.data;
  const canAct = can("companies.archive");
  return (
    <div className="grid gap-4">
      <CcHeader title={co.name} crumbs={[{ label: "My Companies", href: href("/companies") }, { label: co.name }]}
        actions={canAct ? (STATUS_ACTIONS[co.status] ?? []).map((a) => <Button key={a} variant="secondary" onClick={() => setAction(a)}>{ACTION_TEXT[a].submit}</Button>) : null} />
      <p><Status value={co.status} text={co.status === "suspended" ? "Inactive" : undefined} /> <span className="a-hint">· code {co.code} · {co.country} · {co.base_currency}</span></p>
      {co.status !== "active" ? <Alert tone="info">{co.status === "archived" ? "This company is archived and read-only." : "This company is inactive and read-only."}</Alert> : null}
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="grid gap-4">
        {tab === "overview" ? <Overview co={co} onSaved={c.reload} /> : null}
        {tab === "accounting" ? <Accounting co={co} onSaved={c.reload} /> : null}
        {tab === "accountants" ? <Accountants co={co} /> : null}
        {tab === "users" ? <ClientUsers co={co} /> : null}
        {tab === "config" ? <Configuration co={co} /> : null}
        {tab === "documents" ? <DocumentsTab co={co} /> : null}
        {tab === "history" ? <HistoryTab co={co} /> : null}
      </div>
      {action ? (
        <ActionDialog open title={ACTION_TEXT[action].title} description={ACTION_TEXT[action].help} submitLabel={ACTION_TEXT[action].submit} danger={ACTION_TEXT[action].danger}
          needsReason={false} reasonLabel="Reason" onClose={() => setAction(null)}
          onSubmit={async () => { await tapi(orgId, `/companies/${id}/status`, { method: "POST", body: { action } }); toast(`${ACTION_TEXT[action].title.split(" ")[0]} done.`); setAction(null); c.reload(); }} />
      ) : null}
    </div>
  );
}

const dash = (v?: string | null) => v || "—";

function Overview({ co, onSaved }: { co: Company; onSaved: () => void }) {
  const { orgId, can } = useOrg();
  const toast = useToast();
  const editable = can("companies.update") && co.status === "active";
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ name: co.name, legal_name: co.legal_name ?? "", registration_number: co.registration_number ?? "", company_type: co.company_type ?? "", industry: co.industry ?? "", city: co.city ?? "", address_line: co.address_line ?? "", contact_email: co.contact_email ?? "", contact_phone: co.contact_phone ?? "", tax_registration: co.tax_registration ?? "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, k === "name" ? v.trim() : v.trim() || null]));
      await tapi(orgId, `/companies/${co.id}`, { method: "PATCH", body });
      toast("Company details saved."); setEdit(false); onSaved();
    } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
  };
  const fields: [keyof typeof f, string][] = [["name", "Display name"], ["legal_name", "Legal name"], ["registration_number", "Registration number"], ["company_type", "Company type"], ["industry", "Industry"], ["city", "City"], ["address_line", "Address"], ["contact_email", "Contact e-mail"], ["contact_phone", "Contact phone"], ["tax_registration", "Tax registration"]];
  return (
    <Panel className="grid gap-3">
      <div className="a-pagehead"><h2>Company details</h2>{editable && !edit ? <Button variant="secondary" onClick={() => setEdit(true)}>Edit</Button> : null}</div>
      {edit ? (
        <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          {fields.map(([k, l]) => <Field key={k} label={l}>{(i) => <input id={i} value={f[k]} required={k === "name"} type={k === "contact_email" ? "email" : "text"} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</Field>)}
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <div className="a-actions"><Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button><Button type="button" variant="secondary" onClick={() => setEdit(false)}>Cancel</Button></div>
        </form>
      ) : (
        <dl className="a-dl">
          <dt>Display name</dt><dd>{co.name}</dd><dt>Code</dt><dd>{co.code}</dd>
          <dt>Legal name</dt><dd>{dash(co.legal_name)}</dd><dt>Registration number</dt><dd>{dash(co.registration_number)}</dd>
          <dt>Company type</dt><dd>{dash(co.company_type)}</dd><dt>Industry</dt><dd>{dash(co.industry)}</dd>
          <dt>Country</dt><dd>{co.country}</dd><dt>City</dt><dd>{dash(co.city)}</dd><dt>Address</dt><dd>{dash(co.address_line)}</dd>
          <dt>Contact e-mail</dt><dd>{dash(co.contact_email)}</dd><dt>Contact phone</dt><dd>{dash(co.contact_phone)}</dd>
          <dt>Tax registration</dt><dd>{dash(co.tax_registration)}</dd><dt>Created</dt><dd>{fmtDateTime(co.created_at)}</dd>
        </dl>
      )}
    </Panel>
  );
}

function Accounting({ co, onSaved }: { co: Company; onSaved: () => void }) {
  const { orgId, can } = useOrg();
  const toast = useToast();
  const editable = can("company_settings.manage") && co.status === "active";
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ base_currency: co.base_currency, fy_month: String(co.financial_year_start_month), fy_day: String(co.financial_year_start_day), basis: co.accounting_basis ?? "", vat: co.vat_registered, vat_number: co.vat_number ?? "", tax_jurisdiction: co.tax_jurisdiction ?? "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      await tapi(orgId, `/companies/${co.id}/settings`, { method: "PATCH", body: { base_currency: f.base_currency, financial_year_start_month: Number(f.fy_month), financial_year_start_day: Number(f.fy_day), accounting_basis: f.basis || null, vat_registered: f.vat, vat_number: f.vat ? f.vat_number.trim() || null : null, tax_jurisdiction: f.tax_jurisdiction.trim() || null } });
      toast("Accounting settings saved."); setEdit(false); onSaved();
    } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
  };
  const monthName = (m: number) => new Date(Date.UTC(2000, m - 1, 1)).toLocaleString("en-GB", { month: "long", timeZone: "UTC" });
  return (
    <Panel className="grid gap-3">
      <div className="a-pagehead"><h2>Accounting settings</h2>{editable && !edit ? <Button variant="secondary" onClick={() => setEdit(true)}>Edit</Button> : null}</div>
      {edit ? (
        <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <Field label="Base currency">{(i) => <input id={i} value={f.base_currency} pattern="[A-Z]{3}" maxLength={3} onChange={(e) => setF({ ...f, base_currency: e.target.value.toUpperCase() })} />}</Field>
          <Field label="Financial year starts in">{(i) => <select id={i} value={f.fy_month} onChange={(e) => setF({ ...f, fy_month: e.target.value })}>{Array.from({ length: 12 }, (_, k) => <option key={k} value={k + 1}>{monthName(k + 1)}</option>)}</select>}</Field>
          <Field label="Day of month">{(i) => <input id={i} type="number" min={1} max={31} value={f.fy_day} onChange={(e) => setF({ ...f, fy_day: e.target.value })} />}</Field>
          <Field label="Accounting basis">{(i) => <select id={i} value={f.basis} onChange={(e) => setF({ ...f, basis: e.target.value })}><option value="">Not specified</option><option value="accrual">Accrual</option><option value="cash">Cash</option></select>}</Field>
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.vat} onChange={(e) => setF({ ...f, vat: e.target.checked })} /> VAT registered</label>
          {f.vat ? <Field label="VAT number">{(i) => <input id={i} value={f.vat_number} onChange={(e) => setF({ ...f, vat_number: e.target.value })} />}</Field> : null}
          <Field label="Tax jurisdiction">{(i) => <input id={i} value={f.tax_jurisdiction} onChange={(e) => setF({ ...f, tax_jurisdiction: e.target.value })} />}</Field>
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <div className="a-actions"><Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button><Button type="button" variant="secondary" onClick={() => setEdit(false)}>Cancel</Button></div>
        </form>
      ) : (
        <dl className="a-dl">
          <dt>Base currency</dt><dd>{co.base_currency}</dd>
          <dt>Financial year starts</dt><dd>{monthName(co.financial_year_start_month)} {co.financial_year_start_day}</dd>
          <dt>Accounting basis</dt><dd>{co.accounting_basis ? label(co.accounting_basis) : "Not specified"}</dd>
          <dt>VAT</dt><dd>{co.vat_registered ? `Registered${co.vat_number ? ` · ${co.vat_number}` : " · number not recorded"}` : "Not registered"}</dd>
          <dt>Tax jurisdiction</dt><dd>{dash(co.tax_jurisdiction)}</dd>
        </dl>
      )}
      {!can("company_settings.manage") ? <p className="a-hint">Your role can view but not change accounting settings.</p> : null}
    </Panel>
  );
}

function Accountants({ co }: { co: Company }) {
  const { orgId, can } = useOrg();
  const toast = useToast();
  const list = useTenantApi<Assignment[]>(orgId, can("assignments.read") ? `/companies/${co.id}/assignments` : null);
  const pool = useTenantApi<Page<Accountant>>(orgId, can("assignments.manage") && can("members.read") ? `/orgs/${orgId}/accountants?status=active&limit=100` : null);
  const hist = useTenantApi<Page<History>>(orgId, can("assignments.read") ? `/companies/${co.id}/assignments/history?limit=10` : null);
  const [add, setAdd] = useState({ user_id: "", assignment_type: "secondary", responsibility: "", starts_on: "", ends_on: "" });
  const [target, setTarget] = useState<Assignment | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const manage = can("assignments.manage") && co.status === "active";
  const assigned = new Set(list.data?.map((a) => a.user_id));
  const doAdd = async () => {
    setErr(null);
    try {
      await tapi(orgId, `/companies/${co.id}/assignments`, { method: "POST", body: { user_id: add.user_id, assignment_type: add.assignment_type, responsibility: add.responsibility.trim() || null, starts_on: add.starts_on || null, ends_on: add.ends_on || null } });
      toast("Accountant assigned."); setAdd({ user_id: "", assignment_type: "secondary", responsibility: "", starts_on: "", ends_on: "" }); list.reload(); hist.reload();
    } catch (ex) { setErr(errorText(ex)); }
  };
  if (!can("assignments.read")) return <Alert tone="info">Your role cannot view assignments.</Alert>;
  return (
    <div className="grid gap-4">
      <DataTable<Assignment> caption="Assigned accountants" rows={list.data ?? undefined} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(r) => r.user_id} empty="No accountant is assigned to this company."
        columns={[
          { key: "n", header: "Accountant", render: (r) => <>{r.display_name}<span className="a-hint" style={{ display: "block" }}>{r.email}</span></> },
          { key: "role", header: "Role", render: (r) => ORG_ROLE_LABEL[r.role] ?? r.role },
          { key: "t", header: "Type", render: (r) => label(r.assignment_type) },
          { key: "r", header: "Responsibility", render: (r) => dash(r.responsibility) },
          { key: "w", header: "Period", render: (r) => `${fmtDate(r.starts_on)} – ${r.ends_on ? fmtDate(r.ends_on) : "open"}` },
          { key: "s", header: "State", render: (r) => <Status value={r.active ? "active" : "inactive"} text={r.active ? "Active" : "Not in period"} /> },
          { key: "a", header: "Actions", render: (r) => (manage ? <Button variant="secondary" className="a-btn--sm" onClick={() => setTarget(r)}>Manage {r.display_name}</Button> : null) },
        ]} />
      {manage ? (
        <Panel className="grid gap-3">
          <h3>Assign an accountant</h3>
          {pool.data && pool.data.items.filter((a) => !assigned.has(a.user_id)).length === 0 ? <Empty>Every active accountant is already assigned, or none exist yet.</Empty> : (
            <form className="a-toolbar" onSubmit={(e) => { e.preventDefault(); void doAdd(); }}>
              <Field label="Accountant">{(i) => <select id={i} value={add.user_id} onChange={(e) => setAdd({ ...add, user_id: e.target.value })} required><option value="">Select…</option>{pool.data?.items.filter((a) => !assigned.has(a.user_id)).map((a) => <option key={a.user_id} value={a.user_id}>{a.display_name}</option>)}</select>}</Field>
              <Field label="Type">{(i) => <select id={i} value={add.assignment_type} onChange={(e) => setAdd({ ...add, assignment_type: e.target.value })}><option value="primary">Primary</option><option value="secondary">Secondary</option><option value="reviewer">Reviewer</option></select>}</Field>
              <Field label="Responsibility">{(i) => <input id={i} value={add.responsibility} maxLength={200} onChange={(e) => setAdd({ ...add, responsibility: e.target.value })} />}</Field>
              <Field label="Starts on">{(i) => <input id={i} type="date" value={add.starts_on} onChange={(e) => setAdd({ ...add, starts_on: e.target.value })} />}</Field>
              <Field label="Ends on">{(i) => <input id={i} type="date" value={add.ends_on} onChange={(e) => setAdd({ ...add, ends_on: e.target.value })} />}</Field>
              <Button type="submit" disabled={!add.user_id}>Assign</Button>
            </form>
          )}
          {err ? <Alert tone="danger">{err}</Alert> : null}
        </Panel>
      ) : null}
      {target ? <ManageAssignment co={co} a={target} onClose={() => setTarget(null)} onDone={() => { setTarget(null); list.reload(); hist.reload(); }} /> : null}
      <Panel><h3>Recent assignment history</h3><HistoryList data={hist.data?.items} error={hist.error} loading={hist.loading} /></Panel>
    </div>
  );
}

function ManageAssignment({ co, a, onClose, onDone }: { co: Company; a: Assignment; onClose: () => void; onDone: () => void }) {
  const { orgId } = useOrg();
  const toast = useToast();
  const [f, setF] = useState({ assignment_type: a.assignment_type, responsibility: a.responsibility ?? "", starts_on: a.starts_on, ends_on: a.ends_on ?? "" });
  const [removing, setRemoving] = useState(false);
  return removing ? (
    <ActionDialog open title={`Remove ${a.display_name}`} description="The accountant loses access to this company immediately. The assignment is recorded in the history." submitLabel="Remove assignment" danger needsReason={false}
      onClose={onClose} onSubmit={async () => { await tapi(orgId, `/companies/${co.id}/assignments/${a.user_id}`, { method: "DELETE" }); toast("Assignment removed."); onDone(); }} />
  ) : (
    <ActionDialog open title={`Manage ${a.display_name}`} submitLabel="Save" needsReason={false} onClose={onClose}
      onSubmit={async () => { await tapi(orgId, `/companies/${co.id}/assignments/${a.user_id}`, { method: "PATCH", body: { assignment_type: f.assignment_type, responsibility: f.responsibility.trim() || null, starts_on: f.starts_on, ends_on: f.ends_on || null } }); toast("Assignment updated."); onDone(); }}>
      <Field label="Type">{(i) => <select id={i} value={f.assignment_type} onChange={(e) => setF({ ...f, assignment_type: e.target.value })}><option value="primary">Primary</option><option value="secondary">Secondary</option><option value="reviewer">Reviewer</option></select>}</Field>
      <Field label="Responsibility">{(i) => <input id={i} value={f.responsibility} maxLength={200} onChange={(e) => setF({ ...f, responsibility: e.target.value })} />}</Field>
      <Field label="Starts on">{(i) => <input id={i} type="date" value={f.starts_on} onChange={(e) => setF({ ...f, starts_on: e.target.value })} />}</Field>
      <Field label="Ends on" hint="Leave empty for an open-ended assignment.">{(i) => <input id={i} type="date" value={f.ends_on} onChange={(e) => setF({ ...f, ends_on: e.target.value })} />}</Field>
      <div><Button type="button" variant="secondary" className="a-btn--danger" onClick={() => setRemoving(true)}>Remove assignment…</Button></div>
    </ActionDialog>
  );
}

function ClientUsers({ co }: { co: Company }) {
  const { orgId, can } = useOrg();
  const toast = useToast();
  const m = useTenantApi<Member[]>(orgId, can("company_members.read") ? `/companies/${co.id}/members` : null);
  const [inv, setInv] = useState({ email: "", role: "company_employee" });
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const manage = can("company_members.manage") && co.status === "active";
  if (!can("company_members.read")) return <Alert tone="info">Your role cannot view client users.</Alert>;
  const invite = async () => {
    setErr(null); setNote(null);
    try {
      const r = await tapi<Schemas["InvitationOut"]>(orgId, `/companies/${co.id}/invitations`, { method: "POST", body: inv });
      setNote(String((r.email_delivery as { note?: string } | null)?.note ?? "Invitation created.")); toast("Invitation created."); setInv({ email: "", role: "company_employee" });
    } catch (ex) { setErr(errorText(ex)); }
  };
  const change = async (u: Member, body: Record<string, string>) => {
    try { await tapi(orgId, `/companies/${co.id}/members/${u.user_id}`, { method: "PATCH", body }); toast("Updated."); m.reload(); } catch (ex) { toast(errorText(ex), "danger"); }
  };
  return (
    <div className="grid gap-4">
      <p className="a-hint">Client users belong to this company only (for example the company’s own staff). They never see other companies.</p>
      <DataTable<Member> caption="Client users" rows={m.data ?? undefined} loading={m.loading} error={m.error} onRetry={m.reload} rowKey={(r) => r.user_id} empty="No client users yet."
        columns={[
          { key: "n", header: "Name", render: (r) => <>{r.display_name}<span className="a-hint" style={{ display: "block" }}>{r.email}</span></> },
          { key: "r", header: "Role", render: (r) => ORG_ROLE_LABEL[r.role] ?? r.role },
          { key: "s", header: "Status", render: (r) => <Status value={r.status} /> },
          { key: "a", header: "Actions", render: (r) => manage && r.status !== "removed" ? (
            <span className="a-actions">
              {r.status === "active" ? <Button variant="secondary" className="a-btn--sm" onClick={() => change(r, { status: "suspended" })}>Suspend {r.display_name}</Button> : <Button variant="secondary" className="a-btn--sm" onClick={() => change(r, { status: "active" })}>Reactivate {r.display_name}</Button>}
              <Button variant="secondary" className="a-btn--sm" onClick={() => change(r, { status: "removed" })}>Remove {r.display_name}</Button>
            </span>
          ) : null },
        ]} />
      {manage ? (
        <Panel className="grid gap-3">
          <h3>Invite a client user</h3>
          <form className="a-toolbar" onSubmit={(e) => { e.preventDefault(); void invite(); }}>
            <Field label="E-mail">{(i) => <input id={i} type="email" required value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} />}</Field>
            <Field label="Role">{(i) => <select id={i} value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}><option value="company_employee">Company employee</option><option value="company_admin">Company administrator</option><option value="company_owner">Company owner</option></select>}</Field>
            <Button type="submit">Send invitation</Button>
          </form>
          {note ? <Alert tone="info">{note}</Alert> : null}
          {err ? <Alert tone="danger">{err}</Alert> : null}
        </Panel>
      ) : null}
    </div>
  );
}

function Configuration({ co }: { co: Company }) {
  const { orgId, href } = useOrg();
  const cfg = useTenantApi<Config>(orgId, `/companies/${co.id}/configuration`);
  if (cfg.error && !cfg.data) return <ErrorBox message={cfg.error} onRetry={cfg.reload} />;
  if (!cfg.data) return <Loading what="configuration" />;
  return (
    <Panel className="grid gap-3">
      <h2>Company configuration</h2>
      <p>Workspace state: <Status value={cfg.data.workspace_state === "configured" ? "active" : "not_configured"} text={cfg.data.workspace_state === "configured" ? "Configured" : "Not configured"} /> · version {cfg.data.config_version}</p>
      <p className="a-hint">Each company owns its own workspace: sections, folders, categories, custom fields, naming and filing rules and permissions. Changing one company never changes another.</p>
      <p><Link className="a-btn a-btn--primary" href={href(`/companies/${co.id}/workspace`)}>Open the workspace builder</Link></p>
    </Panel>
  );
}

function HistoryList({ data, error, loading }: { data?: History[]; error: string | null; loading: boolean }) {
  if (error) return <Alert tone="danger">{error}</Alert>;
  if (!data && loading) return <Loading />;
  if (!data?.length) return <Empty>No history yet.</Empty>;
  return (
    <ul style={{ listStyle: "none", padding: 0 }} className="grid gap-1">
      {data.map((e) => <li key={e.id}><strong>{label(e.action)}</strong> <span className="a-hint">· {e.actor_name ?? "System"} · {fmtDateTime(e.created_at)}</span></li>)}
    </ul>
  );
}

function HistoryTab({ co }: { co: Company }) {
  const { orgId } = useOrg();
  const [offset, setOffset] = useState(0);
  const h = useTenantApi<Page<History>>(orgId, `/companies/${co.id}/history?limit=25&offset=${offset}`);
  return (
    <DataTable<History> caption="Company history" rows={h.data?.items} loading={h.loading} error={h.error} onRetry={h.reload} rowKey={(r) => r.id} empty="No history yet."
      total={h.data?.total} limit={25} offset={offset} onPage={setOffset}
      columns={[
        { key: "w", header: "When", render: (r) => fmtDateTime(r.created_at) },
        { key: "a", header: "Event", render: (r) => label(r.action) },
        { key: "u", header: "By", render: (r) => r.actor_name ?? "System" },
        { key: "d", header: "Details", render: (r) => Object.entries(r.summary).filter(([, v]) => v !== null && typeof v !== "object").map(([k, v]) => `${label(k)}: ${String(v)}`).join(" · ") || "—" },
      ]} />
  );
}


interface DocRow { id: string; display_name: string; extension: string; status: string; size_bytes: number; created_at: string; created_by_name: string | null; filed_path_snapshot: string | null; current_version_number: number; scan_status: string }
interface UploadRow { id: string; original_filename: string; status: string; error_code: string | null; error_message: string | null; created_at: string; declared_size: number }
interface CompanyStorage { documents: { active: number; archived: number; removed: number }; stored_bytes: number; stored_versions: number; by_type: { extension: string; documents: number; bytes: number }[]; uploads_last_7_days: { completed: number; failed: number } }

function DocumentsTab({ co }: { co: Company }) {
  const { orgId, can } = useOrg();
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const stats = useTenantApi<CompanyStorage>(orgId, `/companies/${co.id}/documents/storage`);
  const list = useTenantApi<Page<DocRow>>(orgId, `/companies/${co.id}/documents?limit=25&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const failed = useTenantApi<Page<UploadRow>>(orgId, can("workspace.manage") ? `/companies/${co.id}/uploads?mine=false&status=failed&limit=10` : null);
  const s = stats.data;
  return (
    <div className="grid gap-4">
      <p className="a-hint">Documents are imported and managed in the Windows desktop application. This view shows what is stored for this company; it never changes documents.</p>
      {stats.error && !s ? <ErrorBox message={stats.error} onRetry={stats.reload} /> : s ? (
        <div className="a-kpis">
          <div className="a-kpi"><span className="a-kpi__label">Active documents</span><span className="a-kpi__value">{fmtNumber(s.documents.active)}</span></div>
          <div className="a-kpi"><span className="a-kpi__label">Archived</span><span className="a-kpi__value">{fmtNumber(s.documents.archived)}</span></div>
          <div className="a-kpi"><span className="a-kpi__label">Stored (all versions)</span><span className="a-kpi__value">{fmtBytes(s.stored_bytes)}</span><span className="a-hint">{fmtNumber(s.stored_versions)} files</span></div>
          <div className="a-kpi"><span className="a-kpi__label">Uploads, last 7 days</span><span className="a-kpi__value">{fmtNumber(s.uploads_last_7_days.completed)} done</span><span className="a-hint">{fmtNumber(s.uploads_last_7_days.failed)} failed</span></div>
        </div>
      ) : <Loading what="storage" />}
      {s && s.by_type.length ? <p className="a-hint">By type: {s.by_type.map((t) => `${t.extension.toUpperCase()} ${fmtNumber(t.documents)} (${fmtBytes(t.bytes)})`).join(" · ")}</p> : null}
      <form role="search" className="a-toolbar" onSubmit={(e) => { e.preventDefault(); setOffset(0); }}>
        <Field label="Search documents">{(id) => <input id={id} type="search" value={q} placeholder="Name or metadata value" onChange={(e) => { setQ(e.target.value); setOffset(0); }} />}</Field>
      </form>
      <DataTable<DocRow> caption="Company documents" rows={list.data?.items} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(r) => r.id}
        empty={q ? "No documents match." : "No documents have been imported for this company yet."} total={list.data?.total} limit={25} offset={offset} onPage={setOffset}
        columns={[
          { key: "n", header: "Document", render: (r) => <>{r.display_name}<span className="a-hint" style={{ display: "block" }}>{r.filed_path_snapshot ?? "—"}</span></> },
          { key: "t", header: "Type", render: (r) => r.extension.toUpperCase() },
          { key: "s", header: "Size", numeric: true, render: (r) => fmtBytes(r.size_bytes) },
          { key: "v", header: "Version", numeric: true, render: (r) => r.current_version_number },
          { key: "u", header: "Uploaded by", render: (r) => r.created_by_name ?? "—" },
          { key: "d", header: "Uploaded", render: (r) => fmtDateTime(r.created_at) },
          { key: "x", header: "Scan", render: (r) => (r.scan_status === "not_scanned" ? "Not scanned" : label(r.scan_status)) },
        ]} />
      {failed.data && failed.data.items.length ? (
        <Panel>
          <h3>Failed uploads</h3>
          <ul style={{ listStyle: "none", padding: 0 }} className="grid gap-1">
            {failed.data.items.map((u) => <li key={u.id}><strong>{u.original_filename}</strong> <span className="a-hint">· {u.error_message ?? label(u.error_code ?? "failed")} · {fmtDateTime(u.created_at)}</span></li>)}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}
