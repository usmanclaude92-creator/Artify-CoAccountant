"use client";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { Empty, ErrorBox, Loading, Status, useToast } from "@/components/kit";
import { errorText, fmtDateTime, tapi, useTenantApi } from "@/lib/tenant";

type Grant = Schemas["SupportGrantOut"];

export default function SupportAccessPage() {
  const { orgId, can } = useOrg();
  const toast = useToast();
  const { data, error, reload } = useTenantApi<Grant[]>(orgId, can("support_grants.manage") ? `/orgs/${orgId}/support-grants` : null);
  const [hours, setHours] = useState<Record<string, string>>({});
  const [sensitive, setSensitive] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const act = async (g: Grant, verb: "approve" | "deny" | "revoke") => {
    setErr(null);
    try {
      const body = verb === "approve" ? { hours: Number(hours[g.id] ?? g.requested_hours), access_level: g.access_level, confirm_sensitive: !!sensitive[g.id] }
        : verb === "revoke" ? { reason: "Revoked by the organization owner" } : {};
      await tapi(orgId, `/orgs/${orgId}/support-grants/${g.id}/${verb}`, { method: "POST", body });
      toast("Done."); reload();
    } catch (ex) { setErr(errorText(ex)); }
  };
  return (
    <div className="grid gap-4">
      <CcHeader title="Artify support access" />
      <Alert tone="info">Artify staff can only look at your organization if you approve a request here. Approved access is limited in time and scope, shows only your member list and activity log, and every use appears in your audit log. You can end it at any moment.</Alert>
      {!can("support_grants.manage") ? <Alert tone="info">Only the organization owner can review support-access requests.</Alert> : null}
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {error && !data ? <ErrorBox message={error} onRetry={reload} /> : !data ? (can("support_grants.manage") ? <Loading /> : null) : data.length === 0 ? <Empty>No support-access requests.</Empty> : data.map((g) => (
        <Panel key={g.id}>
          <div className="a-pagehead"><h2>{g.requester_name}</h2><Status value={g.status} /></div>
          <dl className="a-dl">
            <dt>Reason</dt><dd>{g.reason}</dd><dt>Requested</dt><dd>{g.requested_hours} hours, level “{g.access_level}”, on {fmtDateTime(g.created_at)}</dd>
            {g.expires_at ? <><dt>Access window</dt><dd>{fmtDateTime(g.starts_at)} – {fmtDateTime(g.expires_at)} (“{g.approved_level}”)</dd></> : null}
            {g.revoked_at ? <><dt>Ended</dt><dd>{fmtDateTime(g.revoked_at)} by {g.revoked_by_type}</dd></> : null}
          </dl>
          {g.status === "requested" ? (
            <div className="a-toolbar mt-3">
              <div className="a-field"><label htmlFor={`h-${g.id}`}>Hours to allow (max {g.requested_hours})</label>
                <input id={`h-${g.id}`} type="number" min={1} max={g.requested_hours} value={hours[g.id] ?? String(g.requested_hours)} onChange={(e) => setHours({ ...hours, [g.id]: e.target.value })} /></div>
              {g.access_level === "read" ? <label className="flex items-center gap-2"><input type="checkbox" checked={!!sensitive[g.id]} onChange={(e) => setSensitive({ ...sensitive, [g.id]: e.target.checked })} /> I understand this includes access to sensitive data</label> : null}
              <Button onClick={() => act(g, "approve")} disabled={g.access_level === "read" && !sensitive[g.id]}>Approve</Button>
              <Button variant="secondary" onClick={() => act(g, "deny")}>Deny</Button>
            </div>
          ) : null}
          {g.status === "active" ? <div className="mt-3"><Button className="a-btn--danger" onClick={() => act(g, "revoke")}>End access now</Button></div> : null}
        </Panel>
      ))}
    </div>
  );
}
