"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { ActionDialog, DataTable, ErrorBox, Field, Loading, PageHeader, Status, useToast } from "@/components/platform/kit";
import { fmtDate, fmtDateTime, fmtMoney, label, papi, useApi, type S } from "@/lib/platform";

type Dlg = "pay" | "refund" | "adjust" | "void" | null;

export default function InvoiceDetail() {
  const { id } = useParams<{ id: string }>();
  const can = useCan();
  const notify = useToast();
  const { data, error, loading, reload } = useApi<S["InvoiceDetail"]>(`/billing/invoices/${id}`);
  const [dlg, setDlg] = useState<Dlg>(null);
  const [f, setF] = useState({ amount: "", method: "bank_transfer", reference: "", note: "" });
  if (error) return <><PageHeader title="Invoice" crumbs={[{ label: "Billing & Payments", href: "/platform/billing" }]} /><ErrorBox message={error} onRetry={reload} /></>;
  if (!data || loading) return <><PageHeader title="Invoice" /><Loading what="invoice" /></>;
  const i = data.invoice;
  const manage = can("platform.billing.manage");
  const done = (m: string) => { notify(m); setDlg(null); setF({ amount: "", method: "bank_transfer", reference: "", note: "" }); reload(); };
  const method = <Field label="Method">{(x) => <select id={x} value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["bank_transfer", "cash", "cheque", "other"].map((v) => <option key={v} value={v}>{label(v)}</option>)}</select>}</Field>;
  const amount = <Field label={`Amount (${i.currency})`}>{(x) => <input id={x} inputMode="decimal" pattern="\d+(\.\d{1,3})?" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required />}</Field>;
  return (
    <>
      <PageHeader title={`Invoice ${i.number}`} crumbs={[{ label: "Billing & Payments", href: "/platform/billing" }, { label: i.number }]} actions={<>
        <Status value={i.status} />
        {manage && i.status === "draft" ? <Button onClick={async () => { await papi(`/billing/invoices/${id}/issue`, { method: "POST" }); done("Invoice issued."); }}>Issue</Button> : null}
        {manage && i.status === "open" ? <Button onClick={() => setDlg("pay")}>Record payment</Button> : null}
        {manage && i.status === "open" ? <Button variant="secondary" onClick={() => setDlg("adjust")}>Adjustment</Button> : null}
        {manage && ["open", "paid"].includes(i.status) && Number(i.paid_total) > 0 ? <Button variant="secondary" onClick={() => setDlg("refund")}>Record refund</Button> : null}
        {manage && ["draft", "open"].includes(i.status) && Number(i.paid_total) === 0 ? <Button className="a-btn--danger" onClick={() => setDlg("void")}>Void</Button> : null}
      </>} />
      <Panel>
        <dl className="a-dl">
          <dt>Organization</dt><dd><Link className="a-link" href={`/platform/organizations/${i.organization_id}`}>{i.organization_name}</Link></dd>
          <dt>Total</dt><dd>{fmtMoney(i.amount_total, i.currency)}</dd><dt>Adjustments</dt><dd>{fmtMoney(i.adjustments_total, i.currency)}</dd>
          <dt>Net paid</dt><dd>{fmtMoney(i.paid_total, i.currency)}</dd><dt>Outstanding</dt><dd><strong>{fmtMoney(i.outstanding, i.currency)}</strong>{i.overdue ? " (overdue)" : ""}</dd>
          <dt>Issued</dt><dd>{fmtDate(i.issued_at)}</dd><dt>Due</dt><dd>{fmtDate(i.due_at)}</dd><dt>Description</dt><dd>{i.description ?? "—"}</dd>
        </dl>
      </Panel>
      <Panel><h2 className="mb-2 font-semibold">Payments and refunds</h2>
        <DataTable caption="Payments" loading={false} error={null} empty="No payments recorded." rowKey={(p) => p.id} rows={data.payments}
          columns={[{ key: "w", header: "Received", render: (p) => fmtDateTime(p.received_at) }, { key: "k", header: "Kind", render: (p) => label(p.kind) },
            { key: "a", header: "Amount", numeric: true, render: (p) => fmtMoney(p.kind === "refund" ? `-${p.amount}` : p.amount, p.currency) },
            { key: "m", header: "Method", render: (p) => label(p.method) }, { key: "r", header: "Reference", render: (p) => p.reference ?? "" }, { key: "n", header: "Note", render: (p) => p.note ?? "" }]} />
        <p className="a-hint mt-2">Records are append-only. A mistake is corrected with a new refund or adjustment, never by editing.</p>
      </Panel>
      <Panel><h2 className="mb-2 font-semibold">Adjustments</h2>
        <DataTable caption="Adjustments" loading={false} error={null} empty="No adjustments." rowKey={(a) => a.id} rows={data.adjustments}
          columns={[{ key: "w", header: "When", render: (a) => fmtDateTime(a.created_at) }, { key: "a", header: "Amount", numeric: true, render: (a) => fmtMoney(a.amount, a.currency) }, { key: "r", header: "Reason", render: (a) => a.reason }]} />
      </Panel>

      <ActionDialog open={dlg === "pay"} title="Record a payment" submitLabel="Record payment" needsReason={false} description="Record money Artify actually received. Nothing is charged by this action."
        onSubmit={async () => { await papi(`/billing/invoices/${id}/payments`, { method: "POST", body: { amount: f.amount, method: f.method, reference: f.reference || null, note: f.note || null } }); done("Payment recorded."); }} onClose={() => setDlg(null)}>
        {amount}{method}
        <Field label="Reference">{(x) => <input id={x} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} maxLength={120} />}</Field>
      </ActionDialog>
      <ActionDialog open={dlg === "refund"} title="Record a refund" submitLabel="Record refund" needsReason reasonLabel="Reason for the refund (recorded)"
        onSubmit={async ({ reason }) => { await papi(`/billing/invoices/${id}/refunds`, { method: "POST", body: { amount: f.amount, method: f.method, reference: f.reference || null, note: reason } }); done("Refund recorded."); }} onClose={() => setDlg(null)}>
        {amount}{method}
      </ActionDialog>
      <ActionDialog open={dlg === "adjust"} title="Manual adjustment" submitLabel="Record adjustment" reasonLabel="Reason for the adjustment (recorded)"
        description="Use a negative amount for a credit, positive for an extra charge. The invoice total itself is never edited."
        onSubmit={async ({ reason }) => { await papi(`/billing/invoices/${id}/adjustments`, { method: "POST", body: { amount: f.amount, reason } }); done("Adjustment recorded."); }} onClose={() => setDlg(null)}>
        <Field label={`Amount (${i.currency}, may be negative)`}>{(x) => <input id={x} inputMode="decimal" pattern="-?\d+(\.\d{1,3})?" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required />}</Field>
      </ActionDialog>
      <ActionDialog open={dlg === "void"} title={`Void invoice ${i.number}?`} danger submitLabel="Void invoice" confirmWord={i.number}
        onSubmit={async ({ reason }) => { await papi(`/billing/invoices/${id}/void`, { method: "POST", body: { reason } }); done("Invoice voided."); }} onClose={() => setDlg(null)} />
    </>
  );
}
