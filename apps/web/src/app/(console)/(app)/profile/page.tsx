"use client";
import Link from "next/link";
import { useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { DataTable, Field, Status, useToast } from "@/components/kit";
import { ORG_ROLE_LABEL, errorText, fmtDateTime, tapi, useTenantApi } from "@/lib/tenant";

type Session = Schemas["SessionOut"];

export default function ProfilePage() {
  const { me, orgId, role } = useOrg();
  const toast = useToast();
  const [name, setName] = useState(me.display_name);
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwErr, setPwErr] = useState<string | null>(null);
  const sessions = useTenantApi<Session[]>(undefined, "/auth/sessions");
  const saveName = async () => {
    setNameErr(null);
    try { await tapi(orgId, "/me", { method: "PATCH", body: { display_name: name.trim() } }); toast("Name updated. Reload to see it everywhere."); } catch (ex) { setNameErr(errorText(ex)); }
  };
  const savePw = async () => {
    setPwErr(null);
    try { await tapi(orgId, "/me/password", { method: "POST", body: { current_password: pw.current, new_password: pw.next } }); setPw({ current: "", next: "" }); toast("Password changed. Your other sessions were signed out."); sessions.reload(); } catch (ex) { setPwErr(errorText(ex)); }
  };
  return (
    <div className="grid gap-4">
      <CcHeader title="My Profile" />
      <Panel className="grid gap-3">
        <h2>Account</h2>
        <dl className="a-dl" data-testid="profile">
          <dt>E-mail</dt><dd>{me.email}</dd>
          <dt>Role</dt><dd>{role ? (ORG_ROLE_LABEL[role] ?? role) : "Company access"}</dd>
          <dt>Two-factor authentication</dt><dd><Status value={me.mfa_enabled ? "active" : "inactive"} text={me.mfa_enabled ? "On" : "Off"} /> {!me.mfa_enabled ? <Link className="a-link" href="/security/mfa">Set up</Link> : null}</dd>
        </dl>
        <form className="a-toolbar" onSubmit={(e) => { e.preventDefault(); void saveName(); }}>
          <Field label="Display name">{(id) => <input id={id} value={name} required maxLength={100} onChange={(e) => setName(e.target.value)} />}</Field>
          <Button type="submit" disabled={!name.trim() || name.trim() === me.display_name}>Save name</Button>
        </form>
        {nameErr ? <Alert tone="danger">{nameErr}</Alert> : null}
      </Panel>
      <Panel className="grid gap-3">
        <h2>Change password</h2>
        <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void savePw(); }}>
          <Field label="Current password">{(id) => <input id={id} type="password" autoComplete="current-password" required value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />}</Field>
          <Field label="New password">{(id) => <input id={id} type="password" autoComplete="new-password" required value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />}</Field>
          {pwErr ? <Alert tone="danger">{pwErr}</Alert> : null}
          <div><Button type="submit">Change password</Button></div>
        </form>
      </Panel>
      <Panel className="grid gap-3">
        <h2>Active sessions</h2>
        <DataTable<Session> caption="Active sessions" rows={sessions.data ?? undefined} loading={sessions.loading} error={sessions.error} onRetry={sessions.reload} rowKey={(r) => r.id} empty="No active sessions."
          columns={[
            { key: "ua", header: "Device", render: (r) => <>{r.user_agent ?? "Unknown"}{r.current ? " (this session)" : ""}</> },
            { key: "c", header: "Signed in", render: (r) => fmtDateTime(r.created_at) },
            { key: "l", header: "Last used", render: (r) => fmtDateTime(r.last_used_at) },
            { key: "m", header: "2FA", render: (r) => (r.mfa_verified ? "Verified" : "No") },
            { key: "a", header: "Actions", render: (r) => (r.current ? null : <Button variant="secondary" className="a-btn--sm" onClick={async () => { try { await tapi(orgId, `/auth/sessions/${r.id}`, { method: "DELETE" }); toast("Session revoked."); sessions.reload(); } catch (ex) { toast(errorText(ex), "danger"); } }}>Revoke session from {fmtDateTime(r.created_at)}</Button>) },
          ]} />
      </Panel>
    </div>
  );
}
