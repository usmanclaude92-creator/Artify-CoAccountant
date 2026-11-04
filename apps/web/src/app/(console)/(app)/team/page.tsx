"use client";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Dialog, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { ActionDialog, DataTable, Field, SearchBox, SelectFilter, Status, useToast } from "@/components/kit";
import { ORG_ROLE_LABEL, errorText, fmtDate, fmtDateTime, tapi, useListState, useTenantApi, type Page } from "@/lib/tenant";

type Member = Schemas["TeamMember"];
type Role = Schemas["RoleOut"];
const KEYS = ["q", "status", "role", "sort", "order", "offset"];

export default function TeamPage() {
  const { orgId, can, me, role: myRole } = useOrg();
  const toast = useToast();
  const ls = useListState({ sort: "name", order: "asc" });
  const limit = 25;
  const list = useTenantApi<Page<Member>>(orgId, `/orgs/${orgId}/team${ls.query(KEYS, { limit: String(limit) })}`);
  const roles = useTenantApi<Role[]>(orgId, "/roles");
  const orgRoles = (roles.data ?? []).filter((r) => r.scope === "organization").sort((a, b) => (b.rank ?? 0) - (a.rank ?? 0));
  const myRank = orgRoles.find((r) => r.key === myRole)?.rank ?? 0;
  const [dlg, setDlg] = useState<{ kind: "role" | "status" | "remove" | "profile" | "transfer"; m: Member } | null>(null);
  const [showRoles, setShowRoles] = useState(false);
  const manage = can("members.manage");
  const done = (msg: string) => { toast(msg); setDlg(null); list.reload(); };
  const onSort = (k: string) => ls.set({ sort: k, order: ls.get("sort") === k && ls.get("order") === "asc" ? "desc" : "asc" });
  return (
    <div className="grid gap-4">
      <CcHeader title="Team Members" actions={<Button variant="secondary" onClick={() => setShowRoles((s) => !s)} aria-expanded={showRoles}>Roles and permissions</Button>} />
      {showRoles ? <RolesPanel roles={roles.data ?? []} /> : null}
      <div className="a-toolbar">
        <SearchBox key={ls.get("q")} value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Name or e-mail" />
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={[{ value: "active", label: "Active" }, { value: "suspended", label: "Suspended" }, { value: "removed", label: "Removed" }]} />
        <SelectFilter label="Role" value={ls.get("role")} onChange={(v) => ls.set({ role: v })} options={orgRoles.map((r) => ({ value: r.key, label: r.name }))} />
      </div>
      <DataTable<Member> caption="Team members" rows={list.data?.items} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(r) => r.user_id}
        empty="No team members match." total={list.data?.total} limit={limit} offset={Number(ls.get("offset") || 0)} onPage={(o) => ls.set({ offset: String(o) })}
        sort={ls.get("sort")} order={ls.get("order")} onSort={onSort}
        columns={[
          { key: "name", header: "Name", sortKey: "name", render: (r) => <>{r.display_name}{r.user_id === me.user_id ? " (you)" : ""}<span className="a-hint" style={{ display: "block" }}>{r.email}</span></> },
          { key: "role", header: "Role", sortKey: "role", render: (r) => ORG_ROLE_LABEL[r.role] ?? r.role },
          { key: "job", header: "Job title", render: (r) => r.job_title ?? "—" },
          { key: "status", header: "Status", sortKey: "status", render: (r) => <Status value={r.status} /> },
          { key: "co", header: "Assigned companies", numeric: true, render: (r) => r.assigned_company_count ?? "—" },
          { key: "act", header: "Last activity", sortKey: "activity", render: (r) => fmtDateTime(r.last_activity_at) },
          { key: "joined", header: "Joined", sortKey: "joined", render: (r) => fmtDate(r.joined_at) },
          { key: "a", header: "Actions", render: (r) => {
            const rank = orgRoles.find((x) => x.key === r.role)?.rank ?? 0;
            const self = r.user_id === me.user_id;
            return (
              <span className="a-actions">
                {can("member_profiles.manage") ? <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg({ kind: "profile", m: r })}>Profile {r.display_name}</Button> : null}
                {manage && !self && r.status !== "removed" && rank <= myRank && r.role !== "firm_owner" ? <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg({ kind: "role", m: r })}>Change role of {r.display_name}</Button> : null}
                {manage && !self && r.status !== "removed" && r.role !== "firm_owner" && rank <= myRank ? <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg({ kind: "status", m: r })}>{r.status === "active" ? "Suspend" : "Reactivate"} {r.display_name}</Button> : null}
                {manage && !self && r.status !== "removed" && r.role !== "firm_owner" && rank <= myRank ? <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg({ kind: "remove", m: r })}>Remove {r.display_name}</Button> : null}
                {can("ownership.transfer") && !self && r.status === "active" ? <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg({ kind: "transfer", m: r })}>Transfer ownership to {r.display_name}</Button> : null}
              </span>
            );
          } },
        ]} />
      {dlg?.kind === "role" ? <RoleDialog m={dlg.m} roles={orgRoles.filter((r) => r.key !== "firm_owner" && (r.rank ?? 0) <= myRank)} onClose={() => setDlg(null)} onDone={() => done("Role updated.")} /> : null}
      {dlg?.kind === "status" ? (
        <ActionDialog open title={`${dlg.m.status === "active" ? "Suspend" : "Reactivate"} ${dlg.m.display_name}`} needsReason={false}
          description={dlg.m.status === "active" ? "They are signed out of this organization immediately and lose all access until reactivated. Their history is kept." : "They regain the access of their role."}
          submitLabel={dlg.m.status === "active" ? "Suspend" : "Reactivate"} danger={dlg.m.status === "active"} onClose={() => setDlg(null)}
          onSubmit={async () => { await tapi(orgId, `/orgs/${orgId}/members/${dlg.m.user_id}`, { method: "PATCH", body: { status: dlg.m.status === "active" ? "suspended" : "active" } }); done("Member updated."); }} />
      ) : null}
      {dlg?.kind === "remove" ? (
        <ActionDialog open title={`Remove ${dlg.m.display_name}`} needsReason={false} submitLabel="Remove from organization" danger onClose={() => setDlg(null)}
          description="They lose all access to this organization and their company assignments end. Their past activity stays in the audit history. They can be invited again later."
          onSubmit={async () => { await tapi(orgId, `/orgs/${orgId}/members/${dlg.m.user_id}`, { method: "PATCH", body: { status: "removed" } }); done("Member removed."); }} />
      ) : null}
      {dlg?.kind === "profile" ? <ProfileDialog m={dlg.m} onClose={() => setDlg(null)} onDone={() => done("Professional profile saved.")} /> : null}
      {dlg?.kind === "transfer" ? <TransferDialog m={dlg.m} onClose={() => setDlg(null)} onDone={() => done("Ownership transferred.")} /> : null}
      {myRole === null ? <Alert tone="info">You have company-level access only.</Alert> : null}
    </div>
  );
}

