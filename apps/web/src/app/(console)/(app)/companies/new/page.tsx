"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { Empty, ErrorBox, Field, Loading, useToast } from "@/components/kit";
import { ORG_ROLE_LABEL, errorText, fmtNumber, tapi, useTenantApi, type Page } from "@/lib/tenant";

type Accountant = Schemas["AccountantRow"];
type Settings = Schemas["OrgSettingsOut"];
interface Pick { on: boolean; type: "primary" | "secondary" | "reviewer"; responsibility: string }

const STEPS = ["Basic information", "Accounting information", "Assign accountants", "Review"];

export default function NewCompanyPage() {
  const { orgId, can, href } = useOrg();
  const router = useRouter();
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const org = useTenantApi<Settings>(orgId, can("organization.read") ? `/orgs/${orgId}/settings` : null);
  const team = useTenantApi<Page<Accountant>>(orgId, can("members.read") ? `/orgs/${orgId}/accountants?status=active&limit=100` : null);
  const [f, setF] = useState({
    name: "", code: "", legal_name: "", registration_number: "", company_type: "", industry: "", city: "", address_line: "",
    contact_email: "", contact_phone: "", country: "", base_currency: "",
    fy_month: "1", fy_day: "1", basis: "", vat_registered: false, vat_number: "", tax_jurisdiction: "",
  });
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const set = (k: keyof typeof f, v: string | boolean) => setF((p) => ({ ...p, [k]: v }));
  // Defaults come from the organization settings once, and only while the field is untouched.
  const country = f.country || org.data?.country || "";
  const currency = f.base_currency || org.data?.default_currency || "";

  if (!can("companies.create")) return <div className="grid gap-4"><CcHeader title="Create company" crumbs={[{ label: "My Companies", href: href("/companies") }, { label: "New" }]} /><Alert tone="info">Your role cannot create companies.</Alert></div>;

  const step1Ok = f.name.trim() && /^[A-Za-z0-9_-]{1,32}$/.test(f.code) && /^[A-Z]{2}$/.test(country) && /^[A-Z]{3}$/.test(currency);
  const step2Ok = !(f.vat_number.trim() && !f.vat_registered);
  const chosen = Object.entries(picks).filter(([, p]) => p.on);
  const primaries = chosen.filter(([, p]) => p.type === "primary").length;
  const step3Ok = primaries <= 1;
  const ok = [step1Ok, step2Ok, step3Ok, true];
  const opt = (v: string) => (v.trim() ? v.trim() : null);

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const created = await tapi<Schemas["CompanyOut"]>(orgId, `/orgs/${orgId}/companies`, {
        method: "POST",
        body: {
          name: f.name.trim(), code: f.code.trim(), country, base_currency: currency,
          legal_name: opt(f.legal_name), registration_number: opt(f.registration_number), company_type: opt(f.company_type), industry: opt(f.industry),
          city: opt(f.city), address_line: opt(f.address_line), contact_email: opt(f.contact_email), contact_phone: opt(f.contact_phone),
          financial_year_start_month: Number(f.fy_month), financial_year_start_day: Number(f.fy_day),
          accounting_basis: f.basis || null, vat_registered: f.vat_registered, vat_number: f.vat_registered ? opt(f.vat_number) : null,
          tax_jurisdiction: opt(f.tax_jurisdiction),
          assignments: chosen.map(([user_id, p]) => ({ user_id, assignment_type: p.type, responsibility: opt(p.responsibility) })),
        },
      });
      toast(`Company “${created.name}” created.`);
      router.push(href(`/companies/${created.id}`));
    } catch (ex) {
      setErr(errorText(ex));
      setBusy(false);
    }
  };

  const months = Array.from({ length: 12 }, (_, i) => ({ v: String(i + 1), l: new Date(Date.UTC(2000, i, 1)).toLocaleString("en-GB", { month: "long", timeZone: "UTC" }) }));
  return (
    <div className="grid gap-4">
      <CcHeader title="Create company" crumbs={[{ label: "My Companies", href: href("/companies") }, { label: "New" }]} />
      <ol className="a-steps" aria-label="Progress" style={{ display: "flex", gap: "1rem", flexWrap: "wrap", listStyle: "none", padding: 0 }}>
        {STEPS.map((s, i) => <li key={s} aria-current={i === step ? "step" : undefined} style={{ fontWeight: i === step ? 700 : 400 }}>{i + 1}. {s}</li>)}
      </ol>
      <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (step < 3) setStep(step + 1); else void submit(); }}>
        {step === 0 ? (
          <Panel className="grid gap-3">
            <h2>Basic information</h2>
            <Field label="Company display name *">{(id) => <input id={id} value={f.name} onChange={(e) => set("name", e.target.value)} required maxLength={200} />}</Field>
            <Field label="Company code *" hint="Short unique identifier within your organization: letters, digits, - and _.">{(id) => <input id={id} value={f.code} onChange={(e) => set("code", e.target.value)} required maxLength={32} pattern="[A-Za-z0-9_\-]{1,32}" />}</Field>
            <Field label="Legal name">{(id) => <input id={id} value={f.legal_name} onChange={(e) => set("legal_name", e.target.value)} maxLength={200} />}</Field>
            <Field label="Registration number">{(id) => <input id={id} value={f.registration_number} onChange={(e) => set("registration_number", e.target.value)} maxLength={64} />}</Field>
            <Field label="Company type">{(id) => <input id={id} value={f.company_type} onChange={(e) => set("company_type", e.target.value)} maxLength={60} />}</Field>
            <Field label="Industry">{(id) => <input id={id} value={f.industry} onChange={(e) => set("industry", e.target.value)} maxLength={100} />}</Field>
            <Field label="Country (ISO 3166 code) *" hint="Two capital letters, e.g. OM.">{(id) => <input id={id} value={country} onChange={(e) => set("country", e.target.value.toUpperCase())} required maxLength={2} pattern="[A-Z]{2}" />}</Field>
            <Field label="City">{(id) => <input id={id} value={f.city} onChange={(e) => set("city", e.target.value)} maxLength={100} />}</Field>
            <Field label="Address">{(id) => <input id={id} value={f.address_line} onChange={(e) => set("address_line", e.target.value)} maxLength={300} />}</Field>
            <Field label="Contact e-mail">{(id) => <input id={id} type="email" value={f.contact_email} onChange={(e) => set("contact_email", e.target.value)} />}</Field>
            <Field label="Contact phone">{(id) => <input id={id} value={f.contact_phone} onChange={(e) => set("contact_phone", e.target.value)} maxLength={40} />}</Field>
          </Panel>
        ) : null}
        {step === 1 ? (
          <Panel className="grid gap-3">
            <h2>Accounting information</h2>
            <p className="a-hint">These settings belong to this company alone. Tax rules are not built into the system: the jurisdiction you enter is configuration that later phases interpret.</p>
            <Field label="Base currency (ISO 4217 code) *">{(id) => <input id={id} value={currency} onChange={(e) => set("base_currency", e.target.value.toUpperCase())} required maxLength={3} pattern="[A-Z]{3}" />}</Field>
            <Field label="Financial year starts in">{(id) => <select id={id} value={f.fy_month} onChange={(e) => set("fy_month", e.target.value)}>{months.map((m) => <option key={m.v} value={m.v}>{m.l}</option>)}</select>}</Field>
            <Field label="Day of month">{(id) => <input id={id} type="number" min={1} max={31} value={f.fy_day} onChange={(e) => set("fy_day", e.target.value)} />}</Field>
            <Field label="Accounting basis">{(id) => <select id={id} value={f.basis} onChange={(e) => set("basis", e.target.value)}><option value="">Not specified</option><option value="accrual">Accrual</option><option value="cash">Cash</option></select>}</Field>
            <label className="flex items-center gap-2"><input type="checkbox" checked={f.vat_registered} onChange={(e) => { set("vat_registered", e.target.checked); if (!e.target.checked) set("vat_number", ""); }} /> VAT registered</label>
            {f.vat_registered ? <Field label="VAT number">{(id) => <input id={id} value={f.vat_number} onChange={(e) => set("vat_number", e.target.value)} maxLength={64} />}</Field> : null}
            <Field label="Tax jurisdiction" hint="Free text for now; it is not interpreted yet.">{(id) => <input id={id} value={f.tax_jurisdiction} onChange={(e) => set("tax_jurisdiction", e.target.value)} maxLength={100} />}</Field>
          </Panel>
        ) : null}
        {step === 2 ? (
          <Panel className="grid gap-3">
            <h2>Assign accountants</h2>
            <p className="a-hint">Optional. Assignments decide which companies an accountant can see. You can change them later.</p>
            {!can("assignments.manage") ? <Alert tone="info">Your role cannot manage assignments; you can assign accountants after creation if someone with that permission does it.</Alert> : null}
            {team.loading && !team.data ? <Loading what="accountants" /> : team.error && !team.data ? <ErrorBox message={team.error} onRetry={team.reload} /> : team.data?.items.length === 0 ? <Empty>No active accountants exist yet. Invite one from Invitations, then assign them to this company.</Empty> : (
              <ul style={{ listStyle: "none", padding: 0 }} className="grid gap-3">
                {team.data?.items.map((a) => {
                  const p = picks[a.user_id] ?? { on: false, type: "secondary", responsibility: "" };
                  const upd = (patch: Partial<Pick>) => setPicks((s) => ({ ...s, [a.user_id]: { ...p, ...patch } }));
                  return (
                    <li key={a.user_id} className="a-toolbar">
                      <label className="flex items-center gap-2"><input type="checkbox" disabled={!can("assignments.manage")} checked={p.on} onChange={(e) => upd({ on: e.target.checked })} /> <span>{a.display_name} <span className="a-hint">· {ORG_ROLE_LABEL[a.role] ?? a.role} · {fmtNumber(a.active_company_count)} companies</span></span></label>
                      {p.on ? (
                        <>
                          <Field label={`Assignment type for ${a.display_name}`}>{(id) => <select id={id} value={p.type} onChange={(e) => upd({ type: e.target.value as Pick["type"] })}><option value="primary">Primary</option><option value="secondary">Secondary</option><option value="reviewer">Reviewer</option></select>}</Field>
                          <Field label={`Responsibility for ${a.display_name}`}>{(id) => <input id={id} value={p.responsibility} maxLength={200} onChange={(e) => upd({ responsibility: e.target.value })} />}</Field>
                        </>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
            {!step3Ok ? <Alert tone="danger">Only one primary accountant can be chosen.</Alert> : null}
          </Panel>
        ) : null}
        {step === 3 ? (
          <Panel className="grid gap-3">
            <h2>Review</h2>
            <dl className="a-dl">
              <dt>Name</dt><dd>{f.name}</dd><dt>Code</dt><dd>{f.code}</dd>
              {f.legal_name ? <><dt>Legal name</dt><dd>{f.legal_name}</dd></> : null}
              <dt>Country</dt><dd>{country}</dd><dt>Base currency</dt><dd>{currency}</dd>
              <dt>Financial year starts</dt><dd>{months[Number(f.fy_month) - 1]?.l} {f.fy_day}</dd>
              <dt>Accounting basis</dt><dd>{f.basis || "Not specified"}</dd>
              <dt>VAT</dt><dd>{f.vat_registered ? `Registered${f.vat_number ? ` (${f.vat_number})` : ""}` : "Not registered"}</dd>
              <dt>Tax jurisdiction</dt><dd>{f.tax_jurisdiction || "—"}</dd>
              <dt>Accountants</dt><dd>{chosen.length ? chosen.map(([id, p]) => `${team.data?.items.find((a) => a.user_id === id)?.display_name ?? id} (${p.type})`).join(", ") : "None yet"}</dd>
            </dl>
            <p className="a-hint">Creating the company reserves one unit of your company limit. Its workspace structure is configured separately; nothing is created automatically.</p>
          </Panel>
        ) : null}
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <div className="a-actions">
          {step > 0 ? <Button type="button" variant="secondary" onClick={() => setStep(step - 1)} disabled={busy}>Back</Button> : null}
          <Button type="submit" disabled={!ok[step] || busy}>{step < 3 ? "Next" : busy ? "Creating…" : "Create company"}</Button>
        </div>
      </form>
    </div>
  );
}
