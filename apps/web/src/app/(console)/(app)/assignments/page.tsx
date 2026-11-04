"use client";
import Link from "next/link";
import type { Schemas } from "@artify/api-client";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, SelectFilter, Status } from "@/components/kit";
import { fmtDate, label, useListState, useTenantApi, type Page } from "@/lib/tenant";

type Row = Schemas["AssignmentRow"];
const KEYS = ["company_id", "user_id", "active", "offset"];

export default function AssignmentsPage() {
  const { orgId, href, can } = useOrg();
  const ls = useListState();
  const limit = 50;
  const { data, error, loading, reload } = useTenantApi<Page<Row>>(orgId, `/orgs/${orgId}/assignments${ls.query(KEYS, { limit: String(limit) })}`);
  const companies = useTenantApi<Schemas["CompanyOut"][]>(orgId, can("companies.list") ? `/orgs/${orgId}/companies` : null);
  const team = useTenantApi<Page<Schemas["AccountantRow"]>>(orgId, `/orgs/${orgId}/accountants?limit=100`);
  return (
    <div className="grid gap-4">
      <CcHeader title="Client Assignments" />
      <p className="a-hint">Which accountant works on which company. Assignments are created and changed on each company’s page.</p>
      <div className="a-toolbar">
        <SelectFilter label="Company" value={ls.get("company_id")} onChange={(v) => ls.set({ company_id: v })} options={(companies.data ?? []).map((c) => ({ value: c.id, label: c.name }))} />
        <SelectFilter label="Accountant" value={ls.get("user_id")} onChange={(v) => ls.set({ user_id: v })} options={(team.data?.items ?? []).map((a) => ({ value: a.user_id, label: a.display_name }))} />
        <SelectFilter label="State" value={ls.get("active")} onChange={(v) => ls.set({ active: v })} options={[{ value: "true", label: "Currently active" }, { value: "false", label: "Not in period" }]} />
      </div>
      <DataTable<Row> caption="Client assignments" rows={data?.items} loading={loading} error={error} onRetry={reload} rowKey={(r) => `${r.company_id}-${r.user_id}`} empty="No assignments match."
        total={data?.total} limit={limit} offset={Number(ls.get("offset") || 0)} onPage={(o) => ls.set({ offset: String(o) })}
        columns={[
          { key: "c", header: "Company", render: (r) => <Link className="a-link" href={href(`/companies/${r.company_id}`)}>{r.company_name}</Link> },
          { key: "a", header: "Accountant", render: (r) => <>{r.display_name}<span className="a-hint" style={{ display: "block" }}>{r.email}</span></> },
          { key: "t", header: "Type", render: (r) => label(r.assignment_type) },
          { key: "r", header: "Responsibility", render: (r) => r.responsibility ?? "—" },
          { key: "w", header: "Period", render: (r) => `${fmtDate(r.starts_on)} – ${r.ends_on ? fmtDate(r.ends_on) : "open"}` },
          { key: "s", header: "State", render: (r) => <Status value={r.active ? "active" : "inactive"} text={r.active ? "Active" : "Not in period"} /> },
        ]} />
    </div>
  );
}