function RoleDialog({ m, roles, onClose, onDone }: { m: Member; roles: Role[]; onClose: () => void; onDone: () => void }) {
  const { orgId } = useOrg();
  const [role, setRole] = useState(m.role);
  return (
    <ActionDialog open title={`Change role of ${m.display_name}`} needsReason={false} submitLabel="Save role" onClose={onClose}
      description="Roles above your own cannot be granted. Privileged roles require two-factor authentication before they take effect."
      onSubmit={async () => { await tapi(orgId, `/orgs/${orgId}/members/${m.user_id}`, { method: "PATCH", body: { role } }); onDone(); }}>
      <Field label="Role">{(id) => <select id={id} value={role} onChange={(e) => setRole(e.target.value)}>{roles.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</select>}</Field>
    </ActionDialog>
  );
}

function ProfileDialog({ m, onClose, onDone }: { m: Member; onClose: () => void; onDone: () => void }) {
  const { orgId } = useOrg();
  const [f, setF] = useState({ job_title: m.job_title ?? "", phone: m.phone ?? "", license_number: m.license_number ?? "" });
  return (
    <ActionDialog open title={`Professional profile of ${m.display_name}`} needsReason={false} submitLabel="Save" onClose={onClose}
      description="Job title and licence are descriptive. They do not change what this person is allowed to do."
      onSubmit={async () => { await tapi(orgId, `/orgs/${orgId}/members/${m.user_id}/profile`, { method: "PATCH", body: { job_title: f.job_title.trim() || null, phone: f.phone.trim() || null, license_number: f.license_number.trim() || null } }); onDone(); }}>
      <Field label="Job title">{(id) => <input id={id} value={f.job_title} maxLength={100} onChange={(e) => setF({ ...f, job_title: e.target.value })} />}</Field>
      <Field label="Phone">{(id) => <input id={id} value={f.phone} maxLength={40} onChange={(e) => setF({ ...f, phone: e.target.value })} />}</Field>
      <Field label="Professional licence number">{(id) => <input id={id} value={f.license_number} maxLength={60} onChange={(e) => setF({ ...f, license_number: e.target.value })} />}</Field>
    </ActionDialog>
  );
}

function TransferDialog({ m, onClose, onDone }: { m: Member; onClose: () => void; onDone: () => void }) {
  const { orgId } = useOrg();
  const settings = useTenantApi<Schemas["OrgSettingsOut"]>(orgId, `/orgs/${orgId}/settings`);
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open title={`Transfer ownership to ${m.display_name}`} onClose={onClose}>
      <form className="grid gap-3" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr(null);
        try { await tapi(orgId, `/orgs/${orgId}/ownership-transfer`, { method: "POST", body: { new_owner_user_id: m.user_id, confirm } }); onDone(); }
        catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
      }}>
        <p className="a-hint">{m.display_name} becomes the organization owner and you become an organization administrator. This is the only way to hand over ownership, and it needs a two-factor-verified session.</p>
        <Field label={`Type the organization identifier “${settings.data?.slug ?? "…"}” to confirm`}>{(id) => <input id={id} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" spellCheck={false} />}</Field>
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <div className="a-actions" style={{ justifyContent: "flex-end" }}>
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" className="a-btn--danger" disabled={busy || !confirm.trim() || !settings.data}>{busy ? "Transferring…" : "Transfer ownership"}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function RolesPanel({ roles }: { roles: Role[] }) {
  const org = roles.filter((r) => r.scope === "organization").sort((a, b) => (b.rank ?? 0) - (a.rank ?? 0));
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Panel className="grid gap-3">
      <h2>Roles and permissions</h2>
      <p className="a-hint">Roles are defined by Artify and enforced by the server on every request. Custom roles are not available yet. Organization-level roles see every company or only assigned companies, as shown.</p>
      <div className="a-tablewrap">
        <table className="a-table">
          <caption className="a-visually-hidden">Organization roles</caption>
          <thead><tr><th scope="col">Role</th><th scope="col">Company access</th><th scope="col">Two-factor required</th><th scope="col">Can be invited</th><th scope="col">Permissions</th></tr></thead>
          <tbody>
            {org.map((r) => (
              <tr key={r.key}>
                <td>{r.name}</td>
                <td>{r.reaches_all_companies ? "All companies" : "Assigned companies only"}</td>
                <td>{r.mfa_required ? "Yes" : "No"}</td>
                <td>{r.invitable ? "Yes" : "Only by ownership transfer"}</td>
                <td><Button variant="secondary" className="a-btn--sm" aria-expanded={open === r.key} onClick={() => setOpen(open === r.key ? null : r.key)}>{r.permissions.length} permissions<span className="a-visually-hidden"> of {r.name}</span></Button>
                  {open === r.key ? <ul className="a-hint">{r.permissions.map((p) => <li key={p}>{p}</li>)}</ul> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
