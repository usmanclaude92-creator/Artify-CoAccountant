import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { EventRow, SyncStatus } from "../bridge/types";
import { fmtDateTime, ROLE_LABEL } from "../lib/format";
import { useApp, type Page } from "../state/app";

export function Dashboard() {
  const { identity, org, company, go, bridge, fail } = useApp();
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => { if (org) bridge.companies(org.id).then((l) => setCount(l.items.length)).catch(fail); }, [bridge, org, fail]);
  const role = ROLE_LABEL[identity.memberships.find((m) => m.organization_id === org?.id)?.organization_role ?? ""] ?? "Member";
  return (
    <section aria-labelledby="h-dash" className="d-page">
      <h1 id="h-dash">Dashboard</h1>
      <div className="a-kpis">
        <div className="a-kpi"><span className="a-kpi__label">Signed in as</span><strong>{identity.display_name ?? identity.email}</strong><span className="a-hint">{identity.email} · {role}</span></div>
        <div className="a-kpi"><span className="a-kpi__label">Organization</span><strong>{org?.display_name}</strong></div>
        <div className="a-kpi"><span className="a-kpi__label">Authorized companies</span><strong>{count ?? "…"}</strong></div>
        <div className="a-kpi"><span className="a-kpi__label">Open company</span><strong>{company?.name ?? "None"}</strong>{company ? null : <span className="a-hint">Choose one in My Companies</span>}</div>
      </div>
      <Panel className="d-actions">
        <Button onClick={() => go("companies")}>My Companies</Button>
        <Button variant="secondary" disabled={!company} onClick={() => go("workspace")}>Open workspace</Button>
      </Panel>
      <Alert tone="info">Document import, storage and automatic reading with review are available; posting and exports arrive in later releases. Nothing on this screen is sample data.</Alert>
    </section>
  );
}

const FUTURE: Record<Exclude<Page, "dashboard" | "companies" | "workspace" | "documents" | "import" | "queue" | "archive" | "processing" | "review" | "accounting" | "acct-review" | "sync" | "notifications" | "settings">, { title: string; text: string; phase: string }> = {
  reports: { title: "Reports", text: "Management reports and Excel exports built from approved entries.", phase: "Reports and Excel (a later release)" },
};
export function Placeholder({ page }: { page: keyof typeof FUTURE }) {
  const f = FUTURE[page];
  return (
    <section className="d-page" aria-labelledby="h-future">
      <h1 id="h-future">{f.title}</h1>
      <Panel className="d-future">
        <StatusChip tone="neutral">Not available yet</StatusChip>
        <p>{f.text}</p>
        <p className="a-hint">Planned for: {f.phase}. This area intentionally shows no sample data.</p>
      </Panel>
    </section>
  );
}

export function Sync() {
  const { bridge, org, company, fail } = useApp();
  const [st, setSt] = useState<SyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => { bridge.syncStatus(company?.id ?? null).then(setSt).catch(fail); }, [bridge, company, fail]);
  useEffect(load, [load]);
  const refresh = async () => {
    if (!org || !company) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await bridge.workspaceRefresh(org.id, company.id);
      setMsg(r.changed ? (r.summary ?? `Workspace version ${r.version} loaded.`) : `Already up to date (version ${r.version}).`);
    } catch (e) {
      const m = fail(e);
      if (m) setMsg(m);
    } finally {
      setBusy(false);
      load();
    }
  };
  const online = st?.connectivity.state;
  return (
    <section className="d-page" aria-labelledby="h-sync">
      <h1 id="h-sync">Synchronization</h1>
      {st ? (
        <Panel className="grid-gap">
          <dl className="a-dl">
            <dt>Connection</dt><dd><StatusChip tone={online === "online" ? "success" : online === "offline" ? "danger" : "neutral"}>{online === "online" ? "Online" : online === "offline" ? "Offline" : "Not checked yet"}</StatusChip></dd>
            <dt>Open company</dt><dd>{company?.name ?? "None"}</dd>
            <dt>Last successful synchronization</dt><dd>{fmtDateTime(st.company?.last_success_at)}</dd>
            <dt>Last attempt</dt><dd>{fmtDateTime(st.company?.last_attempt_at)}</dd>
            <dt>Saved workspace version</dt><dd>{st.cached_workspace_version ?? "—"}</dd>
            <dt>Last error</dt><dd>{st.company?.last_error ? st.company.last_error.replace(/_/g, " ") : "None"}</dd>
            <dt>Operations waiting to be sent</dt><dd>{st.pending.pending} pending · {st.pending.in_progress} in progress · {st.pending.failed} failed</dd>
          </dl>
          <div className="d-actions"><Button onClick={() => void refresh()} disabled={busy || !company}>{busy ? "Synchronizing…" : "Synchronize now"}</Button></div>
          {!company ? <p className="a-hint">Open a company to synchronize its workspace.</p> : null}
          {msg ? <Alert tone="info">{msg}</Alert> : null}
          <p className="a-hint">While a company is open, the app checks every minute for a newer workspace configuration. Document upload and offline capture queues arrive with document management.</p>
        </Panel>
      ) : <p role="status">Loading…</p>}
    </section>
  );
}

export function Notifications() {
  const { bridge, fail } = useApp();
  const [items, setItems] = useState<EventRow[] | null>(null);
  useEffect(() => { bridge.notificationsList().then(setItems).catch(fail); }, [bridge, fail]);
  return (
    <section className="d-page" aria-labelledby="h-notif">
      <header className="d-page__head"><h1 id="h-notif">Notifications</h1>
        <Button variant="secondary" disabled={!items?.some((i) => !i.read)} onClick={() => void bridge.notificationsRead().then(() => setItems((l) => l?.map((i) => ({ ...i, read: true })) ?? l)).catch(fail)}>Mark all as read</Button></header>
      {items === null ? <p role="status">Loading…</p> : items.length === 0 ? <Panel><p>No notifications. Workspace updates and synchronization problems will be listed here.</p></Panel> : (
        <ul className="d-list" aria-label="Notifications">
          {items.map((i) => (
            <li key={i.id} className={`d-note ${i.read ? "" : "d-note--unread"}`}>
              <StatusChip tone={i.level === "error" ? "danger" : i.level === "warn" ? "warning" : "info"}>{i.level === "warn" ? "Warning" : i.level === "error" ? "Problem" : "Info"}</StatusChip>
              <span>{i.message}</span><span className="a-hint">{fmtDateTime(i.ts)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
