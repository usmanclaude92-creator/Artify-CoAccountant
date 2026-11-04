import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { DocCounts, DocDetail, DocEvent, DocPage, DocSummary, Preview, QueueItem, StagedFile, UploadConfig, WsNode } from "../bridge/types";
import { fmtBytes, fmtDateTime } from "../lib/format";
import { buildTree, labelOf, type TreeNode } from "../lib/tree";
import { useApp } from "../state/app";

const PAGE_SIZE = 25;
const EVENT_LABEL: Record<string, string> = {
  "document.created": "Imported", "document.version_added": "New version", "document.updated": "Edited", "document.moved": "Moved", "document.archived": "Archived",
  "document.restored": "Restored", "document.deleted": "Deleted", "document.undeleted": "Recovered", "document.downloaded": "Downloaded", "document.viewed": "Viewed",
  "document.version_restored": "Version restored", "document.purged": "Permanently deleted", "document.hold_set": "Retention hold set", "document.hold_released": "Retention hold released",
};

function useCompany() {
  const { bridge, org, company, fail } = useApp();
  return { bridge, orgId: org?.id ?? "", companyId: company?.id ?? "", fail, companyName: company?.name ?? "" };
}

function NoCompany() {
  const { go } = useApp();
  return (
    <section className="d-page"><h1>Documents</h1>
      <Panel><p>Open a company first to see its documents.</p><Button onClick={() => go("companies")}>Choose a company</Button></Panel></section>
  );
}

