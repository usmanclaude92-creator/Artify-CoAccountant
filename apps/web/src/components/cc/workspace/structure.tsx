"use client";
import { useMemo, useState } from "react";
import { Alert, Button, Panel } from "@artify/ui";
import { Empty, Field, Loading, Status } from "@/components/kit";
import { ApiProblem } from "@/lib/client";
import { errorText, useTenantApi } from "@/lib/tenant";
import { FormDialog, NodeSelect, readOnlyNote, useChange, type Category, type Ctx, type FilingRule, type Node } from "./common";

interface Item { version: number; item: Node }
interface Impact { subfolders: number; documents: number; categories: number; filing_rules: number; field_applicability: number; permission_grants: number }
interface Usage { limits: { key: string; unit: string; limit: number | null; unlimited: boolean; current: number; at_limit: boolean }[] }

const USAGE_LABEL: Record<string, string> = {
  "limit.workspace_sections_per_company": "Sections", "limit.folders_per_company": "Folders", "limit.folder_depth": "Deepest level",
  "limit.custom_fields_per_company": "Custom fields", "limit.filing_rules_per_company": "Filing rules",
};

export function UsageStrip({ ctx }: { ctx: Ctx }) {
  const u = useTenantApi<Usage>(ctx.orgId, `/companies/${ctx.companyId}/workspace/usage?v=${ctx.ws.version}`);
  if (!u.data) return null;
  return (
    <ul className="a-actions" style={{ listStyle: "none", padding: 0, margin: 0 }} aria-label="Plan limits for this company">
      {u.data.limits.map((l) => (
        <li key={l.key} className={`a-chip a-chip--${l.at_limit ? "warning" : "neutral"}`} title={l.at_limit ? "Limit reached: you can still edit, archive and reorder; adding more needs a larger plan." : undefined}>
          {USAGE_LABEL[l.key] ?? l.key}: {l.current}{l.unlimited ? " (no limit)" : ` of ${l.limit ?? 0}`}
        </li>
      ))}
    </ul>
  );
}

