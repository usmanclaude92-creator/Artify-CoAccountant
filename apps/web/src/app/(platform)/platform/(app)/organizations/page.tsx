"use client";
import Link from "next/link";
import { Button } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { DataTable, PageHeader, SearchBox, SelectFilter, Status } from "@/components/platform/kit";
import { fmtDate, fmtNumber, label, useApi, useListState, type Page, type S } from "@/lib/platform";

const KEYS = ["q", "type", "status", "subscription_status", "sort", "order", "offset"];

export default function Organizations() {
  const can = useCan();
  const ls = useListState({ sort: "created", order: "desc" });
  const type = ls.get("type");
  const title = type === "accounting_firm" ? "Accounting Firms" : type === "independent_accountant" ? "Independent Accountants" : "All Organizations";
  const { data, error, loading, reload } = useApi<Page<S["OrgRow"]>>(`/organizations${ls.query(KEYS, { limit: "25" })}`);
  const sort = ls.get("sort");
  const order = ls.get("order");
  return (
    <>
      <PageHeader title={title} crumbs={[{ label: title }]}
        actions={can("platform.organizations.manage") ? <Link href="/platform/organizations/new"><Button>New organization</Button></Link> : null} />
      <div className="a-toolbar">
        <SearchBox value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Name, slug or owner e-mail" />
        {!type || !["accounting_firm", "independent_accountant"].includes(type) ? (
          <SelectFilter label="Type" value={type} onChange={(v) => ls.set({ type: v })} options={[{ value: "accounting_firm", label: "Accounting firm" }, { value: "independent_accountant", label: "Independent accountant" }]} />
        ) : null}
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={["active", "suspended", "closed"].map((v) => ({ value: v, label: label(v) }))} />
        <SelectFilter label="Subscription" value={ls.get("subscription_status")} onChange={(v) => ls.set({ subscription_status: v })}
          options={["trialing", "active", "past_due", "suspended", "cancelled", "expired"].map((v) => ({ value: v, label: label(v) }))} />
      </div>
      <DataTable caption={`${title} list`} loading={loading} error={error} onRetry={reload} rows={data?.items} total={data?.total} limit={data?.limit} offset={data?.offset}
        onPage={(o) => ls.set({ offset: String(o) })} sort={sort} order={order}
        onSort={(k) => ls.set({ sort: k, order: sort === k && order === "asc" ? "desc" : "asc" })}
        empty="No organizations match these filters." rowKey={(o) => o.id}
        columns={[
          { key: "n", header: "Organization", sortKey: "name", render: (o) => <Link className="a-link" href={`/platform/organizations/${o.id}`}>{o.display_name}</Link> },
          { key: "t", header: "Type", render: (o) => label(o.type) },
          { key: "o", header: "Owner", render: (o) => o.owner_email ?? <span className="a-hint">invited, not yet accepted</span> },
          { key: "p", header: "Plan", render: (o) => o.plan_name ?? "—" },
          { key: "ss", header: "Subscription", render: (o) => o.subscription_status ? <Status value={o.subscription_status} /> : "—" },
          { key: "u", header: "Users", sortKey: "users", numeric: true, render: (o) => fmtNumber(o.user_count) },
          { key: "c", header: "Companies", sortKey: "companies", numeric: true, render: (o) => fmtNumber(o.company_count) },
          { key: "s", header: "Status", sortKey: "status", render: (o) => <Status value={o.status} /> },
          { key: "d", header: "Registered", sortKey: "created", render: (o) => fmtDate(o.created_at) },
        ]} />
    </>
  );
}
