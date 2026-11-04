/**
 * In-memory bridge for unit tests and for `VITE_BRIDGE=mock pnpm dev` (browser preview without Rust). It is NEVER selected
 * in a production build, and the dev preview shows a permanent "demo data" banner.
 */
import type { AppError, Assignment, Bridge, DocDetail, DocQuery, QueueItem, StagedFile, CompanyDetail, CompanyView, EventRow, Identity, LocalDevice, Organization, RefreshOutcome, RootReport, ServerDevice, Settings, SyncStatus, WorkspaceData, WorkspaceView, WsNode } from "./types";

export function node(id: string, parent: string | null, name: string, extra: Partial<WsNode> = {}): WsNode {
  return { id, parent_id: parent, node_type: parent ? "folder" : "section", key: id, name, display_name: null, description: "", position: 0, depth: 1, is_active: true, archived_at: null, ...extra };
}
const ws = (company: string, version: number, nodes: WsNode[]): WorkspaceData => ({ company_id: company, version, workspace_state: "configured", nodes: nodes.map((n, i) => ({ ...n, position: n.position || i })), categories: [] });

export const DEMO_COMPANIES: CompanyView[] = [
  { id: "c-alpha", name: "Alpha Trading LLC", code: "ALPHA", country: "OM", base_currency: "OMR", status: "active", favorite: false, last_opened_at: null },
  { id: "c-beta", name: "Beta Contracting", code: "BETA", country: "OM", base_currency: "OMR", status: "active", favorite: false, last_opened_at: null },
  { id: "c-gamma", name: "Gamma Retail", code: "GAMMA", country: "AE", base_currency: "AED", status: "suspended", favorite: false, last_opened_at: null },
];
export const DEMO_WORKSPACES: Record<string, WorkspaceData> = {
  "c-alpha": ws("c-alpha", 5, ["Bank Statements", "Receivables", "Payables", "Expenses"].map((n, i) => node(`a${i}`, null, n))),
  "c-beta": ws("c-beta", 13, [
    ...["Finance", "Sales", "Purchases", "Payroll", "VAT", "Projects", "Contracts", "Management Reports", "Fixed Assets", "Legal"].map((n, i) => node(`b${i}`, null, n)),
    node("b-bank", "b0", "Banking", { depth: 2 }), node("b-rec", "b0", "Reconciliation", { depth: 2 }),
    node("b-inv", "b1", "Customer Invoices", { depth: 2 }), node("b-cn", "b1", "Credit Notes", { depth: 2 }),
    node("b-muscat", "b-bank", "Bank of Muscat", { depth: 3 }), node("b-old", "b2", "Old suppliers", { depth: 2, archived_at: "2026-02-01T00:00:00Z" }),
  ]),
  "c-gamma": ws("c-gamma", 2, [node("g0", null, "Daily Sales Reports"), node("g1", null, "Card Settlements"), node("g2", null, "Stores")]),
};

export interface MockOptions {
  mfa?: boolean;
  orgs?: Organization[];
  companies?: Record<string, CompanyView[]>;
  workspaces?: Record<string, WorkspaceData>;
  denied?: string[];
  offline?: boolean;
  restore?: boolean;
  storageRoot?: string | null;
  notifications?: EventRow[];
  /** what the native file dialog returns */
  pickFiles?: StagedFile[];
  documents?: DocDetail[];
}
export type MockBridge = Bridge & {
  calls: string[];
  state: { docs: DocDetail[]; queue: QueueItem[]; opts: MockOptions; settings: Settings; workspaces: Record<string, WorkspaceData>; companies: Record<string, CompanyView[]>; signedIn: boolean; devices: ServerDevice[]; root: string | null };
  emit(event: string, payload?: unknown): void;
};

const err = (code: string, message: string, status?: number, retryable = false): AppError => ({ code, message, status: status ?? null, retryable });

