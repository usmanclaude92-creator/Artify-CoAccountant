"use client";
import { useState } from "react";
import { Button, Dialog } from "@artify/ui";
import { DataTable, PageHeader, SearchBox, SelectFilter, Status } from "@/components/kit";
import { fmtDateTime, useApi, useListState, type Page, type S } from "@/lib/platform";

export default function AuditLogs() {
  const ls = useListState();
  const { data, error, loading, reload } = useApi<Page<S["AuditOut"]>>(`/audit-events${ls.query(["q", "action", "outcome", "organization_id", "actor_id", "since", "until", "offset"], { limit: "50" })}`);
  const [open, setOpen] = useState<S["AuditOut"] | null>(null);
  return (
    <>
      <PageHeader title="Audit Logs" crumbs={[{ label: "Audit Logs" }]} />
      <p className="a-hint">Append-only. Records cannot be edited or deleted by anyone through the application or the database roles it uses. Secrets and credentials are never written here.</p>
      <div className="a-toolbar">
        <SearchBox value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Action, target or reason" />
        <div className="a-field"><label htmlFor="action">Action starts with</label><input id="action" defaultValue={ls.get("action")} onBlur={(e) => ls.set({ action: e.target.value.trim() })} placeholder="organization." /></div>
        <SelectFilter label="Outcome" value={ls.get("outcome")} onChange={(v) => ls.set({ outcome: v })} options={[{ value: "success", label: "Success" }, { value: "denied", label: "Denied" }, { value: "failure", label: "Failure" }]} />
        <div className="a-field"><label htmlFor="since">From</label><input id="since" type="date" defaultValue={ls.get("since").slice(0, 10)} onChange={(e) => ls.set({ since: e.target.value ? `${e.target.value}T00:00:00Z` : "" })} /></div>
        <div className="a-field"><label htmlFor="until">Until</label><input id="until" type="date" defaultValue={ls.get("until").slice(0, 10)} onChange={(e) => ls.set({ until: e.target.value ? `${e.target.value}T00:00:00Z` : "" })} /></div>
      </div>
      <DataTable caption="Platform audit events" loading={loading} error={error} onRetry={reload} rows={data?.items} total={data?.total} limit={data?.limit} offset={data?.offset}
        onPage={(o) => ls.set({ offset: String(o) })} empty="No audit events match these filters." rowKey={(a) => a.id}
        columns={[
          { key: "w", header: "When", render: (a) => fmtDateTime(a.created_at) },
          { key: "a", header: "Actor", render: (a) => a.actor_name ?? "system" },
          { key: "ac", header: "Action", render: (a) => <button type="button" className="a-link" style={{ all: "unset", cursor: "pointer", textDecoration: "underline" }} onClick={() => setOpen(a)}>{a.action}</button> },
          { key: "o", header: "Organization", render: (a) => a.organization_name ?? "—" },
          { key: "oc", header: "Outcome", render: (a) => <Status value={a.outcome} /> },
          { key: "r", header: "Reason", render: (a) => a.reason ?? "" },
        ]} />
      <Dialog open={!!open} title="Audit event" onClose={() => setOpen(null)}>
        {open ? (
          <>
            <dl className="a-dl">
              <dt>Event id</dt><dd>{open.id}</dd><dt>When</dt><dd>{fmtDateTime(open.created_at)}</dd><dt>Actor</dt><dd>{open.actor_name ?? "system"} {open.actor_id ? `(${open.actor_id})` : ""}</dd>
              <dt>Action</dt><dd>{open.action}</dd><dt>Target</dt><dd>{open.target_type ?? "—"} {open.target_id ?? ""}</dd><dt>Organization</dt><dd>{open.organization_name ?? "—"}</dd>
              <dt>Outcome</dt><dd>{open.outcome}</dd><dt>Reason</dt><dd>{open.reason ?? "—"}</dd><dt>IP address</dt><dd>{open.ip ?? "—"}</dd><dt>Correlation id</dt><dd>{open.correlation_id ?? "—"}</dd>
            </dl>
            <pre className="a-hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify(open.summary, null, 2)}</pre>
            <div className="a-actions" style={{ justifyContent: "flex-end" }}><Button variant="secondary" onClick={() => setOpen(null)}>Close</Button></div>
          </>
        ) : null}
      </Dialog>
    </>
  );
}
