"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { ActionDialog, DataTable, ErrorBox, Field, Loading, PageHeader, Status, useToast } from "@/components/platform/kit";
import { ApiProblem } from "@/lib/client";
import { fmtDate, fmtDateTime, label, papi, useApi, type S } from "@/lib/platform";

type Dlg = "plan" | "extend" | "suspend" | "reactivate" | "status" | "cancel" | null;
interface Violation { key: string; limit: number; current: number }

export default function SubscriptionDetail() {
  const { id } = useParams<{ id: string }>();
  const can = useCan();
  const notify = useToast();
  const { data, error, loading, reload } = useApi<S["SubDetail"]>(`/subscriptions/${id}`);
  const plans = useApi<S["PlanRow"][]>("/plans?status=active");
  const [dlg, setDlg] = useState<Dlg>(null);
  const [version, setVersion] = useState("");
  const [days, setDays] = useState("14");
  const [status, setStatus] = useState("active");
  const [violations, setViolations] = useState<Violation[] | null>(null);
  const [ack, setAck] = useState(false);
  if (error) return <><PageHeader title="Subscription" crumbs={[{ label: "Subscriptions", href: "/platform/subscriptions" }]} /><ErrorBox message={error} onRetry={reload} /></>;
  if (!data || loading) return <><PageHeader title="Subscription" /><Loading what="subscription" /></>;
  const s = data.subscription;
  const manage = can("platform.subscriptions.manage");
  const done = (msg: string) => { notify(msg); setDlg(null); setViolations(null); setAck(false); reload(); };
  const live = ["trialing", "active", "past_due"].includes(s.status);
  const close = () => { setDlg(null); setViolations(null); setAck(false); };
  const slug = s.organization_slug;
  return (
    <>
      <PageHeader title={`${s.organization_name} — subscription`} crumbs={[{ label: "Subscriptions", href: "/platform/subscriptions" }, { label: s.organization_name }]} actions={<>
        <Status value={s.status} />
        {manage && s.status !== "cancelled" && s.status !== "expired" ? <Button variant="secondary" onClick={() => { setVersion(""); setDlg("plan"); }}>Change plan</Button> : null}
        {manage && live ? <Button variant="secondary" onClick={() => setDlg("extend")}>Extend</Button> : null}
        {manage && live ? <Button className="a-btn--danger" onClick={() => setDlg("suspend")}>Suspend</Button> : null}
        {manage && s.status === "suspended" ? <Button onClick={() => setDlg("reactivate")}>Reactivate</Button> : null}
        {manage && s.status !== "suspended" ? <Button variant="secondary" onClick={() => setDlg("status")}>Set status…</Button> : null}
      </>} />
      <Panel>
        <dl className="a-dl">
          <dt>Organization</dt><dd><Link className="a-link" href={`/platform/organizations/${s.organization_id}`}>{s.organization_name}</Link></dd>
          <dt>Plan</dt><dd>{s.plan_name} (version {s.plan_version})</dd><dt>Started</dt><dd>{fmtDate(s.current_period_start)}</dd>
          <dt>Renewal / period end</dt><dd>{fmtDate(s.current_period_end)}</dd><dt>Trial ends</dt><dd>{fmtDate(s.trial_ends_at)}</dd>
          <dt>Cancellation</dt><dd>{s.cancelled_at ? `Cancelled ${fmtDate(s.cancelled_at)}` : s.cancel_at_period_end ? "Scheduled at period end" : "None"}
            {manage && live ? <> <Button variant="secondary" className="a-btn--sm" onClick={() => setDlg("cancel")}>{s.cancel_at_period_end ? "Remove schedule" : "Schedule cancellation"}</Button></> : null}</dd>
          <dt>Payment status</dt><dd>{s.latest_invoice_status ? <Status value={s.latest_invoice_status} /> : "No invoices recorded"}</dd>
        </dl>
        {["suspended", "cancelled", "expired"].includes(s.status) ? <div className="mt-3"><Alert tone="warning">Customers cannot create companies, invite users or register devices while a subscription is {s.status}. Existing data stays readable.</Alert></div> : null}
      </Panel>
      <Panel>
        <h2 className="mb-2 font-semibold">History</h2>
        <DataTable caption="Subscription history" loading={false} error={null} empty="No events." rowKey={(e) => e.id} rows={data.events}
          columns={[
            { key: "w", header: "When", render: (e) => fmtDateTime(e.created_at) }, { key: "t", header: "Event", render: (e) => label(e.event_type) },
            { key: "c", header: "Change", render: (e) => e.to_status ? `${e.from_status ?? "—"} → ${e.to_status}` : e.to_plan_version_id ? "plan version changed" : "" },
            { key: "r", header: "Reason", render: (e) => e.reason ?? "" }, { key: "a", header: "By", render: (e) => e.actor_type === "system" ? "system" : "administrator" },
          ]} />
      </Panel>

      <ActionDialog open={dlg === "plan"} title="Change plan" submitLabel={violations ? "Change plan anyway" : "Change plan"} danger={!!violations}
        description="Existing data is never deleted. If the new plan is below current usage, the organization keeps what it has but cannot create more until usage falls under the limit."
        onSubmit={async ({ reason }) => {
          try {
            await papi(`/subscriptions/${id}/change-plan`, { method: "POST", body: { plan_version_id: version, reason, acknowledge_over_limit: ack } });
            done("Plan changed.");
          } catch (ex) {
            if (ex instanceof ApiProblem && ex.problem.code === "over_limit") { setViolations(ex.problem.violations as Violation[]); return false; }
            throw ex;
          }
        }} onClose={close}>
        <Field label="New plan version">
          {(i) => <select id={i} value={version} onChange={(e) => { setVersion(e.target.value); setViolations(null); setAck(false); }} required>
            <option value="">Choose…</option>
            {plans.data?.map((p) => <option key={p.id} value={p.latest_version_id} disabled={p.latest_version_id === s.plan_version_id}>{p.name} v{p.latest_version}{p.latest_version_id === s.plan_version_id ? " (current)" : ""}</option>)}
          </select>}
        </Field>
        {violations ? (
          <Alert tone="warning">
            <strong>Below current usage:</strong> {violations.map((v) => `${v.key}: using ${v.current}, new limit ${v.limit}`).join("; ")}.
            <label className="flex items-center gap-2 mt-2"><input type="checkbox" checked={ack} onChange={(e) => { setAck(e.target.checked); }} /> I understand; keep existing data and block further creation.</label>
          </Alert>
        ) : null}
      </ActionDialog>
      <ActionDialog open={dlg === "extend"} title="Extend subscription" submitLabel="Extend" onSubmit={async ({ reason }) => { await papi(`/subscriptions/${id}/extend`, { method: "POST", body: { days: Number(days), reason } }); done("Subscription extended."); }} onClose={close}>
        <Field label="Days to add">{(i) => <input id={i} type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} required />}</Field>
      </ActionDialog>
      <ActionDialog open={dlg === "suspend"} title="Suspend subscription?" danger submitLabel="Suspend" confirmWord={slug}
        description="The customer keeps read access but cannot create or invite anything until reactivated."
        onSubmit={async ({ reason, confirm }) => { await papi(`/subscriptions/${id}/suspend`, { method: "POST", body: { reason, confirm } }); done("Subscription suspended."); }} onClose={close} />
      <ActionDialog open={dlg === "reactivate"} title="Reactivate subscription" submitLabel="Reactivate" onSubmit={async ({ reason }) => { await papi(`/subscriptions/${id}/reactivate`, { method: "POST", body: { reason } }); done("Subscription reactivated."); }} onClose={close} />
      <ActionDialog open={dlg === "status"} title="Set subscription status" danger={["cancelled", "expired"].includes(status)} submitLabel="Apply status" confirmWord={slug}
        onSubmit={async ({ reason, confirm }) => { await papi(`/subscriptions/${id}/status`, { method: "POST", body: { status, reason, confirm } }); done("Status updated."); }} onClose={close}>
        <Field label="New status" hint="Only transitions that make sense from the current status are accepted.">
          {(i) => <select id={i} value={status} onChange={(e) => setStatus(e.target.value)}>{["active", "past_due", "cancelled", "expired"].map((v) => <option key={v} value={v}>{label(v)}</option>)}</select>}
        </Field>
      </ActionDialog>
      <ActionDialog open={dlg === "cancel"} title={s.cancel_at_period_end ? "Remove scheduled cancellation" : "Schedule cancellation at period end"} submitLabel="Confirm"
        onSubmit={async ({ reason }) => { await papi(`/subscriptions/${id}/cancel-at-period-end`, { method: "POST", body: { enabled: !s.cancel_at_period_end, reason } }); done("Cancellation schedule updated."); }} onClose={close} />
    </>
  );
}
