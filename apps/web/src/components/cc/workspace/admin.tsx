"use client";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { ApiProblem } from "@/lib/client";
import { DataTable, Empty, Field, Loading, Status, useToast } from "@/components/kit";
import { ORG_ROLE_LABEL, errorText, fmtDateTime, tapi, useTenantApi, type Page } from "@/lib/tenant";
import { FormDialog, NodeSelect, readOnlyNote, useChange, type Ctx } from "./common";

const base = (c: Ctx) => `/companies/${c.companyId}/workspace`;
const ACTIONS: [string, string][] = [["view", "View"], ["upload", "Upload"], ["edit", "Add / edit"], ["rename", "Rename"], ["move", "Move"], ["archive", "Archive / restore"], ["delete", "Delete"], ["configure", "Manage configuration"], ["manage_permissions", "Manage permissions"]];
const ROLES = ["practice_manager", "accountant", "assistant_accountant", "staff", "document_operator", "read_only"];

/* ---------------------------------------------------------------- permissions */
interface Grant { id: string; node_id: string | null; node_name: string | null; subject_type: string; subject_user_id: string | null; subject_role: string | null; user_name: string | null; permission: string; effect: "allow" | "deny"; created_at: string }
interface Team { user_id: string; display_name: string; email: string; role: string }

export function PermissionsTab({ ctx }: { ctx: Ctx }) {
  const change = useChange(ctx);
  const toast = useToast();
  const can = ctx.editable && ctx.ws.capabilities.manage_permissions;
  const grants = useTenantApi<{ access_mode: string; items: Grant[] }>(ctx.orgId, ctx.ws.capabilities.manage_permissions ? `${base(ctx)}/permissions?v=${ctx.ws.version}` : null);
  const [add, setAdd] = useState(false);
  if (!ctx.ws.capabilities.manage_permissions) {
    return <Panel className="grid gap-2"><h2>Permissions</h2><Alert tone="info">Only organization owners and administrators can see or change who may use which part of this workspace. Your own access is shown per item in the Structure tab.</Alert></Panel>;
  }
  const restricted = ctx.ws.access_mode === "restricted";
  return (
    <div className="grid gap-4">
      {readOnlyNote(ctx.editable)}
      <Panel className="grid gap-3">
        <h2>Who can see this workspace</h2>
        <p className="a-hint"><strong>Open</strong> (default): everyone with access to this company can view it, and their organization role decides what they can change. <strong>Restricted</strong>: members below manager see only the sections you explicitly allow. Owners, administrators and practice managers always keep access.</p>
        <p>Current mode: <Status value={restricted ? "pending" : "active"} text={restricted ? "Restricted" : "Open"} /></p>
        <div><Button variant="secondary" disabled={!can} onClick={() => void change("PATCH", "/settings", { access_mode: restricted ? "open" : "restricted" }, "Access mode changed.").catch((e) => toast(errorText(e)))}>{restricted ? "Switch to open" : "Switch to restricted"}</Button></div>
      </Panel>
      <Panel className="grid gap-3">
        <div className="a-actions" style={{ justifyContent: "space-between" }}><h2>Access grants</h2><Button disabled={!can} onClick={() => setAdd(true)}>Add grant</Button></div>
        <p className="a-hint">A grant on a section applies to everything inside it. The closest grant to an item decides; on the same level, <strong>deny beats allow</strong>. Grants never reach up to parents.</p>
        <DataTable<Grant> caption="Access grants" rows={grants.data?.items} loading={grants.loading} error={grants.error} onRetry={grants.reload} rowKey={(g) => g.id} empty="No grants yet: role defaults apply."
          columns={[
            { key: "w", header: "Where", render: (g) => g.node_id ? (ctx.ws.nodes.find((n) => n.id === g.node_id)?.label ?? g.node_name ?? "—") : "Whole workspace" },
            { key: "s", header: "Who", render: (g) => g.subject_type === "user" ? (g.user_name ?? "Member") : `Everyone with role ${ORG_ROLE_LABEL[g.subject_role ?? ""] ?? g.subject_role}` },
            { key: "p", header: "Permission", render: (g) => ACTIONS.find((a) => a[0] === g.permission)?.[1] ?? g.permission },
            { key: "e", header: "Effect", render: (g) => <Status value={g.effect === "allow" ? "active" : "denied"} text={g.effect === "allow" ? "Allow" : "Deny"} /> },
            { key: "d", header: "Granted", render: (g) => fmtDateTime(g.created_at) },
            { key: "a", header: "Actions", render: (g) => <Button variant="secondary" className="a-btn--sm" disabled={!can} onClick={() => void change("DELETE", `/permissions/${g.id}`, undefined, "Grant removed.").catch((e) => toast(errorText(e)))}>Remove</Button> },
          ]} />
      </Panel>
      <CheckAccess ctx={ctx} />
      {add ? <GrantDialog ctx={ctx} onClose={() => setAdd(false)} /> : null}
    </div>
  );
}

