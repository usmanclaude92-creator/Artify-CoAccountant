"use client";
import type { Schemas } from "@artify/api-client";
import { Panel } from "@artify/ui";
import { useState } from "react";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, ErrorBox, Loading, Status } from "@/components/kit";
import { fmtNumber, useTenantApi } from "@/lib/tenant";

type Overview = Schemas["BookkeepingOverviewOut"];
type Row = Overview["companies"][number];

const CLOSING: Record<string, string> = { draft: "Draft", in_progress: "In progress", ready_for_review: "Ready for review", closed: "Closed" };

/** Monitoring only. Preparing, reviewing, closing and reopening a day happen in the Windows app; nothing here shows a transaction, a document or an amount. */
export default function BookkeepingPage() {
  const { orgId } = useOrg();
  const [day, setDay] = useState("");
  const ov = useTenantApi<Overview>(orgId, `/bookkeeping/overview${day ? `?business_date=${day}` : ""}`);
  if (ov.error && !ov.data) return <div className="grid gap-4"><CcHeader title="Daily Closing" /><ErrorBox message={ov.error} onRetry={ov.reload} /></div>;
  if (!ov.data) return <div className="grid gap-4"><CcHeader title="Daily Closing" /><Loading what="Daily closing overview" /></div>;
  const d = ov.data;
  return (
    <div className="grid gap-4">
      <CcHeader title="Daily Closing" />
      <p className="a-hint">
        Each company closes its own days in its own time zone and keeps its own checklists and rules. Accountants prepare and close days in the Windows app; a different person approves where the
        company requires it. This page shows status and counts only, never transactions, documents or amounts, and it cannot close, reopen or override anything.
      </p>
      <label className="grid gap-1">
        <span>Business day (leave empty for each company&apos;s own today)</span>
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Business day" />
      </label>
      <Panel className="grid gap-2">
        <h2>Summary</h2>
        <dl className="a-dl">
          <dt>Companies</dt><dd>{fmtNumber(d.summary.companies)}</dd>
          <dt>Closed</dt><dd>{fmtNumber(d.summary.closed)}</dd>
          <dt>Waiting for review</dt><dd>{fmtNumber(d.summary.ready_for_review)}</dd>
          <dt>Not started</dt><dd>{fmtNumber(d.summary.not_started)}</dd>
          <dt>Open errors and critical problems</dt><dd>{fmtNumber(d.summary.open_exceptions)}</dd>
          <dt>Overdue tasks</dt><dd>{fmtNumber(d.summary.overdue_tasks)}</dd>
        </dl>
      </Panel>
      <h2>Companies</h2>
      <DataTable<Row> caption="Daily closing by company" rows={d.companies} loading={false} error={null} rowKey={(r) => r.company_id} empty="No companies to show."
        columns={[
          { key: "c", header: "Company", render: (r) => r.company_name },
          { key: "d", header: "Day", render: (r) => `${r.day} (${r.timezone})` },
          { key: "s", header: "Closing", render: (r) => <Status value={r.closing_status === "closed" ? "ok" : r.closing_status === "ready_for_review" ? "pending" : "inactive"} text={r.closing_status ? (CLOSING[r.closing_status] ?? r.closing_status) : "Not started"} /> },
          { key: "e", header: "Open errors", numeric: true, render: (r) => fmtNumber(r.open_exceptions) },
          { key: "t", header: "Open tasks", numeric: true, render: (r) => fmtNumber(r.open_tasks) },
          { key: "o", header: "Overdue tasks", numeric: true, render: (r) => fmtNumber(r.overdue_tasks) },
          { key: "u", header: "Recent days not closed", numeric: true, render: (r) => fmtNumber(r.unclosed_recent_days) },
        ]} />
    </div>
  );
}
