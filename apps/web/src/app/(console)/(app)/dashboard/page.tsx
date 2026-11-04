"use client";
import Link from "next/link";
import type { Schemas } from "@artify/api-client";
import { Alert, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { Empty, ErrorBox, Kpi, Loading, Meter, Status } from "@/components/kit";
import { fmtDate, fmtDateTime, fmtNumber, label, useTenantApi } from "@/lib/tenant";

type Dash = Schemas["DashboardOut"];

const USAGE_LABEL: Record<string, string> = {
  "limit.companies": "Client companies", "limit.users": "Team members", "limit.accountants": "Accountants", "limit.devices": "Devices",
};

export default function DashboardPage() {
  const { orgId, orgName, can, href, role } = useOrg();
  const { data, error, loading, reload } = useTenantApi<Dash>(orgId, `/orgs/${orgId}/dashboard`);
  return (
    <div className="grid gap-6">
      <CcHeader title="Dashboard" />
      {error && !data ? (
        /permit|forbidden/i.test(error) ? <Alert tone="info">Your role does not include the organization dashboard. Open <Link className="a-link" href={href("/companies")}>My Companies</Link> instead.</Alert> : <ErrorBox message={error} onRetry={reload} />
      ) : !data || loading ? <Loading what="the dashboard" /> : (
        <>
          <p className="a-hint">{orgName} · {role ? "Organization member" : "Company access"} · organization status <Status value={data.organization_status} /></p>
          {!data.email_delivery.configured ? <Alert tone="warning">{String(data.email_delivery.note)}</Alert> : null}

          <section aria-labelledby="h-companies" className="grid gap-3">
            <h2 id="h-companies">Companies</h2>
            <div className="a-kpis">
              <Kpi label="Companies" value={fmtNumber(data.companies.total)} note="you can see" />
              <Kpi label="Active" value={fmtNumber(data.companies.active)} />
              <Kpi label="Inactive" value={fmtNumber(data.companies.inactive)} />
              <Kpi label="Archived" value={fmtNumber(data.companies.archived)} />
              <Kpi label="Assigned to me" value={fmtNumber(data.assigned_to_me)} />
            </div>
            {data.companies.total === 0 ? <Empty>No companies yet.{can("companies.create") ? <> <Link className="a-link" href={href("/companies/new")}>Create your first company</Link>.</> : null}</Empty> : null}
          </section>

          {data.team ? (
            <section aria-labelledby="h-team" className="grid gap-3">
              <h2 id="h-team">Team</h2>
              <div className="a-kpis">
                <Kpi label="Active members" value={fmtNumber(data.team.members_active)} />
                <Kpi label="Suspended" value={fmtNumber(data.team.members_suspended)} />
                <Kpi label="Active accountants" value={fmtNumber(data.team.accountants_active)} />
                {data.pending_invitations !== null ? <Kpi label="Pending invitations" value={fmtNumber(data.pending_invitations)} /> : null}
              </div>
            </section>
          ) : null}

          {data.subscription || data.usage ? (
            <section aria-labelledby="h-usage" className="grid gap-3">
              <h2 id="h-usage">Subscription and usage</h2>
              {data.subscription ? (
                <p>Plan <strong>{data.subscription.plan_name}</strong> <Status value={data.subscription.status} />
                  {data.subscription.trial_ends_at ? <> · trial ends {fmtDate(data.subscription.trial_ends_at)}</> : null}
                  {data.subscription.current_period_end ? <> · current period ends {fmtDate(data.subscription.current_period_end)}</> : null}
                </p>
              ) : <Empty>No subscription is recorded for this organization.</Empty>}
              {data.usage?.length ? (
                <ul className="grid gap-2" style={{ listStyle: "none", padding: 0 }}>
                  {data.usage.map((u) => (
                    <li key={u.key}>
                      <div className="a-pagehead"><span>{USAGE_LABEL[u.key] ?? label(u.key)}</span>
                        <span>{u.unlimited ? `${fmtNumber(u.current)} (unlimited)` : `${fmtNumber(u.current)} of ${fmtNumber(u.limit)}`} <Status value={u.status} /></span></div>
                      <Meter percent={u.unlimited ? null : u.percent} status={u.status} />
                    </li>
                  ))}
                </ul>
              ) : null}
              {can("entitlements.read") ? <Link className="a-link" href={href("/subscription")}>View subscription details</Link> : null}
            </section>
          ) : null}

          <section aria-labelledby="h-short" className="grid gap-2">
            <h2 id="h-short">Shortcuts</h2>
            <ul className="a-actions" style={{ listStyle: "none", padding: 0 }}>
              {can("companies.create") ? <li><Link className="a-link" href={href("/companies/new")}>Create company</Link></li> : null}
              {can("companies.list") ? <li><Link className="a-link" href={href("/companies?assigned_to_me=true")}>My assigned companies</Link></li> : null}
              {can("invitations.manage") ? <li><Link className="a-link" href={href("/invitations")}>Invite a team member</Link></li> : null}
              {can("invitations.manage") ? <li><Link className="a-link" href={href("/invitations")}>Add an accountant</Link></li> : null}
              {can("organization.read") ? <li><Link className="a-link" href={href("/settings")}>Manage organization</Link></li> : null}
              {can("members.read") ? <li><Link className="a-link" href={href("/assignments")}>Client assignments</Link></li> : null}
              {can("audit.read") ? <li><Link className="a-link" href={href("/activity")}>Activity history</Link></li> : null}
            </ul>
          </section>

          {data.recent_activity ? (
            <Panel>
              <h2>Recent activity</h2>
              {data.recent_activity.length === 0 ? <Empty>No activity recorded yet.</Empty> : (
                <ul style={{ listStyle: "none", padding: 0 }} className="grid gap-1">
                  {data.recent_activity.map((a) => (
                    <li key={a.id}><strong>{label(a.action)}</strong> <span className="a-hint">· {a.actor_name ?? "System"}{a.company_name ? ` · ${a.company_name}` : ""} · {fmtDateTime(a.created_at)}</span></li>
                  ))}
                </ul>
              )}
            </Panel>
          ) : null}
        </>
      )}
    </div>
  );
}
