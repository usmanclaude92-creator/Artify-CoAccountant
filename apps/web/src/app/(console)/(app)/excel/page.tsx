"use client";
import type { Schemas } from "@artify/api-client";
import { Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, ErrorBox, Loading, Meter, Status } from "@/components/kit";
import { fmtNumber, useTenantApi } from "@/lib/tenant";

type Overview = Schemas["ExcelOverviewOut"];
type TemplateRow = Overview["templates"][number];
type ExportRow = Overview["recent_exports"][number];
type Usage = Overview["usage"][number];

const TEMPLATE_STATUS: Record<string, string> = { draft: "Draft", active: "Active", inactive: "Inactive", archived: "Archived" };
const EXPORT_STATUS: Record<string, string> = { queued: "Queued", running: "Running", completed: "Completed", failed: "Failed" };
const RISK: Record<string, string> = { none: "No warnings", warned: "Some features may not survive", unsupported: "Unsupported features present" };
const USAGE_LABEL: Record<string, string> = { "quota.excel_exports_per_month": "Excel exports this month" };

const bytes = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`);
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

/** Monitoring only. Templates are managed and exports are generated and downloaded in the Windows app; nothing here shows workbook content. */
export default function ExcelPage() {
  const { orgId } = useOrg();
  const ov = useTenantApi<Overview>(orgId, "/excel/overview");
  if (ov.error && !ov.data) return <div className="grid gap-4"><CcHeader title="Excel Templates & Reports" /><ErrorBox message={ov.error} onRetry={ov.reload} /></div>;
  if (!ov.data) return <div className="grid gap-4"><CcHeader title="Excel Templates & Reports" /><Loading what="Excel overview" /></div>;
  const d = ov.data;
  return (
    <div className="grid gap-4">
      <CcHeader title="Excel Templates & Reports" />
      <p className="a-hint">
        Each company keeps its own Excel templates, mappings and export history. Accountants set up templates and generate exports in the Windows app; exports contain approved accounting data
        unless an authorised person explicitly includes unapproved items. This page shows names, counts and statuses, never workbook content, and offers no downloads.
      </p>
      <Panel className="grid gap-2">
        <h2>Exports</h2>
        <dl className="a-dl">
          <dt>Completed</dt><dd>{fmtNumber(d.stats.completed)}</dd>
          <dt>Failed</dt><dd>{fmtNumber(d.stats.failed)}</dd>
          <dt>In progress</dt><dd>{fmtNumber(d.stats.running)}</dd>
          <dt>This month</dt><dd>{fmtNumber(d.stats.this_month)}</dd>
          <dt>Stored templates</dt><dd>{bytes(d.storage.template_bytes)}</dd>
          <dt>Stored exports</dt><dd>{bytes(d.storage.export_bytes)}</dd>
          <dt>Custom reports</dt><dd>{fmtNumber(d.custom_reports)}</dd>
        </dl>
      </Panel>
      <h2>Usage</h2>
      <DataTable<Usage> caption="Excel usage" rows={d.usage} loading={false} error={null} rowKey={(r) => r.key} empty="No usage recorded."
        columns={[
          { key: "l", header: "Allowance", render: (r) => USAGE_LABEL[r.key] ?? r.key },
          { key: "u", header: "Used", numeric: true, render: (r) => fmtNumber(r.used) },
          { key: "m", header: "Plan limit", numeric: true, render: (r) => (r.unlimited ? "Unlimited" : r.limit === null ? "Not set" : fmtNumber(r.limit)) },
          { key: "p", header: "Usage", render: (r) => <Meter percent={r.unlimited || !r.limit ? null : Math.round((1000 * r.used) / r.limit) / 10} status={r.limit && r.used > r.limit ? "exceeded" : r.limit && r.used >= 0.8 * r.limit ? "approaching" : "ok"} /> },
        ]} />
      <h2>Templates by company</h2>
      <DataTable<TemplateRow> caption="Excel templates" rows={d.templates} loading={false} error={null} rowKey={(r) => r.id} empty="No templates have been added yet."
        columns={[
          { key: "c", header: "Company", render: (r) => r.company_name },
          { key: "n", header: "Template", render: (r) => r.name },
          { key: "k", header: "Category", render: (r) => r.category.replaceAll("_", " ") },
          { key: "s", header: "Status", render: (r) => <Status value={r.status === "active" ? "ok" : "inactive"} text={TEMPLATE_STATUS[r.status] ?? r.status} /> },
          { key: "v", header: "Version", numeric: true, render: (r) => `v${r.current_version_no}` },
          { key: "r", header: "Compatibility", render: (r) => RISK[r.risk_level] ?? r.risk_level },
          { key: "e", header: "Exports", numeric: true, render: (r) => fmtNumber(r.exports) },
          { key: "l", header: "Last export", render: (r) => when(r.last_export_at) },
        ]} />
      <h2>Recent exports</h2>
      <DataTable<ExportRow> caption="Recent Excel exports" rows={d.recent_exports} loading={false} error={null} rowKey={(r) => r.id} empty="No exports yet."
        columns={[
          { key: "t", header: "Created", render: (r) => when(r.created_at) },
          { key: "c", header: "Company", render: (r) => r.company_name },
          { key: "n", header: "Template", render: (r) => r.template_name },
          { key: "s", header: "Status", render: (r) => <Status value={r.status === "completed" ? "ok" : r.status === "failed" ? "failed" : "pending"} text={EXPORT_STATUS[r.status] ?? r.status} /> },
          { key: "u", header: "Data", render: (r) => (r.include_unapproved ? "Includes unapproved" : "Approved only") },
          { key: "w", header: "By", render: (r) => r.created_by_name ?? "—" },
          { key: "m", header: "Note", render: (r) => r.error_message ?? "—" },
        ]} />
    </div>
  );
}
