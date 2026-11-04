"use client";
import Link from "next/link";
import { DataTable, Meter, PageHeader, SearchBox, SelectFilter, Status } from "@/components/platform/kit";
import { fmtNumber, useApi, useListState, type Page, type S } from "@/lib/platform";

const COUNT_KEYS = ["limit.companies", "limit.users", "limit.accountants", "limit.devices"];
const SHORT: Record<string, string> = { "limit.companies": "Companies", "limit.users": "Users", "limit.accountants": "Accountants", "limit.devices": "Devices" };

export default function Usage() {
  const ls = useListState();
  const { data, error, loading, reload } = useApi<Page<S["OrgUsage"]>>(`/usage${ls.query(["q", "attention", "offset"], { limit: "25" })}`);
  const cell = (u: S["OrgUsage"], key: string) => {
    const r = u.rows.find((x) => x.key === key);
    if (!r) return "—";
    return (
      <div style={{ minInlineSize: "7rem" }}>
        <span>{fmtNumber(r.current)} / {r.unlimited ? "∞" : fmtNumber(r.limit)}</span>
        <Meter percent={r.percent} status={r.status} />
        {r.status === "exceeded" || r.status === "approaching" ? <Status value={r.status} text={r.status === "exceeded" ? "Over limit" : "Near limit"} /> : null}
      </div>
    );
  };
  return (
    <>
      <PageHeader title="Usage & Limits" crumbs={[{ label: "Usage & Limits" }]} />
      <p className="a-hint">Current usage against each organization&apos;s effective limit (plan, or an unexpired override). Document, AI and storage allowances are shown on each organization&apos;s page as <em>not metered yet</em> until those features exist.</p>
      <div className="a-toolbar">
        <SearchBox value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Organization name or slug" />
        <SelectFilter label="Attention" value={ls.get("attention")} onChange={(v) => ls.set({ attention: v })} options={[{ value: "approaching", label: "Approaching a limit" }, { value: "exceeded", label: "Over a limit" }]} />
      </div>
      <DataTable caption="Usage per organization" loading={loading} error={error} onRetry={reload} rows={data?.items} total={data?.total} limit={data?.limit} offset={data?.offset}
        onPage={(o) => ls.set({ offset: String(o) })} empty="No organizations match." rowKey={(u) => u.organization_id}
        columns={[
          { key: "o", header: "Organization", render: (u) => <Link className="a-link" href={`/platform/organizations/${u.organization_id}`}>{u.organization_name}</Link> },
          { key: "p", header: "Plan", render: (u) => u.plan_name ?? "—" },
          ...COUNT_KEYS.map((k) => ({ key: k, header: SHORT[k] ?? k, render: (u: S["OrgUsage"]) => cell(u, k) })),
          { key: "w", header: "Overall", render: (u) => <Status value={u.worst} text={u.worst === "ok" ? "Within limits" : u.worst === "approaching" ? "Near a limit" : "Over a limit"} /> },
        ]} />
    </>
  );
}
