"use client";
import Link from "next/link";
import { useState } from "react";
import { Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { DataTable, ErrorBox, Loading, PageHeader, Status } from "@/components/kit";
import { fmtMoney, fmtNumber, label, useApi, type S } from "@/lib/platform";

export default function Plans() {
  const can = useCan();
  const { data, error, loading, reload } = useApi<S["PlanRow"][]>("/plans");
  const [picked, setPicked] = useState<string[]>([]);
  const cmp = useApi<S["Comparison"]>(picked.length >= 2 ? `/plans/compare?${picked.map((p) => `plan_id=${p}`).join("&")}` : null);
  return (
    <>
      <PageHeader title="Subscription Plans" crumbs={[{ label: "Subscription Plans" }]}
        actions={can("platform.plans.manage") ? <Link href="/platform/plans/new"><Button>New plan</Button></Link> : null} />
      <DataTable caption="Subscription plans" loading={loading} error={error} onRetry={reload} rows={data ?? undefined} empty="No plans yet." rowKey={(p) => p.id}
        columns={[
          { key: "c", header: "Compare", render: (p) => <input type="checkbox" aria-label={`Compare ${p.name}`} checked={picked.includes(p.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, p.id].slice(-5) : picked.filter((x) => x !== p.id))} /> },
          { key: "n", header: "Plan", render: (p) => <><Link className="a-link" href={`/platform/plans/${p.id}`}>{p.name}</Link><span className="a-hint" style={{ display: "block" }}>{p.key}{p.is_default ? " · default for sign-ups" : ""}</span></> },
          { key: "s", header: "Status", render: (p) => <Status value={p.status} text={p.status === "retired" ? "Archived" : undefined} /> },
          { key: "pr", header: "Price", render: (p) => p.price_amount ? `${fmtMoney(p.price_amount, p.price_currency)} / ${p.billing_interval}` : "Not priced" },
          { key: "sl", header: "Support", render: (p) => label(p.support_level) },
          { key: "t", header: "Trial", render: (p) => p.trial_days ? `${p.trial_days} days` : "—" },
          { key: "v", header: "Latest version", numeric: true, render: (p) => `v${p.latest_version}` },
          { key: "su", header: "Subscriptions", numeric: true, render: (p) => fmtNumber(p.subscriptions) },
        ]} />
      {picked.length >= 2 ? (
        <Panel>
          <h2 className="mb-2 font-semibold">Plan comparison (latest versions)</h2>
          {cmp.loading ? <Loading what="comparison" /> : cmp.error ? <ErrorBox message={cmp.error} /> : cmp.data ? (
            <div className="a-tablewrap"><table className="a-table"><caption className="a-visually-hidden">Plan comparison</caption>
              <thead><tr><th scope="col">Feature / limit</th>{cmp.data.plans.map((c) => <th key={c.plan_id} scope="col">{c.name} v{c.version}<span className="a-hint" style={{ display: "block" }}>{c.price_amount ? fmtMoney(c.price_amount, c.price_currency) : "Not priced"} · {label(c.support_level)}</span></th>)}</tr></thead>
              <tbody>{cmp.data.rows.map((r) => (
                <tr key={r.key}><th scope="row">{r.key}{r.enforced_now ? "" : <span className="a-hint"> (enforced when the feature ships)</span>}</th>
                  {r.values.map((v, i) => <td key={i}>{!v ? "—" : v.unlimited ? "Unlimited" : v.enabled !== null ? (v.enabled ? "Included" : "Not included") : fmtNumber(v.value)}</td>)}</tr>))}</tbody></table></div>
          ) : null}
        </Panel>
      ) : <p className="a-hint">Tick two or more plans to compare them.</p>}
    </>
  );
}
