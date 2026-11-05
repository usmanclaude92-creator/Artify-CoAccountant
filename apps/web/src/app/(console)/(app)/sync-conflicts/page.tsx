"use client";
import { Panel } from "@artify/ui";
import { useState } from "react";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { ActionDialog, DataTable, ErrorBox, Field, Loading, Status, useToast } from "@/components/kit";
import { fmtDateTime, tapi, useTenantApi } from "@/lib/tenant";

interface Conflict {
  id: string; item_kind: string; item_id: string; client_id: string; conflict_type: "stale" | "locked"; base_revision: number | null; server_revision: number | null;
  state: "open" | "resolved"; resolution: string | null; resolution_note: string | null; detected_at: string; resolved_at: string | null;
}
interface Company { id: string; name: string }

const KIND: Record<string, string> = { document: "Document", transaction: "Transaction", closing: "Daily closing", export: "Excel export", workspace: "Workspace" };
const RES: Record<string, string> = { keep_server: "Server copy kept", apply_local: "Local change applied on the new version", merged: "Merged by hand", discarded: "Local change discarded" };

/** Conflicts found by people's devices, per company. Deciding records the decision; it never edits the record (the app or the normal screen does that on top of the revision the server returns). */
export default function SyncConflictsPage() {
  const { orgId } = useOrg();
  const toast = useToast();
  const companies = useTenantApi<Company[]>(orgId, `/orgs/${orgId}/companies`);
  const [chosen, setChosen] = useState("");
  const company = chosen || companies.data?.[0]?.id || "";
  const [state, setState] = useState<"open" | "resolved">("open");
  const list = useTenantApi<{ items: Conflict[] }>(orgId, company ? `/companies/${company}/sync/conflicts?state=${state}` : null);
  const [dlg, setDlg] = useState<{ c: Conflict; resolution: "merged" | "keep_server" | "discarded" } | null>(null);

  const decide = async (c: Conflict, resolution: string, note: string) => {
    const out = await tapi<{ next_base_revision: number | null }>(orgId, `/companies/${company}/sync/conflicts/${c.id}/resolve`, { method: "POST", body: { resolution, note: note || undefined } });
    toast(out.next_base_revision != null ? `Recorded. The change must be sent on top of server revision ${out.next_base_revision}.` : "Decision recorded.");
    list.reload();
  };

  if (companies.error && !companies.data) return <div className="grid gap-4"><CcHeader title="Sync Conflicts" /><ErrorBox message={companies.error} onRetry={companies.reload} /></div>;
  if (!companies.data) return <div className="grid gap-4"><CcHeader title="Sync Conflicts" /><Loading what="Companies" /></div>;
  return (
    <div className="grid gap-4">
      <CcHeader title="Sync Conflicts" />
      <p className="a-hint">
        A conflict means someone&apos;s device held an older version of a record, or tried to change a record that is now locked (approved, exported or closed). The server verified each one. Your decision is
        recorded with your name and the reason; it does not change the record itself. A locked record can only keep the server copy.
      </p>
      <Panel className="grid gap-3">
        <Field label="Company">
          {(id) => (
            <select id={id} value={company} onChange={(e) => setChosen(e.target.value)}>
              {companies.data!.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Show">
          {(id) => (
            <select id={id} value={state} onChange={(e) => setState(e.target.value as "open" | "resolved")}>
              <option value="open">Open conflicts</option>
              <option value="resolved">Decided conflicts</option>
            </select>
          )}
        </Field>
      </Panel>
      {companies.data.length === 0 ? <p>There are no companies to show.</p> : (
      <DataTable<Conflict> caption={state === "open" ? "Open synchronization conflicts" : "Decided synchronization conflicts"} rows={list.data?.items ?? []} loading={list.loading} error={list.error}
        rowKey={(r) => r.id} empty={state === "open" ? "No open conflicts. The server reports everything in step." : "Nothing has been decided yet."}
        columns={[
          { key: "k", header: "Record", render: (r) => KIND[r.item_kind] ?? r.item_kind },
          { key: "t", header: "What happened", render: (r) => <Status value={r.conflict_type === "locked" ? "pending" : "inactive"} text={r.conflict_type === "locked" ? "Record is locked" : "Newer version on the server"} /> },
          { key: "r", header: "Revisions (device / server)", numeric: true, render: (r) => `${r.base_revision ?? "?"} / ${r.server_revision ?? "?"}` },
          { key: "d", header: "Found", render: (r) => fmtDateTime(r.detected_at) },
          {
            key: "a", header: state === "open" ? "Decide" : "Decision",
            render: (r) => state === "resolved" ? <span>{RES[r.resolution ?? ""] ?? r.resolution}{r.resolution_note ? ` — ${r.resolution_note}` : ""}</span> : (
              <span className="a-actions">
                <button className="a-btn a-btn--secondary" onClick={() => setDlg({ c: r, resolution: "keep_server" })}>Keep server copy</button>
                {r.conflict_type === "stale" ? <button className="a-btn a-btn--secondary" onClick={() => setDlg({ c: r, resolution: "merged" })}>Record merged by hand</button> : null}
                <button className="a-btn a-btn--secondary" onClick={() => setDlg({ c: r, resolution: "discarded" })}>Discard device change</button>
              </span>
            ),
          },
        ]} />
      )}
      {dlg ? (
        <ActionDialog open title={dlg.resolution === "merged" ? "Record that this was merged by hand" : dlg.resolution === "keep_server" ? "Keep the server copy" : "Discard the device change"}
          submitLabel="Record decision" minReason={dlg.resolution === "merged" ? 5 : 0} needsReason={dlg.resolution === "merged"} reasonLabel="What was merged, and how"
          description="This records your decision and writes an audit event. It does not edit the record."
          onSubmit={async ({ reason }) => { await decide(dlg.c, dlg.resolution, reason); setDlg(null); }} onClose={() => setDlg(null)} />
      ) : null}
    </div>
  );
}
