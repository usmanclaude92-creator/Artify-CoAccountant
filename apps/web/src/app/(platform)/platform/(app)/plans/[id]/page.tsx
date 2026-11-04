"use client";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { ActionDialog, DataTable, ErrorBox, Field, Loading, PageHeader, Status, useToast } from "@/components/kit";
import { errorText, fmtDateTime, fmtMoney, fmtNumber, label, papi, useApi, type S } from "@/lib/platform";
import { EntitlementEditor, fromVersion, toBody, type EntState } from "../entitlement-editor";

export default function PlanDetail() {
  const { id } = useParams<{ id: string }>();
  const can = useCan();
  const notify = useToast();
  const { data, error, loading, reload } = useApi<S["PlanDetail"]>(`/plans/${id}`);
  const [editing, setEditing] = useState(false);
  const [ents, setEnts] = useState<EntState>({});
  const [meta, setMeta] = useState({ price_amount: "", price_currency: "OMR", support_level: "standard", trial_days: "", billing_interval: "monthly", notes: "" });
  const [dlg, setDlg] = useState<"archive" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (error) return <><PageHeader title="Plan" crumbs={[{ label: "Subscription Plans", href: "/platform/plans" }]} /><ErrorBox message={error} onRetry={reload} /></>;
  if (!data || loading) return <><PageHeader title="Plan" /><Loading what="plan" /></>;
  const p = data.plan;
  const latest = data.versions[0];
  const manage = can("platform.plans.manage");
  const start = async () => {
    if (!latest) return;
    const cat = await papi<S["CatalogEntry"][]>("/entitlements");
    setEnts(fromVersion(latest.entitlements, cat));
    setMeta({ price_amount: latest.price_amount ?? "", price_currency: latest.price_currency ?? "OMR", support_level: latest.support_level, trial_days: latest.trial_days?.toString() ?? "", billing_interval: latest.billing_interval, notes: "" });
    setEditing(true);
  };
  const post = async (path: string, ok: string) => { try { await papi(path, { method: "POST" }); notify(ok); reload(); } catch (ex) { notify(errorText(ex), "danger"); } };
  return (
    <>
      <PageHeader title={p.name} crumbs={[{ label: "Subscription Plans", href: "/platform/plans" }, { label: p.name }]} actions={<>
        <Status value={p.status} text={p.status === "retired" ? "Archived" : undefined} />
        {manage && p.status === "inactive" ? <Button onClick={() => post(`/plans/${id}/activate`, "Plan activated.")}>Activate</Button> : null}
        {manage && p.status === "active" ? <Button variant="secondary" onClick={() => post(`/plans/${id}/deactivate`, "Plan deactivated.")}>Deactivate</Button> : null}
        {manage && p.status !== "retired" ? <Button variant="secondary" onClick={start}>Publish new version</Button> : null}
        {manage && p.status !== "retired" ? <Button className="a-btn--danger" onClick={() => setDlg("archive")}>Archive</Button> : null}
      </>} />
      <Panel>
        <dl className="a-dl">
          <dt>Key</dt><dd>{p.key}{p.is_default ? " (default plan for new sign-ups)" : ""}</dd><dt>Description</dt><dd>{p.description ?? "—"}</dd>
          <dt>Price</dt><dd>{p.price_amount ? `${fmtMoney(p.price_amount, p.price_currency)} / ${p.billing_interval}` : "Not priced"}</dd>
          <dt>Support level</dt><dd>{label(p.support_level)}</dd><dt>Trial</dt><dd>{p.trial_days ? `${p.trial_days} days` : "None"}</dd>
          <dt>Subscriptions</dt><dd>{fmtNumber(p.subscriptions)} across {p.versions} version(s)</dd>
        </dl>
      </Panel>

      {editing ? (
        <Panel>
          <h2 className="mb-2 font-semibold">Publish version {p.latest_version + 1}</h2>
          <Alert tone="info">Existing subscriptions keep the version they use. Move individual organizations to this version from their subscription page.</Alert>
          <form className="grid gap-4 mt-3" onSubmit={async (e) => {
            e.preventDefault(); setBusy(true); setErr(null);
            try {
              const priced = meta.price_amount !== "";
              await papi(`/plans/${id}/versions`, { method: "POST", body: { billing_interval: meta.billing_interval, price_amount: priced ? meta.price_amount : null, price_currency: priced ? meta.price_currency : null,
                support_level: meta.support_level, trial_days: meta.trial_days === "" ? null : Number(meta.trial_days), notes: meta.notes || null, entitlements: toBody(ents) } });
              notify("New version published."); setEditing(false); reload();
            } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
          }}>
            <div className="a-grid2">
              <Field label="Billing period">{(i) => <select id={i} value={meta.billing_interval} onChange={(e) => setMeta({ ...meta, billing_interval: e.target.value })}><option value="monthly">Monthly</option><option value="annual">Annual</option><option value="none">None</option></select>}</Field>
              <Field label="Price (exact decimal)">{(i) => <input id={i} value={meta.price_amount} onChange={(e) => setMeta({ ...meta, price_amount: e.target.value })} inputMode="decimal" />}</Field>
              <Field label="Currency">{(i) => <input id={i} value={meta.price_currency} onChange={(e) => setMeta({ ...meta, price_currency: e.target.value })} disabled={meta.price_amount === ""} />}</Field>
              <Field label="Support level">{(i) => <select id={i} value={meta.support_level} onChange={(e) => setMeta({ ...meta, support_level: e.target.value })}>{["community", "standard", "priority", "dedicated"].map((v) => <option key={v}>{v}</option>)}</select>}</Field>
              <Field label="Trial days">{(i) => <input id={i} type="number" min={0} max={365} value={meta.trial_days} onChange={(e) => setMeta({ ...meta, trial_days: e.target.value })} />}</Field>
            </div>
            <Field label="Notes on this version">{(i) => <textarea id={i} value={meta.notes} onChange={(e) => setMeta({ ...meta, notes: e.target.value })} maxLength={1000} />}</Field>
            <EntitlementEditor value={ents} onChange={setEnts} />
            {err ? <Alert tone="danger">{err}</Alert> : null}
            <div className="a-actions"><Button type="submit" disabled={busy}>{busy ? "Publishing…" : "Publish version"}</Button><Button type="button" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button></div>
          </form>
        </Panel>
      ) : null}

      {data.versions.map((v) => (
        <Panel key={v.id}>
          <h2 className="mb-1 font-semibold">Version {v.version}</h2>
          <p className="a-hint mb-2">Published {fmtDateTime(v.created_at)} · {v.subscriptions} subscription(s) {v.subscriptions ? "· permanent (in use)" : "· unused"}{v.notes ? ` · ${v.notes}` : ""}</p>
          <DataTable caption={`Version ${v.version} entitlements`} loading={false} error={null} empty="This version grants nothing." rowKey={(e) => e[0]} rows={Object.entries(v.entitlements)}
            columns={[{ key: "k", header: "Entitlement", render: (e) => e[0] }, { key: "v", header: "Value", render: (e) => e[1].unlimited ? "Unlimited" : e[1].enabled !== null && e[1].enabled !== undefined ? (e[1].enabled ? "Included" : "Not included") : fmtNumber(e[1].value) }]} />
        </Panel>
      ))}
      <ActionDialog open={dlg === "archive"} title={`Archive ${p.name}?`} danger submitLabel="Archive plan" confirmWord={p.key}
        description="An archived plan can no longer be assigned or receive versions. Existing subscriptions keep working on the version they use."
        onSubmit={async ({ reason, confirm }) => { await papi(`/plans/${id}/archive`, { method: "POST", body: { reason, confirm } }); notify("Plan archived."); setDlg(null); reload(); }} onClose={() => setDlg(null)} />
    </>
  );
}
