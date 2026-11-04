"use client";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import { ApiProblem, bff } from "@/lib/client";
import { errorText } from "@/lib/platform";

type Grant = Schemas["SupportGrantOut"];
const fmt = (v?: string | null) => (v ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(v)) + " UTC" : "—");
const TONE = { requested: "warning", active: "success", revoked: "neutral", expired: "neutral", denied: "danger" } as const;

function Page() {
  const router = useRouter();
  const org = useSearchParams().get("org") ?? "";
  const [orgId, setOrgId] = useState(org);
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hours, setHours] = useState<Record<string, string>>({});
  const [sensitive, setSensitive] = useState<Record<string, boolean>>({});
  const load = (id: string) =>
    bff<Grant[]>(`/bff/orgs/${id}/support-grants`, { org: id }).then(setGrants).catch((ex) => {
      if (ex instanceof ApiProblem && ex.status === 401) router.replace("/login");
      setErr(ex instanceof ApiProblem && ex.status === 403 ? "Only the firm owner can review support-access requests." : errorText(ex));
    });
  useEffect(() => {
    if (orgId) { load(orgId); return; }
    bff<Schemas["MeOut"]>("/bff/me").then((me) => setOrgId(me.memberships[0]?.organization_id ?? "")).catch(() => router.replace("/login"));
  }, [orgId]); // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (g: Grant, verb: "approve" | "deny" | "revoke") => {
    setErr(null);
    try {
      const body = verb === "approve" ? { hours: Number(hours[g.id] ?? g.requested_hours), access_level: g.access_level, confirm_sensitive: !!sensitive[g.id] }
        : verb === "revoke" ? { reason: "Revoked by the organization owner" } : {};
      await bff(`/bff/orgs/${orgId}/support-grants/${g.id}/${verb}`, { method: "POST", org: orgId, body });
      await load(orgId);
    } catch (ex) { setErr(errorText(ex)); }
  };
  return (
    <main id="main" className="mx-auto grid max-w-4xl gap-6 px-4 py-8">
      <header className="flex items-center justify-between"><h1 className="text-xl font-semibold">Artify support access</h1><Link className="a-link" href="/dashboard">Back to dashboard</Link></header>
      <Alert tone="info">Artify staff can only look at your organization if you approve a request here. Approved access is limited in time and scope, shows only your member list and activity log, and every use appears in your audit log. You can end it at any moment.</Alert>
      {err ? <Alert tone="danger">{err}</Alert> : null}
      {!grants ? <p role="status">Loading…</p> : grants.length === 0 ? <p className="a-hint">No support-access requests.</p> : grants.map((g) => (
        <Panel key={g.id}>
          <div className="a-pagehead"><h2 className="font-semibold">{g.requester_name}</h2><StatusChip tone={TONE[g.status]}>{g.status}</StatusChip></div>
          <dl className="a-dl mt-2">
            <dt>Reason</dt><dd>{g.reason}</dd><dt>Requested</dt><dd>{g.requested_hours} hours, level “{g.access_level}”, on {fmt(g.created_at)}</dd>
            {g.expires_at ? <><dt>Access window</dt><dd>{fmt(g.starts_at)} – {fmt(g.expires_at)} (“{g.approved_level}”)</dd></> : null}
            {g.revoked_at ? <><dt>Ended</dt><dd>{fmt(g.revoked_at)} by {g.revoked_by_type}</dd></> : null}
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
    </main>
  );
}
export default function SupportAccessPage() {
  return <Suspense><Page /></Suspense>;
}
