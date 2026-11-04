"use client";
import { Alert, Button, Panel } from "@artify/ui";
import { DataTable, ErrorBox, Kpi, Loading, PageHeader, Status } from "@/components/platform/kit";
import { fmtDateTime, useApi, type S } from "@/lib/platform";

export default function HealthPage() {
  const { data: h, error, loading, reload } = useApi<S["Health"]>("/health");
  if (error) return <><PageHeader title="System Health" /><ErrorBox message={error} onRetry={reload} /></>;
  if (!h || loading) return <><PageHeader title="System Health" /><Loading what="health checks" /></>;
  return (
    <>
      <PageHeader title="System Health" crumbs={[{ label: "System Health" }]} actions={<><Status value={h.overall} text={`Overall: ${h.overall}`} /><Button variant="secondary" onClick={reload}>Refresh</Button></>} />
      <Alert tone={h.environment === "production" ? "warning" : "info"}><strong>Environment: {h.environment}.</strong> {h.environment_note}</Alert>
      <section className="a-kpis" aria-label="Runtime">
        <Kpi label="Application version" value={h.version} />
        <Kpi label="API uptime (this process)" value={`${Math.floor(h.uptime_seconds / 60)} min`} />
        <Kpi label="Server errors (5xx) since start" value={h.response_counts_since_start["5xx"] ?? 0} note={`${h.response_counts_since_start["4xx"] ?? 0} client errors · ${h.response_counts_since_start["2xx"] ?? 0} successful`} />
      </section>
      <Panel>
        <DataTable caption="Service checks" loading={false} error={null} empty="No checks." rowKey={(c) => c.name} rows={h.checks}
          columns={[
            { key: "n", header: "Check", render: (c) => c.name.replace("_", " ") },
            { key: "s", header: "Status", render: (c) => <Status value={c.status} text={c.status.replace("_", " ")} /> },
            { key: "l", header: "Latency", numeric: true, render: (c) => c.latency_ms === null || c.latency_ms === undefined ? "—" : `${c.latency_ms} ms` },
            { key: "d", header: "Detail", render: (c) => c.detail },
          ]} />
      </Panel>
      <p className="a-hint">Checked {fmtDateTime(h.generated_at)}. {h.monitoring_note}</p>
    </>
  );
}
