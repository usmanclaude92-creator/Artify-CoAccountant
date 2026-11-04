"use client";
import Link from "next/link";
import type { Schemas } from "@artify/api-client";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, SearchBox, SelectFilter, Status } from "@/components/kit";
import { ORG_ROLE_LABEL, useListState, useTenantApi, type Page } from "@/lib/tenant";

type Row = Schemas["AccountantRow"];
const KEYS = ["q", "status", "offset"];

export default function AccountantsPage() {
  const { orgId, href } = useOrg();
  const ls = useListState();
  const limit = 25;
  const { data, error, loading, reload } = useTenantApi<Page<Row>>(orgId, `/orgs/${orgId}/accountants${ls.query(KEYS, { limit: String(limit) })}`);
  return (
    <div className="grid gap-4">
      <CcHeader title="Accountants" />
      <p className="a-hint">Professional staff and their workload. The professional title is separate from the authorization role, which decides what a person may do.</p>
      <div className="a-toolbar">
        <SearchBox key={ls.get("q")} value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Name or e-mail" />
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={[{ value: "active", label: "Active" }, { value: "suspended", label: "Suspended" }]} />
      </div>
      <DataTable<Row> caption="Accountants" rows={data?.items} loading={loading} error={error} onRetry={reload} rowKey={(r) => r.user_id} empty="No accountants yet. Invite one from Invitations."
        total={data?.total} limit={limit} offset={Number(ls.get("offset") || 0)} onPage={(o) => ls.set({ offset: String(o) })}
        columns={[
          { key: "n", header: "Name", render: (r) => <>{r.display_name}<span className="a-hint" style={{ display: "block" }}>{r.email}</span></> },
          { key: "job", header: "Professional title", render: (r) => <>{r.job_title ?? "—"}{r.license_number ? <span className="a-hint" style={{ display: "block" }}>Licence {r.license_number}</span> : null}</> },
          { key: "role", header: "Authorization role", render: (r) => ORG_ROLE_LABEL[r.role] ?? r.role },
          { key: "s", header: "Status", render: (r) => <Status value={r.status} /> },
          { key: "c", header: "Active companies", numeric: true, render: (r) => r.active_company_count },
          { key: "as", header: "Companies", render: (r) => r.assignments.length === 0 ? "—" : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>{r.assignments.map((a) => <li key={a.company_id}><Link className="a-link" href={href(`/companies/${a.company_id}`)}>{a.company_name}</Link> <span className="a-hint">· {a.assignment_type}{a.active ? "" : " · not in period"}</span></li>)}</ul>
          ) },
        ]} />
    </div>
  );
}
