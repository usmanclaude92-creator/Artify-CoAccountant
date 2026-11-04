/**
 * In-memory bridge for unit tests and for `VITE_BRIDGE=mock pnpm dev` (browser preview without Rust). It is NEVER selected
 * in a production build, and the dev preview shows a permanent "demo data" banner.
 */
import type { AppError, Assignment, DocCounts, DocEvent, DocOpName, DocPage, DocVersion, DocumentRow, ImportRow, QueueItem, Staged, Bridge, CompanyDetail, CompanyView, EventRow, Identity, LocalDevice, Organization, RefreshOutcome, RootReport, ServerDevice, Settings, SyncStatus, WorkspaceData, WorkspaceView, WsNode } from "./types";

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
  /** files the native picker would return */
  pick?: Staged[];
  documents?: DocumentRow[];
  /** a quota the mock enforces when uploads are processed */
  maxDocuments?: number;
}
export type MockBridge = Bridge & {
  calls: string[];
  state: { opts: MockOptions; settings: Settings; workspaces: Record<string, WorkspaceData>; companies: Record<string, CompanyView[]>; signedIn: boolean; devices: ServerDevice[]; root: string | null; documents: DocumentRow[]; imports: ImportRow[]; opened: string[] };
  emit(event: string, payload?: unknown): void;
};

const err = (code: string, message: string, status?: number, retryable = false): AppError => ({ code, message, status: status ?? null, retryable });