// ------------------------------------------------------------------ viewer
function b64Blob(b64: string, mime: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

export function Viewer({ preview, onClose }: { preview: Preview; onClose: () => void }) {
  const [zoom, setZoom] = useState(1);
  const [rot, setRot] = useState(0);
  const [fit, setFit] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const isPdf = preview.mime === "application/pdf";
  const url = useMemo(() => URL.createObjectURL(b64Blob(preview.data_base64, preview.mime)), [preview]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  const print = () => (isPdf ? frame.current?.contentWindow?.print() : window.print());
  return (
    <div className="d-viewer" ref={box} role="region" aria-label={`Preview of ${preview.name}`}>
      <div className="d-viewer__bar">
        <strong>{preview.name}</strong>
        {!isPdf ? (
          <>
            <Button variant="secondary" className="a-btn--sm" onClick={() => setZoom((z) => Math.min(z * 1.25, 8))} aria-label="Zoom in">+</Button>
            <Button variant="secondary" className="a-btn--sm" onClick={() => setZoom((z) => Math.max(z / 1.25, 0.1))} aria-label="Zoom out">−</Button>
            <Button variant="secondary" className="a-btn--sm" onClick={() => { setFit(true); setZoom(1); }}>Fit to window</Button>
            <Button variant="secondary" className="a-btn--sm" onClick={() => { setFit(false); setZoom(1); }}>Actual size</Button>
            <Button variant="secondary" className="a-btn--sm" onClick={() => setRot((r) => (r + 90) % 360)}>Rotate</Button>
          </>
        ) : <span className="a-hint">Paging, zoom and rotation use the built-in PDF viewer.</span>}
        <Button variant="secondary" className="a-btn--sm" onClick={() => void box.current?.requestFullscreen?.()}>Full screen</Button>
        <Button variant="secondary" className="a-btn--sm" onClick={print}>Print</Button>
        <Button variant="secondary" className="a-btn--sm" onClick={onClose}>Close preview</Button>
      </div>
      <div className="d-viewer__stage d-print-area">
        {isPdf ? <iframe ref={frame} title={`PDF ${preview.name}`} src={url} className="d-viewer__pdf" sandbox="allow-same-origin allow-modals" />
          : <img src={url} alt={preview.name} className={fit ? "d-viewer__img d-viewer__img--fit" : "d-viewer__img"} style={{ transform: `scale(${zoom}) rotate(${rot}deg)` }} />}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ tree
function flat(forest: TreeNode[], depth = 0): { node: WsNode; depth: number }[] {
  return forest.flatMap((t) => [{ node: t.node, depth }, ...flat(t.children, depth + 1)]);
}

function Tree({ forest, counts, selected, onSelect }: { forest: TreeNode[]; counts: DocCounts | null; selected: string | null; onSelect: (id: string | null) => void }) {
  return (
    <nav aria-label="Workspace folders" className="d-doctree">
      <ul>
        <li><button type="button" className="d-nav" aria-current={selected === null ? "true" : undefined} onClick={() => onSelect(null)}><span>All documents</span><span className="d-badge">{counts?.totals.active ?? 0}</span></button></li>
        {flat(forest).map(({ node, depth }) => (
          <li key={node.id} style={{ paddingInlineStart: `${depth * 0.9}rem` }}>
            <button type="button" className="d-nav" aria-current={selected === node.id ? "true" : undefined} onClick={() => onSelect(node.id)}>
              <span>{labelOf(node)}</span>{(counts?.nodes[node.id]?.active ?? 0) > 0 ? <span className="d-badge">{counts?.nodes[node.id]?.active}</span> : null}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

// ------------------------------------------------------------------ all / archived documents
export function Documents({ mode }: { mode: "active" | "archived" }) {
  const { bridge, orgId, companyId, fail } = useCompany();
  const [nodes, setNodes] = useState<WsNode[]>([]);
  const [counts, setCounts] = useState<DocCounts | null>(null);
  const [node, setNode] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<"archived" | "deleted">("archived");
  const [sort, setSort] = useState("created");
  const [order, setOrder] = useState("desc");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<DocPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const effective = mode === "active" ? "active" : status;

  useEffect(() => {
    if (!orgId || !companyId) return;
    let live = true;
    bridge.workspaceGet(orgId, companyId).then((w) => live && setNodes(w.data.nodes)).catch((e) => live && fail(e));
    return () => { live = false; };
  }, [bridge, orgId, companyId, fail]);

  useEffect(() => {
    if (!orgId || !companyId) return;
    let live = true;
    bridge.docsCounts(orgId, companyId).then((c) => live && setCounts(c)).catch(() => undefined);
    bridge.docsList(orgId, companyId, { node_id: node ?? undefined, include_subfolders: true, status: effective, q: q || undefined, sort, order, limit: PAGE_SIZE, offset })
      .then((p) => { if (live) { setPage(p); setError(null); } })
      .catch((e) => { if (live) { const m = fail(e); if (m) setError(m); } });
    return () => { live = false; };
  }, [bridge, orgId, companyId, node, q, sort, order, offset, effective, tick, fail]);

  useEffect(() => {
    const off = bridge.on("documents-changed", reload);
    return () => { void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, reload]);

  const forest = useMemo(() => buildTree(nodes, false), [nodes]);
  if (!companyId) return <NoCompany />;
  const total = page?.total ?? 0;
  const title = mode === "active" ? "All Documents" : "Archived Documents";
  return (
    <section className="d-page d-docs" aria-labelledby="h-docs">
      <h1 id="h-docs">{title}</h1>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="d-docs__grid">
        <Panel className="d-docs__tree"><Tree forest={forest} counts={counts} selected={node} onSelect={(id) => { setNode(id); setOffset(0); setSel(null); }} /></Panel>
        <div className="d-docs__main">
          <div className="d-docs__tools">
            <label className="d-inline"><span className="a-visually-hidden">Search documents</span>
              <input type="search" placeholder="Search by name" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} /></label>
            {mode === "archived" ? (
              <label className="d-inline"><span className="a-visually-hidden">Show</span>
                <select value={status} onChange={(e) => { setStatus(e.target.value as "archived" | "deleted"); setOffset(0); setSel(null); }}><option value="archived">Archived</option><option value="deleted">Deleted items</option></select></label>
            ) : null}
            <label className="d-inline"><span className="a-visually-hidden">Sort by</span>
              <select value={`${sort}:${order}`} onChange={(e) => { const [s, o] = e.target.value.split(":"); setSort(s ?? "created"); setOrder(o ?? "desc"); }}>
                <option value="created:desc">Newest first</option><option value="created:asc">Oldest first</option><option value="name:asc">Name A–Z</option><option value="name:desc">Name Z–A</option><option value="size:desc">Largest first</option></select></label>
          </div>
          {page === null ? <p role="status">Loading documents…</p> : page.items.length === 0 ? (
            <Panel><p>{q ? "No document matches your search." : mode === "active" ? "No documents here yet. Use Import Documents to add files." : "Nothing here."}</p></Panel>
          ) : (
            <table className="d-table">
              <caption className="a-visually-hidden">Documents</caption>
              <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Size</th><th scope="col">Added</th><th scope="col">Version</th></tr></thead>
              <tbody>{page.items.map((d: DocSummary) => (
                <tr key={d.id} aria-selected={sel === d.id} className={sel === d.id ? "d-row--sel" : undefined}>
                  <th scope="row"><button type="button" className="d-linkbtn" onClick={() => setSel(d.id)}>{d.display_name}</button></th>
                  <td>{d.extension.toUpperCase()}</td><td>{fmtBytes(d.size_bytes)}</td><td>{fmtDateTime(d.created_at)}</td><td>v{d.current_version_no}</td>
                </tr>))}</tbody>
            </table>
          )}
          {total > PAGE_SIZE ? (
            <div className="d-pager" role="navigation" aria-label="Pages">
              <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
              <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
              <Button variant="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
            </div>) : null}
        </div>
        {sel ? <Details key={sel} docId={sel} nodes={nodes} onChanged={() => { reload(); }} onGone={() => { setSel(null); reload(); }} /> : null}
      </div>
    </section>
  );
}

function Details({ docId, nodes, onChanged, onGone }: { docId: string; nodes: WsNode[]; onChanged: () => void; onGone: () => void }) {
  const { bridge, orgId, companyId, fail } = useCompany();
  const [d, setD] = useState<DocDetail | null>(null);
  const [events, setEvents] = useState<DocEvent[]>([]);
  const [msg, setMsg] = useState<{ tone: "success" | "danger" | "info"; text: string } | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [moveTo, setMoveTo] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    bridge.docsGet(orgId, companyId, docId).then(setD).catch((e) => { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); });
    bridge.docsEvents(orgId, companyId, docId).then((r) => setEvents(r.items)).catch(() => undefined);
  }, [bridge, orgId, companyId, docId, fail]);
  useEffect(() => { load(); }, [load]);

  const run = async (label: string, f: () => Promise<unknown>, gone = false) => {
    setBusy(true); setMsg(null);
    try { await f(); setMsg({ tone: "success", text: label }); onChanged(); if (gone) onGone(); else load(); }
    catch (e) { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); }
    finally { setBusy(false); }
  };
  if (!d) return <Panel className="d-docs__details"><p role="status">Loading details…</p>{msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}</Panel>;
  const targets = flat(buildTree(nodes, false)).filter((n) => n.node.id !== d.node_id);
  const active = d.status === "active";
  return (
    <section className="a-panel d-docs__details" aria-label={`Details of ${d.display_name}`}>
      <h2>{d.display_name}</h2>
      <StatusChip tone={active ? "success" : "neutral"}>{d.status === "deleted" ? "Deleted" : d.status === "archived" ? "Archived" : "Active"}</StatusChip>
      {msg ? <Alert tone={msg.tone}>{msg.text}</Alert> : null}
      <dl className="a-dl">
        <dt>Location</dt><dd>{d.location.length ? d.location.join(" › ") : "—"}</dd>
        <dt>Size</dt><dd>{fmtBytes(d.size_bytes)}</dd><dt>Version</dt><dd>{d.current_version_no}</dd>
        <dt>Added by</dt><dd>{d.created_by_name ?? "—"} · {fmtDateTime(d.created_at)}</dd>
        <dt>Malware scan</dt><dd>{d.scan_status === "clean" ? "Clean" : d.scan_status === "not_scanned" ? "Not scanned (no scanner configured)" : d.scan_status}</dd>
        {d.retention_hold ? <><dt>Retention</dt><dd>On hold — cannot be permanently deleted</dd></> : null}
      </dl>
      {d.duplicates.length ? <Alert tone="warning">Identical content also exists as: {d.duplicates.map((x) => x.display_name).join(", ")}.</Alert> : null}
      {d.node_archived ? <Alert tone="warning">Its folder is archived; restoring requires the folder to be restored first.</Alert> : null}
      <div className="d-actions">
        {active ? <Button disabled={busy} onClick={() => void run("Opened with your default program.", () => bridge.docsOpen(orgId, companyId, d.id, null))}>Open</Button> : null}
        {active ? <Button variant="secondary" disabled={busy} onClick={() => void run("Copy saved to the company folder.", () => bridge.docsSaveCopy(orgId, companyId, d.id, null))}>Save a copy</Button> : null}
        {active ? <Button variant="secondary" disabled={busy} onClick={() => void run("Opened the folder.", () => bridge.mirrorOpen(orgId, companyId, d.node_id))}>Show in folder</Button> : null}
        {d.previewable && active ? <Button variant="secondary" disabled={busy} onClick={() => { setBusy(true); bridge.docsPreview(orgId, companyId, d.id, null).then(setPreview).catch((e) => { const m = fail(e); if (m) setMsg({ tone: "danger", text: m }); }).finally(() => setBusy(false)); }}>Preview</Button>
          : active ? <span className="a-hint">No in-app preview for this file type; use Open.</span> : null}
      </div>
      {preview ? <Viewer preview={preview} onClose={() => setPreview(null)} /> : null}
      {active && d.can.rename ? (
        <form className="d-actions" onSubmit={(e) => { e.preventDefault(); if (name && name !== d.display_name) void run("Renamed.", () => bridge.docsPatch(orgId, companyId, d.id, { display_name: name, base_row_version: d.row_version })); }}>
          <label className="d-inline">Rename<input value={name ?? d.display_name} onChange={(e) => setName(e.target.value)} /></label><Button type="submit" variant="secondary" disabled={busy}>Rename</Button>
        </form>) : null}
      {active && d.can.move && targets.length ? (
        <form className="d-actions" onSubmit={(e) => { e.preventDefault(); if (moveTo) void run("Moved. The file itself was not touched.", () => bridge.docsMove(orgId, companyId, d.id, moveTo, null, d.row_version)); }}>
          <label className="d-inline">Move to<select value={moveTo} onChange={(e) => setMoveTo(e.target.value)}><option value="">Choose a folder…</option>{targets.map(({ node, depth }) => <option key={node.id} value={node.id}>{"— ".repeat(depth)}{labelOf(node)}</option>)}</select></label>
          <Button type="submit" variant="secondary" disabled={busy || !moveTo}>Move</Button>
        </form>) : null}
      <div className="d-actions">
        {active && d.can.archive ? <Button variant="secondary" disabled={busy} onClick={() => void run("Archived.", () => bridge.docsAction(orgId, companyId, d.id, "archive"), true)}>Archive</Button> : null}
        {d.status === "archived" ? <Button disabled={busy} onClick={() => void run("Restored.", () => bridge.docsAction(orgId, companyId, d.id, "restore"), true)}>Restore</Button> : null}
        {d.status === "deleted" ? <Button disabled={busy} onClick={() => void run("Recovered.", () => bridge.docsAction(orgId, companyId, d.id, "undelete"), true)}>Recover</Button> : null}
        {active ? <Button variant="secondary" disabled={busy} onClick={() => void run("Duplicated.", () => bridge.docsAction(orgId, companyId, d.id, "duplicate"))}>Duplicate</Button> : null}
        {d.status !== "deleted" && d.can.delete ? <Button variant="secondary" disabled={busy} onClick={() => { if (window.confirm(`Move “${d.display_name}” to the deleted items? You can recover it later.`)) void run("Moved to deleted items.", () => bridge.docsAction(orgId, companyId, d.id, "delete"), true); }}>Delete</Button> : null}
        {d.status === "deleted" ? <Button variant="secondary" disabled={busy || d.retention_hold} onClick={() => { if (window.confirm(`Permanently delete “${d.display_name}” and all of its versions? This cannot be undone.`)) void run("Permanently deleted.", () => bridge.docsAction(orgId, companyId, d.id, "purge"), true); }}>Delete permanently</Button> : null}
      </div>
      {d.metadata.length ? (<><h3>Details</h3><dl className="a-dl">{d.metadata.map((m) => <><dt key={`k${m.key}`}>{m.label}</dt><dd key={`v${m.key}`}>{String(m.value)}{m.applies ? "" : " (not used in this folder)"}</dd></>)}</dl></>) : null}
      <h3>Versions</h3>
      <ul className="d-list">{d.versions.map((v) => (
        <li key={v.version_no}>v{v.version_no} · {fmtBytes(v.size_bytes)} · {fmtDateTime(v.uploaded_at)}{v.uploaded_by_name ? ` · ${v.uploaded_by_name}` : ""}{v.change_reason ? ` — ${v.change_reason}` : ""}
          {active && v.version_no !== d.current_version_no ? <> <Button variant="secondary" className="a-btn--sm" disabled={busy} onClick={() => void run(`Version ${v.version_no} restored as a new version.`, () => bridge.docsRestoreVersion(orgId, companyId, d.id, v.version_no))}>Restore this version</Button></> : null}</li>))}</ul>
      <h3>Activity</h3>
      {events.length === 0 ? <p className="a-hint">No activity recorded.</p> : <ul className="d-list">{events.slice(0, 12).map((e) => <li key={e.id}>{EVENT_LABEL[e.event_type] ?? e.event_type} · {e.actor_name ?? "system"} · {fmtDateTime(e.created_at)}</li>)}</ul>}
    </section>
  );
}

// ------------------------------------------------------------------ import
interface Row { file: StagedFile; node: string; name: string; category: string; policy: "block" | "allow_copy"; suggestion: string | null }

export function ImportDocuments() {
  const { bridge, orgId, companyId, fail } = useCompany();
  const { go } = useApp();
  const [cfg, setCfg] = useState<UploadConfig | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [refused, setRefused] = useState<StagedFile[]>([]);
  const [dups, setDups] = useState<Record<string, string[]>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!orgId || !companyId) return;
    let live = true;
    bridge.uploadConfig(orgId, companyId).then((c) => live && setCfg(c)).catch((e) => live && fail(e));
    return () => { live = false; };
  }, [bridge, orgId, companyId, fail]);

  const accept = useCallback((files: StagedFile[]) => {
    setRefused((r) => [...r, ...files.filter((f) => f.problem)]);
    const ok = files.filter((f) => !f.problem);
    setRows((r) => [...r, ...ok.map((file): Row => ({ file, node: "", name: "", category: "", policy: "block", suggestion: null }))]);
    for (const f of ok) {
      if (!f.sha256) continue;
      // identical content already in THIS company? (advisory; the server decides again when the file is registered)
      bridge.docsDuplicates(orgId, companyId, f.sha256)
        .then((r) => { if (r.duplicates.length) setDups((d) => ({ ...d, [f.id]: r.duplicates.map((x) => x.display_name) })); })
        .catch(() => undefined);
    }
  }, [bridge, orgId, companyId]);

  useEffect(() => {
    const off = bridge.on("files-dropped", (p) => { setDragging(false); accept(p as StagedFile[]); });
    return () => { void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, accept]);

  if (!companyId) return <NoCompany />;
  const targets = cfg?.targets ?? [];
  const cats = cfg?.categories ?? [];
  const set = (id: string, patch: Partial<Row>) => setRows((r) => r.map((x) => (x.file.id === id ? { ...x, ...patch } : x)));
  const pick = async () => { try { accept(await bridge.importPick()); } catch (e) { const m = fail(e); if (m) setMsg(m); } };
  const suggest = async (r: Row) => {
    try {
      const s = await bridge.docsSuggest(orgId, companyId, { filename: r.file.name, category_id: r.category || null, node_id: r.node || null });
      set(r.file.id, { suggestion: s.naming.suggested_name, ...(s.filing.node_id && !r.node ? { node: s.filing.node_id } : {}), ...(s.filing.category_id && !r.category ? { category: s.filing.category_id } : {}) });
    } catch (e) { const m = fail(e); if (m) setMsg(m); }
  };
  const ready = rows.length > 0 && rows.every((r) => r.node);
  const start = async () => {
    try {
      const batch = `import-${Date.now()}`;
      await bridge.queueAdd(orgId, companyId, rows.map((r) => ({ staged_id: r.file.id, node_id: r.node, category_id: r.category || null, display_name: r.name || r.suggestion || null, duplicate_policy: r.policy, batch_local: batch })));
      setRows([]); setRefused([]);
      go("queue");
    } catch (e) { const m = fail(e); if (m) setMsg(m); }
  };
  return (
    <section className="d-page" aria-labelledby="h-import">
      <h1 id="h-import">Import Documents</h1>
      <div className={`d-drop${dragging ? " d-drop--on" : ""}`} onDragEnter={() => setDragging(true)} onDragLeave={() => setDragging(false)} onDragOver={(e) => e.preventDefault()} onDrop={(e) => e.preventDefault()}>
        <p>Drag files here, or</p><Button onClick={() => void pick()}>Choose files…</Button>
        <p className="a-hint">PDF, images (JPG, PNG, TIFF, BMP, HEIC), Word, Excel, CSV and text files. Each file is checked again by the server; nothing counts as imported until the server confirms it.</p>
      </div>
      {msg ? <Alert tone="danger">{msg}</Alert> : null}
      {targets.length === 0 && cfg ? <Alert tone="warning">This company has no folders you can file into. Ask an administrator to set up the workspace or grant you upload access.</Alert> : null}
      {refused.length ? <Alert tone="warning"><strong>Not added:</strong><ul>{refused.map((f, i) => <li key={`${f.name}${i}`}>{f.name}: {f.problem}</li>)}</ul></Alert> : null}
      {rows.length ? (
        <>
          <table className="d-table">
            <caption>Review before importing</caption>
            <thead><tr><th scope="col">File</th><th scope="col">Destination</th><th scope="col">Category</th><th scope="col">Name in workspace</th><th scope="col">If it already exists</th><th scope="col"><span className="a-visually-hidden">Remove</span></th></tr></thead>
            <tbody>{rows.map((r) => {
              const allowed = cats.filter((c) => !c.node_id || c.node_id === r.node || true);
              return (
                <tr key={r.file.id}>
                  <th scope="row">{r.file.name}<span className="a-hint" style={{ display: "block" }}>{fmtBytes(r.file.size)}</span></th>
                  <td><select aria-label={`Destination for ${r.file.name}`} value={r.node} onChange={(e) => set(r.file.id, { node: e.target.value })}><option value="">Choose…</option>{targets.map((t) => <option key={t.id} value={t.id}>{"— ".repeat(Math.max(0, t.depth - 1))}{t.label}</option>)}</select></td>
                  <td><select aria-label={`Category for ${r.file.name}`} value={r.category} onChange={(e) => set(r.file.id, { category: e.target.value })}><option value="">None</option>{allowed.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></td>
                  <td><input aria-label={`Name for ${r.file.name}`} value={r.name} placeholder={r.suggestion ?? r.file.name} onChange={(e) => set(r.file.id, { name: e.target.value })} />
                    <Button variant="secondary" className="a-btn--sm" onClick={() => void suggest(r)}>Suggest</Button>{r.suggestion ? <span className="a-hint" style={{ display: "block" }}>Suggested: {r.suggestion}</span> : null}</td>
                  <td><select aria-label={`Duplicate handling for ${r.file.name}`} value={r.policy} onChange={(e) => set(r.file.id, { policy: e.target.value as Row["policy"] })}><option value="block">Don’t import it</option><option value="allow_copy">Keep both</option></select>
                    {dups[r.file.id]?.length ? <span className="a-hint">Same content as {dups[r.file.id]?.join(", ")}</span> : null}</td>
                  <td><Button variant="secondary" className="a-btn--sm" onClick={() => { void bridge.importUnstage(r.file.id); setRows((x) => x.filter((y) => y.file.id !== r.file.id)); }}>Remove</Button></td>
                </tr>);
            })}</tbody>
          </table>
          <div className="d-actions"><Button onClick={() => void start()} disabled={!ready}>Import {rows.length} file{rows.length === 1 ? "" : "s"}</Button>{!ready ? <span className="a-hint">Choose a destination for every file.</span> : null}</div>
        </>) : null}
    </section>
  );
}

// ------------------------------------------------------------------ upload queue
const STATUS: Record<string, { label: string; tone: "info" | "success" | "danger" | "warning" | "neutral" }> = {
  preparing: { label: "Preparing", tone: "info" }, waiting: { label: "Waiting", tone: "neutral" }, uploading: { label: "Uploading", tone: "info" },
  verification_pending: { label: "Verification pending", tone: "warning" }, completed: { label: "Completed", tone: "success" }, failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "neutral" }, quarantined: { label: "Quarantined", tone: "danger" },
};

export function UploadQueue() {
  const { bridge, companyId, fail } = useCompany();
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => { bridge.queueList(null).then(setItems).catch((e) => { const m = fail(e); if (m) setMsg(m); }); }, [bridge, fail]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const offs = [bridge.on("queue-changed", load), bridge.on("queue-progress", (p) => {
      const u = p as { id: string; bytes_sent: number };
      setItems((it) => it?.map((x) => (x.id === u.id ? { ...x, bytes_sent: u.bytes_sent } : x)) ?? it);
    })];
    return () => { offs.forEach((o) => void o.then((f) => f()).catch(() => undefined)); };
  }, [bridge, load]);
  const act = async (f: () => Promise<unknown>) => { try { await f(); load(); } catch (e) { const m = fail(e); if (m) setMsg(m); } };
  if (!companyId && items === null) return <NoCompany />;
  const list = items ?? [];
  return (
    <section className="d-page" aria-labelledby="h-queue">
      <h1 id="h-queue">Upload Queue</h1>
      <p className="a-hint">A file is “Completed” only after the server has stored and verified it. Unfinished uploads survive closing the app and continue when you sign in again.</p>
      {msg ? <Alert tone="danger">{msg}</Alert> : null}
      <div className="d-actions">
        <Button variant="secondary" onClick={() => void act(() => bridge.queueRun())}>Retry waiting uploads now</Button>
        <Button variant="secondary" onClick={() => void act(() => bridge.queueClearFinished(null))}>Clear finished</Button>
      </div>
      {items === null ? <p role="status">Loading…</p> : list.length === 0 ? <Panel><p>The queue is empty.</p></Panel> : (
        <table className="d-table">
          <caption className="a-visually-hidden">Uploads</caption>
          <thead><tr><th scope="col">File</th><th scope="col">Status</th><th scope="col">Progress</th><th scope="col">Details</th><th scope="col"><span className="a-visually-hidden">Actions</span></th></tr></thead>
          <tbody>{list.map((q) => {
            const s = STATUS[q.status] ?? { label: q.status, tone: "neutral" as const };
            const pct = q.size > 0 ? Math.min(100, Math.round((100 * q.bytes_sent) / q.size)) : 0;
            return (
              <tr key={q.id}>
                <th scope="row">{q.display_name ?? q.file_name}<span className="a-hint" style={{ display: "block" }}>{fmtBytes(q.size)}</span></th>
                <td><StatusChip tone={s.tone}>{s.label}</StatusChip></td>
                <td>{q.status === "uploading" ? <progress max={100} value={pct} aria-label={`${q.file_name} progress`}>{pct}%</progress> : q.status === "completed" ? "100%" : "—"}</td>
                <td>{q.error_message ?? (q.status === "verification_pending" ? "Sent. Waiting for the server to confirm it is stored." : "")}{q.duplicates?.length ? ` Existing: ${q.duplicates.map((d) => d.display_name).join(", ")}.` : ""}{q.attempts > 0 && q.status === "waiting" ? ` Retrying (attempt ${q.attempts}).` : ""}</td>
                <td className="d-actions">
                  {q.can_retry ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.queueRetry(q.id))}>Retry</Button> : null}
                  {["waiting", "preparing", "uploading", "verification_pending"].includes(q.status) ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.queueCancel(q.id))}>Cancel</Button> : null}
                  {!["uploading", "preparing"].includes(q.status) ? <Button variant="secondary" className="a-btn--sm" onClick={() => void act(() => bridge.queueRemove(q.id))}>Remove</Button> : null}
                </td>
              </tr>);
          })}</tbody>
        </table>)}
    </section>
  );
}