export function StructureTab({ ctx }: { ctx: Ctx }) {
  const { ws } = ctx;
  const change = useChange(ctx);
  const [sel, setSel] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dlg, setDlg] = useState<null | "section" | "folder" | "move" | "archive" | "delete" | "duplicate">(null);
  const selected = ws.nodes.find((n) => n.id === sel) ?? null;
  const children = useMemo(() => {
    const m = new Map<string | null, Node[]>();
    for (const n of ws.nodes) m.set(n.parent_id, [...(m.get(n.parent_id) ?? []), n]);
    return m;
  }, [ws.nodes]);
  const ids = new Set(ws.nodes.map((n) => n.id));
  const roots = ws.nodes.filter((n) => !n.parent_id || !ids.has(n.parent_id));
  const siblings = selected ? (selected.parent_id && ids.has(selected.parent_id) ? children.get(selected.parent_id) : roots)?.filter((n) => !n.archived_at) ?? [] : [];
  const idx = selected ? siblings.findIndex((s) => s.id === selected.id) : -1;
  const can = (a: string) => ctx.editable && !!selected?.can?.[a];
  const canAddSection = ctx.editable && ws.capabilities.configure;

  const reorder = (dir: -1 | 1) => {
    if (!selected || idx < 0) return;
    const order = siblings.map((s) => s.id);
    const a = order[idx]; const b = order[idx + dir];
    if (a === undefined || b === undefined) return;
    order[idx] = b; order[idx + dir] = a;
    void change("POST", "/nodes/reorder", { parent_id: selected.parent_id, ordered_ids: order }, "Order saved.").catch(() => undefined);
  };
  const desktop: string[] = [];
  const walk = (parent: string | null, prefix: string) => {
    for (const n of (children.get(parent) ?? []).filter((x) => !x.archived_at)) { const p = `${prefix}${n.name}/`; desktop.push(p); walk(n.id, p); }
  };
  walk(null, "");
  const renderNode = (n: Node): React.ReactNode => {
    const kids = children.get(n.id) ?? [];
    const open = !collapsed.has(n.id);
    return (
      <li key={n.id}>
        <div className="a-tree__row">
          {kids.length ? (
            <button type="button" className="a-tree__toggle" aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${n.label}`}
              onClick={() => setCollapsed((s) => { const x = new Set(s); if (open) x.add(n.id); else x.delete(n.id); return x; })}>{open ? "▾" : "▸"}</button>
          ) : <span className="a-tree__toggle" aria-hidden="true">·</span>}
          <button type="button" className={`a-tree__item ${n.archived_at ? "a-tree__item--archived" : ""}`} aria-current={sel === n.id} onClick={() => setSel(n.id)}>
            {n.label}{n.archived_at ? " (archived)" : ""}{!n.is_active && !n.archived_at ? " (inactive)" : ""}
          </button>
        </div>
        {kids.length && open ? <ul>{kids.map(renderNode)}</ul> : null}
      </li>
    );
  };

  return (
    <div className="grid gap-4">
      {readOnlyNote(ctx.editable)}
      <div className="a-split">
        <Panel className="grid gap-3">
          <div className="a-actions">
            <Button variant="secondary" className="a-btn--sm" disabled={!canAddSection} onClick={() => setDlg("section")}>Add section</Button>
            <Button variant="secondary" className="a-btn--sm" disabled={!can("edit")} onClick={() => setDlg("folder")}>Add folder here</Button>
          </div>
          {roots.length === 0 ? (
            <Empty>This company has no sections yet. Add a section, or start from a template in the Templates tab. Nothing is created for you automatically.</Empty>
          ) : <ul className="a-tree" aria-label="Workspace structure">{roots.map(renderNode)}</ul>}
        </Panel>
        <div className="grid gap-4">
          {selected ? (
            <NodePanel key={selected.id + ws.version} ctx={ctx} node={selected} categories={ws.categories.filter((c) => c.node_id === selected.id)}
              rules={ws.filing_rules.filter((r) => r.target_node_id === selected.id)}
              actions={(
                <div className="a-actions">
                  <Button variant="secondary" className="a-btn--sm" disabled={!can("edit") || selected.archived_at !== null} onClick={() => setDlg("folder")}>Add subfolder</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!can("move") || selected.node_type === "section" || !!selected.archived_at} onClick={() => setDlg("move")}>Move…</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!can("edit") || idx <= 0} aria-label={`Move up: ${selected.label}`} onClick={() => reorder(-1)}>Move up</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!can("edit") || idx < 0 || idx >= siblings.length - 1} aria-label={`Move down: ${selected.label}`} onClick={() => reorder(1)}>Move down</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!ctx.editable || !!selected.archived_at || !selected.can?.view} onClick={() => setDlg("duplicate")}>Duplicate</Button>
                  <Button variant="secondary" className="a-btn--sm" disabled={!can("archive")} onClick={() => setDlg("archive")}>{selected.archived_at ? "Restore" : "Archive"}</Button>
                  <Button variant="secondary" className="a-btn--sm a-btn--danger" disabled={!can("delete")} onClick={() => setDlg("delete")}>Delete…</Button>
                </div>
              )} />
          ) : <Panel><Empty>Select a section or folder to see and change its details.</Empty></Panel>}
          <Panel className="grid gap-2">
            <h2>Windows folder preview</h2>
            <p className="a-hint">How the Desktop app will mirror this structure (from the live, non-archived items).</p>
            {desktop.length ? <pre className="a-mono" style={{ margin: 0, overflow: "auto", maxBlockSize: "14rem" }}>{desktop.join("\n")}</pre> : <Empty>No folders yet.</Empty>}
          </Panel>
        </div>
      </div>

      {dlg === "section" || dlg === "folder" ? (
        <NameDialog title={dlg === "section" ? "Add section" : `Add folder inside “${selected?.label}”`} submit="Create" onClose={() => setDlg(null)}
          onSave={async (name, description) => { const r = await change<Item>("POST", "/nodes", { name, description, parent_id: dlg === "folder" ? selected?.id : null }, "Created."); if (r) { setSel(r.item.id); } }} />
      ) : null}
      {dlg === "duplicate" && selected ? (
        <NameDialog title={`Duplicate “${selected.label}”`} submit="Duplicate" initial={`${selected.name} (copy)`} hideDescription onClose={() => setDlg(null)}
          onSave={async (name) => { const r = await change<Item>("POST", `/nodes/${selected.id}/duplicate`, { name }, "Duplicated (structure only: categories, rules and permissions are not copied)."); if (r) setSel(r.item.id); }} />
      ) : null}
      {dlg === "move" && selected ? <MoveDialog ctx={ctx} node={selected} onClose={() => setDlg(null)} /> : null}
      {dlg === "archive" && selected ? <ArchiveDialog ctx={ctx} node={selected} onClose={() => setDlg(null)} /> : null}
      {dlg === "delete" && selected ? (
        <FormDialog title={`Delete “${selected.label}”`} submit="Delete permanently" danger onClose={() => setDlg(null)}
          onSubmit={async () => { try { await change("DELETE", `/nodes/${selected.id}`, undefined, "Deleted."); setSel(null); } catch (e) { throw e instanceof ApiProblem && e.status === 409 ? new Error(`${e.problem.detail} Choose Archive instead.`) : e; } }}>
          <p>Only items that nothing has ever used can be deleted. If this item contains folders or is used by categories, rules, fields, permissions or documents, the server refuses and you can archive it instead. Documents are never deleted with a folder.</p>
        </FormDialog>
      ) : null}
    </div>
  );
}

function NameDialog({ title, submit, initial = "", hideDescription, onClose, onSave }: {
  title: string; submit: string; initial?: string; hideDescription?: boolean; onClose: () => void; onSave: (name: string, description: string) => Promise<void>;
}) {
  const [name, setName] = useState(initial);
  const [desc, setDesc] = useState("");
  return (
    <FormDialog title={title} submit={submit} onClose={onClose} disabled={!name.trim()} onSubmit={() => onSave(name.trim(), desc)}>
      <Field label="Name" hint="Used as the folder name on Windows. Cannot contain / \ : * ? &quot; < > |">{(id) => <input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}</Field>
      {hideDescription ? null : <Field label="Description (optional)">{(id) => <textarea id={id} value={desc} maxLength={1000} onChange={(e) => setDesc(e.target.value)} />}</Field>}
    </FormDialog>
  );
}

function NodePanel({ ctx, node, categories, rules, actions }: { ctx: Ctx; node: Node; categories: Category[]; rules: FilingRule[]; actions: React.ReactNode }) {
  const change = useChange(ctx);
  const [name, setName] = useState(node.name);
  const [display, setDisplay] = useState(node.display_name ?? "");
  const [desc, setDesc] = useState(node.description);
  const [active, setActive] = useState(node.is_active);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const editable = ctx.editable && !node.archived_at && !!node.can?.edit;
  const canRename = ctx.editable && !node.archived_at && !!node.can?.rename;
  const impact = useTenantApi<Impact>(ctx.orgId, `/companies/${ctx.companyId}/workspace/nodes/${node.id}/impact`);
  const dirty = name !== node.name || display !== (node.display_name ?? "") || desc !== node.description || active !== node.is_active;
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const body: Record<string, unknown> = {};
      if (name !== node.name) body.name = name.trim();
      if (display !== (node.display_name ?? "")) body.display_name = display.trim() || null;
      if (desc !== node.description) body.description = desc;
      if (active !== node.is_active) body.is_active = active;
      await change("PATCH", `/nodes/${node.id}`, body, "Saved.");
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  return (
    <Panel className="grid gap-4">
      <div className="a-actions" style={{ justifyContent: "space-between" }}>
        <h2>{node.node_type === "section" ? "Section" : "Folder"}: {node.label}</h2>
        <Status value={node.archived_at ? "retired" : node.is_active ? "active" : "inactive"} text={node.archived_at ? "Archived" : node.is_active ? "Active" : "Inactive"} />
      </div>
      {actions}
      <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <Field label="Name" hint="Renaming keeps the same item: documents and rules refer to it by a stable id, never by name.">{(id) => <input id={id} value={name} disabled={!canRename} maxLength={120} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Display name (optional)" hint="A friendlier label for screens; the name above is used for folders.">{(id) => <input id={id} value={display} disabled={!editable} maxLength={160} onChange={(e) => setDisplay(e.target.value)} />}</Field>
        <Field label="Description">{(id) => <textarea id={id} value={desc} disabled={!editable} maxLength={1000} onChange={(e) => setDesc(e.target.value)} />}</Field>
        <label className="a-hint" style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}><input type="checkbox" checked={active} disabled={!editable} onChange={(e) => setActive(e.target.checked)} /> Active (inactive items stay visible but cannot receive new filing)</label>
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <div><Button type="submit" disabled={busy || !dirty || (!editable && !canRename)}>{busy ? "Saving…" : "Save changes"}</Button></div>
      </form>
      <div className="grid gap-1">
        <h3>What uses this item</h3>
        {impact.data ? (
          <dl className="a-dl">
            <dt>Subfolders</dt><dd>{impact.data.subfolders}</dd>
            <dt>Documents</dt><dd>{impact.data.documents} <span className="a-hint">(document storage arrives in a later phase)</span></dd>
            <dt>Categories filed here</dt><dd>{categories.map((c) => c.name).join(", ") || "None"}</dd>
            <dt>Filing rules targeting it</dt><dd>{rules.map((r) => r.name).join(", ") || "None"}</dd>
            <dt>Permission grants</dt><dd>{impact.data.permission_grants}</dd>
          </dl>
        ) : <Loading what="usage" />}
      </div>
    </Panel>
  );
}

function MoveDialog({ ctx, node, onClose }: { ctx: Ctx; node: Node; onClose: () => void }) {
  const change = useChange(ctx);
  const blocked = useMemo(() => {
    const s = new Set<string>([node.id]);
    let grew = true;
    while (grew) { grew = false; for (const n of ctx.ws.nodes) if (n.parent_id && s.has(n.parent_id) && !s.has(n.id)) { s.add(n.id); grew = true; } }
    return s;
  }, [ctx.ws.nodes, node.id]);
  const [dest, setDest] = useState(node.parent_id ?? "");
  return (
    <FormDialog title={`Move “${node.label}”`} submit="Move" onClose={onClose} disabled={!dest || dest === node.parent_id}
      onSubmit={async () => { await change("POST", `/nodes/${node.id}/move`, { parent_id: dest }, "Moved."); }}>
      <p className="a-hint">The folder and everything inside it move together. A folder cannot be moved into itself or its own subfolders.</p>
      <NodeSelect nodes={ctx.ws.nodes} value={dest} onChange={setDest} label="New location" exclude={blocked} />
    </FormDialog>
  );
}

function ArchiveDialog({ ctx, node, onClose }: { ctx: Ctx; node: Node; onClose: () => void }) {
  const change = useChange(ctx);
  const restoring = !!node.archived_at;
  const impact = useTenantApi<Impact>(ctx.orgId, `/companies/${ctx.companyId}/workspace/nodes/${node.id}/impact`);
  return (
    <FormDialog title={`${restoring ? "Restore" : "Archive"} “${node.label}”`} submit={restoring ? "Restore" : "Archive"} danger={!restoring} onClose={onClose}
      onSubmit={async () => { await change("POST", `/nodes/${node.id}/${restoring ? "restore" : "archive"}`, { confirm_impact: true }, restoring ? "Restored." : "Archived."); }}>
      {restoring ? <p>Restoring brings back this item and everything archived with it. Its parent must be active and its name still free.</p> : (
        <>
          <p>Archiving hides this item and its subfolders from new filing, upload pickers and the mobile app. Nothing is deleted and existing documents stay where they are. You can restore it at any time.</p>
          {impact.data ? <Alert tone={impact.data.categories || impact.data.filing_rules || impact.data.subfolders ? "warning" : "info"}>
            Affects {impact.data.subfolders} subfolder(s), {impact.data.categories} categor{impact.data.categories === 1 ? "y" : "ies"}, {impact.data.filing_rules} filing rule(s) (they will be switched off) and {impact.data.documents} document(s).
          </Alert> : <Loading what="impact" />}
        </>
      )}
    </FormDialog>
  );
}

