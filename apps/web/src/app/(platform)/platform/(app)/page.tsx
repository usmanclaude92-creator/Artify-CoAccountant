"use client";
import Link from "next/link";
import { Alert, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { DataTable, ErrorBox, Kpi, Loading, PageHeader, Status } from "@/components/kit";
import { fmtDateTime, fmtNumber, label, useApi, type S } from "@/lib/platform";

export default function Overview() {
  const can = useCan();
  const { data: d, error, loading, reload } = useApi<S["Dashboard"]>("/dashboard");
  if (error) return <><PageHeader title="Overview" /><ErrorBox message={error} onRetry={reload} /></>;
  if (!d || loading) return <><PageHeader title="Overview" /><Loading what="platform overview" /></>;
  const max = Math.max(1, ...d.organization_growth.map((p) => p.count));
  const sub = d.subscriptions.breakdown;
  return (
    <>
      <PageHeader title="Overview" actions={<Status value="info" text={`Environment: ${d.environment}`} />} />
      {d.alerts.length ? (
        <section aria-label="Needs attention" className="grid gap-2">
          {d.alerts.map((a) => (
            <Alert key={a.kind} tone={a.severity === "critical" ? "danger" : a.severity === "warning" ? "warning" : "info"}>
              <strong>{a.count}</strong> — {a.message} <Link className="a-link" href={a.href}>Review</Link>
            </Alert>
          ))}
        </section>
      ) : null}

      <section aria-label="Key figures" className="a-kpis">
        <Kpi label="Organizations" value={fmtNumber(d.organizations.total)} note={`${d.organization_types.accounting_firm ?? 0} firms · ${d.organization_types.independent_accountant ?? 0} independent`} />
        <Kpi label="Active organizations" value={fmtNumber(d.organizations.breakdown.active ?? 0)} note={`${d.organizations.breakdown.suspended ?? 0} suspended`} />
        <Kpi label="Client companies" value={fmtNumber(d.companies.total)} note={`${d.companies.breakdown.active ?? 0} active`} />
        <Kpi label="Registered users" value={fmtNumber(d.users.total)} note={`${d.users.breakdown.active ?? 0} active`} />
        <Kpi label="Active subscriptions" value={fmtNumber(sub.active ?? 0)} />
        <Kpi label="Trial subscriptions" value={fmtNumber(sub.trialing ?? 0)} />
        <Kpi label="Past due" value={fmtNumber(sub.past_due ?? 0)} />
        <Kpi label="Suspended / expired" value={`${sub.suspended ?? 0} / ${sub.expired ?? 0}`} note={`${sub.cancelled ?? 0} cancelled`} />
        <Kpi label="Failed background jobs" value={fmtNumber(d.jobs.failed)} note={`${d.jobs.pending} pending`} />
        <Kpi label="Documents processed" available={false} note={d.documents.note} />
        <Kpi label="Storage consumption" available={false} note={d.storage.note} />
        <Kpi label="AI processing" available={false} note={d.ai_usage.note} />
        {d.revenue ? (
          d.revenue.available ? (
            <Kpi label="Collected, last 30 days" value={d.revenue.collected_last_30_days.map((t) => `${t.amount} ${t.currency}`).join(" · ")} note={d.revenue.note} />
          ) : <Kpi label="Subscription revenue" available={false} note={d.revenue.note} />
        ) : null}
      </section>

      <div className="a-grid2">
        <Panel>
          <h2 className="mb-2 font-semibold">Organization growth (last 12 months)</h2>
          <ul className="a-bars" aria-label="New organizations per month">
            {d.organization_growth.map((p) => (
              <li key={p.month} title={`${p.month}: ${p.count}`}>
                <span className="a-visually-hidden">{p.month}: {p.count}</span>
                <span className="bar" style={{ blockSize: `${(p.count / max) * 100}%` }} aria-hidden="true" />
                <span aria-hidden="true">{p.month.slice(5)}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel>
          <h2 className="mb-2 font-semibold">Subscription changes by month</h2>
          <DataTable caption="Subscription trend" loading={false} error={null} empty="No subscription events yet." rowKey={(p) => p.month}
            rows={d.subscription_trend.filter((p) => p.created || p.status_changes || p.plan_changes).slice(-6)}
            columns={[
              { key: "m", header: "Month", render: (p) => p.month },
              { key: "c", header: "New", numeric: true, render: (p) => p.created },
              { key: "s", header: "Status changes", numeric: true, render: (p) => p.status_changes },
              { key: "p", header: "Plan changes", numeric: true, render: (p) => p.plan_changes },
            ]} />
          <p className="a-hint mt-2">Usage trend: {d.usage_trend.note}</p>
        </Panel>
      </div>

      <div className="a-grid2">
        <Panel>
          <h2 className="mb-2 font-semibold">Recent registrations</h2>
          <DataTable caption="Recent registrations" loading={false} error={null} empty="No organizations have registered yet." rowKey={(o) => o.id} rows={d.recent_registrations}
            columns={[
              { key: "n", header: "Organization", render: (o) => can("platform.organizations.read") ? <Link className="a-link" href={`/platform/organizations/${o.id}`}>{o.display_name}</Link> : o.display_name },
              { key: "t", header: "Type", render: (o) => label(o.type) },
              { key: "d", header: "Registered", render: (o) => fmtDateTime(o.created_at) },
            ]} />
        </Panel>
        {d.recent_subscription_changes ? (
          <Panel>
            <h2 className="mb-2 font-semibold">Recent subscription changes</h2>
            <DataTable caption="Recent subscription changes" loading={false} error={null} empty="No subscription changes yet." rowKey={(e) => `${e.organization_id}${e.created_at}${e.event_type}`} rows={d.recent_subscription_changes}
              columns={[
                { key: "o", header: "Organization", render: (e) => e.organization_name },
                { key: "e", header: "Change", render: (e) => `${label(e.event_type)}${e.to_status ? `: ${e.from_status ?? "—"} → ${e.to_status}` : ""}` },
                { key: "d", header: "When", render: (e) => fmtDateTime(e.created_at) },
              ]} />
          </Panel>
        ) : null}
      </div>

      {d.recent_admin_activity ? (
        <Panel>
          <h2 className="mb-2 font-semibold">Recent administrative activity</h2>
          <DataTable caption="Recent administrative activity" loading={false} error={null} empty="No administrative actions recorded yet." rowKey={(e) => e.id} rows={d.recent_admin_activity}
            columns={[
              { key: "a", header: "Action", render: (e) => e.action },
              { key: "w", header: "By", render: (e) => e.actor_name ?? "system" },
              { key: "o", header: "Outcome", render: (e) => <Status value={e.outcome} /> },
              { key: "d", header: "When", render: (e) => fmtDateTime(e.created_at) },
            ]} />
        </Panel>
      ) : null}
      <p className="a-hint">Generated {fmtDateTime(d.generated_at)}. Every figure is read from the database; items that are not measured yet say so.</p>
    </>
  );
}
