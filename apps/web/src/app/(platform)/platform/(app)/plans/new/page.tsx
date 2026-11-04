"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { Field, PageHeader, useToast } from "@/components/kit";
import { errorText, papi, type S } from "@/lib/platform";
import { EntitlementEditor, toBody, type EntState } from "../entitlement-editor";

export default function NewPlan() {
  const router = useRouter();
  const notify = useToast();
  const [f, setF] = useState({ key: "", name: "", description: "", billing_interval: "monthly", price_amount: "", price_currency: "OMR", support_level: "standard", trial_days: "" });
  const [ents, setEnts] = useState<EntState>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <PageHeader title="New plan" crumbs={[{ label: "Subscription Plans", href: "/platform/plans" }, { label: "New" }]} />
      <Panel>
        <form className="grid gap-4" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true); setErr(null);
          try {
            const priced = f.price_amount !== "";
            const d = await papi<S["PlanDetail"]>("/plans", { method: "POST", body: {
              key: f.key, name: f.name, description: f.description || null,
              version: { billing_interval: f.billing_interval, price_amount: priced ? f.price_amount : null, price_currency: priced ? f.price_currency : null,
                support_level: f.support_level, trial_days: f.trial_days === "" ? null : Number(f.trial_days), entitlements: toBody(ents) },
            } });
            notify("Plan created as inactive. Activate it when it is ready to be assigned.");
            router.push(`/platform/plans/${d.plan.id}`);
          } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
        }}>
          <Alert tone="info">A new plan is created <strong>inactive</strong>: it cannot be assigned or offered until you activate it. Once any subscription uses a version, that version is permanent; later edits publish a new version.</Alert>
          <div className="a-grid2">
            <Field label="Key (lowercase letters, digits, underscore)">{(id) => <input id={id} value={f.key} onChange={set("key")} pattern="[a-z0-9_]{2,40}" required />}</Field>
            <Field label="Name">{(id) => <input id={id} value={f.name} onChange={set("name")} required minLength={2} />}</Field>
            <Field label="Billing period">{(id) => <select id={id} value={f.billing_interval} onChange={set("billing_interval")}><option value="monthly">Monthly</option><option value="annual">Annual</option><option value="none">None</option></select>}</Field>
            <Field label="Price (optional, exact decimal)">{(id) => <input id={id} inputMode="decimal" pattern="\d+(\.\d{1,3})?" value={f.price_amount} onChange={set("price_amount")} />}</Field>
            <Field label="Price currency">{(id) => <input id={id} value={f.price_currency} onChange={set("price_currency")} pattern="[A-Z]{3}" disabled={f.price_amount === ""} />}</Field>
            <Field label="Support level">{(id) => <select id={id} value={f.support_level} onChange={set("support_level")}>{["community", "standard", "priority", "dedicated"].map((v) => <option key={v}>{v}</option>)}</select>}</Field>
            <Field label="Trial days (blank = no trial)">{(id) => <input id={id} type="number" min={0} max={365} value={f.trial_days} onChange={set("trial_days")} />}</Field>
          </div>
          <Field label="Description">{(id) => <textarea id={id} value={f.description} onChange={set("description")} maxLength={1000} />}</Field>
          <EntitlementEditor value={ents} onChange={setEnts} />
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <div><Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create plan"}</Button></div>
        </form>
      </Panel>
    </>
  );
}
