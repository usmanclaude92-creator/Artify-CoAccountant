import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { RefreshOutcome, WorkspaceView } from "../bridge/types";
import { buildTree, countNodes, filterTree, findNode, labelOf, type TreeNode } from "../lib/tree";
import { fmtDateTime } from "../lib/format";
import { describe, isSessionExpired } from "../lib/errors";
import { useApp } from "../state/app";

const ACTIONS: [string, string][] = [["view", "View"], ["upload", "Upload"], ["edit", "Edit"], ["rename", "Rename"], ["move", "Move"], ["archive", "Archive"], ["delete", "Delete"], ["configure", "Configure"]];

export function Workspace() {
  const { bridge, org, company, fail, go, settings } = useApp();
  const [view, setView] = useState<WorkspaceView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Set<string> | null>(null); // null = default (sections open)
  const [selected, setSelected] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [localDir, setLocalDir] = useState<{ path?: string; error?: string } | null>(null);
  const [team, setTeam] = useState<string | null>(null);

  const orgId = org?.id;
  const companyId = company?.id;
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!orgId || !companyId) return;
    let live = true;
    bridge.workspaceGet(orgId, companyId)
      .then((v) => { if (live) { setView(v); setError(null); setLoading(false); } })
      .catch((e) => { if (!live) return; const m = fail(e); if (m) setError(m); setLoading(false); });
    return () => { live = false; };
  }, [bridge, orgId, companyId, fail, tick]); // (the screen is keyed by company, so state starts fresh for each one)

  // assigned accountants are shown only to roles the server lets see them (anyone else gets an empty list)
  useEffect(() => {
    if (!orgId || !companyId) return;
    let live = true;
    bridge.companyAssignments(orgId, companyId)
      .then((a) => { if (live) setTeam(a.filter((x) => x.active).map((x) => `${x.display_name} (${x.assignment_type})`).join(", ") || null); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [bridge, orgId, companyId]);

  // a newer configuration arrived in the background: reload it; the selection survives by stable id
  useEffect(() => {
    const off = bridge.on("workspace-updated", (p) => {
      const out = p as RefreshOutcome;
      if (settings?.notifications !== "false" && out.summary) setBanner(out.summary);
      reload();
    });
    return () => { void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, reload, settings?.notifications]);

  const nodes = view?.data.nodes;
  const forest = useMemo(() => buildTree(nodes ?? [], showArchived), [nodes, showArchived]);
  const { forest: shown, expand } = useMemo(() => filterTree(forest, query), [forest, query]);
  const selectedNode = selected && nodes ? findNode(buildTree(nodes, true), selected) : null;
  const lostSelection = selected !== null && view !== null && !selectedNode;

  if (!company) {
    return <section className="d-page"><h1>Workspace</h1><Panel><p>Choose a company first. The workspace is different for every company.</p><Button onClick={() => go("companies")}>Go to My Companies</Button></Panel></section>;
  }
  const isOpen = (t: TreeNode) => (query ? expand.has(t.node.id) : open ? open.has(t.node.id) : t.node.node_type === "section");
  const toggle = (t: TreeNode) => setOpen((cur) => {
    const base = cur ?? new Set(forest.map((r) => r.node.id));
    const next = new Set(base);
    if (next.has(t.node.id)) next.delete(t.node.id); else next.add(t.node.id);
    return next;
  });
  const allIds = (ts: TreeNode[]): string[] => ts.flatMap((t) => [t.node.id, ...allIds(t.children)]);

  const render = (t: TreeNode): React.ReactNode => {
    const expanded = t.children.length > 0 && isOpen(t);
    return (
      <li key={t.node.id} role="treeitem" aria-expanded={t.children.length ? expanded : undefined} aria-selected={selected === t.node.id}>
        <div className="d-tree__row">
          {t.children.length ? <button type="button" className="d-tree__toggle" onClick={() => toggle(t)} aria-label={`${expanded ? "Collapse" : "Expand"} ${labelOf(t.node)}`}>{expanded ? "▾" : "▸"}</button> : <span className="d-tree__toggle" aria-hidden="true">·</span>}
          <button type="button" className={`d-tree__item ${t.node.archived_at ? "d-tree__item--muted" : ""}`} aria-current={selected === t.node.id} onClick={() => setSelected(t.node.id)}>
            {labelOf(t.node)}{t.node.archived_at ? " (archived)" : !t.node.is_active ? " (inactive)" : ""}
          </button>
        </div>
        {expanded ? <ul role="group">{t.children.map(render)}</ul> : null}
      </li>
    );
  };

  const prepareFolder = async () => {
    if (!org || !company) return;
    try { setLocalDir({ path: await bridge.companyDir(org.id, company.id) }); } catch (e) { if (isSessionExpired(e)) fail(e); else setLocalDir({ error: describe(e) }); }
  };

  return (
    <section aria-labelledby="h-ws" className="d-page">
      <header className="d-page__head">
        <div><h1 id="h-ws">Workspace</h1><p className="a-hint">{company.name} · configuration version {view?.version ?? "…"}{view ? ` · checked ${fmtDateTime(view.last_success_at)}` : ""}{team ? ` · Accountants: ${team}` : ""}</p></div>
        <Button variant="secondary" onClick={() => { setLoading(true); reload(); }} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</Button>
      </header>
      {banner ? <Alert tone="info">{banner} <button type="button" className="d-linkbtn" onClick={() => setBanner(null)}>Dismiss</button></Alert> : null}
      {view?.stale ? <Alert tone="warning">The latest workspace could not be confirmed ({view.last_error?.replace(/_/g, " ") ?? "offline"}). Showing the copy saved on this computer, last confirmed {fmtDateTime(view.last_success_at)}.</Alert> : null}
      {error && !view ? <Alert tone="danger">{error} <Button variant="secondary" className="a-btn--sm" onClick={() => { setLoading(true); reload(); }}>Try again</Button></Alert> : null}
      {loading && !view ? <p role="status">Loading the workspace…</p> : null}
      {view ? (
        <div className="d-split">
          <Panel className="d-split__nav">
            <div className="a-field"><label htmlFor="ws-search">Search folders</label><input id="ws-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a section or folder" /></div>
            <div className="d-actions">
              <Button variant="secondary" className="a-btn--sm" onClick={() => setOpen(new Set(allIds(forest)))}>Expand all</Button>
              <Button variant="secondary" className="a-btn--sm" onClick={() => setOpen(new Set())}>Collapse all</Button>
            </div>
            <label className="a-hint d-check"><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived items</label>
            {view.data.nodes.length === 0 ? (
              <p>This company has no sections yet. A firm administrator sets up the workspace in the Control Center; it will appear here automatically.</p>
            ) : shown.length === 0 ? <p>No section or folder matches “{query}”.</p> : (
              <ul role="tree" aria-label={`${company.name} workspace`} className="d-tree">{shown.map(render)}</ul>
            )}
            <p className="a-hint">{countNodes(forest)} item{countNodes(forest) === 1 ? "" : "s"} · structure comes from this company’s own configuration.</p>
          </Panel>
          <Panel className="d-split__detail" aria-live="polite">
            {lostSelection ? <Alert tone="info">The item you had selected was removed or is no longer visible to you.</Alert> : null}
            {selectedNode ? <Details t={selectedNode} view={view} onPrepare={prepareFolder} localDir={localDir} /> : <p>Select a section or folder to see its details.</p>}
          </Panel>
        </div>
      ) : null}
    </section>
  );
}

/** The server omits `view` from `can{}` (a delivered node is viewable by definition); show it first. */
function permissionText(can?: Record<string, boolean>): string {
  const extra = can ? ACTIONS.filter(([k]) => k !== "view" && can[k]).map(([, l]) => l) : [];
  return extra.length === 0 ? "View only" : ["View", ...extra].join(", ");
}

function Details({ t, view, onPrepare, localDir }: { t: TreeNode; view: WorkspaceView; onPrepare: () => void; localDir: { path?: string; error?: string } | null }) {
  const n = t.node;
  const cats = view.data.categories.filter((c) => c.node_id === n.id);
  return (
    <div className="d-details">
      <h2>{labelOf(n)}</h2>
      <p className="d-crumbs" aria-label="Location">{t.path.join(" / ")}</p>
      <p>{n.archived_at ? <StatusChip tone="neutral">Archived</StatusChip> : n.is_active ? <StatusChip tone="success">Active</StatusChip> : <StatusChip tone="warning">Inactive</StatusChip>} <StatusChip tone="info">{n.node_type === "section" ? "Section" : "Folder"}</StatusChip></p>
      {n.archived_at ? <Alert tone="info">Archived items cannot receive new documents. They are kept for existing content.</Alert> : null}
      <dl className="a-dl">
        <dt>Description</dt><dd>{n.description || "—"}</dd>
        <dt>Subfolders</dt><dd>{t.children.length}</dd>
        <dt>Document categories</dt><dd>{cats.length ? cats.map((c) => c.name).join(", ") : "None assigned"}</dd>
        <dt>Your permissions</dt><dd>{permissionText(n.can)}</dd>
        <dt>Identifier</dt><dd className="a-mono">{n.id}</dd>
      </dl>
      <div className="d-actions"><Button variant="secondary" onClick={onPrepare}>Prepare local company folder</Button></div>
      {localDir?.path ? <Alert tone="success">Local folder ready: <span className="a-mono">{localDir.path}</span>. Document folders are created here once document management is available.</Alert> : null}
      {localDir?.error ? <Alert tone="danger">{localDir.error}</Alert> : null}
    </div>
  );
}
