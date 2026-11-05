import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { CloudSyncStatus, ConflictResolution, EventRow, SyncConflict, SyncStatus } from "../bridge/types";
import { fmtDateTime, ROLE_LABEL } from "../lib/format";
import { useApp } from "../state/app";

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
      <Alert tone="info">Document import, storage and automatic reading with review are available; accounting review and Excel exports from approved data are available; posting arrives in a later release. Nothing on this screen is sample data.</Alert>
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
          <p className="a-hint">While a company is open, the app checks every minute for a newer workspace configuration.</p>
        </Panel>
      ) : <p role="status">Loading…</p>}
      {company && org ? <CloudSync org={org.id} company={company.id} /> : null}
    </section>
  );
}

const STATE_LABEL: Record<CloudSyncStatus["state"], string> = { pending: "Pending", uploading: "Uploading", failed: "Failed", conflict_detected: "Conflict detected", synchronized: "Synchronized" };
const STATE_TONE: Record<CloudSyncStatus["state"], "success" | "danger" | "warning" | "neutral"> = { pending: "neutral", uploading: "neutral", failed: "danger", conflict_detected: "warning", synchronized: "success" };

/** Phase 12: what the server confirmed. "Synchronized" appears only when the server says there is nothing open. */
function CloudSync({ org, company }: { org: string; company: string }) {
  const { bridge, fail } = useApp();
  const [st, setSt] = useState<CloudSyncStatus | null>(null);
  const [conflicts, setConflicts] = useState<SyncConflict[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => {
    bridge.cloudSyncStatus(org, company).then(setSt).catch(fail);
    bridge.cloudSync<{ items: SyncConflict[] }>(org, company, "conflicts", { query: { state: "open" } }).then((l) => setConflicts(l.items)).catch(fail);
  }, [bridge, org, company, fail]);
  useEffect(load, [load]);
  const check = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await bridge.cloudSyncPull(org, company);
      setMsg(r.caught_up ? `Checked with the server: ${r.items} change(s) read.` : "More changes remain; check again to continue.");
    } catch (e) { const m = fail(e); if (m) setMsg(m); } finally { setBusy(false); load(); }
  };
  const decide = async (c: SyncConflict, resolution: ConflictResolution, note?: string) => {
    setMsg(null);
    try {
      const r = await bridge.cloudSync<SyncConflict>(org, company, "conflict_resolve", { id: c.id, body: { resolution, note } });
      setMsg(r.next_base_revision != null ? `Decision recorded. Send your change on top of server revision ${r.next_base_revision}.` : "Decision recorded. The server copy is kept.");
    } catch (e) { const m = fail(e); if (m) setMsg(m); } finally { load(); }
  };
  return (
    <section aria-labelledby="h-cloud-sync"><Panel className="grid-gap">
      <h2 id="h-cloud-sync">Cloud synchronization</h2>
      {st ? (
        <>
          <p><StatusChip tone={STATE_TONE[st.state]}>{STATE_LABEL[st.state]}</StatusChip> <span>{st.label}</span></p>
          <dl className="a-dl">
            <dt>Last check with the server</dt><dd>{fmtDateTime(st.last_pull_at)}</dd>
            <dt>Files waiting / uploading / failed</dt><dd>{st.waiting_uploads} / {st.uploading} / {st.failed_uploads}</dd>
            <dt>Open conflicts (counted by the server)</dt><dd>{st.open_conflicts ?? "Not confirmed"}</dd>
            <dt>Known to this computer</dt><dd>{st.known_items.length ? st.known_items.map(([k, n]) => `${n} ${k}`).join(" · ") : "Nothing yet"}</dd>
          </dl>
          <div className="d-actions"><Button onClick={() => void check()} disabled={busy}>{busy ? "Checking…" : "Check with the server"}</Button></div>
        </>
      ) : <p role="status">Loading…</p>}
      {msg ? <Alert tone="info">{msg}</Alert> : null}
      {conflicts.length > 0 ? (
        <ul aria-label="Open conflicts" className="grid-gap">
          {conflicts.map((c) => (
            <li key={c.id}>
              <strong>{c.item_kind}</strong> — {c.conflict_type === "locked" ? "this record is locked on the server" : "the server has a newer version"} (your copy: revision {c.base_revision ?? "?"}, server: {c.server_revision ?? "?"}).
              <div className="d-actions">
                <Button variant="secondary" onClick={() => void decide(c, "keep_server")}>Keep the server copy</Button>
                {c.conflict_type === "stale" ? <Button variant="secondary" onClick={() => void decide(c, "apply_local", "Apply my change on top of the newer version")}>Apply my change on the new version</Button> : null}
                <Button variant="secondary" onClick={() => void decide(c, "discarded", "Not needed any more")}>Discard my change</Button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="a-hint">“Synchronized” is shown only when this computer has read the server’s changes, nothing is waiting or failed, and the server reports no open conflicts. Resolving a conflict records your decision; it does not change the record.</p>
    </Panel></section>
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
