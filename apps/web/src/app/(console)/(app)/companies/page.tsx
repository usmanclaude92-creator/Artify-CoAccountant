"use client";
import Link from "next/link";
import type { Schemas } from "@artify/api-client";
import { Button } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, Field, SearchBox, SelectFilter, Status } from "@/components/kit";
import { useListState, useTenantApi, type Page } from "@/lib/tenant";

type Row = Schemas["CompanyListRow"];
const KEYS = ["q", "status", "country", "assigned_to_me", "sort", "order", "offset"];
const STATUS_OPTIONS = [
  { value: "open", label: "Active and inactive" }, { value: "active", label: "Active" },
  { value: "suspended", label: "Inactive" }, { value: "archived", label: "Archived" },
];

export default function CompaniesPage() {
  const { orgId, can, href } = useOrg();
  const ls = useListState({ sort: "name", order: "asc" });
  const limit = 25;
  const { data, error, loading, reload } = useTenantApi<Page<Row>>(orgId, `/orgs/${orgId}/companies/overview${ls.query(KEYS, { limit: String(limit) })}`);
  const onSort = (k: string) => ls.set({ sort: k, order: ls.get("sort") === k && ls.get("order") === "asc" ? "desc" : "asc" });
  return (
    <div className="grid gap-4">
      <CcHeader title="My Companies" actions={can("companies.create") ? <Link className="a-btn" href={href("/companies/new")}>Create company</Link> : null} />
      <div className="a-toolbar">
        <SearchBox key={ls.get("q")} value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Name, legal name or code" />
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={STATUS_OPTIONS} />
        <Field label="Country (ISO code)">
          {(id) => <input id={id} defaultValue={ls.get("country")} maxLength={2} pattern="[A-Za-z]{2}" placeholder="e.g. OM" style={{ inlineSize: "6rem" }}
            onBlur={(e) => ls.set({ country: e.target.value.toUpperCase() })} />}
        </Field>
        <label className="flex items-center gap-2"><input type="checkbox" checked={ls.get("assigned_to_me") === "true"} onChange={(e) => ls.set({ assigned_to_me: e.target.checked ? "true" : null })} /> Assigned to me</label>
        {KEYS.slice(0, 4).some((k) => ls.get(k)) ? <Button variant="secondary" onClick={() => ls.set({ q: null, status: null, country: null, assigned_to_me: null })}>Clear filters</Button> : null}
      </div>
      <DataTable<Row>
        caption="Companies" rows={data?.items} loading={loading} error={error} onRetry={reload} rowKey={(r) => r.id}
        empty={ls.get("q") || ls.get("status") || ls.get("country") ? "No companies match these filters." : "No companies yet."}
        total={data?.total} limit={limit} offset={Number(ls.get("offset") || 0)} onPage={(o) => ls.set({ offset: String(o) })}
        sort={ls.get("sort")} order={ls.get("order")} onSort={onSort}
        columns={[
          { key: "name", header: "Company", sortKey: "name", render: (r) => <><Link className="a-link" href={href(`/companies/${r.id}`)}>{r.name}</Link>{r.legal_name && r.legal_name !== r.name ? <span className="a-hint" style={{ display: "block" }}>{r.legal_name}</span> : null}</> },
          { key: "code", header: "Code", sortKey: "code", render: (r) => r.code },
          { key: "country", header: "Country", sortKey: "country", render: (r) => r.country },
          { key: "cur", header: "Base currency", render: (r) => r.base_currency },
          { key: "status", header: "Status", sortKey: "status", render: (r) => <Status value={r.status} text={r.status === "suspended" ? "Inactive" : undefined} /> },
          { key: "acc", header: "Accountants", numeric: true, render: (r) => r.accountant_count },
          { key: "mem", header: "Client users", numeric: true, render: (r) => r.member_count },
          { key: "me", header: "Mine", render: (r) => (r.assigned_to_me ? "Yes" : "—") },
        ]}
      />
    </div>
  );
}
