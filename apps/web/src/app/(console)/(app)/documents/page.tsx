"use client";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, ErrorBox, Loading, Meter, Status } from "@/components/kit";
import { errorText, fmtDateTime, fmtNumber, tapi, useTenantApi } from "@/lib/tenant";

type Overview = Schemas["StorageOverviewOut"];
type Company = Schemas["CompanyStorage"];
type Failure = Schemas["UploadFailure"];
type Reconcile = Schemas["ReconcileOut"];

/** Human-readable byte size (binary units; the plan limits are in bytes). */
function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

export default function DocumentsPage() {
  const { orgId } = useOrg();
  const { data, error, reload } = useTenantApi<Overview>(orgId, "/storage/overview");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<Reconcile | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  if (error && !data) return <div className="grid gap-4"><CcHeader title="Documents & Storage" /><ErrorBox message={error} onRetry={reload} /></div>;
  if (!data) return <div className="grid gap-4"><CcHeader title="Documents & Storage" /><Loading what="storage overview" /></div>;

  const check = async () => {
    setBusy(true);
    setProblem(null);
    try {
      setReport(await tapi<Reconcile>(orgId, "/storage/reconcile", { method: "POST", body: { repair: false } }));
    } catch (err) {
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const used = data.storage.used_bytes;
  const limit = data.storage.limit_bytes;
  const percent = data.storage.unlimited || !limit ? null : Math.round((1000 * used) / limit) / 10;
  const status = percent === null ? "ok" : percent > 100 ? "exceeded" : percent >= 80 ? "approaching" : "ok";
  const month = data.documents_this_month;
  const failed = data.uploads_last_7_days.failed ?? 0;
  const quarantined = data.uploads_last_7_days.quarantined ?? 0;

  return (
    <div className="grid gap-4">
      <CcHeader title="Documents & Storage" />
      <p className="a-hint">
        An overview for the firm. Documents are imported and managed in the Windows app; this page shows how much space they use, what failed to upload and
        whether the document store matches the database. Reaching a limit blocks new uploads only; it never deletes or hides existing documents.
      </p>
      {!data.scanner.active ? (
        <Alert tone="warning">Malware scanning is not active on this installation. Files are checked for type and structure and dangerous content is refused, but they are not scanned by an antivirus engine.</Alert>
      ) : null}
      <Panel className="grid gap-2">
        <h2>Storage</h2>
        <dl className="a-dl">
          <dt>Stored</dt><dd>{bytes(used)}{limit !== null && !data.storage.unlimited ? ` of ${bytes(limit)}` : data.storage.unlimited ? " (unlimited plan)" : " (no limit configured)"}</dd>
          <dt>Usage</dt><dd><Meter percent={percent} status={status} /></dd>
          <dt>Documents this month</dt><dd>{fmtNumber(month.used)}{month.unlimited ? " (unlimited)" : month.limit !== null ? ` of ${fmtNumber(month.limit)}` : ""}</dd>
          <dt>Largest file</dt><dd>{bytes(data.max_file_size_bytes)}</dd>
          <dt>Document store</dt><dd>{data.backend === "s3" ? "Private cloud object storage" : "Local development storage"}</dd>
          <dt>Malware scanner</dt><dd>{data.scanner.active ? `Active (${data.scanner.name})` : "Not configured"}</dd>
        </dl>
        <p className="a-hint">{data.accounting}</p>
      </Panel>
      <h2>Storage by company</h2>
      <DataTable<Company> caption="Storage by company" rows={data.companies} loading={false} error={null} rowKey={(r) => r.company_id} empty="No companies yet."
        columns={[
          { key: "n", header: "Company", render: (r) => r.name },
          { key: "b", header: "Stored", numeric: true, render: (r) => bytes(r.stored_bytes) },
          { key: "a", header: "Active", numeric: true, render: (r) => fmtNumber(r.active_documents) },
          { key: "r", header: "Archived", numeric: true, render: (r) => fmtNumber(r.archived_documents) },
          { key: "d", header: "Deleted items", numeric: true, render: (r) => fmtNumber(r.deleted_documents) },
        ]} />
      <Panel className="grid gap-2">
        <h2>Uploads in the last 7 days</h2>
        <dl className="a-dl">
          {Object.entries(data.uploads_last_7_days).map(([k, v]) => (<><dt key={`k${k}`}>{k.replaceAll("_", " ")}</dt><dd key={`v${k}`}>{fmtNumber(v)}</dd></>))}
          {Object.keys(data.uploads_last_7_days).length === 0 ? <><dt>Uploads</dt><dd>None yet</dd></> : null}
        </dl>
        {failed + quarantined > 0 ? <Alert tone="warning">{failed} failed and {quarantined} quarantined upload(s) in the last 7 days. Quarantined files were refused and never stored.</Alert> : null}
      </Panel>
      <h2>Recent failed uploads</h2>
      <DataTable<Failure> caption="Recent failed uploads" rows={data.recent_failures} loading={false} error={null} rowKey={(r) => r.id} empty="No failed uploads."
        columns={[
          { key: "f", header: "File", render: (r) => r.original_filename },
          { key: "c", header: "Company", render: (r) => r.company_name },
          { key: "s", header: "Status", render: (r) => <Status value={r.status} /> },
          { key: "e", header: "Reason", render: (r) => r.error_message ?? r.error_code ?? "—" },
          { key: "t", header: "When", render: (r) => fmtDateTime(r.updated_at) },
        ]} />
      <Panel className="grid gap-2">
        <h2>Storage check</h2>
        <p>Compares the database with the document store: objects that went missing, stray objects nobody refers to, and abandoned uploads. This check only reads; repairs are run by an administrator through the API.</p>
        <div><Button onClick={check} disabled={busy}>{busy ? "Checking…" : "Run storage check"}</Button></div>
        {problem ? <Alert tone="danger">{problem}</Alert> : null}
        {report ? (
          <dl className="a-dl" aria-label="Storage check result">
            <dt>Objects in database</dt><dd>{fmtNumber(report.objects_in_database)}</dd>
            <dt>Missing from the store</dt><dd>{fmtNumber(report.missing_objects)}</dd>
            <dt>Size mismatches</dt><dd>{fmtNumber(report.size_mismatches)}</dd>
            <dt>Stray objects</dt><dd>{fmtNumber(report.orphan_objects)}</dd>
            <dt>Expired upload sessions</dt><dd>{fmtNumber(report.expired_uploads)}</dd>
          </dl>
        ) : null}
      </Panel>
    </div>
  );
}
