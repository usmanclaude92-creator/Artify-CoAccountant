"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { ActionDialog, DataTable, ErrorBox, Field, Loading, Meter, PageHeader, Status, Tabs, useToast } from "@/components/platform/kit";
import { fmtDate, fmtDateTime, fmtNumber, label, papi, useApi, type Page, type S } from "@/lib/platform";

export default function OrganizationDetail() {
  const { id } = useParams<{ id: string }>();
  const can = useCan();
  const notify = useToast();
  const { data, error, loading, reload } = useApi<S["OrgDetail"]>(`/organizations/${id}`);
  const [tab, setTab] = useState("summary");
  const [dlg, setDlg] = useState<"suspend" | "reactivate" | "edit" | "override" | null>(null);
  const [ending, setEnding] = useState<string | null>(null);
  const [edit, setEdit] = useState({ display_name: "", legal_name: "" });
  const [ov, setOv] = useState({ key: "limit.companies", value: "", unlimited: false, expires: "" });
  const history = useApi<S["TimelineEntry"][]>(tab === "history" ? `/organizations/${id}/history` : null);
  const companies = useApi<Page<S["CompanyRow"]>>(tab === "companies" ? `/companies?organization_id=${id}&limit=50` : null);
  const overrides = useApi<S["OverrideOut"][]>(tab === "overrides" && can("platform.subscriptions.read") ? `/organizations/${id}/overrides` : null);
  if (error) return <><PageHeader title="Organization" crumbs={[{ label: "Organizations", href: "/platform/organizations" }]} /><ErrorBox message={error} onRetry={reload} /></>;
  if (!data || loading) return <><PageHeader title="Organization" /><Loading what="organization" /></>;
  const o = data.organization;
  const sub = data.subscription;
  const crumbs = [{ label: label(o.type) + "s", href: `/platform/organizations?type=${o.type}` }, { label: o.display_name }];
  const act = (path: string, body: unknown, ok: string) => async () => { await papi(path, { method: "POST", body }); notify(ok); setDlg(null); reload(); };
  return (
    <>
      <PageHeader title={o.display_name} crumbs={crumbs} actions={<>
        <Status value={o.status} />
        {can("platform.organizations.manage") ? <Button variant="secondary" onClick={() => { setEdit({ display_name: o.display_name, legal_name: o.legal_name }); setDlg("edit"); }}>Edit profile</Button> : null}
        {can("platform.support.request") ? <Link href={`/platform/support?organization=${o.id}`}><Button variant="secondary">Request support access</Button></Link> : null}
        {can("platform.organizations.suspend") ? (o.status === "active"
          ? <Button className="a-btn--danger" onClick={() => setDlg("suspend")}>Suspend</Button>
          : o.status === "suspended" ? <Button onClick={() => setDlg("reactivate")}>Reactivate</Button> : null) : null}
      </>} />
      <Tabs active={tab} onChange={setTab} tabs={[{ id: "summary", label: "Summary" }, { id: "subscription", label: "Subscription" }, { id: "usage", label: "Usage & limits" }, { id: "companies", label: "Client companies" }, { id: "overrides", label: "Limit overrides" }, { id: "history", label: "History" }]} />

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="grid gap-4">
        {tab === "summary" ? (
          <div className="a-grid2">
            <Panel>
              <h2 className="mb-2 font-semibold">Profile</h2>
              <dl className="a-dl">
                <dt>Identifier</dt><dd>{o.id}</dd><dt>Slug</dt><dd>{o.slug}</dd><dt>Legal name</dt><dd>{o.legal_name}</dd>
                <dt>Type</dt><dd>{label(o.type)}</dd><dt>Country</dt><dd>{o.country}</dd><dt>Currency</dt><dd>{o.default_currency}</dd>
                <dt>Language</dt><dd>{o.locale === "ar" ? "Arabic" : "English"}</dd><dt>Registered</dt><dd>{fmtDateTime(o.created_at)}</dd>
              </dl>
            </Panel>
            <Panel>
              <h2 className="mb-2 font-semibold">Owner and size</h2>
              <dl className="a-dl">
                <dt>Owner</dt><dd>{o.owner_name ?? "Invitation pending"}</dd><dt>Owner e-mail</dt><dd>{o.owner_email ?? "—"}</dd>
                <dt>Users</dt><dd>{fmtNumber(o.user_count)}</dd><dt>Accountants</dt><dd>{fmtNumber(o.accountant_count)}</dd>
                <dt>Client companies</dt><dd>{fmtNumber(o.company_count)} active of {fmtNumber(o.company_total)}</dd>
              </dl>
              <p className="a-hint mt-3">Contact details beyond the owner&apos;s e-mail are not collected at registration. Customer documents and financial records are never shown here; special access follows the audited support-access process.</p>
            </Panel>
          </div>
        ) : null}

        {tab === "subscription" ? (
          <Panel>
            {sub ? (
              <dl className="a-dl">
                <dt>Plan</dt><dd>{sub.plan_name} (v{sub.plan_version})</dd><dt>Status</dt><dd><Status value={sub.status} /></dd>
                <dt>Period start</dt><dd>{fmtDate(sub.current_period_start)}</dd><dt>Renewal / period end</dt><dd>{fmtDate(sub.current_period_end)}</dd>
                <dt>Trial ends</dt><dd>{fmtDate(sub.trial_ends_at)}</dd><dt>Cancelled</dt><dd>{sub.cancelled_at ? fmtDate(sub.cancelled_at) : sub.cancel_at_period_end ? "Scheduled at period end" : "No"}</dd>
              </dl>
            ) : <p className="a-hint">No subscription.</p>}
            {sub && can("platform.subscriptions.read") ? <p className="mt-3"><Link className="a-link" href={`/platform/subscriptions/${sub.id}`}>Manage subscription and see its history</Link></p> : null}
          </Panel>
        ) : null}

        {tab === "usage" ? (
          <Panel>
            <DataTable caption="Usage against plan limits" loading={false} error={null} empty="No entitlements." rowKey={(u) => u.key} rows={data.usage}
              columns={[
                { key: "k", header: "Entitlement", render: (u) => <><strong>{u.key}</strong><span className="a-hint" style={{ display: "block" }}>{u.description}</span></> },
                { key: "c", header: "Current", numeric: true, render: (u) => u.enabled !== null ? (u.enabled ? "on" : "off") : fmtNumber(u.current) },
                { key: "l", header: "Limit", numeric: true, render: (u) => u.unlimited ? "Unlimited" : fmtNumber(u.limit) },
                { key: "m", header: "Used", render: (u) => <Meter percent={u.percent} status={u.status} /> },
                { key: "s", header: "Status", render: (u) => <Status value={u.status} text={u.status === "not_metered" ? "Not metered yet" : undefined} /> },
                { key: "e", header: "Enforced", render: (u) => u.enforced_now ? "Yes" : "When feature ships" },
                { key: "src", header: "Source", render: (u) => u.source },
              ]} />
          </Panel>
        ) : null}

        {tab === "companies" ? (
          <Panel>
            <DataTable caption="Client companies of this organization" loading={companies.loading} error={companies.error} empty="This organization has no client companies." rowKey={(c) => c.id} rows={companies.data?.items}
              columns={[
                { key: "n", header: "Company", render: (c) => c.name }, { key: "c", header: "Code", render: (c) => c.code },
                { key: "co", header: "Country", render: (c) => c.country }, { key: "s", header: "Status", render: (c) => <Status value={c.status} /> },
                { key: "a", header: "Accountants", numeric: true, render: (c) => c.accountant_count }, { key: "u", header: "Users", numeric: true, render: (c) => c.user_count },
                { key: "d", header: "Created", render: (c) => fmtDate(c.created_at) },
              ]} />
          </Panel>
        ) : null}

        {tab === "overrides" ? (
          <Panel>
            <div className="a-pagehead"><h2 className="font-semibold">Negotiated limit overrides</h2>
              {can("platform.entitlements.override") ? <Button onClick={() => setDlg("override")}>Add override</Button> : null}</div>
            <DataTable caption="Limit overrides" loading={overrides.loading} error={overrides.error} empty="No overrides. The organization uses its plan's limits." rowKey={(x) => x.id} rows={overrides.data ?? undefined}
              columns={[
                { key: "k", header: "Entitlement", render: (x) => x.entitlement_key },
                { key: "v", header: "Value", render: (x) => x.value.unlimited ? "Unlimited" : x.value.enabled !== null ? String(x.value.enabled) : fmtNumber(x.value.value) },
                { key: "r", header: "Reason", render: (x) => x.reason }, { key: "e", header: "Expires", render: (x) => x.expires_at ? fmtDateTime(x.expires_at) : "Never" },
                { key: "s", header: "State", render: (x) => <Status value={x.active ? "active" : "expired"} /> },
                { key: "a", header: "", render: (x) => x.active && can("platform.entitlements.override") ? <Button variant="secondary" className="a-btn--sm" onClick={() => setEnding(x.id)}>End now</Button> : null },
              ]} />
          </Panel>
        ) : null}

        {tab === "history" ? (
          <Panel>
            <DataTable caption="Organization history" loading={history.loading} error={history.error} empty="No history." rowKey={(h) => h.at + h.title} rows={history.data ?? undefined}
              columns={[
                { key: "w", header: "When", render: (h) => fmtDateTime(h.at) }, { key: "k", header: "Kind", render: (h) => label(h.kind) },
                { key: "t", header: "Event", render: (h) => h.title }, { key: "d", header: "Reason", render: (h) => h.detail ?? "" },
              ]} />
          </Panel>
        ) : null}
      </div>

      <ActionDialog open={dlg === "suspend"} title={`Suspend ${o.display_name}?`} danger submitLabel="Suspend organization" confirmWord={o.slug}
        description="The organization becomes read-only: members can sign in and read, but cannot create or change anything. No data is deleted. You can reactivate at any time."
        onSubmit={({ reason, confirm }) => act(`/organizations/${id}/suspend`, { reason, confirm }, "Organization suspended.")()} onClose={() => setDlg(null)} />
      <ActionDialog open={dlg === "reactivate"} title={`Reactivate ${o.display_name}?`} submitLabel="Reactivate" confirmWord={o.slug}
        onSubmit={({ reason, confirm }) => act(`/organizations/${id}/reactivate`, { reason, confirm }, "Organization reactivated.")()} onClose={() => setDlg(null)} />
      <ActionDialog open={dlg === "edit"} title="Edit organization profile" submitLabel="Save" needsReason={false}
        onSubmit={async () => { await papi(`/organizations/${id}`, { method: "PATCH", body: edit }); notify("Profile updated."); setDlg(null); reload(); }} onClose={() => setDlg(null)}>
        <Field label="Display name">{(i) => <input id={i} value={edit.display_name} onChange={(e) => setEdit({ ...edit, display_name: e.target.value })} required minLength={2} />}</Field>
        <Field label="Legal name">{(i) => <input id={i} value={edit.legal_name} onChange={(e) => setEdit({ ...edit, legal_name: e.target.value })} required minLength={2} />}</Field>
      </ActionDialog>
      <ActionDialog open={!!ending} title="End this override now?" submitLabel="End override" danger
        description="The organization returns to the limits of its plan immediately."
        onSubmit={async ({ reason }) => { await papi(`/organizations/${id}/overrides/${ending}/expire`, { method: "POST", body: { reason } }); notify("Override ended."); setEnding(null); overrides.reload(); reload(); }} onClose={() => setEnding(null)} />
      <ActionDialog open={dlg === "override"} title="Add a limit override" submitLabel="Add override"
        description="A time-limited exception to this organization's plan. It is recorded with your reason in the audit log."
        onSubmit={async ({ reason }) => {
          const flag = data.usage.find((u) => u.key === ov.key)?.enabled !== null;
          const value = flag ? { enabled: ov.value === "true" } : ov.unlimited ? { unlimited: true } : { value: Number(ov.value) };
          await papi(`/organizations/${id}/overrides`, { method: "POST", body: { entitlement_key: ov.key, value, reason, expires_at: ov.expires ? new Date(ov.expires).toISOString() : null } });
          notify("Override added."); setDlg(null); reload(); overrides.reload();
        }} onClose={() => setDlg(null)}>
        <Field label="Entitlement">{(i) => <select id={i} value={ov.key} onChange={(e) => setOv({ ...ov, key: e.target.value })}>{data.usage.map((u) => <option key={u.key} value={u.key}>{u.key}</option>)}</select>}</Field>
        <Field label="Value (number, or true/false for a feature)">{(i) => <input id={i} value={ov.value} onChange={(e) => setOv({ ...ov, value: e.target.value })} disabled={ov.unlimited} required={!ov.unlimited} />}</Field>
        <label className="flex items-center gap-2"><input type="checkbox" checked={ov.unlimited} onChange={(e) => setOv({ ...ov, unlimited: e.target.checked })} /> Unlimited</label>
        <Field label="Expires (optional)">{(i) => <input id={i} type="datetime-local" value={ov.expires} onChange={(e) => setOv({ ...ov, expires: e.target.value })} />}</Field>
      </ActionDialog>
    </>
  );
}