function GrantDialog({ ctx, onClose }: { ctx: Ctx; onClose: () => void }) {
  const change = useChange(ctx);
  const team = useTenantApi<Page<Team>>(ctx.orgId, `/orgs/${ctx.orgId}/team?status=active&limit=200`);
  const [f, setF] = useState({ node: "", type: "user", user: "", role: "accountant", perm: "view", effect: "allow" });
  const members = team.data?.items.filter((m) => !["firm_owner", "practice_admin"].includes(m.role)) ?? [];
  return (
    <FormDialog title="Add access grant" submit="Save grant" onClose={onClose} disabled={f.type === "user" ? !f.user : !f.role}
      onSubmit={async () => { await change("POST", "/permissions", { node_id: f.node || null, subject_type: f.type, user_id: f.type === "user" ? f.user : null, role: f.type === "role" ? f.role : null, permission: f.perm, effect: f.effect }, "Grant saved."); }}>
      <NodeSelect nodes={ctx.ws.nodes} value={f.node} onChange={(v) => setF({ ...f, node: v })} label="Where" allowEmpty emptyLabel="Whole workspace" hint="Applies to this item and everything inside it." />
      <Field label="Who">{(id) => <select id={id} value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}><option value="user">A specific team member</option><option value="role">Everyone with a role</option></select>}</Field>
      {f.type === "user" ? (
        <Field label="Team member">{(id) => <select id={id} value={f.user} onChange={(e) => setF({ ...f, user: e.target.value })}><option value="">Choose…</option>{members.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name} ({ORG_ROLE_LABEL[m.role] ?? m.role})</option>)}</select>}</Field>
      ) : <Field label="Role">{(id) => <select id={id} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{ORG_ROLE_LABEL[r] ?? r}</option>)}</select>}</Field>}
      <Field label="Permission">{(id) => <select id={id} value={f.perm} onChange={(e) => setF({ ...f, perm: e.target.value })}>{ACTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
      <Field label="Effect">{(id) => <select id={id} value={f.effect} onChange={(e) => setF({ ...f, effect: e.target.value })}><option value="allow">Allow</option><option value="deny">Deny</option></select>}</Field>
    </FormDialog>
  );
}

function CheckAccess({ ctx }: { ctx: Ctx }) {
  const team = useTenantApi<Page<Team>>(ctx.orgId, `/orgs/${ctx.orgId}/team?status=active&limit=200`);
  const [user, setUser] = useState(""); const [node, setNode] = useState(""); const [action, setAction] = useState("view");
  const [res, setRes] = useState<string | null>(null);
  return (
    <Panel className="grid gap-3">
      <h2>Check what a member can do</h2>
      <div className="a-toolbar">
        <Field label="Member">{(id) => <select id={id} value={user} onChange={(e) => setUser(e.target.value)}><option value="">Choose…</option>{team.data?.items.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name}</option>)}</select>}</Field>
        <NodeSelect nodes={ctx.ws.nodes} value={node} onChange={setNode} label="Where" allowEmpty emptyLabel="Whole workspace" />
        <Field label="Action">{(id) => <select id={id} value={action} onChange={(e) => setAction(e.target.value)}>{ACTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
        <Button variant="secondary" disabled={!user} onClick={async () => { try { const r = await tapi<{ allowed: boolean; reason: string }>(ctx.orgId, `${base(ctx)}/permissions/check`, { method: "POST", body: { user_id: user, node_id: node || null, action } }); setRes(`${r.allowed ? "Allowed" : "Not allowed"} (${r.reason.replace(/_/g, " ")})`); } catch (e) { setRes(errorText(e)); } }}>Check</Button>
      </div>
      {res ? <Alert tone="info">{res}</Alert> : null}
    </Panel>
  );
}

