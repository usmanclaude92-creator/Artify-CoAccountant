"use client";
import { useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { ActionDialog, DataTable, useToast } from "@/components/kit";
import { fmtDateTime, tapi, useTenantApi } from "@/lib/tenant";

interface Tpl { id: string; key: string; name: string; description: string; is_system: boolean; latest_version: number; created_at: string; counts: Record<string, number> }
interface Detail { versions: { version: number; changelog: string; created_at: string }[]; definition: { sections: { name: string; folders: unknown[] }[] } }

export default function WorkspaceTemplatesPage() {
  const { orgId, can } = useOrg();
  const toast = useToast();
  const list = useTenantApi<{ items: Tpl[] }>(orgId, can("companies.list") ? `/orgs/${orgId}/workspace-templates` : null);
  const [open, setOpen] = useState<Tpl | null>(null);
  const [archive, setArchive] = useState<Tpl | null>(null);
  const detail = useTenantApi<Detail>(orgId, open ? `/orgs/${orgId}/workspace-templates/${open.id}` : null);
  const manage = can("workspace_templates.manage");
  return (
    <div className="grid gap-4">
      <CcHeader title="Workspace Templates" crumbs={[{ label: "Workspace Templates" }]} />
      <Alert tone="info">Templates are starting points. Applying one copies it into a company with new ids, so later edits to a template never change a company that already used it. Save a company’s workspace as a template from that company’s Workspace → Templates tab.</Alert>
      <Panel className="grid gap-3">
        <DataTable<Tpl> caption="Workspace templates" rows={list.data?.items} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(t) => t.id} empty="No templates."
          columns={[
            { key: "n", header: "Template", render: (t) => <strong>{t.name}</strong> },
            { key: "o", header: "Owner", render: (t) => (t.is_system ? "Artify starter" : "Your organization") },
            { key: "v", header: "Version", numeric: true, render: (t) => t.latest_version },
            { key: "c", header: "Contents", render: (t) => `${t.counts.sections} sections · ${t.counts.folders} folders · ${t.counts.categories} categories · ${t.counts.fields} fields` },
            { key: "a", header: "Actions", render: (t) => (
              <span className="a-actions">
                <Button variant="secondary" className="a-btn--sm" onClick={() => setOpen(t)}>View</Button>
                {!t.is_system ? <Button variant="secondary" className="a-btn--sm" disabled={!manage} onClick={() => setArchive(t)}>Archive</Button> : null}
              </span>) },
          ]} />
      </Panel>
      {open ? (
        <Panel className="grid gap-2">
          <h2>{open.name}</h2>
          <p className="a-hint">{open.description || "No description."}</p>
          {detail.data ? (
            <>
              <h3>Sections in the latest version</h3>
              <ul>{detail.data.definition.sections.map((s) => <li key={s.name}>{s.name} <span className="a-hint">({s.folders.length} folders)</span></li>)}</ul>
              <h3>Versions</h3>
              <ul>{detail.data.versions.map((v) => <li key={v.version}>v{v.version} · {fmtDateTime(v.created_at)} <span className="a-hint">{v.changelog}</span></li>)}</ul>
            </>
          ) : null}
          <div><Button variant="secondary" onClick={() => setOpen(null)}>Close</Button></div>
        </Panel>
      ) : null}
      {archive ? (
        <ActionDialog open title={`Archive “${archive.name}”`} description="The template is hidden from the gallery. Companies that already used it are not affected." submitLabel="Archive" needsReason={false} onClose={() => setArchive(null)}
          onSubmit={async () => { await tapi(orgId, `/orgs/${orgId}/workspace-templates/${archive.id}/archive`, { method: "POST" }); toast("Template archived."); setArchive(null); list.reload(); }} />
      ) : null}
    </div>
  );
}
