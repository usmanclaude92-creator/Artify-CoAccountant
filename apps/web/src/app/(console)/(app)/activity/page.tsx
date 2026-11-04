"use client";
import Link from "next/link";
import type { Schemas } from "@artify/api-client";
import { Button } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, Field, SearchBox } from "@/components/kit";
import { fmtDateTime, label, useListState, useTenantApi, type Page } from "@/lib/tenant";

type Row = Schemas["OrgActivityRow"];
const KEYS = ["q", "action", "company_id", "since", "until", "offset"];

export default function ActivityPage() {
  const { orgId, href, can } = useOrg();
  const ls = useListState();
  const limit = 50;
  const { data, error, loading, reload } = useTenantApi<Page<Row>>(orgId, `/orgs/${orgId}/activity${ls.query(KEYS, { limit: String(limit) })}`);
  const companies = useTenantApi<Schemas["CompanyOut"][]>(orgId, can("companies.list") ? `/orgs/${orgId}/companies` : null);
  const date = (k: "since" | "until") => (
    <Field label={k === "since" ? "From" : "To"}>{(id) => <input id={id} type="date" value={ls.get(k).slice(0, 10)} onChange={(e) => ls.set({ [k]: e.target.value ? (k === "since" ? `${e.target.value}T00:00:00Z` : `${e.target.value}T23:59:59Z`) : null })} />}</Field>
  );
  return (
    <div className="grid gap-4">
      <CcHeader title="Activity & Audit History" />
      <p className="a-hint">Your organization’s own audit trail: append-only and visible only to you. Artify’s internal platform logs are not shown here.</p>
      <div className="a-toolbar">
        <SearchBox key={ls.get("q")} value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Action or person" />
        <Field label="Company">{(id) => (
          <select id={id} value={ls.get("company_id")} onChange={(e) => ls.set({ company_id: e.target.value })}>
            <option value="">All companies</option>{(companies.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}</Field>
        {date("since")}{date("until")}
        {KEYS.slice(0, 5).some((k) => ls.get(k)) ? <Button variant="secondary" onClick={() => ls.set({ q: null, action: null, company_id: null, since: null, until: null })}>Clear filters</Button> : null}
      </div>
      <DataTable<Row> caption="Activity" rows={data?.items} loading={loading} error={error} onRetry={reload} rowKey={(r) => r.id} empty="No activity matches."
        total={data?.total} limit={limit} offset={Number(ls.get("offset") || 0)} onPage={(o) => ls.set({ offset: String(o) })}
        columns={[
          { key: "w", header: "When", render: (r) => fmtDateTime(r.created_at) },
          { key: "a", header: "Event", render: (r) => <button type="button" className="a-link" onClick={() => ls.set({ action: r.action, q: null })}>{label(r.action)}</button> },
          { key: "u", header: "By", render: (r) => r.actor_name ?? "System" },
          { key: "c", header: "Company", render: (r) => (r.company_id ? <Link className="a-link" href={href(`/companies/${r.company_id}`)}>{r.company_name ?? "Company"}</Link> : "—") },
          { key: "d", header: "Details", render: (r) => Object.entries(r.summary).filter(([, v]) => v !== null && typeof v !== "object").slice(0, 4).map(([k, v]) => `${label(k)}: ${String(v)}`).join(" · ") || "—" },
        ]} />
    </div>
  );
}
