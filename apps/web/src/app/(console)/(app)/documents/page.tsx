"use client";
import Link from "next/link";
import { Alert, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, ErrorBox, Kpi, Loading, Meter } from "@/components/kit";
import { fmtBytes, fmtNumber, useTenantApi } from "@/lib/tenant";

interface CompanyRow { company_id: string; company_name: string; stored_bytes: number; documents: number; failed_uploads: number; uploads_7d: number }
interface OrgStorage {
  storage: { used_bytes: number; limit_bytes: number | null; unlimited: boolean; available_bytes: number | null; source: string };
  documents_this_month: { used: number; limit: number | null; unlimited: boolean };
  companies: CompanyRow[];
  accounting_model: string;
}

export default function DocumentsPage() {
  const { orgId, href } = useOrg();
  const { data, error, reload } = useTenantApi<OrgStorage>(orgId, `/orgs/${orgId}/storage`);
  if (error && !data) return <div className="grid gap-4"><CcHeader title="Documents & Storage" /><ErrorBox message={error} onRetry={reload} /></div>;
  if (!data) return <div className="grid gap-4"><CcHeader title="Documents & Storage" /><Loading what="storage" /></div>;
  const s = data.storage;
  const pct = s.limit_bytes ? Math.round((1000 * s.used_bytes) / s.limit_bytes) / 10 : null;
  return (
    <div className="grid gap-4">
      <CcHeader title="Documents & Storage" />
      <p className="a-hint">Documents are imported and managed in the Windows desktop application. This page shows how much your organization stores and where; limits come from your subscription.</p>
      <div className="a-kpis">
        <Kpi label="Stored" value={fmtBytes(s.used_bytes)} note={s.unlimited ? "no storage limit" : s.limit_bytes === null ? "no limit configured" : `of ${fmtBytes(s.limit_bytes)}`} />
        <Kpi label="Available" value={s.unlimited ? "Unlimited" : s.available_bytes === null ? "—" : fmtBytes(s.available_bytes)} />
        <Kpi label="New documents this month" value={fmtNumber(data.documents_this_month.used)} note={data.documents_this_month.unlimited ? "unlimited" : data.documents_this_month.limit === null ? "no limit configured" : `of ${fmtNumber(data.documents_this_month.limit)}`} />
      </div>
      {pct !== null ? <div><p className="a-hint">Storage used: {pct}%</p><Meter percent={pct} status={pct > 100 ? "exceeded" : pct >= 80 ? "approaching" : "ok"} /></div> : null}
      {pct !== null && pct >= 100 ? <Alert tone="warning">The storage limit is reached. Existing documents stay available; new uploads are refused until space is freed or the plan changes. Contact Artify support to change your plan.</Alert> : null}
      <DataTable<CompanyRow> caption="Storage by company" rows={data.companies} loading={false} error={null} rowKey={(r) => r.company_id} empty="No companies yet."
        columns={[
          { key: "c", header: "Company", render: (r) => <Link className="a-link" href={href(`/companies/${r.company_id}`)}>{r.company_name}</Link> },
          { key: "d", header: "Documents", numeric: true, render: (r) => fmtNumber(r.documents) },
          { key: "b", header: "Stored", numeric: true, render: (r) => fmtBytes(r.stored_bytes) },
          { key: "u", header: "Uploads, last 7 days", numeric: true, render: (r) => fmtNumber(r.uploads_7d) },
          { key: "f", header: "Failed uploads", numeric: true, render: (r) => fmtNumber(r.failed_uploads) },
        ]} />
      <Panel>
        <h2>How storage is counted</h2>
        <p>{data.accounting_model}</p>
      </Panel>
    </div>
  );
}
