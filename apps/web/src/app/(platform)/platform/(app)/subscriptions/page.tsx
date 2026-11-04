"use client";
import Link from "next/link";
import { DataTable, PageHeader, SearchBox, SelectFilter, Status } from "@/components/kit";
import { fmtDate, label, useApi, useListState, type Page, type S } from "@/lib/platform";

const KEYS = ["q", "status", "plan", "sort", "order", "offset"];

export default function Subscriptions() {
  const ls = useListState({ sort: "created", order: "desc" });
  const plans = useApi<S["PlanRow"][]>("/plans");
  const { data, error, loading, reload } = useApi<Page<S["SubRow"]>>(`/subscriptions${ls.query(KEYS, { limit: "25" })}`);
  const sort = ls.get("sort");
  const order = ls.get("order");
  return (
    <>
      <PageHeader title="Subscriptions" crumbs={[{ label: "Subscriptions" }]} />
      <div className="a-toolbar">
        <SearchBox value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Organization name or slug" />
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={["trialing", "active", "past_due", "suspended", "cancelled", "expired"].map((v) => ({ value: v, label: label(v) }))} />
        <SelectFilter label="Plan" value={ls.get("plan")} onChange={(v) => ls.set({ plan: v })} options={(plans.data ?? []).map((p) => ({ value: p.key, label: p.name }))} />
      </div>
      <DataTable caption="Subscriptions" loading={loading} error={error} onRetry={reload} rows={data?.items} total={data?.total} limit={data?.limit} offset={data?.offset}
        onPage={(o) => ls.set({ offset: String(o) })} sort={sort} order={order} onSort={(k) => ls.set({ sort: k, order: sort === k && order === "asc" ? "desc" : "asc" })}
        empty="No subscriptions match these filters." rowKey={(s) => s.id}
        columns={[
          { key: "o", header: "Organization", sortKey: "organization", render: (s) => <Link className="a-link" href={`/platform/subscriptions/${s.id}`}>{s.organization_name}</Link> },
          { key: "p", header: "Plan", sortKey: "plan", render: (s) => `${s.plan_name} v${s.plan_version}` },
          { key: "s", header: "Status", sortKey: "status", render: (s) => <Status value={s.status} /> },
          { key: "st", header: "Started", render: (s) => fmtDate(s.current_period_start) },
          { key: "r", header: "Renewal", sortKey: "renewal", render: (s) => fmtDate(s.current_period_end) },
          { key: "t", header: "Trial ends", render: (s) => s.status === "trialing" ? fmtDate(s.trial_ends_at) : "—" },
          { key: "c", header: "Cancellation", render: (s) => s.cancelled_at ? fmtDate(s.cancelled_at) : s.cancel_at_period_end ? "At period end" : "—" },
          { key: "pay", header: "Latest invoice", render: (s) => s.latest_invoice_status ? <Status value={s.latest_invoice_status} /> : <span className="a-hint">none</span> },
        ]} />
    </>
  );
}
