"use client";
import { use, useState } from "react";
import { Alert } from "@artify/ui";
import { CcHeader, useOrg } from "@/components/cc/shell";
import { ErrorBox, Loading, Status, Tabs } from "@/components/kit";
import { useTenantApi } from "@/lib/tenant";
import { HistoryTab, PermissionsTab, TemplatesTab } from "@/components/cc/workspace/admin";
import type { Ctx, Kinds, Workspace } from "@/components/cc/workspace/common";
import { CategoriesTab, FieldsTab, FilingTab, NamingTab } from "@/components/cc/workspace/rules";
import { StructureTab, UsageStrip } from "@/components/cc/workspace/structure";

const TABS = [
  { id: "structure", label: "Structure" }, { id: "categories", label: "Categories" }, { id: "fields", label: "Custom fields" },
  { id: "naming", label: "Naming rules" }, { id: "filing", label: "Filing rules" }, { id: "permissions", label: "Permissions" },
  { id: "templates", label: "Templates" }, { id: "history", label: "History" },
];

export default function WorkspacePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { orgId, href } = useOrg();
  const [tab, setTab] = useState("structure");
  const [archived, setArchived] = useState(false);
  const co = useTenantApi<{ name: string; status: string }>(orgId, `/companies/${id}`);
  const ws = useTenantApi<Workspace>(orgId, `/companies/${id}/workspace${archived ? "?include_archived=true" : ""}`);
  const kinds = useTenantApi<Kinds>(orgId, "/reference/document-kinds");
  const crumbs = [{ label: "My Companies", href: href("/companies") }, { label: co.data?.name ?? "Company", href: href(`/companies/${id}`) }, { label: "Workspace" }];
  if (ws.error && !ws.data) return <div className="grid gap-4"><CcHeader title="Workspace" crumbs={crumbs} />{/not found/i.test(ws.error) ? <Alert tone="info">This company does not exist, or you do not have access to its workspace.</Alert> : <ErrorBox message={ws.error} onRetry={ws.reload} />}</div>;
  if (!ws.data || !co.data) return <Loading what="the workspace" />;
  const ctx: Ctx = { orgId, companyId: id, ws: ws.data, reload: ws.reload, editable: co.data.status === "active" };
  return (
    <div className="grid gap-4">
      <CcHeader title={`${co.data.name} · Workspace`} crumbs={crumbs} />
      <p>
        <Status value={ws.data.workspace_state === "configured" ? "active" : "not_configured"} text={ws.data.workspace_state === "configured" ? "Configured" : "Not configured"} />{" "}
        <span className="a-hint">· version {ws.data.version} · {ws.data.access_mode === "restricted" ? "restricted access" : "open access"}</span>
      </p>
      <UsageStrip ctx={ctx} />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "structure" ? (
        <label className="a-hint" style={{ display: "flex", gap: "0.5rem" }}><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Show archived items</label>
      ) : null}
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="grid gap-4">
        {tab === "structure" ? <StructureTab ctx={ctx} /> : null}
        {tab === "categories" ? <CategoriesTab ctx={ctx} kinds={kinds.data} /> : null}
        {tab === "fields" ? <FieldsTab ctx={ctx} /> : null}
        {tab === "naming" ? <NamingTab ctx={ctx} kinds={kinds.data} /> : null}
        {tab === "filing" ? <FilingTab ctx={ctx} kinds={kinds.data} /> : null}
        {tab === "permissions" ? <PermissionsTab ctx={ctx} /> : null}
        {tab === "templates" ? <TemplatesTab ctx={ctx} /> : null}
        {tab === "history" ? <HistoryTab ctx={ctx} /> : null}
      </div>
    </div>
  );
}