export function createMockBridge(opts: MockOptions = {}): MockBridge {
  const orgs = opts.orgs ?? [{ id: "org-1", display_name: "Gulf Accounting Partners" }];
  const identity: Identity = { user_id: "u-1", email: "accountant@example.com", display_name: "Test Accountant", memberships: orgs.map((o) => ({ organization_id: o.id, organization_name: o.display_name, organization_status: "active", organization_role: "accountant", company_roles: {} })) };
  const handlers = new Map<string, ((p: unknown) => void)[]>();
  const calls: string[] = [];
  const state = {
    opts, docs: structuredClone(opts.documents ?? []), queue: [] as QueueItem[], signedIn: false, root: opts.storageRoot ?? null,
    settings: { theme: "system", language: "en", start_on_login: "false", default_company_id: "", notifications: "true", update_channel: "stable" } as Settings,
    workspaces: structuredClone(opts.workspaces ?? DEMO_WORKSPACES) as Record<string, WorkspaceData>,
    companies: structuredClone(opts.companies ?? { [orgs[0]?.id ?? "org-1"]: DEMO_COMPANIES }) as Record<string, CompanyView[]>,
    devices: [] as ServerDevice[],
  };
  const rec = (n: string) => calls.push(n);
  const needAuth = () => { if (!state.signedIn) throw err("session_expired", "Your session has ended. Please sign in again.", 401); };
  const down = () => { if (opts.offline) throw err("network_unavailable", "The server cannot be reached.", undefined, true); };
  const report = (path: string): RootReport => ({ path, exists: true, is_dir: true, writable: true, free_bytes: 120 * 1024 ** 3, status: "ok", warnings: [] });

  const b: MockBridge = {
    calls, state,
    emit: (e, p) => (handlers.get(e) ?? []).forEach((h) => h(p)),
    appInfo: async () => ({ name: "Artify Co-Accountant", version: "0.1.0", build: "mock", os: "windows", api_configured: true, api_base: "https://api.example.test/api/v1", db_schema_version: 1, db_recovered_from: null, data_dir: "C:\\Users\\demo\\AppData\\Roaming\\com.artify.coaccountant", update_configured: false }),
    authLogin: async (email, password) => {
      rec("authLogin"); down();
      if (password !== "correct" || !email) throw err("unauthenticated", "Invalid email or password.", 401);
      if (opts.mfa) return { status: "mfa_required", mfa_token: "challenge" };
      state.signedIn = true;
      return { status: "ok", identity };
    },
    authMfa: async (_t, code) => { rec("authMfa"); if (code !== "123456") throw err("unauthenticated", "Invalid code.", 401); state.signedIn = true; return identity; },
    authRestore: async () => { rec("authRestore"); if (opts.restore) { state.signedIn = true; return identity; } return null; },
    authLogout: async () => { rec("authLogout"); state.signedIn = false; },
    organizations: async () => { rec("organizations"); needAuth(); return { items: orgs, stale: !!opts.offline }; },
    companies: async (org) => { rec(`companies:${org}`); needAuth(); return { items: structuredClone((state.companies[org] ?? []).filter((c) => !(opts.denied ?? []).includes(c.id))), stale: !!opts.offline }; },
    companySelect: async (org, company) => {
      rec(`companySelect:${company}`); needAuth(); down();
      const c = (state.companies[org] ?? []).find((x) => x.id === company);
      if (!c || (opts.denied ?? []).includes(company)) throw err("company_unavailable", "You no longer have access to this company, or it does not exist.", 404);
      return structuredClone(c) as CompanyDetail;
    },
    companyFavorite: async (company, favorite) => { rec("companyFavorite"); for (const list of Object.values(state.companies)) for (const c of list) if (c.id === company) c.favorite = favorite; },
    companyAssignments: async (): Promise<Assignment[]> => { rec("companyAssignments"); return [{ user_id: "u-1", display_name: "Test Accountant", assignment_type: "primary", active: true }]; },
    workspaceGet: async (_org, company) => {
      rec(`workspaceGet:${company}`); needAuth();
      const data = state.workspaces[company];
      if (!data || (opts.denied ?? []).includes(company)) throw err("not_found", "Company not found.", 404);
      const view: WorkspaceView = { company_id: company, version: data.version, fetched_at: new Date().toISOString(), data, stale: !!opts.offline, last_success_at: opts.offline ? null : new Date().toISOString(), last_error: opts.offline ? "network_unavailable" : null };
      return structuredClone(view);
    },
    workspaceRefresh: async (_org, company): Promise<RefreshOutcome> => { rec(`workspaceRefresh:${company}`); needAuth(); down(); return { changed: false, version: state.workspaces[company]?.version ?? 0, summary: null, changes: [] }; },
    settingsGet: async () => ({ ...state.settings }),
    settingsSet: async (k, v) => { rec(`settingsSet:${k}`); (state.settings as Record<string, string>)[k] = v; },
    storagePick: async () => { rec("storagePick"); return "D:\\Artify"; },
    storageSetRoot: async (p) => { rec("storageSetRoot"); state.root = p; return report(p); },
    storageStatus: async () => (state.root ? report(state.root) : null),
    companyDir: async (_o, c) => { rec("companyDir"); if (!state.root) throw err("storage_not_configured", "Choose a storage location first."); return `${state.root}\\${c}`; },
    deviceEnsure: async (): Promise<LocalDevice> => { rec("deviceEnsure"); down(); const d: ServerDevice = { id: "dev-1", platform: "windows", name: "ACCOUNTING-PC-01", app_version: "0.1.0", status: "active", registered_at: new Date().toISOString(), last_seen_at: new Date().toISOString() }; state.devices = [d]; return { device_id: d.id, name: d.name, app_version: "0.1.0", registered_at: d.registered_at ?? "", last_seen_at: d.last_seen_at ?? null, status: "active" }; },
    devicesList: async () => structuredClone(state.devices),
    deviceRevoke: async (_o, id) => { rec("deviceRevoke"); state.devices = state.devices.map((d) => (d.id === id ? { ...d, status: "revoked" } : d)); },
    syncStatus: async (): Promise<SyncStatus> => ({ connectivity: { state: opts.offline ? "offline" : "online", last_checked_at: new Date().toISOString() }, api_configured: true, company: { last_success_at: opts.offline ? null : new Date().toISOString(), last_attempt_at: new Date().toISOString(), last_error: opts.offline ? "network_unavailable" : null, last_version: 5 }, cached_workspace_version: 5, pending: { pending: 0, in_progress: 0, failed: 0 } }),
    notificationsList: async () => opts.notifications ?? [],
    notificationsRead: async () => undefined,
    diagnosticsExport: async () => "C:\\Users\\demo\\AppData\\Roaming\\com.artify.coaccountant\\diagnostics\\artify-diagnostics.json",
    updateCheck: async () => ({ configured: false, available: false, version: null, message: "Automatic updates are not configured in this build (no signing key or update server)." }),
    importPick: async () => { rec("importPick"); return structuredClone(opts.pickFiles ?? []); },
    importUnstage: async () => undefined,
    queueAdd: async (org, company, items) => {
      rec("queueAdd"); needAuth();
      const picked = new Map((opts.pickFiles ?? []).map((f) => [f.id, f]));
      const added = items.map((it, i): QueueItem => {
        const f = picked.get(it.staged_id);
        return { id: `q-${state.queue.length + i + 1}`, org_id: org, company_id: company, file_name: f?.name ?? "file", display_name: it.display_name ?? null, size: f?.size ?? 0, status: "waiting", bytes_sent: 0, attempts: 0, node_id: it.node_id ?? null, category_id: it.category_id ?? null, target_document_id: it.target_document_id ?? null, error_code: null, error_message: null, duplicates: null, result_document_id: null, can_retry: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      });
      state.queue.push(...added);
      b.emit("queue-changed", { company_id: company });
      return structuredClone(added);
    },
    queueList: async (company) => structuredClone(state.queue.filter((q) => !company || q.company_id === company)),
    queueRun: async () => {
      rec("queueRun");
      const sum = { completed: 0, failed: 0, quarantined: 0, cancelled: 0, waiting: 0, stopped: null as string | null };
      for (const q of state.queue.filter((x) => x.status === "waiting")) {
        if (q.file_name.includes("evil")) { q.status = "quarantined"; q.error_code = "quarantined"; q.error_message = "Windows executables are not accepted."; sum.quarantined++; }
        else if (q.file_name.includes("bad")) { q.status = "failed"; q.error_code = "content_mismatch"; q.error_message = "The file is not a PDF document."; q.can_retry = true; sum.failed++; }
        else {
          const id = `doc-${state.docs.length + 1}`;
          q.status = "completed"; q.bytes_sent = q.size; q.result_document_id = id; sum.completed++;
          state.docs.push(mockDoc(id, q.node_id ?? "", q.display_name ?? q.file_name, q.size));
        }
      }
      b.emit("queue-changed", {}); b.emit("documents-changed", {});
      return sum;
    },
    queueRetry: async (id) => { const q = state.queue.find((x) => x.id === id); if (!q) throw err("not_found", "Unknown queue item."); q.status = "waiting"; q.error_code = null; q.error_message = null; q.can_retry = false; return structuredClone(q); },
    queueCancel: async (id) => { const q = state.queue.find((x) => x.id === id); if (!q) throw err("not_found", "Unknown queue item."); q.status = "cancelled"; return structuredClone(q); },
    queueRemove: async (id) => { const n = state.queue.length; state.queue = state.queue.filter((x) => x.id !== id); return state.queue.length < n; },
    queueClearFinished: async () => { const n = state.queue.length; state.queue = state.queue.filter((x) => !["completed", "cancelled"].includes(x.status)); return n - state.queue.length; },
    uploadConfig: async (_o, company) => ({ company_id: company, version: 1, targets: (state.workspaces[company]?.nodes ?? []).filter((n) => !n.archived_at).map((n) => ({ id: n.id, parent_id: n.parent_id, node_type: n.node_type, key: n.key, label: n.display_name || n.name, position: n.position, depth: n.depth })), categories: [], fields: [] }),
    docsList: async (_o, _c, q: DocQuery) => {
      rec("docsList");
      const status = q.status ?? "active";
      let items = state.docs.filter((d) => d.status === status && (!q.node_id || d.node_id === q.node_id) && (!q.q || d.display_name.toLowerCase().includes(q.q.toLowerCase())));
      const total = items.length;
      items = items.slice(q.offset ?? 0, (q.offset ?? 0) + (q.limit ?? 50));
      return structuredClone({ items, total, limit: q.limit ?? 50, offset: q.offset ?? 0 });
    },
    docsCounts: async () => {
      const nodes: Record<string, { own_active: number; active: number; archived: number; deleted: number }> = {};
      for (const d of state.docs) { const n = (nodes[d.node_id] ??= { own_active: 0, active: 0, archived: 0, deleted: 0 }); if (d.status === "active") { n.own_active++; n.active++; } else if (d.status === "archived") n.archived++; else n.deleted++; }
      return { nodes, totals: { active: state.docs.filter((d) => d.status === "active").length, archived: state.docs.filter((d) => d.status === "archived").length, bytes: state.docs.reduce((a, d) => a + d.size_bytes, 0) } };
    },
    docsGet: async (_o, _c, id) => { const d = state.docs.find((x) => x.id === id); if (!d) throw err("not_found", "Document not found.", 404); return structuredClone(d); },
    docsEvents: async () => ({ items: [] }),
    docsPatch: async (_o, _c, id, patch) => { rec("docsPatch"); const d = state.docs.find((x) => x.id === id); if (!d) throw err("not_found", "Document not found.", 404); if (patch.display_name) d.display_name = patch.display_name.endsWith(`.${d.extension}`) ? patch.display_name : `${patch.display_name}.${d.extension}`; d.row_version++; return structuredClone(d); },
    docsMove: async (_o, _c, id, node) => { rec("docsMove"); const d = state.docs.find((x) => x.id === id); if (!d) throw err("not_found", "Document not found.", 404); d.node_id = node; return structuredClone(d); },
    docsAction: async (_o, _c, id, action) => {
      rec(`docsAction:${action}`);
      const d = state.docs.find((x) => x.id === id); if (!d) throw err("not_found", "Document not found.", 404);
      if (action === "archive") d.status = "archived"; else if (action === "restore" || action === "undelete") d.status = "active"; else if (action === "delete") d.status = "deleted";
      else if (action === "purge") state.docs = state.docs.filter((x) => x.id !== id);
      else if (action === "duplicate") state.docs.push(mockDoc(`doc-${state.docs.length + 1}`, d.node_id, d.display_name.replace(/(\.\w+)$/, " - copy$1"), d.size_bytes));
      return {};
    },
    docsRestoreVersion: async (_o, _c, id) => structuredClone(state.docs.find((x) => x.id === id) as DocDetail),
    docsSuggest: async (_o, _c, body) => ({ filing: { node_id: body.node_id ?? null, category_id: null, rule_name: null }, naming: { suggested_name: body.filename } }),
    docsDuplicates: async (_o, _c, sha) => ({ duplicates: state.docs.filter((d) => d.content_sha256 === sha && d.status === "active").map((d) => ({ id: d.id, display_name: d.display_name })) }),
    docsPreview: async (_o, _c, id) => { const d = state.docs.find((x) => x.id === id); return { mime: d?.mime_type ?? "image/png", size: 68, data_base64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", name: d?.display_name ?? "preview" }; },
    docsSaveCopy: async (_o, _c, id) => ({ path: `D:\\Artify\\Acme\\${id}.pdf`, created: true, name: `${id}.pdf` }),
    docsOpen: async (_o, _c, id) => { rec("docsOpen"); return { path: `D:\\Artify\\Acme\\${id}.pdf`, created: true, name: `${id}.pdf` }; },
    mirrorSync: async () => ({ created: 3, renamed: 0, unchanged: 0, conflicts: [], too_long: [], orphaned: 0 }),
    mirrorOpen: async () => "D:\\Artify\\Acme",
    on: async (event, handler) => { handlers.set(event, [...(handlers.get(event) ?? []), handler]); return () => handlers.set(event, (handlers.get(event) ?? []).filter((h) => h !== handler)); },
  };
  return b;
}

export function mockDoc(id: string, node: string, name: string, size = 1000, extra: Partial<DocDetail> = {}): DocDetail {
  const ext = name.includes(".") ? name.split(".").pop() ?? "pdf" : "pdf";
  const mime = ext === "pdf" ? "application/pdf" : ext === "png" ? "image/png" : "application/octet-stream";
  return { id, node_id: node, category_id: null, display_name: name, original_filename: name, extension: ext, mime_type: mime, size_bytes: size, content_sha256: "0".repeat(64), current_version_no: 1, status: "active", retention_hold: false, created_by_name: "Test Accountant", created_at: "2026-03-01T10:00:00Z", updated_at: "2026-03-01T10:00:00Z", row_version: 1, previewable: ["pdf", "png", "jpg", "jpeg", "bmp"].includes(ext), versions: [{ version_no: 1, original_filename: name, size_bytes: size, sha256: "0".repeat(64), uploaded_by_name: "Test Accountant", uploaded_at: "2026-03-01T10:00:00Z", change_reason: null, restored_from_version: null, scan_status: "not_scanned" }], metadata: [], duplicates: [], location: [], can: { edit: true, rename: true, move: true, archive: true, delete: true, upload: true }, scan_status: "not_scanned", node_archived: false, ...extra };
}

export const DEMO_BANNER = "DEMO DATA — browser preview with a mock bridge. Sign in with any email and the password “correct”.";
