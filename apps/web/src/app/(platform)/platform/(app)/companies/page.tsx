"use client";
import Link from "next/link";
import { DataTable, PageHeader, SearchBox, SelectFilter, Status } from "@/components/platform/kit";
import { Alert } from "@artify/ui";
import { fmtBytes, fmtDate, fmtNumber, label, useApi, useListState, type Page, type S } from "@/lib/platform";

const KEYS = ["q", "status", "country", "organization_id", "sort", "order", "offset"];

export default function Companies() {
  const ls = useListState({ sort: "created", order: "desc" });
  const { data, error, loading, reload } = useApi<Page<S["CompanyRow"]>>(`/companies${ls.query(KEYS, { limit: "25" })}`);
  const sort = ls.get("sort");
  const order = ls.get("order");
  return (
    <>
      <PageHeader title="Client Companies" crumbs={[{ label: "Client Companies" }]} />
      <Alert tone="info">Administrative overview only. Company names, status and counts are shown; documents, transactions and tax details are never available here.</Alert>
      <div className="a-toolbar">
        <SearchBox value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Company, code or organization" />
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={["active", "suspended", "archived"].map((v) => ({ value: v, label: label(v) }))} />
        <div className="a-field"><label htmlFor="country">Country (ISO code)</label><input id="country" maxLength={2} defaultValue={ls.get("country")} onBlur={(e) => ls.set({ country: e.target.value.toUpperCase() })} /></div>
      </div>
      <DataTable caption="Client companies" loading={loading} error={error} onRetry={reload} rows={data?.items} total={data?.total} limit={data?.limit} offset={data?.offset}
        onPage={(o) => ls.set({ offset: String(o) })} sort={sort} order={order} onSort={(k) => ls.set({ sort: k, order: sort === k && order === "asc" ? "desc" : "asc" })}
        empty="No companies match these filters." rowKey={(c) => c.id}
        columns={[
          { key: "n", header: "Company", sortKey: "name", render: (c) => <>{c.name}<span className="a-hint" style={{ display: "block" }}>{c.code}</span></> },
          { key: "o", header: "Parent organization", sortKey: "organization", render: (c) => <Link className="a-link" href={`/platform/organizations/${c.organization_id}`}>{c.organization_name}</Link> },
          { key: "co", header: "Country", render: (c) => `${c.country} · ${c.base_currency}` },
          { key: "s", header: "Status", sortKey: "status", render: (c) => <Status value={c.status} /> },
          { key: "a", header: "Accountants", numeric: true, render: (c) => fmtNumber(c.accountant_count) },
          { key: "u", header: "Users", numeric: true, render: (c) => fmtNumber(c.user_count) },
          { key: "d", header: "Documents", numeric: true, render: (c) => c.documents_processed === null ? <span className="a-hint">not metered</span> : fmtNumber(c.documents_processed) },
          { key: "st", header: "Storage", numeric: true, render: (c) => c.storage_bytes === null ? <span className="a-hint">not metered</span> : fmtBytes(c.storage_bytes) },
          { key: "cr", header: "Created", sortKey: "created", render: (c) => fmtDate(c.created_at) },
        ]} />
    </>
  );
}