/* ---------------------------------------------------------------- templates */
interface Tpl { id: string; key: string; name: string; description: string; is_system: boolean; latest_version: number; counts: Record<string, number> }
interface Fit { key: string; limit: number | null; unlimited: boolean; after: number; fits: boolean }
interface Co { id: string; name: string; status: string }

export function TemplatesTab({ ctx }: { ctx: Ctx }) {
  const list = useTenantApi<{ items: Tpl[] }>(ctx.orgId, `/orgs/${ctx.orgId}/workspace-templates`);
  const cos = useTenantApi<Co[]>(ctx.orgId, `/orgs/${ctx.orgId}/companies`);
  const [apply, setApply] = useState<Tpl | null>(null);
  const [copy, setCopy] = useState(false);
  const [save, setSave] = useState(false);
  const can = ctx.editable && ctx.ws.capabilities.configure;
  return (
    <div className="grid gap-4">
      {readOnlyNote(can)}
      {ctx.ws.applied_template ? <Alert tone="info">This workspace was started from a template. It is an independent copy: later template changes never reach it.</Alert> : null}
      <Panel className="grid gap-3">
        <div className="a-actions" style={{ justifyContent: "space-between" }}>
          <h2>Template gallery</h2>
          <span className="a-actions">
            <Button variant="secondary" disabled={!can} onClick={() => setCopy(true)}>Copy from another company</Button>
            <Button variant="secondary" disabled={!ctx.ws.capabilities.templates} onClick={() => setSave(true)}>Save this workspace as a template</Button>
          </span>
        </div>
        {list.loading && !list.data ? <Loading what="templates" /> : null}
        {list.error ? <Alert tone="danger">{list.error}</Alert> : null}
        <ul className="a-kpis" style={{ listStyle: "none", padding: 0 }}>
          {list.data?.items.map((t) => (
            <li key={t.id} className="a-kpi">
              <strong>{t.name}</strong>
              <span className="a-kpi__label">{t.is_system ? "Artify starter" : "Your organization"} · v{t.latest_version}</span>
              <span className="a-hint">{t.description}</span>
              <span className="a-hint">{t.counts.sections} sections, {t.counts.folders} folders, {t.counts.categories} categories, {t.counts.fields} fields</span>
              <Button variant="secondary" className="a-btn--sm" disabled={!can} onClick={() => setApply(t)}>Preview & apply</Button>
            </li>
          ))}
        </ul>
        {list.data && list.data.items.length === 0 ? <Empty>No templates available.</Empty> : null}
      </Panel>
      {apply ? <ApplyDialog ctx={ctx} tpl={apply} onClose={() => setApply(null)} /> : null}
      {copy ? <CopyDialog ctx={ctx} companies={(cos.data ?? []).filter((c) => c.id !== ctx.companyId)} onClose={() => setCopy(false)} /> : null}
      {save ? <SaveDialog ctx={ctx} onClose={() => setSave(false)} /> : null}
    </div>
  );
}

