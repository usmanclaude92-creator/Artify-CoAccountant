import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { AppInfo, CompanyView, MirrorStatus, RootReport, ServerDevice, UpdateStatus } from "../bridge/types";
import { fmtBytes, fmtDateTime, ROLE_LABEL } from "../lib/format";
import { describe } from "../lib/errors";
import { useApp } from "../state/app";

const TABS = [["general", "General"], ["storage", "Storage"], ["sync", "Synchronization"], ["account", "Account"], ["devices", "Devices"], ["about", "About"]] as const;

export function Settings() {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("general");
  return (
    <section className="d-page" aria-labelledby="h-set">
      <h1 id="h-set">Settings</h1>
      <div className="a-tabs" role="tablist">{TABS.map(([id, l]) => <button key={id} role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls="set-panel" onClick={() => setTab(id)}>{l}</button>)}</div>
      <div role="tabpanel" id="set-panel" aria-labelledby={`tab-${tab}`} className="d-tabpanel">
        {tab === "general" ? <General /> : tab === "storage" ? <Storage /> : tab === "sync" ? <SyncSettings /> : tab === "account" ? <Account /> : tab === "devices" ? <Devices /> : <About />}
      </div>
    </section>
  );
}

function General() {
  const { settings, setSetting, bridge, org, fail } = useApp();
  const [companies, setCompanies] = useState<CompanyView[]>([]);
  useEffect(() => { if (org) bridge.companies(org.id).then((l) => setCompanies(l.items.filter((c) => c.status === "active"))).catch(fail); }, [bridge, org, fail]);
  if (!settings) return null;
  const set = (k: string, v: string) => void setSetting(k, v).catch(fail);
  return (
    <Panel className="d-form">
      <div className="a-field"><label htmlFor="s-theme">Theme</label>
        <select id="s-theme" value={settings.theme} onChange={(e) => set("theme", e.target.value)}><option value="system">Match Windows</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
      <div className="a-field"><label htmlFor="s-lang">Language</label>
        <select id="s-lang" value="en" disabled><option value="en">English</option></select>
        <span className="a-hint">Arabic and right-to-left layout are planned; the setting is prepared but not yet available in the desktop app.</span></div>
      <div className="a-field"><label htmlFor="s-def">Open this company at startup</label>
        <select id="s-def" value={settings.default_company_id} onChange={(e) => set("default_company_id", e.target.value)}>
          <option value="">None — I choose each time</option>{companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        <span className="a-hint">The server still verifies your access every time.</span></div>
      <label className="d-check"><input type="checkbox" checked={settings.notifications !== "false"} onChange={(e) => set("notifications", String(e.target.checked))} /> Show a message when a company’s workspace changes</label>
    </Panel>
  );
}

function Storage() {
  const { bridge, company, org, fail } = useApp();
  const [root, setRoot] = useState<RootReport | null | undefined>(undefined);
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  useEffect(() => { bridge.storageStatus().then(setRoot).catch((e) => { setRoot(null); fail(e); }); }, [bridge, fail]);
  const choose = async () => {
    setMsg(null);
    try {
      const picked = await bridge.storagePick();
      if (!picked) return;
      const r = await bridge.storageSetRoot(picked);
      setRoot(r);
      setMsg({ tone: "success", text: "Storage location saved. Existing files were not moved or changed." });
    } catch (e) { setMsg({ tone: "danger", text: describe(e) }); }
  };
  const prepare = async () => {
    if (!org || !company) return;
    try { setMsg({ tone: "success", text: `Company folder ready: ${await bridge.companyDir(org.id, company.id)}` }); } catch (e) { setMsg({ tone: "danger", text: describe(e) }); }
  };
  const statusText: Record<string, string> = { ok: "Ready", missing: "Missing — the folder or drive is not available", not_a_directory: "Not a folder", not_writable: "The app cannot write here", low_space: "Too little free space" };
  return (
    <Panel className="d-form">
      <h2>Local storage location</h2>
      {root === undefined ? <p role="status">Checking…</p> : root === null ? <p>No location chosen yet. Choose where Artify keeps local company folders. Each company gets its own subfolder.</p> : (
        <dl className="a-dl">
          <dt>Folder</dt><dd className="a-mono">{root.path}</dd>
          <dt>Status</dt><dd><StatusChip tone={root.status === "ok" ? "success" : "danger"}>{statusText[root.status] ?? root.status}</StatusChip></dd>
          <dt>Free space</dt><dd>{fmtBytes(root.free_bytes)}</dd>
        </dl>
      )}
      {root?.warnings.map((w) => <Alert key={w} tone="warning">{w}</Alert>)}
      <div className="d-actions">
        <Button onClick={() => void choose()}>{root ? "Change location…" : "Choose location…"}</Button>
        <Button variant="secondary" disabled={!root || root.status !== "ok" || !company} onClick={() => void prepare()}>Prepare folder for {company?.name ?? "the open company"}</Button>
      </div>
      {msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}
      <p className="a-hint">Changing the location never moves or deletes existing files. Local document folders mirror each company’s own workspace; the cloud copy stays the official one.</p>
      <Mirror ready={root?.status === "ok" || root?.status === "low_space"} />
    </Panel>
  );
}

const POLICY_LABEL: Record<string, string> = { on_demand: "Only documents I open or save", recent: "Documents from the last 30 days", all: "All documents I can see" };

/** Local copies of cloud documents. Files are placed by workspace folder id, never overwritten, and checked against the cloud fingerprint. */
function Mirror({ ready }: { ready: boolean }) {
  const { bridge, company, org, settings, setSetting, fail } = useApp();
  const [st, setSt] = useState<MirrorStatus | null>(null);
  const [msg, setMsg] = useState<{ tone: "success" | "danger" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const companyId = company?.id;
  const load = useCallback(() => { if (companyId) bridge.mirrorStatus(companyId).then(setSt).catch(fail); }, [bridge, companyId, fail]);
  useEffect(load, [load]);
  if (!company || !org) return <p className="a-hint">Open a company to manage its local copies.</p>;
  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setMsg(null);
    try { await fn(); setMsg({ tone: "success", text: label }); } catch (e) { setMsg({ tone: "danger", text: describe(e) }); } finally { setBusy(false); load(); }
  };
  return (
    <div className="d-form">
      <h3>Local copies of documents</h3>
      <div className="a-field"><label htmlFor="mp">Keep local copies of</label>
        <select id="mp" value={settings?.mirror_policy ?? "on_demand"} onChange={(e) => void setSetting("mirror_policy", e.target.value).catch((err) => setMsg({ tone: "danger", text: describe(err) }))}>
          {Object.entries(POLICY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
      <p>{st ? `${st.files} local cop${st.files === 1 ? "y" : "ies"} of ${company.name}${st.missing ? ` · ${st.missing} missing from the folder` : ""}` : "Checking…"}</p>
      <div className="d-actions">
        <Button variant="secondary" disabled={busy || !ready} onClick={() => void run("Local folders now match the cloud folder structure. Nothing was deleted.", () => bridge.mirrorReconcile(org.id, company.id))}>Match folders to the cloud</Button>
        <Button variant="secondary" disabled={busy || !ready || settings?.mirror_policy === "on_demand"} onClick={() => void run("Local copies updated.", () => bridge.mirrorSync(org.id, company.id))}>Download now</Button>
      </div>
      {!ready ? <p className="a-hint">Choose a storage location first.</p> : null}
      {msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}
    </div>
  );
}

function SyncSettings() {
  const { bridge, company, org, fail } = useApp();
  const [text, setText] = useState<string | null>(null);
  useEffect(() => { bridge.syncStatus(company?.id ?? null).then((s) => setText(`${s.connectivity.state === "online" ? "Online" : s.connectivity.state === "offline" ? "Offline" : "Not checked yet"} · last successful synchronization ${fmtDateTime(s.company?.last_success_at)}`)).catch(fail); }, [bridge, company, fail]);
  return (
    <Panel className="d-form">
      <p>{text ?? "Loading…"}</p>
      <div><Button disabled={!company || !org} onClick={() => org && company && void bridge.workspaceRefresh(org.id, company.id).then((r) => setText(r.summary ?? `Up to date (version ${r.version}).`)).catch((e) => setText(describe(e)))}>Synchronize now</Button></div>
      <p className="a-hint">Details are on the Synchronization page.</p>
    </Panel>
  );
}

function Account() {
  const { identity, org, orgs, chooseOrg, signOut } = useApp();
  const role = ROLE_LABEL[identity.memberships.find((m) => m.organization_id === org?.id)?.organization_role ?? ""] ?? "Member";
  return (
    <Panel className="d-form">
      <dl className="a-dl"><dt>Name</dt><dd>{identity.display_name ?? "—"}</dd><dt>Email</dt><dd>{identity.email}</dd><dt>Role in this organization</dt><dd>{role}</dd></dl>
      {orgs.length > 1 ? (
        <div className="a-field"><label htmlFor="s-org">Organization</label>
          <select id="s-org" value={org?.id ?? ""} onChange={(e) => { const o = orgs.find((x) => x.id === e.target.value); if (o) chooseOrg(o); }}>{orgs.map((o) => <option key={o.id} value={o.id}>{o.display_name}</option>)}</select></div>
      ) : <p>Organization: {org?.display_name}</p>}
      <p className="a-hint">Your session is stored in the Windows credential store and renewed automatically. It ends when you sign out, when an administrator revokes it, or when it expires.</p>
      <div><Button variant="secondary" onClick={() => void signOut()}>Sign out</Button></div>
    </Panel>
  );
}

function Devices() {
  const { bridge, org, fail } = useApp();
  const [items, setItems] = useState<ServerDevice[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (org) bridge.devicesList(org.id).then(setItems).catch((e) => setErr(describe(e))); }, [bridge, org]);
  if (!org) return null;
  const revoke = async (d: ServerDevice) => {
    if (!window.confirm(`Revoke “${d.name}”? It will be signed out and must be registered again.`)) return;
    try { await bridge.deviceRevoke(org.id, d.id); setItems((l) => l?.map((x) => (x.id === d.id ? { ...x, status: "revoked" } : x)) ?? l); } catch (e) { fail(e); }
  };
  return (
    <Panel className="d-form">
      <h2>Your devices</h2>
      {err ? <Alert tone="danger">{err}</Alert> : items === null ? <p role="status">Loading…</p> : items.length === 0 ? <p>No devices registered yet.</p> : (
        <div className="a-tablewrap"><table className="a-table"><caption className="a-visually-hidden">Registered devices</caption>
          <thead><tr><th scope="col">Name</th><th scope="col">Platform</th><th scope="col">Version</th><th scope="col">Last active</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
          <tbody>{items.map((d) => (<tr key={d.id}><th scope="row">{d.name}</th><td>{d.platform}</td><td>{d.app_version ?? "—"}</td><td>{fmtDateTime(d.last_seen_at)}</td>
            <td><StatusChip tone={d.status === "active" ? "success" : "neutral"}>{d.status === "active" ? "Active" : "Revoked"}</StatusChip></td>
            <td><Button variant="secondary" className="a-btn--sm" disabled={d.status !== "active"} onClick={() => void revoke(d)} aria-label={`Revoke ${d.name}`}>Revoke</Button></td></tr>))}</tbody></table></div>
      )}
      <p className="a-hint">The number of devices is limited by your organization’s plan. Revoking a device ends its session immediately.</p>
    </Panel>
  );
}

function About() {
  const { bridge, fail } = useApp();
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [upd, setUpd] = useState<UpdateStatus | null>(null);
  const [diag, setDiag] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { bridge.appInfo().then(setInfo).catch(fail); }, [bridge, fail]);
  if (!info) return <p role="status">Loading…</p>;
  return (
    <Panel className="d-form">
      <dl className="a-dl">
        <dt>Application</dt><dd>{info.name}</dd><dt>Version</dt><dd>{info.version}</dd><dt>Build</dt><dd>{info.build}</dd><dt>System</dt><dd>{info.os}</dd>
        <dt>Server</dt><dd className="a-mono">{info.api_base ?? "not configured"}</dd><dt>Local database</dt><dd>schema {info.db_schema_version}</dd>
        <dt>Data folder</dt><dd className="a-mono">{info.data_dir}</dd>
      </dl>
      {info.db_recovered_from ? <Alert tone="warning">The local cache was damaged and has been rebuilt from the server. The damaged file was kept for support.</Alert> : null}
      <div className="d-actions">
        <Button variant="secondary" onClick={() => { setErr(null); bridge.updateCheck().then(setUpd).catch((e) => setErr(describe(e))); }}>Check for updates</Button>
        <Button variant="secondary" onClick={() => { setErr(null); bridge.diagnosticsExport().then(setDiag).catch((e) => setErr(describe(e))); }}>Export diagnostics</Button>
      </div>
      {upd ? <Alert tone={upd.configured ? "info" : "warning"}>{upd.message}</Alert> : null}
      {diag ? <Alert tone="success">Diagnostics saved to <span className="a-mono">{diag}</span>. It contains version, system and redacted log lines only — no passwords, tokens, company names or documents. Send it to support yourself if asked.</Alert> : null}
      {err ? <Alert tone="danger">{err}</Alert> : null}
      <p className="a-hint">Support: contact your Artify administrator or your firm’s account owner.</p>
    </Panel>
  );
}
