"use client";
import { Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, ErrorBox, Loading, Status, useToast } from "@/components/kit";
import { fmtDateTime, tapi, useTenantApi } from "@/lib/tenant";

interface Notice { id: string; title: string; body: string; created_at: string; read_at: string | null; email_state: string; event_type: string }

const MAIL: Record<string, string> = { sent: "E-mailed", failed: "E-mail failed", skipped: "In-app only", not_requested: "In-app only" };

/** The signed-in person's own notifications. Texts are generic: they never contain names, amounts or document content. */
export default function NotificationsPage() {
  const { orgId } = useOrg();
  const toast = useToast();
  const list = useTenantApi<{ items: Notice[]; unread: number }>(orgId, "/notifications?limit=50");
  const prefs = useTenantApi<{ email_enabled: boolean }>(orgId, "/notifications/preferences");
  if (list.error && !list.data) return <div className="grid gap-4"><CcHeader title="Notifications" /><ErrorBox message={list.error} onRetry={list.reload} /></div>;
  if (!list.data) return <div className="grid gap-4"><CcHeader title="Notifications" /><Loading what="Notifications" /></div>;
  return (
    <div className="grid gap-4">
      <CcHeader title="Notifications" actions={<button className="a-btn a-btn--secondary" disabled={list.data.unread === 0} onClick={async () => { await tapi(orgId, "/notifications/read-all", { method: "POST" }); list.reload(); }}>Mark all as read</button>} />
      <Panel className="grid gap-2">
        <label className="a-check">
          <input type="checkbox" checked={prefs.data?.email_enabled ?? true} disabled={!prefs.data}
            onChange={async (e) => { await tapi(orgId, "/notifications/preferences", { method: "PUT", body: { email_enabled: e.target.checked } }); toast(e.target.checked ? "E-mail turned on." : "E-mail turned off."); prefs.reload(); }} />
          <span>Also e-mail me about assigned tasks, review requests and closing results</span>
        </label>
        <p className="a-hint">The notice always stays here, whatever you choose. Push alerts to phones are not available yet.</p>
      </Panel>
      <DataTable<Notice> caption="Your notifications" rows={list.data.items} loading={false} error={null} rowKey={(r) => r.id} empty="Nothing new."
        columns={[
          { key: "s", header: "Status", render: (r) => <Status value={r.read_at ? "inactive" : "pending"} text={r.read_at ? "Read" : "Unread"} /> },
          { key: "t", header: "Notification", render: (r) => <span><strong>{r.title}</strong><br />{r.body}</span> },
          { key: "m", header: "Delivery", render: (r) => MAIL[r.email_state] ?? r.email_state },
          { key: "w", header: "When", render: (r) => fmtDateTime(r.created_at) },
          { key: "a", header: "", render: (r) => r.read_at ? null : <button className="a-btn a-btn--secondary" onClick={async () => { await tapi(orgId, `/notifications/${r.id}/read`, { method: "POST" }); list.reload(); }}>Mark as read</button> },
        ]} />
    </div>
  );
}
