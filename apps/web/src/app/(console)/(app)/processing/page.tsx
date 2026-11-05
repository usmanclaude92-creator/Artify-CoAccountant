"use client";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, ErrorBox, Loading, Meter, Status } from "@/components/kit";
import { errorText, fmtNumber, tapi, useTenantApi } from "@/lib/tenant";

type Overview = Schemas["ProcessingOverviewOut"];
type Provider = Schemas["ProcessingProviderState"];
type ProcRow = Schemas["ProcessingCompanyRow"];
type FailedRow = Schemas["ProcessingFailedRow"];
type Usage = Schemas["ProcessingUsageRow"];
interface ProviderConfig { kind: string; provider_key: string; enabled: boolean; allow_external_content: boolean; company_id: string | null }
interface ProviderInfo { providers: Provider[]; configuration: ProviderConfig[] }

const STATUS_LABEL: Record<string, string> = {
  queued: "Queued", preparing_pages: "Preparing pages", ocr_processing: "Reading text", classifying: "Classifying", extracting: "Extracting",
  validating: "Validating", suggesting: "Suggesting filing", awaiting_review: "Awaiting review", approved: "Approved", failed: "Failed", cancelled: "Cancelled",
  needs_manual: "Needs manual processing",
};

/** Monitoring only. Review happens in the Windows app; nothing on this page shows document content. */
export default function ProcessingPage() {
  const { orgId } = useOrg();
  const ov = useTenantApi<Overview>(orgId, "/processing/overview");
  const pv = useTenantApi<ProviderInfo>(orgId, "/processing/providers");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  if (ov.error && !ov.data) return <div className="grid gap-4"><CcHeader title="Document Processing" /><ErrorBox message={ov.error} onRetry={ov.reload} /></div>;
  if (!ov.data) return <div className="grid gap-4"><CcHeader title="Document Processing" /><Loading what="processing overview" /></div>;
  const data = ov.data;

  const setExternal = async (p: Provider, allow: boolean) => {
    setBusy(true);
    setProblem(null);
    try {
      await tapi(orgId, "/processing/providers", { method: "PUT", body: { kind: p.kind, provider_key: p.key, enabled: true, allow_external_content: allow, company_id: null } });
      pv.reload();
    } catch (err) {
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const cfg = (p: Provider) => pv.data?.configuration.find((c) => c.provider_key === p.key && c.company_id === null);
  const total = Object.values(data.jobs_by_status).reduce((a, b) => a + b, 0);

  return (
    <div className="grid gap-4">
      <CcHeader title="Document Processing" />
      <p className="a-hint">
        Monitoring for the last {data.window_days} days. Documents are read and reviewed in the Windows app; automatic reading only ever suggests values, and an accountant must approve them.
        This page shows counts, statuses and names, never the content of a document.
      </p>
      <Panel className="grid gap-2">
        <h2>Jobs</h2>
        {total === 0 ? <p>No documents have been processed yet.</p> : (
          <dl className="a-dl">{Object.entries(data.jobs_by_status).map(([k, v]) => (<><dt key={`k${k}`}>{STATUS_LABEL[k] ?? k.replaceAll("_", " ")}</dt><dd key={`v${k}`}>{fmtNumber(v)}</dd></>))}</dl>
        )}
      </Panel>
      <h2>Usage this month</h2>
      <DataTable<Usage> caption="Processing usage" rows={data.usage} loading={false} error={null} rowKey={(r) => r.key} empty="No usage recorded."
        columns={[
          { key: "l", header: "Allowance", render: (r) => r.label },
          { key: "u", header: "Used", numeric: true, render: (r) => fmtNumber(r.used) },
          { key: "m", header: "Plan limit", numeric: true, render: (r) => (r.unlimited ? "Unlimited" : r.limit === null ? "Not set" : fmtNumber(r.limit)) },
          { key: "p", header: "Usage", render: (r) => <Meter percent={r.unlimited || !r.limit ? null : Math.round((1000 * r.used) / r.limit) / 10} status={r.limit && r.used > r.limit ? "exceeded" : r.limit && r.used >= 0.8 * r.limit ? "approaching" : "ok"} /> },
        ]} />
      <h2>By company</h2>
      <DataTable<ProcRow> caption="Processing by company" rows={data.companies} loading={false} error={null} rowKey={(r) => r.company_id} empty="No companies yet."
        columns={[
          { key: "n", header: "Company", render: (r) => r.name },
          { key: "a", header: "Awaiting review", numeric: true, render: (r) => fmtNumber(r.awaiting_review) },
          { key: "f", header: "Failed", numeric: true, render: (r) => fmtNumber(r.failed) },
          { key: "j", header: "Jobs (30 days)", numeric: true, render: (r) => fmtNumber(r.jobs_30d) },
        ]} />
      <h2>Awaiting review</h2>
      <DataTable<Overview["awaiting_review"][number]> caption="Documents awaiting review" rows={data.awaiting_review} loading={false} error={null} rowKey={(r) => r.id} empty="Nothing is waiting for review."
        columns={[{ key: "d", header: "Document", render: (r) => r.display_name }, { key: "c", header: "Company", render: (r) => r.company_name }]} />
      <h2>Failed</h2>
      <DataTable<FailedRow> caption="Failed processing" rows={data.failed} loading={false} error={null} rowKey={(r) => r.id} empty="No failed jobs."
        columns={[
          { key: "d", header: "Document", render: (r) => r.display_name },
          { key: "c", header: "Company", render: (r) => r.company_name },
          { key: "e", header: "Reason", render: (r) => r.error_message ?? r.error_code ?? "—" },
          { key: "a", header: "Attempts", numeric: true, render: (r) => fmtNumber(r.attempt_count) },
        ]} />
      <Panel className="grid gap-2">
        <h2>Engines and providers</h2>
        <p className="a-hint">Provider credentials are kept on the server and never shown. “Not configured” means the engine is not available; nothing is simulated.</p>
        {problem ? <Alert tone="danger">{problem}</Alert> : null}
        {data.providers.some((p) => p.kind === "ocr" && !p.configured) ? <Alert tone="warning">The text recognition engine is not installed on this server, so scanned documents cannot be read.</Alert> : null}
        <DataTable<Provider> caption="Providers" rows={data.providers} loading={false} error={null} rowKey={(r) => `${r.kind}:${r.key}`} empty="No providers."
          columns={[
            { key: "k", header: "Purpose", render: (r) => (r.kind === "ocr" ? "Text recognition" : "Understanding") },
            { key: "n", header: "Provider", render: (r) => r.key },
            { key: "s", header: "State", render: (r) => <Status value={r.configured ? "ok" : "inactive"} text={r.configured ? "Configured" : "Not configured"} /> },
            { key: "e", header: "Where it runs", render: (r) => (r.external ? "External service" : "On this server") },
            { key: "v", header: "Version", render: (r) => r.version ?? "—" },
            { key: "x", header: "Send document text outside", render: (r) => (r.external ? (
              <Button variant="secondary" disabled={busy || !r.configured} onClick={() => setExternal(r, !cfg(r)?.allow_external_content)}>{cfg(r)?.allow_external_content ? "Allowed — turn off" : "Off — allow"}</Button>
            ) : "Not applicable") },
          ]} />
      </Panel>
    </div>
  );
}
