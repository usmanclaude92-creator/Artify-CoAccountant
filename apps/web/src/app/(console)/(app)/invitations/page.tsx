"use client";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { ActionDialog, DataTable, Field, SelectFilter, Status, useToast } from "@/components/kit";
import { ORG_ROLE_LABEL, errorText, fmtDateTime, tapi, useListState, useTenantApi } from "@/lib/tenant";

type Inv = Schemas["InvitationOut"];
type Role = Schemas["RoleOut"];

export default function InvitationsPage() {
  const { orgId, can, role: myRole } = useOrg();
  const toast = useToast();
  const ls = useListState();
  const list = useTenantApi<Inv[]>(orgId, `/orgs/${orgId}/invitations${ls.query(["state"])}`);
  const roles = useTenantApi<Role[]>(orgId, "/roles");
  const companies = useTenantApi<Schemas["CompanyOut"][]>(orgId, can("companies.list") ? `/orgs/${orgId}/companies` : null);
  const myRank = roles.data?.find((r) => r.key === myRole)?.rank ?? 0;
  const invitable = (roles.data ?? []).filter((r) => r.scope === "organization" && r.invitable && (r.rank ?? 0) <= myRank).sort((a, b) => (b.rank ?? 0) - (a.rank ?? 0));
  const [f, setF] = useState({ email: "", role: "", company_ids: [] as string[] });
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<{ tone: "info" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [cancel, setCancel] = useState<Inv | null>(null);
  const role = f.role || invitable.find((r) => r.key === "accountant")?.key || invitable[0]?.key || "";
  const picked = roles.data?.find((r) => r.key === role);
  const send = async () => {
    setBusy(true); setErr(null); setNote(null);
    try {
      const r = await tapi<Inv>(orgId, `/orgs/${orgId}/invitations`, { method: "POST", body: { email: f.email.trim(), role, company_ids: picked?.assignable_to_companies ? f.company_ids : [] } });
      const d = r.email_delivery as { configured?: boolean; note?: string } | null;
      setNote({ tone: d?.configured ? "info" : "warning", text: d?.note ?? "Invitation created." });
      toast("Invitation created."); setF({ email: "", role: "", company_ids: [] }); list.reload();
    } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
  };
  const resend = async (i: Inv) => {
    try { const r = await tapi<Inv>(orgId, `/orgs/${orgId}/invitations/${i.id}/resend`, { method: "POST", body: {} }); toast((r.email_delivery as { note?: string } | null)?.note ?? "Invitation renewed.", "info"); list.reload(); }
    catch (ex) { toast(errorText(ex), "danger"); }
  };
  return (
    <div className="grid gap-4">
      <CcHeader title="Invitations" />
      {can("invitations.manage") ? (
        <Panel className="grid gap-3">
          <h2>Invite a team member</h2>
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <Field label="E-mail">{(id) => <input id={id} type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />}</Field>
            <Field label="Role" hint={picked ? `${picked.reaches_all_companies ? "Sees all companies." : "Sees only assigned companies."}${picked.mfa_required ? " Two-factor authentication is required." : ""}` : undefined}>
              {(id) => <select id={id} value={role} onChange={(e) => setF({ ...f, role: e.target.value })}>{invitable.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</select>}
            </Field>
            {picked?.assignable_to_companies && (companies.data?.length ?? 0) > 0 ? (
              <fieldset className="grid gap-1"><legend>Assign to companies on acceptance (optional)</legend>
                {companies.data?.filter((c) => c.status !== "archived").map((c) => (
                  <label key={c.id} className="flex items-center gap-2"><input type="checkbox" checked={f.company_ids.includes(c.id)} onChange={(e) => setF({ ...f, company_ids: e.target.checked ? [...f.company_ids, c.id] : f.company_ids.filter((x) => x !== c.id) })} /> {c.name}</label>
                ))}
              </fieldset>
            ) : null}
            {err ? <Alert tone="danger">{err}</Alert> : null}
            {note ? <Alert tone={note.tone}>{note.text}</Alert> : null}
            <div><Button type="submit" disabled={busy || !role}>{busy ? "Creating…" : "Send invitation"}</Button></div>
          </form>
        </Panel>
      ) : null}
      <div className="a-toolbar">
        <SelectFilter label="State" value={ls.get("state")} onChange={(v) => ls.set({ state: v })} options={["pending", "accepted", "declined", "revoked", "expired"].map((s) => ({ value: s, label: s[0]!.toUpperCase() + s.slice(1) }))} />
      </div>
      <DataTable<Inv> caption="Invitations" rows={list.data ?? undefined} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(r) => r.id} empty="No invitations."
        columns={[
          { key: "e", header: "E-mail", render: (r) => r.email },
          { key: "r", header: "Role", render: (r) => ORG_ROLE_LABEL[r.role] ?? r.role },
          { key: "s", header: "State", render: (r) => <Status value={r.state === "pending" ? "pending" : r.state === "accepted" ? "accepted" : r.state === "revoked" ? "revoked" : r.state === "declined" ? "denied" : "expired"} text={r.state[0]!.toUpperCase() + r.state.slice(1)} /> },
          { key: "c", header: "Companies", numeric: true, render: (r) => r.company_ids.length || "—" },
          { key: "by", header: "Invited by", render: (r) => r.invited_by_name ?? "—" },
          { key: "x", header: "Expires", render: (r) => fmtDateTime(r.expires_at) },
          { key: "n", header: "Resent", numeric: true, render: (r) => r.resent_count },
          { key: "a", header: "Actions", render: (r) => (r.state === "pending" || r.state === "expired") && can("invitations.manage") ? (
            <span className="a-actions">
              <Button variant="secondary" className="a-btn--sm" onClick={() => resend(r)}>Resend to {r.email}</Button>
              {r.state === "pending" ? <Button variant="secondary" className="a-btn--sm" onClick={() => setCancel(r)}>Cancel invitation for {r.email}</Button> : null}
            </span>
          ) : null },
        ]} />
      {cancel ? (
        <ActionDialog open title="Cancel invitation" needsReason={false} submitLabel="Cancel invitation" danger description={`The link sent to ${cancel.email} stops working immediately.`} onClose={() => setCancel(null)}
          onSubmit={async () => { await tapi(orgId, `/orgs/${orgId}/invitations/${cancel.id}`, { method: "DELETE" }); toast("Invitation cancelled."); setCancel(null); list.reload(); }} />
      ) : null}
    </div>
  );
}
