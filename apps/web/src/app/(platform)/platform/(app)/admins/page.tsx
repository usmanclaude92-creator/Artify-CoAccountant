"use client";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { useCan, useMe } from "@/components/platform/shell";
import { ActionDialog, DataTable, Field, PageHeader, Status, useToast } from "@/components/platform/kit";
import { ApiProblem } from "@/lib/client";
import { ROLE_LABEL, fmtDateTime, papi, useApi, type Page, type S } from "@/lib/platform";

type Dlg = { kind: "invite" } | { kind: "role" | "suspend" | "reactivate" | "sessions" | "activity"; admin: S["AdminRow"] } | null;
const ROLES = Object.keys(ROLE_LABEL);

export default function Admins() {
  const me = useMe();
  const can = useCan();
  const notify = useToast();
  const manage = can("platform.admins.manage");
  const { data, error, loading, reload } = useApi<S["AdminList"]>("/admins");
  const [dlg, setDlg] = useState<Dlg>(null);
  const [role, setRole] = useState("platform_support");
  const [email, setEmail] = useState("");
  const activity = useApi<Page<S["ActivityRow"]>>(dlg?.kind === "activity" ? `/admins/${dlg.admin.id}/activity?limit=15` : null);
  const close = () => setDlg(null);
  const done = (m: string) => { notify(m); close(); reload(); };
  const post = (path: string, body?: unknown) => papi(path, { method: "POST", body });
  const target = dlg && dlg.kind !== "invite" ? dlg.admin : null;
  return (
    <>
      <PageHeader title="Platform Administrators" crumbs={[{ label: "Platform Administrators" }]}
        actions={manage ? <Button onClick={() => { setEmail(""); setRole("platform_support"); setDlg({ kind: "invite" }); }}>Invite administrator</Button> : null} />
      <Alert tone="info">Least privilege: each role holds only the permissions it needs, nobody can change their own role, and nobody can grant a role above their own. Two-factor authentication is mandatory for every administrator.</Alert>
      <Panel>
        <h2 className="mb-2 font-semibold">Administrators</h2>
        <DataTable caption="Platform administrators" loading={loading} error={error} onRetry={reload} rows={data?.administrators} empty="No administrators." rowKey={(a) => a.id}
          columns={[
            { key: "n", header: "Administrator", render: (a) => <>{a.display_name}{a.id === me.admin_id ? " (you)" : ""}<span className="a-hint" style={{ display: "block" }}>{a.email}</span></> },
            { key: "r", header: "Role", render: (a) => ROLE_LABEL[a.role_key] ?? a.role_key },
            { key: "s", header: "Status", render: (a) => <Status value={a.status} text={a.status === "disabled" ? "Suspended" : "Active"} /> },
            { key: "l", header: "Last sign-in", render: (a) => fmtDateTime(a.last_login_at) },
            { key: "p", header: "Permissions", numeric: true, render: (a) => a.permissions.length },
            { key: "x", header: "Actions", render: (a) => (
              <span className="a-actions">
                <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg({ kind: "activity", admin: a })}>History</Button>
                {manage && a.id !== me.admin_id ? <>
                  <Button variant="secondary" className="a-btn--sm" onClick={() => { setRole(a.role_key); setDlg({ kind: "role", admin: a }); }}>Change role</Button>
                  {a.status === "active"
                    ? <Button className="a-btn--sm a-btn--danger" onClick={() => setDlg({ kind: "suspend", admin: a })}>Suspend</Button>
                    : <Button className="a-btn--sm" onClick={() => setDlg({ kind: "reactivate", admin: a })}>Reactivate</Button>}
                  <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg({ kind: "sessions", admin: a })}>Sign out everywhere</Button>
                </> : null}
              </span>) },
          ]} />
      </Panel>
      <Panel>
        <h2 className="mb-2 font-semibold">Invitations</h2>
        <DataTable caption="Administrator invitations" loading={loading} error={null} rows={data?.invitations} empty="No invitations." rowKey={(i) => i.id}
          columns={[
            { key: "e", header: "E-mail", render: (i) => i.email }, { key: "r", header: "Role", render: (i) => ROLE_LABEL[i.role_key] ?? i.role_key },
            { key: "s", header: "State", render: (i) => <Status value={i.state} /> }, { key: "x", header: "Expires", render: (i) => fmtDateTime(i.expires_at) },
            { key: "a", header: "", render: (i) => manage && i.state === "pending" ? <Button variant="secondary" className="a-btn--sm" onClick={async () => { await papi(`/admins/invitations/${i.id}`, { method: "DELETE" }); notify("Invitation revoked."); reload(); }}>Revoke</Button> : null },
          ]} />
      </Panel>

      <ActionDialog open={dlg?.kind === "invite"} title="Invite an administrator" submitLabel="Send invitation" needsReason={false}
        description="They receive a link, choose their own password and must set up two-factor authentication at first sign-in."
        onSubmit={async () => { await post("/admins/invitations", { email, role_key: role }); done("Invitation sent."); }} onClose={close}>
        <Field label="E-mail address">{(i) => <input id={i} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />}</Field>
        <Field label="Role">{(i) => <select id={i} value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select>}</Field>
      </ActionDialog>
      <ActionDialog open={dlg?.kind === "role"} title={`Change role of ${target?.display_name ?? ""}`} submitLabel="Change role" confirmWord={target?.email} danger={role === "platform_owner"}
        onSubmit={async ({ reason, confirm }) => { await papi(`/admins/${target!.id}`, { method: "PATCH", body: { role_key: role, reason, confirm } }); done("Role changed."); }} onClose={close}>
        <Field label="New role">{(i) => <select id={i} value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select>}</Field>
      </ActionDialog>
      <ActionDialog open={dlg?.kind === "suspend"} title={`Suspend ${target?.display_name ?? ""}?`} danger submitLabel="Suspend and sign out" confirmWord={target?.email}
        description="They are signed out of every session immediately and cannot sign in until reactivated."
        onSubmit={async ({ reason, confirm }) => { await post(`/admins/${target!.id}/suspend`, { reason, confirm }); done("Administrator suspended."); }} onClose={close} />
      <ActionDialog open={dlg?.kind === "reactivate"} title={`Reactivate ${target?.display_name ?? ""}`} submitLabel="Reactivate"
        onSubmit={async ({ reason }) => { await post(`/admins/${target!.id}/reactivate`, { reason }); done("Administrator reactivated."); }} onClose={close} />
      <ActionDialog open={dlg?.kind === "sessions"} title={`Sign ${target?.display_name ?? ""} out everywhere`} submitLabel="Revoke sessions"
        onSubmit={async ({ reason }) => { try { await post(`/admins/${target!.id}/revoke-sessions`, { reason }); } catch (ex) { if (!(ex instanceof ApiProblem && ex.status === 204)) throw ex; } done("Sessions revoked."); }} onClose={close} />
      <ActionDialog open={dlg?.kind === "activity"} title={`Sign-ins and actions — ${target?.display_name ?? ""}`} submitLabel="Close" needsReason={false} onSubmit={async () => { close(); }} onClose={close}>
        <DataTable caption="Administrator activity" loading={activity.loading} error={activity.error} empty="No activity." rowKey={(r) => r.id} rows={activity.data?.items}
          columns={[{ key: "w", header: "When", render: (r) => fmtDateTime(r.created_at) }, { key: "a", header: "Action", render: (r) => r.action }, { key: "o", header: "Outcome", render: (r) => <Status value={r.outcome} /> }]} />
      </ActionDialog>
    </>
  );
}
