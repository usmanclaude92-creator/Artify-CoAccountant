"use client";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { ErrorBox, Field, Loading, Status, useToast } from "@/components/kit";
import { errorText, label, tapi, useTenantApi } from "@/lib/tenant";

type Settings = Schemas["OrgSettingsOut"];
const PREF_LABEL: Record<string, string> = {};

export default function SettingsPage() {
  const { orgId } = useOrg();
  const { data, error, reload } = useTenantApi<Settings>(orgId, `/orgs/${orgId}/settings`);
  if (error && !data) return <div className="grid gap-4"><CcHeader title="Organization Settings" /><ErrorBox message={error} onRetry={reload} /></div>;
  if (!data) return <div className="grid gap-4"><CcHeader title="Organization Settings" /><Loading what="settings" /></div>;
  return <Form key={`${data.id}-${JSON.stringify(data)}`} s={data} onSaved={reload} />;
}

function Form({ s, onSaved }: { s: Settings; onSaved: () => void }) {
  const { orgId, can } = useOrg();
  const toast = useToast();
  const editable = can("organization.settings.update");
  const [f, setF] = useState({ contact_email: s.contact_email ?? "", contact_phone: s.contact_phone ?? "", address_line: s.address_line ?? "", city: s.city ?? "", timezone: s.timezone, default_currency: s.default_currency });
  const [prefs, setPrefs] = useState(s.notification_preferences);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      await tapi(orgId, `/orgs/${orgId}/settings`, { method: "PATCH", body: { contact_email: f.contact_email.trim() || null, contact_phone: f.contact_phone.trim() || null, address_line: f.address_line.trim() || null, city: f.city.trim() || null, timezone: f.timezone, default_currency: f.default_currency, notification_preferences: prefs } });
      toast("Settings saved."); onSaved();
    } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
  };
  return (
    <div className="grid gap-4">
      <CcHeader title="Organization Settings" />
      <p className="a-hint">These settings belong to the whole organization. Each client company has its own settings on its company page.</p>
      <Panel className="grid gap-2">
        <h2>Identity</h2>
        <dl className="a-dl">
          <dt>Legal name</dt><dd>{s.legal_name}</dd><dt>Display name</dt><dd>{s.display_name}</dd>
          <dt>Identifier</dt><dd>{s.slug}</dd><dt>Type</dt><dd>{label(s.type)}</dd>
          <dt>Country</dt><dd>{s.country}</dd><dt>Status</dt><dd><Status value={s.status} /></dd>
        </dl>
        <p className="a-hint">Legal name, identifier, type and country are managed by Artify; contact support to change them.</p>
      </Panel>
      <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <Panel className="grid gap-3">
          <h2>Contact and regional settings</h2>
          <Field label="Contact e-mail">{(id) => <input id={id} type="email" disabled={!editable} value={f.contact_email} onChange={(e) => setF({ ...f, contact_email: e.target.value })} />}</Field>
          <Field label="Contact phone">{(id) => <input id={id} disabled={!editable} value={f.contact_phone} maxLength={40} onChange={(e) => setF({ ...f, contact_phone: e.target.value })} />}</Field>
          <Field label="Address">{(id) => <input id={id} disabled={!editable} value={f.address_line} maxLength={300} onChange={(e) => setF({ ...f, address_line: e.target.value })} />}</Field>
          <Field label="City">{(id) => <input id={id} disabled={!editable} value={f.city} maxLength={100} onChange={(e) => setF({ ...f, city: e.target.value })} />}</Field>
          <Field label="Time zone (IANA name)">{(id) => <input id={id} disabled={!editable} value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} />}</Field>
          <Field label="Default currency for new companies" hint="Only a default: each company keeps its own base currency.">{(id) => <input id={id} disabled={!editable} value={f.default_currency} pattern="[A-Z]{3}" maxLength={3} onChange={(e) => setF({ ...f, default_currency: e.target.value.toUpperCase() })} />}</Field>
        </Panel>
        <Panel className="grid gap-2">
          <h2>Notification preferences</h2>
          <Alert tone="info">Preferences are stored, but no notifications are sent yet: the notification system is not part of this release.</Alert>
          {Object.entries(prefs).map(([k, v]) => (
            <label key={k} className="flex items-center gap-2"><input type="checkbox" disabled={!editable} checked={v} onChange={(e) => setPrefs({ ...prefs, [k]: e.target.checked })} /> {PREF_LABEL[k] ?? label(k)}</label>
          ))}
        </Panel>
        <Panel className="grid gap-1">
          <h2>E-mail delivery</h2>
          <p><Status value={s.email_delivery.configured ? "active" : "not_configured"} text={s.email_delivery.configured ? "Configured" : "Not configured"} /></p>
          <p className="a-hint">{String(s.email_delivery.note)}</p>
        </Panel>
        {err ? <Alert tone="danger">{err}</Alert> : null}
        {editable ? <div><Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save settings"}</Button></div> : <Alert tone="info">Your role can view but not change organization settings.</Alert>}
      </form>
    </div>
  );
}