function ApplyDialog({ ctx, tpl, onClose }: { ctx: Ctx; tpl: Tpl; onClose: () => void }) {
  const change = useChange(ctx);
  const [mode, setMode] = useState<"add" | "replace">("add");
  const [confirm, setConfirm] = useState(false);
  const [dry, setDry] = useState<{ fit: Fit[]; fits: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const check = async (m: "add" | "replace") => { setErr(null); try { setDry(await tapi(ctx.orgId, `${base(ctx)}/templates/apply`, { method: "POST", body: { template_id: tpl.id, mode: m, dry_run: true } })); } catch (e) { setErr(errorText(e)); } };
  return (
    <FormDialog title={`Apply “${tpl.name}”`} submit="Apply template" onClose={onClose} disabled={(mode === "replace" && !confirm) || (dry !== null && !dry.fits)}
      onSubmit={async () => { try { await change("POST", "/templates/apply", { template_id: tpl.id, mode, confirm_replace: confirm }, "Template applied. You can now customize everything."); } catch (e) { throw e instanceof ApiProblem && e.problem.code === "entitlement_exceeded" ? new Error(`Your plan does not allow this many items (${e.problem.key}: limit ${e.problem.limit}). Nothing was changed.`) : e; } }}>
      <p>{tpl.counts.sections} sections, {tpl.counts.folders} folders, {tpl.counts.categories} categories, {tpl.counts.fields} fields, {tpl.counts.naming_rules} naming rules and {tpl.counts.filing_rules} filing rules are copied into this company with new ids.</p>
      <fieldset className="a-field"><legend>How should it be applied?</legend>
        <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="radio" name="mode" checked={mode === "add"} onChange={() => { setMode("add"); setDry(null); }} /> Add to the current workspace (existing items are kept; duplicate names get a number)</label>
        <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="radio" name="mode" checked={mode === "replace"} onChange={() => { setMode("replace"); setDry(null); }} /> Replace: archive everything now in the workspace first (nothing is deleted)</label>
      </fieldset>
      {mode === "replace" ? <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> I understand current sections, folders, categories, fields and rules will be archived</label> : null}
      <div><Button type="button" variant="secondary" className="a-btn--sm" onClick={() => void check(mode)}>Check against my plan limits</Button></div>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {dry ? <Alert tone={dry.fits ? "success" : "danger"}>{dry.fits ? "Fits your plan." : "Does not fit your plan:"}{dry.fit.filter((f) => !f.fits).map((f) => <div key={f.key}>{f.key.replace("limit.", "").replace(/_/g, " ")}: would be {f.after}, limit {f.limit}</div>)}</Alert> : null}
    </FormDialog>
  );
}

function CopyDialog({ ctx, companies, onClose }: { ctx: Ctx; companies: Co[]; onClose: () => void }) {
  const change = useChange(ctx);
  const [src, setSrc] = useState("");
  return (
    <FormDialog title="Copy configuration from another company" submit="Copy" onClose={onClose} disabled={!src}
      onSubmit={async () => { await change("POST", "/copy-from", { source_company_id: src, mode: "add" }, "Configuration copied."); }}>
      <p className="a-hint">Copies sections, folders, categories, custom fields, naming rules and filing rules. It never copies documents, users, permissions or financial data, and the copy is independent immediately.</p>
      <Field label="Copy from">{(id) => <select id={id} value={src} onChange={(e) => setSrc(e.target.value)}><option value="">Choose a company…</option>{companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}</Field>
    </FormDialog>
  );
}

function SaveDialog({ ctx, onClose }: { ctx: Ctx; onClose: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  return (
    <FormDialog title="Save as organization template" submit="Save template" onClose={onClose} disabled={!name.trim()}
      onSubmit={async () => { const r = await tapi<{ warnings: string[] }>(ctx.orgId, `/orgs/${ctx.orgId}/workspace-templates`, { method: "POST", body: { name: name.trim(), source_company_id: ctx.companyId } }); toast(r.warnings.length ? `Saved. ${r.warnings.join(" ")}` : "Template saved."); }}>
      <p className="a-hint">Only the configuration is saved: structure, categories, fields, naming and filing rules. Permissions, users and documents are never included. Companies that use this template later stay independent of it.</p>
      <Field label="Template name">{(id) => <input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}</Field>
    </FormDialog>
  );
}

/* ---------------------------------------------------------------- history */
interface Rev { version: number; action: string; created_at: string; actor_name: string | null; change_count: number }
interface RevDetail { version: number; changes: { action: string; entity_type: string; before: unknown; after: unknown }[]; summary: Record<string, number> }

export function HistoryTab({ ctx }: { ctx: Ctx }) {
  const change = useChange(ctx);
  const [offset, setOffset] = useState(0);
  const [view, setView] = useState<number | null>(null);
  const [restore, setRestore] = useState<number | null>(null);
  const revs = useTenantApi<Page<Rev>>(ctx.orgId, ctx.ws.capabilities.configure ? `${base(ctx)}/revisions?limit=15&offset=${offset}&v=${ctx.ws.version}` : null);
  if (!ctx.ws.capabilities.configure) return <Panel><Alert tone="info">History is visible to members who can manage this workspace’s configuration.</Alert></Panel>;
  return (
    <div className="grid gap-4">
      <Panel className="grid gap-3">
        <h2>Workspace history</h2>
        <p className="a-hint">Every change creates a numbered version with who changed what. History is permanent. Restoring returns to an earlier state by archiving later additions and reverting edits; nothing is deleted and permissions are not restored.</p>
        <DataTable<Rev> caption="Workspace versions" rows={revs.data?.items} loading={revs.loading} error={revs.error} onRetry={revs.reload} rowKey={(r) => String(r.version)} empty="No changes yet."
          total={revs.data?.total} limit={15} offset={offset} onPage={setOffset}
          columns={[
            { key: "v", header: "Version", numeric: true, render: (r) => r.version }, { key: "a", header: "Change", render: (r) => r.action.replace(/_/g, " ") },
            { key: "u", header: "By", render: (r) => r.actor_name ?? "System" }, { key: "t", header: "When", render: (r) => fmtDateTime(r.created_at) },
            { key: "x", header: "Actions", render: (r) => (<span className="a-actions"><Button variant="secondary" className="a-btn--sm" onClick={() => setView(r.version)}>Details</Button><Button variant="secondary" className="a-btn--sm" disabled={!ctx.editable} onClick={() => setRestore(r.version)}>Restore this version</Button></span>) },
          ]} />
      </Panel>
      {view !== null ? <RevView ctx={ctx} version={view} onClose={() => setView(null)} /> : null}
      {restore !== null ? <RestoreDialog ctx={ctx} version={restore} onClose={() => setRestore(null)} change={change} /> : null}
    </div>
  );
}

function RevView({ ctx, version, onClose }: { ctx: Ctx; version: number; onClose: () => void }) {
  const d = useTenantApi<RevDetail>(ctx.orgId, `${base(ctx)}/revisions/${version}`);
  return (
    <FormDialog title={`Version ${version}`} submit="Close" onClose={onClose} onSubmit={async () => undefined}>
      {!d.data ? <Loading /> : (
        <>
          <p className="a-hint">State at this version: {Object.entries(d.data.summary).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}.</p>
          <ul>{d.data.changes.map((c, i) => <li key={i}><strong>{c.action.replace(/[._]/g, " ")}</strong> <span className="a-hint">{c.entity_type}</span></li>)}</ul>
        </>
      )}
    </FormDialog>
  );
}

function RestoreDialog({ ctx, version, onClose, change }: { ctx: Ctx; version: number; onClose: () => void; change: ReturnType<typeof useChange> }) {
  const [effect, setEffect] = useState<Record<string, number> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <FormDialog title={`Restore version ${version}`} submit="Restore" danger onClose={onClose} disabled={!effect}
      onSubmit={async () => { await change("POST", `/revisions/${version}/restore`, { dry_run: false }, "Restored."); }}>
      <p>Returns structure, categories, fields and rules to how they were at version {version}. Items added since are archived (not deleted); permissions and access mode are left as they are.</p>
      <div><Button type="button" variant="secondary" className="a-btn--sm" onClick={async () => { setErr(null); try { const r = await tapi<{ effect: Record<string, number> }>(ctx.orgId, `${base(ctx)}/revisions/${version}/restore`, { method: "POST", body: { dry_run: true } }); setEffect(r.effect); } catch (e) { setEffect(null); setErr(errorText(e)); } }}>Preview the effect first</Button></div>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {effect ? <Alert tone="info">Would archive {effect.archived}, restore {effect.restored}, update {effect.updated} and re-create {effect.recreated} item(s). Press Restore to apply.</Alert> : <span className="a-hint">Preview the effect to enable Restore.</span>}
    </FormDialog>
  );
}