export function createMockBridge(opts: MockOptions = {}): MockBridge {
  const orgs = opts.orgs ?? [{ id: "org-1", display_name: "Gulf Accounting Partners" }];
  const identity: Identity = { user_id: "u-1", email: "accountant@example.com", display_name: "Test Accountant", memberships: orgs.map((o) => ({ organization_id: o.id, organization_name: o.display_name, organization_status: "active", organization_role: "accountant", company_roles: {} })) };
  const handlers = new Map<string, ((p: unknown) => void)[]>();
  const calls: string[] = [];
  const state = {
    opts, signedIn: false, root: opts.storageRoot ?? null,
    settings: { theme: "system", language: "en", start_on_login: "false", default_company_id: "", notifications: "true", update_channel: "stable", mirror_policy: "on_demand" } as Settings,
    workspaces: structuredClone(opts.workspaces ?? DEMO_WORKSPACES) as Record<string, WorkspaceData>,
    companies: structuredClone(opts.companies ?? { [orgs[0]?.id ?? "org-1"]: DEMO_COMPANIES }) as Record<string, CompanyView[]>,
    devices: [] as ServerDevice[],
    documents: structuredClone(opts.documents ?? []) as DocumentRow[],
    imports: [] as ImportRow[],
    opened: [] as string[],
  };
  const staged = new Map<string, Staged>();
  const company0 = Object.values(state.workspaces)[0]?.company_id ?? "c-alpha";
  const rec = (n: string) => calls.push(n);
  void company0;
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
    filesPick: async () => { rec("filesPick"); needAuth(); const out = (opts.pick ?? []).map((f, i) => ({ ...f, token: `${f.token}-${state.imports.length}-${i}-${staged.size}` })); out.forEach((f) => staged.set(f.token, f)); return out; },
    importsQueue: async (org, company, items: QueueItem[]) => {
      rec("importsQueue"); needAuth();
      const queued: ImportRow[] = [];
      const rejected: { name: string; code: string; message: string }[] = [];
      for (const it of items) {
        const f = staged.get(it.token);
        if (!f) { rejected.push({ name: "?", code: "unknown_file", message: "That file is no longer available. Choose it again." }); continue; }
        staged.delete(it.token);
        if (!/\.(pdf|png|jpe?g|xlsx?|docx?|csv|txt)$/i.test(f.name)) { rejected.push({ name: f.name, code: "unsupported_type", message: "This file type is not allowed." }); continue; }
        const row: ImportRow = { id: `imp-${state.imports.length + 1}`, org_id: org, company_id: company, batch_id: "batch-1", file_name: f.name, display_name: it.display_name ?? null, size: f.size, sha256: null, node_id: it.node_id, category_id: it.category_id ?? null, document_id: it.document_id ?? null, duplicate_policy: it.duplicate_policy ?? "block", status: "waiting", bytes_sent: 0, attempts: 0, next_retry_at: null, error_code: null, error_message: null, error_detail: null, result_document_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
        state.imports.push(row); queued.push(structuredClone(row));
      }
      return { queued, rejected };
    },
    importsList: async (company) => structuredClone(state.imports.filter((i) => !company || i.company_id === company)),
    importsProcess: async () => {
      rec("importsProcess"); needAuth();
      let n = 0;
      for (const r of state.imports.filter((i) => i.status === "waiting")) {
        n++;
        if (opts.offline) { r.error_code = "network_unavailable"; r.error_message = "The cloud could not be reached."; r.attempts++; continue; }
        const hash = `${r.file_name}:${r.size}`;
        const dup = state.documents.find((d) => d.sha256 === hash && d.status !== "deleted");
        if (dup && r.duplicate_policy === "block") { r.status = "failed"; r.error_code = "duplicate_detected"; r.error_message = "Identical content already exists."; continue; }
        if (opts.maxDocuments !== undefined && state.documents.filter((d) => d.status !== "deleted").length >= opts.maxDocuments) { r.status = "failed"; r.error_code = "storage_quota"; r.error_message = "Limit reached."; continue; }
        const id = `doc-${state.documents.length + 1}`;
        state.documents.push({ id, node_id: r.node_id, category_id: r.category_id, display_name: r.display_name ?? r.file_name, original_filename: r.file_name, extension: r.file_name.split(".").pop() ?? "", status: "active", current_version_number: 1, source: "desktop", legal_hold: false, created_by_name: "Test Accountant", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), archived_at: null, row_version: 1, mime_type: r.file_name.endsWith(".pdf") ? "application/pdf" : r.file_name.endsWith(".png") ? "image/png" : "application/octet-stream", size_bytes: r.size, sha256: hash, scan_status: "not_scanned", can: { view: true, download: true, edit: true, move: true, archive: true, delete: true, purge: false } });
        r.status = "completed"; r.bytes_sent = r.size; r.result_document_id = id; r.error_code = null; r.error_message = null;
      }
      return n;
    },
    importCancel: async (_o, id) => { rec("importCancel"); const r = state.imports.find((i) => i.id === id); if (r && r.status !== "completed") r.status = "cancelled"; },
    importRetry: async (id, o) => { rec("importRetry"); const r = state.imports.find((i) => i.id === id); if (!r || r.status === "completed") return false; r.status = "waiting"; r.error_code = null; r.error_message = null; if (o?.duplicatePolicy) r.duplicate_policy = o.duplicatePolicy; if (o?.nodeId) r.node_id = o.nodeId; return true; },
    importRemove: async (id) => { rec("importRemove"); const n = state.imports.length; state.imports = state.imports.filter((i) => i.id !== id); return state.imports.length < n; },
    documentsOp: async <T,>(_org: string, company: string, op: DocOpName, args: Record<string, unknown> = {}): Promise<T> => {
      rec(`documentsOp:${op}`); needAuth(); down();
      const docs = state.documents;
      const find = () => { const d = docs.find((x) => x.id === args.id); if (!d) throw err("not_found", "Document not found.", 404); return d; };
      const out = ((): unknown => {
        switch (op) {
          case "limits": return { max_file_size: 25 * 1024 ** 2, batch_max_files: 20, allowed_extensions: ["pdf", "png", "jpg", "xlsx", "csv"] };
          case "counts": { const c: DocCounts = { by_node: {}, totals: { active: 0, archived: 0 } }; for (const d of docs) { if (d.status === "deleted") continue; const e = (c.by_node[d.node_id] ??= { active: 0, archived: 0 }); e[d.status as "active" | "archived"]++; c.totals[d.status as "active" | "archived"]++; } return c; }
          case "storage": return { stored_bytes: docs.reduce((a, d) => a + d.size_bytes, 0) };
          case "list": {
            const q = (args.query ?? {}) as Record<string, string>;
            let items = docs.filter((d) => d.status === (q.status ?? "active"));
            if (q.node_id) items = items.filter((d) => d.node_id === q.node_id);
            if (q.q) items = items.filter((d) => d.display_name.toLowerCase().includes(q.q!.toLowerCase()));
            const page: DocPage<DocumentRow> = { items: structuredClone(items), total: items.length, limit: 50, offset: 0 };
            return page;
          }
          case "get": return { ...structuredClone(find()), field_values: {}, path: ["Mock"], versions: [{ id: "v1", version_number: 1, original_filename: find().original_filename, extension: find().extension, mime_type: find().mime_type, size_bytes: find().size_bytes, sha256: find().sha256, scan_status: "not_scanned", change_reason: null, uploaded_by_name: "Test Accountant", created_at: find().created_at, is_current: true, purged_at: null } satisfies DocVersion] };
          case "versions": return [];
          case "events": return { items: [{ id: "e1", event_type: "created", created_at: find().created_at, actor_name: "Test Accountant", detail: null } satisfies DocEvent], total: 1, limit: 50, offset: 0 };
          case "patch": { const d = find(); if (args.display_name) d.display_name = String(args.display_name); d.row_version++; return structuredClone(d); }
          case "archive": { const d = find(); d.status = "archived"; return structuredClone(d); }
          case "restore": { const d = find(); d.status = "active"; return structuredClone(d); }
          case "delete": { const d = find(); d.status = "deleted"; return structuredClone(d); }
          case "check_duplicate": { const d = docs.find((x) => x.sha256 === args.sha256 && x.status !== "deleted"); return d ? { exists: true, document: { id: d.id, display_name: d.display_name, status: d.status, node_id: d.node_id, path: ["Mock"] } } : { exists: false, document: null }; }
          case "suggest": return { filing: { rule: null, target_node_id: null, category_id: null, path: null, matches: [] }, naming: { suggested_name: null, pattern: null, missing: [] } };
          default: throw err("not_supported", `The mock bridge does not implement ${op}.`);
        }
      })();
      void company;
      return out as T;
    },
    documentPreview: async (_o, _c, doc) => { rec("documentPreview"); needAuth(); const d = state.documents.find((x) => x.id === doc); if (!d) throw err("not_found", "Document not found.", 404); return { mime: d.mime_type, size: d.size_bytes, sha256: d.sha256, data_base64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" }; },
    documentDownload: async (_o, _c, doc) => { rec("documentDownload"); return { path: `${state.root ?? "C:\\Temp"}\\${doc}`, rel_path: doc, size: 1, already_present: false }; },
    documentOpen: async (_o, _c, doc) => { rec("documentOpen"); state.opened.push(doc); },
    mirrorStatus: async () => ({ files: 0, missing: 0, available: !!state.root, policy: "on_demand", root: null }),
    mirrorReconcile: async () => ({ moved: 0 }),
    mirrorSync: async () => ({ downloaded: 0 }),
    on: async (event, handler) => { handlers.set(event, [...(handlers.get(event) ?? []), handler]); return () => handlers.set(event, (handlers.get(event) ?? []).filter((h) => h !== handler)); },
  };
  return b;
}

export const DEMO_BANNER = "DEMO DATA — browser preview with a mock bridge. Sign in with any email and the password “correct”.";
