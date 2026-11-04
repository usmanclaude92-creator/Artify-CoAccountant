"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { Field, PageHeader, useToast } from "@/components/platform/kit";
import { errorText, papi, useApi, type S } from "@/lib/platform";

export default function NewOrganization() {
  const router = useRouter();
  const notify = useToast();
  const plans = useApi<S["PlanRow"][]>("/plans?status=active");
  const [f, setF] = useState({ type: "accounting_firm", name: "", country: "OM", default_currency: "OMR", locale: "en", owner_email: "", plan_key: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <PageHeader title="New organization" crumbs={[{ label: "Organizations", href: "/platform/organizations" }, { label: "New" }]} />
      <Panel>
        <form className="grid gap-4" style={{ maxInlineSize: "34rem" }} onSubmit={async (e) => {
          e.preventDefault(); setBusy(true); setErr(null);
          try {
            const d = await papi<S["OrgDetail"]>("/organizations", { method: "POST", body: { ...f, plan_key: f.plan_key || null } });
            notify("Organization created. The owner has been e-mailed an invitation.");
            router.push(`/platform/organizations/${d.organization.id}`);
          } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
        }}>
          <Alert tone="info">Artify never sets a customer password. The owner receives an invitation, chooses their own password and must enrol two-factor authentication at first sign-in.</Alert>
          <Field label="Type">{(id) => <select id={id} value={f.type} onChange={set("type")}><option value="accounting_firm">Accounting firm</option><option value="independent_accountant">Independent accountant</option></select>}</Field>
          <Field label="Legal / display name">{(id) => <input id={id} value={f.name} onChange={set("name")} required minLength={2} />}</Field>
          <Field label="Owner e-mail address">{(id) => <input id={id} type="email" value={f.owner_email} onChange={set("owner_email")} required />}</Field>
          <Field label="Country (ISO code)">{(id) => <input id={id} value={f.country} onChange={set("country")} pattern="[A-Z]{2}" required />}</Field>
          <Field label="Default currency (ISO code)">{(id) => <input id={id} value={f.default_currency} onChange={set("default_currency")} pattern="[A-Z]{3}" required />}</Field>
          <Field label="Language">{(id) => <select id={id} value={f.locale} onChange={set("locale")}><option value="en">English</option><option value="ar">Arabic</option></select>}</Field>
          <Field label="Plan" hint="Leave on default to use the platform's sign-up plan.">
            {(id) => <select id={id} value={f.plan_key} onChange={set("plan_key")}><option value="">Platform default</option>{plans.data?.map((p) => <option key={p.id} value={p.key}>{p.name}</option>)}</select>}
          </Field>
          {err ? <Alert tone="danger">{err}</Alert> : null}
          <div><Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create organization"}</Button></div>
        </form>
      </Panel>
    </>
  );
}
