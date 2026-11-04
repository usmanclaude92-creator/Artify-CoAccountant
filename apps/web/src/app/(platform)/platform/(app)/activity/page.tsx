"use client";
import Link from "next/link";
import { DataTable, PageHeader, Status } from "@/components/platform/kit";
import { fmtDateTime, label, useApi, type S } from "@/lib/platform";

export default function Activity() {
  const { data, error, loading, reload } = useApi<S["ActivityItem"][]>("/activity?limit=100");
  return (
    <>
      <PageHeader title="Platform Activity" crumbs={[{ label: "Platform Activity" }]} />
      <p className="a-hint">A single time-ordered feed of registrations, subscription changes, administrative actions and support requests. For the full evidence trail use Audit Logs.</p>
      <DataTable caption="Platform activity" loading={loading} error={error} onRetry={reload} rows={data ?? undefined} empty="Nothing has happened yet." rowKey={(a) => a.at + a.title + a.kind}
        columns={[
          { key: "w", header: "When", render: (a) => fmtDateTime(a.at) },
          { key: "k", header: "Kind", render: (a) => <Status value="info" text={label(a.kind)} /> },
          { key: "t", header: "Event", render: (a) => a.title },
          { key: "o", header: "Organization", render: (a) => a.organization_id ? <Link className="a-link" href={`/platform/organizations/${a.organization_id}`}>{a.organization_name}</Link> : "—" },
        ]} />
    </>
  );
}
