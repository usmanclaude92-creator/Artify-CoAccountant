"use client";
import Link from "next/link";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { ActionDialog, DataTable, Field, Kpi, PageHeader, SearchBox, SelectFilter, Status, useToast } from "@/components/platform/kit";
import { fmtDate, fmtMoney, label, papi, useApi, useListState, type Page, type S } from "@/lib/platform";

const KEYS = ["q", "status", "organization_id", "overdue", "sort", "order", "offset"];

export default function Billing() {
  const can = useCan();
  const notify = useToast();
  const ls = useListState({ sort: "created", order: "desc" });
  const sum = useApi<S["BillingSummary"]>("/billing/summary");
  const { data, error, loading, reload } = useApi<Page<S["InvoiceRow"]>>(`/billing/invoices${ls.query(KEYS, { limit: "25" })}`);
  const [open, setOpen] = useState(false);
  const orgs = useApi<Page<S["OrgRow"]>>(open ? "/organizations?limit=200&sort=name&order=asc" : null);
  const [f, setF] = useState({ organization_id: "", amount_total: "", currency: "OMR", due_at: "", description: "", issue: true });
  const sort = ls.get("sort");
  const order = ls.get("order");
  const totals = (t?: S["CurrencyTotal"][]) => (t && t.length ? t.map((x) => fmtMoney(x.amount, x.currency)).join(" · ") : "None");
  return (
    <>
      <PageHeader title="Billing & Payments" crumbs={[{ label: "Billing & Payments" }]}
        actions={can("platform.billing.manage") ? <Button onClick={() => setOpen(true)}>New invoice</Button> : null} />
      <Alert tone="info">{sum.data?.note ?? "Manual billing records. No payment provider is integrated; amounts are never summed across currencies."}</Alert>
      <section className="a-kpis" aria-label="Billing summary">
        <Kpi label="Outstanding" value={totals(sum.data?.outstanding)} />
        <Kpi label="Overdue" value={totals(sum.data?.overdue)} />
        <Kpi label="Collected, last 30 days" value={totals(sum.data?.collected_last_30_days)} />
        <Kpi label="Open invoices" value={sum.data?.invoices_by_status.open ?? 0} note={`${sum.data?.invoices_by_status.draft ?? 0} drafts · ${sum.data?.invoices_by_status.paid ?? 0} paid`} />
      </section>
      <div className="a-toolbar">
        <SearchBox value={ls.get("q")} onSubmit={(v) => ls.set({ q: v })} placeholder="Invoice number or organization" />
        <SelectFilter label="Status" value={ls.get("status")} onChange={(v) => ls.set({ status: v })} options={["draft", "open", "paid", "void"].map((v) => ({ value: v, label: label(v) }))} />
        <SelectFilter label="Overdue" value={ls.get("overdue")} onChange={(v) => ls.set({ overdue: v })} options={[{ value: "true", label: "Overdue only" }]} />
      </div>
      <Panel>
        <DataTable caption="Invoices" loading={loading} error={error} onRetry={reload} rows={data?.items} total={data?.total} limit={data?.limit} offset={data?.offset}
          onPage={(o) => ls.set({ offset: String(o) })} sort={sort} order={order} onSort={(k) => ls.set({ sort: k, order: sort === k && order === "asc" ? "desc" : "asc" })}
          empty="No invoices have been recorded. Nothing is billed automatically." rowKey={(i) => i.id}
          columns={[
            { key: "n", header: "Number", sortKey: "number", render: (i) => <Link className="a-link" href={`/platform/billing/${i.id}`}>{i.number}</Link> },
            { key: "o", header: "Organization", sortKey: "organization", render: (i) => i.organization_name },
            { key: "s", header: "Status", render: (i) => <><Status value={i.status} />{i.overdue ? <> <Status value="exceeded" text="Overdue" /></> : null}</> },
            { key: "a", header: "Total", sortKey: "amount", numeric: true, render: (i) => fmtMoney(i.amount_total, i.currency) },
            { key: "p", header: "Paid", numeric: true, render: (i) => fmtMoney(i.paid_total, i.currency) },
            { key: "ou", header: "Outstanding", numeric: true, render: (i) => fmtMoney(i.outstanding, i.currency) },
            { key: "d", header: "Due", sortKey: "due", render: (i) => fmtDate(i.due_at) },
          ]} />
      </Panel>
      <ActionDialog open={open} title="New invoice" submitLabel="Create invoice" needsReason={false}
        description="Creates a manual invoice record. It does not charge anyone."
        onSubmit={async () => {
          await papi("/billing/invoices", { method: "POST", body: { organization_id: f.organization_id, amount_total: f.amount_total, currency: f.currency, due_at: f.due_at ? new Date(f.due_at).toISOString() : null, description: f.description || null, issue: f.issue } });
          notify("Invoice created."); setOpen(false); reload(); sum.reload();
        }} onClose={() => setOpen(false)}>
        <Field label="Organization">{(i) => <select id={i} value={f.organization_id} onChange={(e) => setF({ ...f, organization_id: e.target.value })} required><option value="">Choose…</option>{orgs.data?.items.map((o) => <option key={o.id} value={o.id}>{o.display_name}</option>)}</select>}</Field>
        <Field label="Amount (up to 3 decimals)">{(i) => <input id={i} inputMode="decimal" pattern="\d+(\.\d{1,3})?" value={f.amount_total} onChange={(e) => setF({ ...f, amount_total: e.target.value })} required />}</Field>
        <Field label="Currency">{(i) => <input id={i} value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} pattern="[A-Z]{3}" required />}</Field>
        <Field label="Due date (optional)">{(i) => <input id={i} type="date" value={f.due_at} onChange={(e) => setF({ ...f, due_at: e.target.value })} />}</Field>
        <Field label="Description">{(i) => <input id={i} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} maxLength={500} />}</Field>
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.issue} onChange={(e) => setF({ ...f, issue: e.target.checked })} /> Issue now (otherwise saved as a draft)</label>
      </ActionDialog>
    </>
  );
}
