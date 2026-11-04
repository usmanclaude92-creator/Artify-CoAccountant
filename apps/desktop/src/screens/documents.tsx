import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { DocCounts, DocEvent, DocPage, DocumentRow, LocalCopy, WorkspaceData } from "../bridge/types";
import { Viewer } from "../components/viewer";
import { describe } from "../lib/errors";
import { eventLabel, docStatusLabel } from "../lib/documents";
import { fmtBytes, fmtDateTime } from "../lib/format";
import { buildTree, labelOf, type TreeNode } from "../lib/tree";
import { useApp } from "../state/app";
import { useWs } from "../state/useWs";

type Tab = "preview" | "details" | "versions" | "activity";

/** All Documents (status "active") and Archived Documents (status "archived"): same screen, different filter. */
export function Documents({ status }: { status: "active" | "archived" }) {
  const { bridge, org, company, go, fail } = useApp();
  const { data: ws, stale } = useWs();
  const [nodeId, setNodeId] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [family, setFamily] = useState("");
  const [sort, setSort] = useState("created");
  const [page, setPage] = useState<DocPage<DocumentRow> | null>(null);
  const [counts, setCounts] = useState<DocCounts | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const orgId = org?.id;
  const companyId = company?.id;

  useEffect(() => {
    if (!orgId || !companyId) return;
    let live = true;
    const query: Record<string, string> = { status, sort, order: sort === "name" ? "asc" : "desc", limit: "100" };
    if (nodeId) { query.node_id = nodeId; query.include_subfolders = "true"; }
    if (q.trim()) query.q = q.trim();
    if (family) query.family = family;
    Promise.all([bridge.documentsOp<DocPage<DocumentRow>>(orgId, companyId, "list", { query }), bridge.documentsOp<DocCounts>(orgId, companyId, "counts")])
      .then(([p, c]) => { if (live) { setPage(p); setCounts(c); setError(null); } })
      .catch((e) => { if (live) { const m = fail(e); if (m) setError(m); } });
    return () => { live = false; };
  }, [bridge, orgId, companyId, status, nodeId, q, family, sort, tick, fail]);

  useEffect(() => {
    const off = bridge.on("imports-updated", () => reload());
    return () => { void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, reload]);

  const forest = useMemo(() => buildTree(ws?.nodes ?? [], false), [ws]);
  const nodeName = useMemo(() => new Map((ws?.nodes ?? []).map((n) => [n.id, labelOf(n)])), [ws]);

  if (!company) {
    return <section className="d-page"><h1>{status === "active" ? "Documents" : "Archived Documents"}</h1><Panel><p>Choose a company first. Documents belong to one company.</p><Button onClick={() => go("companies")}>Go to My Companies</Button></Panel></section>;
  }
  const title = status === "active" ? "All Documents" : "Archived Documents";
  const count = (t: TreeNode) => counts?.by_node[t.node.id]?.[status] ?? 0;
  const render = (t: TreeNode): React.ReactNode => (
    <li key={t.node.id}>
      <button type="button" className="d-tree__item" aria-current={nodeId === t.node.id} onClick={() => { setNodeId(t.node.id); setSelected(null); }}>
        {labelOf(t.node)} <span className="a-hint">({count(t)})</span>
      </button>
      {t.children.length ? <ul>{t.children.map(render)}</ul> : null}
    </li>
  );
  const sel = page?.items.find((d) => d.id === selected) ?? null;

  return (
    <section className="d-page" aria-labelledby="h-docs">
      <header className="d-page__head">
        <div><h1 id="h-docs">{title}</h1><p className="a-hint">{company.name} · {counts?.totals[status] ?? "…"} document{counts?.totals[status] === 1 ? "" : "s"} you can see</p></div>
        {status === "active" ? <Button onClick={() => go("import")}>Import documents</Button> : null}
      </header>
      {stale ? <Alert tone="warning">Showing the saved folder structure; the latest could not be confirmed.</Alert> : null}
      {error ? <Alert tone="danger">{error} <Button variant="secondary" className="a-btn--sm" onClick={reload}>Try again</Button></Alert> : null}
      <div className="d-docs">
        <Panel className="d-split__nav">
          <button type="button" className="d-tree__item" aria-current={nodeId === null} onClick={() => { setNodeId(null); setSelected(null); }}>All folders <span className="a-hint">({counts?.totals[status] ?? 0})</span></button>
          {forest.length === 0 ? <p className="a-hint">This company has no folders you can see yet.</p> : <ul className="d-tree" aria-label="Folders">{forest.map(render)}</ul>}
        </Panel>
        <div className="d-docs__main">
          <Panel className="d-filters">
            <div className="a-field"><label htmlFor="doc-q">Search</label><input id="doc-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, file name or a detail value" /></div>
            <div className="a-field"><label htmlFor="doc-fam">Type</label>
              <select id="doc-fam" value={family} onChange={(e) => setFamily(e.target.value)}><option value="">All types</option><option value="pdf">PDF</option><option value="image">Images</option><option value="spreadsheet">Spreadsheets</option><option value="document">Documents</option><option value="text">Text</option></select></div>
            <div className="a-field"><label htmlFor="doc-sort">Sort by</label>
              <select id="doc-sort" value={sort} onChange={(e) => setSort(e.target.value)}><option value="created">Newest</option><option value="updated">Recently changed</option><option value="name">Name</option><option value="size">Size</option></select></div>
          </Panel>
          {page === null && !error ? <p role="status">Loading documents…</p> : null}
          {page && page.items.length === 0 ? <Panel><p>{q || family || nodeId ? "No documents match." : status === "active" ? "There are no documents here yet. Import documents to add the first ones." : "No archived documents."}</p></Panel> : null}
          {page && page.items.length > 0 ? (
            <table className="d-table">
              <caption className="a-visually-hidden">{title}</caption>
              <thead><tr><th scope="col">Name</th><th scope="col">Folder</th><th scope="col">Type</th><th scope="col">Size</th><th scope="col">Added</th><th scope="col">Version</th></tr></thead>
              <tbody>
                {page.items.map((d) => (
                  <tr key={d.id} aria-selected={selected === d.id}>
                    <th scope="row"><button type="button" className="d-linkbtn" onClick={() => setSelected(d.id)}>{d.display_name}</button></th>
                    <td>{nodeName.get(d.node_id) ?? "—"}</td><td>{d.extension.toUpperCase()}</td><td>{fmtBytes(d.size_bytes)}</td><td>{fmtDateTime(d.created_at)}</td><td>{d.current_version_number}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {page && page.total > page.items.length ? <p className="a-hint">Showing the first {page.items.length} of {page.total}. Narrow the search to see others.</p> : null}
          {sel ? <Detail key={sel.id} id={sel.id} ws={ws} onChanged={reload} onClose={() => setSelected(null)} /> : page && page.items.length > 0 ? <p className="a-hint">Select a document to see its preview and details.</p> : null}
        </div>
      </div>
    </section>
  );
}

function Detail({ id, ws, onChanged, onClose }: { id: string; ws: WorkspaceData | null; onChanged: () => void; onClose: () => void }) {
  const { bridge, org, company, fail } = useApp();
  const [doc, setDoc] = useState<DocumentRow | null>(null);
  const [events, setEvents] = useState<DocEvent[] | null>(null);
  const [tab, setTab] = useState<Tab>("preview");
  const [msg, setMsg] = useState<{ tone: "success" | "danger" | "info"; text: string } | null>(null);
  const [rename, setRename] = useState<string | null>(null);
  const [moveTo, setMoveTo] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"delete" | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewVersion, setPreviewVersion] = useState<number | null>(null);
  const orgId = org?.id ?? "";
  const companyId = company?.id ?? "";

  const load = useCallback(() => bridge.documentsOp<DocumentRow>(orgId, companyId, "get", { id }).then(setDoc).catch((e) => { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); }), [bridge, orgId, companyId, id, fail]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (tab !== "activity") return;
    bridge.documentsOp<DocPage<DocEvent>>(orgId, companyId, "events", { id }).then((p) => setEvents(p.items)).catch((e) => { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); });
  }, [tab, bridge, orgId, companyId, id, fail]);

  const act = async (label: string | ((r: unknown) => string), fn: () => Promise<unknown>) => {
    setBusy(true);
    setMsg(null);
    try { const r = await fn(); setMsg({ tone: "success", text: typeof label === "function" ? label(r) : label }); await load(); setEvents(null); onChanged(); } catch (e) { const m = fail(e); if (m) setMsg({ tone: "danger", text: describe(e) }); } finally { setBusy(false); }
  };
  const targets = useMemo(() => (ws?.nodes ?? []).filter((n) => !n.archived_at && n.is_active && n.can?.upload), [ws]);

  if (!doc) return <Panel><p role="status">Loading the document…</p>{msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}</Panel>;
  const can = doc.can;
  const op = (name: Parameters<typeof bridge.documentsOp>[2], args: Record<string, unknown> = {}) => bridge.documentsOp(orgId, companyId, name, { id, ...args });

  return (
    <Panel className="d-detail" aria-label={`Document ${doc.display_name}`}>
      <header className="d-page__head">
        <div><h2>{doc.display_name}</h2><p className="d-crumbs">{(doc.path ?? []).join(" / ")}</p></div>
        <div className="d-actions"><StatusChip tone={doc.status === "active" ? "success" : "neutral"}>{docStatusLabel(doc)}</StatusChip><Button variant="secondary" className="a-btn--sm" onClick={onClose}>Close</Button></div>
      </header>
      {msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}
      {doc.scan_status === "infected" ? <Alert tone="danger">This file was flagged by the safety scan and cannot be opened.</Alert> : null}
      <div className="d-actions" role="toolbar" aria-label="Document actions">
        <Button variant="secondary" disabled={busy || !can.download} onClick={() => void act((r) => { const c = r as LocalCopy; return c.already_present ? `Already saved: ${c.rel_path}` : `Saved to ${c.rel_path}`; }, () => bridge.documentDownload(orgId, companyId, id))}>Save a local copy</Button>
        <Button variant="secondary" disabled={busy || !can.download} onClick={() => void act("Opened.", () => bridge.documentOpen(orgId, companyId, id))}>Open in default app</Button>
        {can.edit ? <Button variant="secondary" disabled={busy} onClick={() => setRename(doc.display_name)}>Rename</Button> : null}
        {can.move ? <Button variant="secondary" disabled={busy} onClick={() => setMoveTo(doc.node_id)}>Move</Button> : null}
        {doc.status === "active" && can.archive ? <Button variant="secondary" disabled={busy} onClick={() => void act("Archived.", () => op("archive"))}>Archive</Button> : null}
        {doc.status === "archived" && can.archive ? <Button variant="secondary" disabled={busy} onClick={() => void act("Restored.", () => op("restore"))}>Restore</Button> : null}
        {can.delete ? <Button variant="secondary" disabled={busy} onClick={() => setConfirm("delete")}>Remove…</Button> : null}
      </div>
      {rename !== null ? (
        <form className="d-inline-form" onSubmit={(e) => { e.preventDefault(); const name = rename.trim(); if (name) void act("Renamed.", () => op("patch", { display_name: name, row_version: doc.row_version })).then(() => setRename(null)); }}>
          <div className="a-field"><label htmlFor="rn">New name</label><input id="rn" value={rename} onChange={(e) => setRename(e.target.value)} maxLength={255} autoFocus /></div>
          <Button type="submit" disabled={busy || !rename.trim()}>Save name</Button><Button type="button" variant="secondary" onClick={() => setRename(null)}>Cancel</Button>
        </form>
      ) : null}
      {moveTo !== null ? (
        <form className="d-inline-form" onSubmit={(e) => { e.preventDefault(); if (moveTo && moveTo !== doc.node_id) void act("Moved.", () => op("move", { node_id: moveTo, row_version: doc.row_version })).then(() => setMoveTo(null)); }}>
          <div className="a-field"><label htmlFor="mv">Move to</label>
            <select id="mv" value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>{targets.map((n) => <option key={n.id} value={n.id}>{labelOf(n)}</option>)}</select></div>
          <Button type="submit" disabled={busy || moveTo === doc.node_id}>Move here</Button><Button type="button" variant="secondary" onClick={() => setMoveTo(null)}>Cancel</Button>
        </form>
      ) : null}
      {confirm === "delete" ? (
        <form className="d-inline-form" onSubmit={(e) => { e.preventDefault(); if (reason.trim()) void act("Removed. An administrator can bring it back.", () => op("delete", { reason: reason.trim() })).then(() => { setConfirm(null); onClose(); }); }}>
          <div className="a-field"><label htmlFor="rs">Why is this being removed?</label><input id="rs" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} autoFocus /></div>
          <Button type="submit" disabled={busy || !reason.trim()}>Remove document</Button><Button type="button" variant="secondary" onClick={() => setConfirm(null)}>Cancel</Button>
        </form>
      ) : null}
      <div role="tablist" aria-label="Document sections" className="d-tabs">
        {(["preview", "details", "versions", "activity"] as Tab[]).map((t) => <button key={t} type="button" role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls="tabpanel" className="d-tab" onClick={() => setTab(t)}>{{ preview: "Preview", details: "Details", versions: "Versions", activity: "Activity" }[t]}</button>)}
      </div>
      <div role="tabpanel" id="tabpanel" aria-labelledby={`tab-${tab}`} className="d-tabpanel">
        {tab === "preview" ? (can.download ? <Viewer key={`${doc.id}-${doc.current_version_number}-${previewVersion ?? "current"}`} doc={doc} version={previewVersion} /> : <Alert tone="info">This document cannot be previewed.</Alert>) : null}
        {tab === "details" ? (
          <dl className="a-dl">
            <dt>Original file name</dt><dd>{doc.original_filename}</dd><dt>Type</dt><dd>{doc.mime_type}</dd><dt>Size</dt><dd>{fmtBytes(doc.size_bytes)}</dd>
            <dt>Added by</dt><dd>{doc.created_by_name ?? "—"} · {fmtDateTime(doc.created_at)}</dd><dt>Last changed</dt><dd>{fmtDateTime(doc.updated_at)}</dd>
            <dt>Source</dt><dd>{doc.source}</dd><dt>Safety check</dt><dd>{doc.scan_status === "clean" ? "Passed" : doc.scan_status === "infected" ? "Flagged" : doc.scan_status === "not_scanned" ? "Not scanned" : doc.scan_status}</dd>
            <dt>Fingerprint (SHA-256)</dt><dd className="a-mono">{doc.sha256}</dd>
            {doc.legal_hold ? <><dt>Legal hold</dt><dd>On: this document cannot be permanently deleted.</dd></> : null}
            {(ws?.fields ?? []).filter((f) => doc.field_values && f.key in doc.field_values).map((f) => <FieldRow key={f.key} label={f.label} value={doc.field_values?.[f.key]} />)}
          </dl>
        ) : null}
        {tab === "versions" ? (
          <ul className="d-list" aria-label="Versions">
            {(doc.versions ?? []).map((v) => (
              <li key={v.id} className="d-note">
                <strong>Version {v.version_number}</strong> {v.is_current ? <StatusChip tone="success">Current</StatusChip> : null}
                <span>{fmtBytes(v.size_bytes)} · {v.uploaded_by_name ?? "—"} · {fmtDateTime(v.created_at)}{v.change_reason ? ` · ${v.change_reason}` : ""}</span>
                {v.purged_at ? <span className="a-hint">Permanently deleted</span> : (
                  <span className="d-actions">
                    <Button variant="secondary" className="a-btn--sm" onClick={() => { setPreviewVersion(v.is_current ? null : v.version_number); setTab("preview"); }}>View</Button>
                    {!v.is_current && can.edit ? <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void act(`Version ${v.version_number} is now the current version.`, () => op("restore_version", { number: v.version_number, reason: "Restored from the desktop app" }))}>Make current</Button> : null}
                  </span>
                )}
              </li>
            ))}
          </ul>
        ) : null}
        {tab === "activity" ? (events === null ? <p role="status">Loading activity…</p> : (
          <ul className="d-list" aria-label="Activity">{events.map((e) => <li key={e.id} className="d-note"><strong>{eventLabel(e.event_type)}</strong><span>{e.actor_name ?? "System"}</span><span className="a-hint">{fmtDateTime(e.created_at)}</span></li>)}</ul>
        )) : null}
      </div>
    </Panel>
  );
}

function FieldRow({ label, value }: { label: string; value: unknown }) {
  const text = value === null || value === undefined ? "—" : typeof value === "object" ? ("amount" in (value as object) ? `${(value as { amount: string }).amount} ${(value as { currency: string }).currency}` : JSON.stringify(value)) : String(value);
  return <><dt>{label}</dt><dd>{text}</dd></>;
}
