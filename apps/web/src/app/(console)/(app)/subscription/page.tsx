"use client";
import type { Schemas } from "@artify/api-client";
import { Alert, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, ErrorBox, Loading, Meter, Status } from "@/components/kit";
import { fmtBytes, fmtDate, fmtMoney, fmtNumber, label, useTenantApi } from "@/lib/tenant";

type Sub = Schemas["SubscriptionView"];
type Row = Schemas["UsageRow"];

export default function SubscriptionPage() {
  const { orgId } = useOrg();
  const { data, error, reload } = useTenantApi<Sub>(orgId, `/orgs/${orgId}/subscription`);
  if (error && !data) return <div className="grid gap-4"><CcHeader title="Subscription & Usage" /><ErrorBox message={error} onRetry={reload} /></div>;
  if (!data) return <div className="grid gap-4"><CcHeader title="Subscription & Usage" /><Loading what="subscription" /></div>;
  const counted = data.entitlements.filter((e) => e.enforcement !== "flag");
  const flags = data.entitlements.filter((e) => e.enforcement === "flag");
  return (
    <div className="grid gap-4">
      <CcHeader title="Subscription & Usage" />
      <p className="a-hint">Read-only. Plans, limits and billing are managed by Artify. Reaching a limit blocks creating more; it never deletes or hides your data.</p>
      {data.attention.map((a) => <Alert key={a} tone="warning">{a}</Alert>)}
      <Panel className="grid gap-2">
        <h2>Plan</h2>
        <dl className="a-dl">
          <dt>Plan</dt><dd>{data.plan.name} (version {data.plan.version})</dd>
          <dt>Status</dt><dd><Status value={data.status} /></dd>
          <dt>Billing interval</dt><dd>{label(data.plan.billing_interval)}</dd>
          <dt>Price</dt><dd>{data.plan.price_amount ? fmtMoney(data.plan.price_amount, data.plan.price_currency) : "Not priced"}</dd>
          <dt>Support level</dt><dd>{label(data.plan.support_level)}</dd>
          <dt>Current period</dt><dd>{fmtDate(data.current_period_start)} – {data.current_period_end ? fmtDate(data.current_period_end) : "open"}</dd>
          {data.trial_ends_at ? <><dt>Trial ends</dt><dd>{fmtDate(data.trial_ends_at)}</dd></> : null}
          {data.cancel_at_period_end ? <><dt>Cancellation</dt><dd>Ends at the close of the current period</dd></> : null}
        </dl>
        <p>{data.next_step}</p>
      </Panel>
      <h2>Usage against limits</h2>
      <DataTable<Row> caption="Usage against limits" rows={counted} loading={false} error={null} rowKey={(r) => r.key} empty="No limits."
        columns={[
          { key: "k", header: "Limit", render: (r) => <>{r.description}<span className="a-hint" style={{ display: "block" }}>{r.enforced_now ? "Enforced now" : "Not enforced yet"}</span></> },
          { key: "c", header: "Current", numeric: true, render: (r) => (r.current === null ? "Not metered" : r.unit === "bytes" ? fmtBytes(r.current) : fmtNumber(r.current)) },
          { key: "l", header: "Limit", numeric: true, render: (r) => (r.unlimited ? "Unlimited" : r.limit === null ? "Not set" : r.unit === "bytes" ? fmtBytes(r.limit) : fmtNumber(r.limit)) },
          { key: "u", header: "Used", render: (r) => <Meter percent={r.percent} status={r.status} /> },
          { key: "s", header: "Status", render: (r) => <Status value={r.status} text={r.status === "not_metered" ? "Not metered" : undefined} /> },
        ]} />
      {flags.length ? (
        <Panel>
          <h2>Included features</h2>
          <ul>{flags.map((f) => <li key={f.key}>{f.description}: <strong>{f.enabled ? "included" : "not included"}</strong></li>)}</ul>
        </Panel>
      ) : null}
    </div>
  );
}
