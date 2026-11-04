"use client";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { ActionDialog, DataTable, Field, PageHeader, SelectFilter, Status, useToast } from "@/components/platform/kit";
import { fmtDateTime, label, papi, useApi, useListState, type Page, type S } from "@/lib/platform";

export default function Support() {
  const can = useCan();
  const notify = useToast();
  const ls = useListState();
  const preset = useSearchParams().get("organization") ?? "";
  const { data, error, loading, reload } = useApi<Page<S["GrantRow"]>>(`/support/grants${ls.query(["status", "offset"], { limit: "25" })}`);
  const [req, setReq] = useState(!!preset);
  const orgs = useApi<Page<S["OrgRow"]>>(req ? "/organizations?limit=200&sort=name&order=asc" : null);
  const [f, setF] = useState({ organization_id: preset, access_level: "metadata", requested_hours: "8" });
  const [view, setView] = useState<S["GrantRow"] | null>(null);
  const [revoke, setRevoke] = useState<S["GrantRow"] | null>(null);
  const members = useApi<S["MemberView"][]>(view ? `/support/grants/${view.id}/members` : null);
  const audit = useApi<S["AuditView"][]>(view ? `/support/grants/${view.id}/audit-events?limit=25` : null);
  return (
    <>
      <PageHeader title="Support Access" crumbs={[{ label: "Support Access" }]}
        actions={can("platform.support.request") ? <Button onClick={() => setReq(true)}>Request access</Button> : null} />
      <Alert tone="info">
        Support access is <strong>customer-authorized, time-limited and read-only</strong>. A request does nothing until the organization&apos;s owner approves it. There is no impersonation: you never act as, or sign in as, a customer.
        Each use is recorded in Artify&apos;s audit log <em>and</em> in the customer&apos;s own audit log.
      </Alert>
      <div className="a-toolbar">
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={["requested", "active", "expired", "revoked", "denied"].map((v) => ({ value: v, label: label(v) }))} />
      </div>
      <Panel>
        <DataTable caption="Support access requests and grants" loading={loading} error={error} onRetry={reload} rows={data?.items} total={data?.total} limit={data?.limit} offset={data?.offset}
          onPage={(o) => ls.set({ offset: String(o) })} empty="No support-access requests." rowKey={(g) => g.id}
          columns={[
            { key: "o", header: "Organization", render: (g) => g.organization_name }, { key: "r", header: "Requested by", render: (g) => g.requester_name },
            { key: "re", header: "Reason", render: (g) => g.reason },
            { key: "l", header: "Level", render: (g) => g.approved_level ? `${label(g.approved_level)} (approved)` : `${label(g.access_level)} (requested)` },
            { key: "s", header: "Status", render: (g) => <Status value={g.status} /> },
            { key: "e", header: "Window", render: (g) => g.expires_at ? `until ${fmtDateTime(g.expires_at)}` : `${g.requested_hours} h requested` },
            { key: "a", header: "Actions", render: (g) => (
              <span className="a-actions">
                {g.status === "active" && can("platform.support.view_data") ? <Button variant="secondary" className="a-btn--sm" onClick={() => setView(g)}>View members</Button> : null}
                {["requested", "active"].includes(g.status) ? <Button variant="secondary" className="a-btn--sm" onClick={() => setRevoke(g)}>{g.status === "requested" ? "Withdraw" : "Revoke"}</Button> : null}
              </span>) },
          ]} />
      </Panel>
      {view ? (
        <Panel>
          <div className="a-pagehead"><h2 className="font-semibold">{view.organization_name} — metadata under grant</h2><Button variant="secondary" onClick={() => setView(null)}>Close view</Button></div>
          <p className="a-hint mb-2">Members and recent audit events only. This view is recorded in both audit logs.</p>
          <DataTable caption="Organization members" loading={members.loading} error={members.error} empty="No members." rowKey={(m) => m.user_id} rows={members.data ?? undefined}
            columns={[{ key: "n", header: "Name", render: (m) => m.display_name }, { key: "e", header: "E-mail", render: (m) => m.email }, { key: "r", header: "Role", render: (m) => label(m.role) }, { key: "s", header: "Status", render: (m) => <Status value={m.membership_status} /> }]} />
          <h3 className="mt-4 mb-2 font-semibold">Recent audit events</h3>
          <DataTable caption="Organization audit events" loading={audit.loading} error={audit.error} empty="No events." rowKey={(a) => a.id} rows={audit.data ?? undefined}
            columns={[{ key: "w", header: "When", render: (a) => fmtDateTime(a.created_at) }, { key: "a", header: "Action", render: (a) => a.action }, { key: "t", header: "Actor", render: (a) => label(a.actor_type) }]} />
        </Panel>
      ) : null}

      <ActionDialog open={req} title="Request support access" submitLabel="Send request" minReason={20} reasonLabel="Why do you need access? (the customer sees this)"
        description="The organization's owner is notified and decides whether to approve."
        onSubmit={async ({ reason }) => { await papi("/support/grants", { method: "POST", body: { organization_id: f.organization_id, access_level: f.access_level, reason, requested_hours: Number(f.requested_hours) } }); notify("Request sent to the customer."); setReq(false); reload(); }}
        onClose={() => setReq(false)}>
        <Field label="Organization">{(i) => <select id={i} value={f.organization_id} onChange={(e) => setF({ ...f, organization_id: e.target.value })} required><option value="">Choose…</option>{orgs.data?.items.map((o) => <option key={o.id} value={o.id}>{o.display_name}</option>)}</select>}</Field>
        <Field label="Access level" hint="Sensitive-data access needs a separate, explicit confirmation by the customer. No documents exist yet, so both levels show the same metadata today.">
          {(i) => <select id={i} value={f.access_level} onChange={(e) => setF({ ...f, access_level: e.target.value })}><option value="metadata">Metadata (members, audit log)</option><option value="read">Sensitive data (reserved)</option></select>}
        </Field>
        <Field label="Hours requested (1–72)">{(i) => <input id={i} type="number" min={1} max={72} value={f.requested_hours} onChange={(e) => setF({ ...f, requested_hours: e.target.value })} required />}</Field>
      </ActionDialog>
      <ActionDialog open={!!revoke} title={revoke?.status === "requested" ? "Withdraw this request?" : "Revoke this grant now?"} danger submitLabel={revoke?.status === "requested" ? "Withdraw" : "Revoke"}
        onSubmit={async ({ reason }) => { await papi(`/support/grants/${revoke!.id}/revoke`, { method: "POST", body: { reason } }); notify("Done."); setRevoke(null); setView(null); reload(); }} onClose={() => setRevoke(null)} />
    </>
  );
}
