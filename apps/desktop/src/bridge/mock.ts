/**
 * In-memory bridge for unit tests and for `VITE_BRIDGE=mock pnpm dev` (browser preview without Rust). It is NEVER selected
 * in a production build, and the dev preview shows a permanent "demo data" banner.
 */
import type { AcctAccount, AcctContact, AcctIssue, AcctOp, AcctTaxCode, AcctTxn, AcctTxnRow, AppError, Assignment, Bridge, DocDetail, ExtractionView, ExtractedField, ProcessingJob, StatementTx, DocQuery, QueueItem, StagedFile, CompanyDetail, CompanyView, EventRow, Identity, LocalDevice, Organization, RefreshOutcome, RootReport, ServerDevice, Settings, SyncStatus, WorkspaceData, WorkspaceView, WsNode } from "./types";

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
  /** draft extractions the review queue shows (synthetic) */
  extractions?: ExtractionView[];
  /** synthetic accounting data the mock serves */
  acctTransactions?: AcctTxn[];
  acctAccounts?: AcctAccount[];
  acctTaxCodes?: AcctTaxCode[];
  acctContacts?: AcctContact[];
}
export type MockBridge = Bridge & {
  calls: string[];
  state: { acct: { txns: AcctTxn[]; accounts: AcctAccount[]; taxCodes: AcctTaxCode[]; contacts: AcctContact[] }; extractions: ExtractionView[]; jobs: ProcessingJob[]; docs: DocDetail[]; queue: QueueItem[]; opts: MockOptions; settings: Settings; workspaces: Record<string, WorkspaceData>; companies: Record<string, CompanyView[]>; signedIn: boolean; devices: ServerDevice[]; root: string | null };
  emit(event: string, payload?: unknown): void;
};

const err = (code: string, message: string, status?: number, retryable = false): AppError => ({ code, message, status: status ?? null, retryable });

export function createMockBridge(opts: MockOptions = {}): MockBridge {
  const orgs = opts.orgs ?? [{ id: "org-1", display_name: "Gulf Accounting Partners" }];
  const identity: Identity = { user_id: "u-1", email: "accountant@example.com", display_name: "Test Accountant", memberships: orgs.map((o) => ({ organization_id: o.id, organization_name: o.display_name, organization_status: "active", organization_role: "accountant", company_roles: {} })) };
  const handlers = new Map<string, ((p: unknown) => void)[]>();
  const calls: string[] = [];
  const state = {
    opts, acct: structuredClone({ txns: opts.acctTransactions ?? [], accounts: opts.acctAccounts ?? [], taxCodes: opts.acctTaxCodes ?? [], contacts: opts.acctContacts ?? [] }), extractions: structuredClone(opts.extractions ?? []), jobs: [] as ProcessingJob[], docs: structuredClone(opts.documents ?? []), queue: [] as QueueItem[], signedIn: false, root: opts.storageRoot ?? null,
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
    // ---- document intelligence (synthetic: the mock never "reads" anything, it serves the extractions it was given)
    intelStart: async (_o, _c, doc, kind, languages, reason) => {
      rec("intelStart"); needAuth();
      const job: ProcessingJob = { id: `job-${state.jobs.length + 1}`, document_id: doc, version_no: 1, kind, status: "queued", languages, pages_total: null, pages_done: 0, attempt_count: 0, max_attempts: 3, error_code: null, error_message: null, reason, created_at: new Date().toISOString(), finished_at: null, progress: 0 };
      state.jobs.push(job); b.emit("processing-changed", {}); return structuredClone(job);
    },
    intelLatest: async (_o, _c, doc) => {
      const ex = state.extractions.find((e) => e.document_id === doc);
      const job = [...state.jobs].reverse().find((j) => j.document_id === doc) ?? null;
      return structuredClone({ job, classification: ex?.classification ?? null, extraction_id: ex?.id ?? null });
    },
    intelQueue: async () => {
      const items = state.extractions.filter((e) => e.status === "draft").map((e) => ({ id: e.id, document_id: e.document_id, display_name: state.docs.find((d) => d.id === e.document_id)?.display_name ?? "Document", type_key: e.type_key, type_name: e.type_key.replace(/_/g, " "), summary: e.summary, confidence: e.classification?.confidence ?? null, created_at: e.created_at }));
      return structuredClone({ items, total: items.length });
    },
    intelJobs: async (_o, _c, status) => { const items = state.jobs.filter((j) => !status || j.status === status); return structuredClone({ items, total: items.length }); },
    intelJobAction: async (_o, _c, id, action) => { const j = state.jobs.find((x) => x.id === id); if (!j) throw err("not_found", "Job not found.", 404); j.status = action === "cancel" ? "cancelled" : "queued"; return structuredClone(j); },
    intelExtraction: async (_o, _c, id) => { const e = state.extractions.find((x) => x.id === id); if (!e) throw err("not_found", "Extraction not found.", 404); return structuredClone(e); },
    intelOcr: async (_o, _c, _doc, from, to) => ({ id: "ocr-1", provider_key: "tesseract", engine_version: "mock", languages: ["eng"], page_count: 1, avg_confidence: 0.9, pages: from <= 1 && to >= 1 ? [{ page_no: 1, width: 100, height: 140, source: "ocr", is_blank: false, avg_confidence: 0.9, rotation_applied: 0, deskew_angle: 0, quality: null, text: "synthetic page text", blocks: [] }] : [] }),
    intelReview: async (_o, _c, id, kind, target, action, payload) => {
      rec(`intelReview:${kind}:${action}`); needAuth();
      const e = state.extractions.find((x) => x.id === id); if (!e) throw err("not_found", "Extraction not found.", 404);
      if (e.status !== "draft") throw err("conflict", "This extraction is already decided.", 409);
      const status = ({ correct: "corrected", confirm: "confirmed", reject: "rejected", missing: "missing", unreadable: "unreadable" } as const)[action];
      if (kind === "field") {
        const f = e.fields.find((x) => x.field_key === target); if (!f) throw err("not_found", "Field not found.", 404);
        if (action === "correct") { f.original_value ??= f.value; f.value = String(payload.value ?? ""); }
        f.review_status = status;
      } else if (kind === "transactions") {
        const t = e.transactions?.find((x) => x.id === target); if (!t) throw err("not_found", "Row not found.", 404);
        if (action === "correct") Object.assign(t, payload.patch as Partial<StatementTx>);
        t.review_status = status;
      } else {
        const l = e.line_items.find((x) => x.id === target); if (!l) throw err("not_found", "Line not found.", 404);
        if (action === "correct") Object.assign(l.values, payload.values as Record<string, string | null>);
        l.review_status = status;
      }
      e.unresolved = { fields: e.fields.filter((f) => f.review_status === "needs_review").length, line_items: e.line_items.filter((l) => l.review_status === "needs_review").length, transactions: (e.transactions ?? []).filter((t) => t.review_status === "needs_review").length };
      return {};
    },
    intelAddTransaction: async (_o, _c, id, data) => {
      const e = state.extractions.find((x) => x.id === id); if (!e) throw err("not_found", "Extraction not found.", 404);
      const t: StatementTx = { id: `tx-${(e.transactions?.length ?? 0) + 1}`, row_no: (e.transactions?.length ?? 0) + 1, page_no: null, bbox: null, source_text: null, tx_date: (data.tx_date as string) ?? null, value_date: null, description: (data.description as string) ?? null, reference: null, debit: (data.debit as string) ?? null, credit: (data.credit as string) ?? null, balance: (data.balance as string) ?? null, currency: null, tx_type: null, confidence: null, review_status: "corrected", manual: true, flags: ["manual"] };
      (e.transactions ??= []).push(t); return structuredClone(t);
    },
    intelStatement: async () => ({}),
    intelClassification: async (_o, _c, doc, action, typeKey) => { const e = state.extractions.find((x) => x.document_id === doc); if (e?.classification) { e.classification.review_status = "confirmed"; e.classification.needs_manual = false; if (action === "correct" && typeKey) e.classification.type_key = typeKey; } return {}; },
    intelDecide: async (_o, _c, id, action, acknowledge) => {
      rec(`intelDecide:${action}`); needAuth();
      const e = state.extractions.find((x) => x.id === id); if (!e) throw err("not_found", "Extraction not found.", 404);
      const open = e.unresolved.fields + e.unresolved.line_items + e.unresolved.transactions;
      if (action === "approve" && open > 0) throw err("conflict", "Resolve every item that needs review before approving.", 409);
      if (action === "approve" && (e.statement?.validation ?? e.validation ?? []).some((i) => i.severity === "error") && !acknowledge) throw err("conflict", "Validation errors must be acknowledged before approving.", 409);
      e.status = action === "approve" ? "approved" : "rejected"; e.approved_at = action === "approve" ? new Date().toISOString() : null; b.emit("processing-changed", {});
      return {};
    },
    intelAccept: async (_o, _c, id, kind, sid) => { const e = state.extractions.find((x) => x.id === id); if (!e) throw err("not_found", "Extraction not found.", 404); (kind === "workspace" ? e.workspace_suggestions : e.metadata_suggestions).forEach((x) => { if (x.id === sid) x.status = "accepted"; }); return {}; },
    intelSplit: async () => ({}),
    intelPageImage: async (_o, _c, _d, page) => ({ page, data_base64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" }),
    // ---- accounting (synthetic: the mock mirrors the server's rules for who may do what, but calculates nothing of its own)
    acct: async <T,>(_o: string, _c: string, op: AcctOp, args?: { ids?: string[]; query?: Record<string, string>; body?: unknown }): Promise<T> => {
      rec(`acct:${op}`); needAuth();
      const A = state.acct;
      const body = (args?.body ?? {}) as Record<string, unknown>;
      const find = (): AcctTxn => { const t = A.txns.find((x) => x.id === args?.ids?.[0]); if (!t) throw err("not_found", "Transaction not found.", 404); return t; };
      const note = (t: AcctTxn, action: string, from: string | null, to: string | null, n: string | null = null) => t.trail.push({ action, from_status: from, to_status: to, actor_type: "user", actor: "Test Accountant", note: n, created_at: "2026-03-02T09:00:00Z" });
      const setStatus = (t: AcctTxn, to: string, action: string, n: string | null = null) => { const from = t.status; t.status = to; note(t, action, from, to, n); return t; };
      const blockingOf = (t: AcctTxn) => t.issues.filter((i) => i.blocking);
      const refresh = (t: AcctTxn) => { t.blocking_count = blockingOf(t).length; t.can.submit = t.status === "draft" && t.blocking_count === 0; };
      const micro = (v: string) => BigInt(Math.round(Number(v) * 1_000_000));
      const out = (v: unknown) => structuredClone(v) as T;
      switch (op) {
        case "config": return out({ initialized: true, config: { base_currency: "OMR", amount_scale: 3 }, company_base_currency: "OMR" });
        case "accounts": return out({ items: A.accounts });
        case "tax_codes": return out({ items: A.taxCodes });
        case "contacts": return out({ items: A.contacts, total: A.contacts.length, limit: 50, offset: 0 });
        case "transactions": {
          const q = args?.query ?? {};
          const items: AcctTxnRow[] = A.txns.filter((t) => (!q.status || t.status === q.status) && (!q.q || t.number.includes(q.q) || (t.doc_number ?? "").includes(q.q))).map((t) => ({ id: t.id, number: t.number, txn_type: t.txn_type, type_label: t.type_label, status: t.status, transaction_date: t.transaction_date, doc_number: t.doc_number, currency: t.currency, gross_amount: t.gross_amount, duplicate_status: t.duplicate_status, contact_name: t.contact?.display_name ?? null, document_name: t.document?.display_name ?? null, blocking_count: t.blocking_count }));
          return out({ items, total: items.length, limit: 50, offset: 0 });
        }
        case "transaction": return out(find());
        case "from_extraction": {
          const x = state.extractions.find((e) => e.id === args?.ids?.[0]);
          if (!x || x.status !== "approved") throw err("conflict", "Only extractions that an accountant has approved can become accounting transactions.", 409);
          const t = mockAcctTxn(`tx-${A.txns.length + 1}`, { source_document_id: x.document_id, extraction_id: x.id, document: { id: x.document_id, display_name: state.docs.find((d) => d.id === x.document_id)?.display_name ?? "Document" } });
          A.txns.push(t); return out(t);
        }
        case "txn_create": { const t = mockAcctTxn(`tx-${A.txns.length + 1}`, { txn_type: String(body.txn_type ?? "purchase_invoice") }); A.txns.push(t); return out(t); }
        case "txn_update": { const t = find(); if (t.status !== "draft") throw err("conflict", "A transaction that is " + t.status.replace("_", " ") + " cannot be edited.", 409); Object.assign(t, body); t.row_version++; return out(t); }
        case "txn_lines": { const t = find(); if (t.status !== "draft") throw err("conflict", "A transaction that is " + t.status.replace("_", " ") + " cannot be edited.", 409); return out(t); }
        case "txn_contact": {
          const t = find(); const id = (body.contact_id as string | null) ?? null;
          t.contact_id = id; t.contact = id ? (A.contacts.find((c) => c.id === id) ? { id, display_name: A.contacts.find((c) => c.id === id)?.display_name ?? "", kind: "supplier", tax_number: null } : null) : null;
          t.issues = t.issues.filter((i) => i.code !== "contact_required"); refresh(t); return out(t);
        }
        case "txn_journal_lines": {
          const t = find(); if (t.status !== "draft") throw err("conflict", "A transaction that is " + t.status.replace("_", " ") + " cannot be edited.", 409);
          const lines = (body.lines as { account_id: string; debit?: string; credit?: string }[]).map((l, i) => { const a = A.accounts.find((x) => x.id === l.account_id); return { id: `jl-${i}`, line_no: i + 1, account_id: l.account_id, account_code: a?.code ?? "?", account_name: a?.name ?? "?", debit: l.debit ?? "0", credit: l.credit ?? "0", base_debit: l.debit ?? "0", base_credit: l.credit ?? "0", currency: t.currency, description: null, reference: null, tax_code_id: null, contact_id: null, source: "manual" as const }; });
          if (t.journal) t.journal.lines = lines; else t.journal = { id: "j-1", number: null, journal_type: "general", journal_date: t.transaction_date, status: "draft", currency: t.currency, base_currency: "OMR", exchange_rate: null, total_debit: "0", total_credit: "0", narration: null, lines };
          const dr = lines.reduce((a, l) => a + micro(l.debit), 0n), cr = lines.reduce((a, l) => a + micro(l.credit), 0n);
          t.balanced = dr === cr; t.journal_mode = "manual"; t.debit_total = String(Number(dr) / 1e6); t.credit_total = String(Number(cr) / 1e6);
          t.issues = t.issues.filter((i) => i.code !== "journal_unbalanced");
          if (!t.balanced) t.issues.push({ code: "journal_unbalanced", severity: "error", message: `Debits and credits differ by ${Number(dr - cr) / 1e6}.`, blocking: true, data: {} } as AcctIssue);
          refresh(t); return out(t);
        }
        case "txn_regenerate": case "txn_validate": { const t = find(); refresh(t); return out(t); }
        case "txn_submit": {
          const t = find(); if (t.status !== "draft") throw err("conflict", "Only a draft can be submitted.", 409);
          if (blockingOf(t).length) throw err("conflict", `Resolve the blocking items before you submit: ${blockingOf(t)[0]?.message}`, 409);
          return out(setStatus(t, t.policy.levels === 2 ? "awaiting_review" : "awaiting_approval", "submit"));
        }
        case "txn_review": { const t = find(); if (t.status !== "awaiting_review") throw err("conflict", "This transaction is not waiting for review.", 409); t.people.reviewed_by = "Test Accountant"; return out(setStatus(t, "awaiting_approval", "review_approve")); }
        case "txn_approve": {
          const t = find();
          if (t.status !== "awaiting_approval") throw err("conflict", "This transaction is not waiting for approval.", 409);
          if (!t.can.approve) throw err("forbidden", "You prepared this transaction; the company's policy requires a different person to approve it.", 403);
          if (blockingOf(t).length) throw err("conflict", `This transaction cannot be approved: ${blockingOf(t)[0]?.message}`, 409);
          const soft = t.issues.filter((i) => i.severity === "error" && !i.blocking);
          if (soft.length && !(body.acknowledge && String(body.note ?? "").trim())) throw err("conflict", "The document differs from the calculation. Review the differences, then approve with an acknowledgement and a written reason.", 409);
          t.can = { ...t.can, approve: false, reject: false, request_correction: false, reverse: true };
          if (t.journal) { t.journal.status = "approved"; t.journal.number = "JV-000001"; }
          return out(setStatus(t, "approved", "approve", (body.note as string | null) ?? null));
        }
        case "txn_reject": { const t = find(); if (String(body.reason ?? "").length < 3) throw err("validation_error", "A reason is required.", 422); t.rejection_reason = String(body.reason); t.can = { ...t.can, approve: false, reject: false, revise: true }; return out(setStatus(t, "rejected", "reject", String(body.reason))); }
        case "txn_request_correction": { const t = find(); t.correction_note = String(body.reason); t.can = { ...t.can, edit: true, approve: false, reject: false }; return out(setStatus(t, "draft", "request_correction", String(body.reason))); }
        case "txn_revise": { const t = find(); t.can = { ...t.can, edit: true, revise: false }; return out(setStatus(t, "draft", "revise")); }
        case "txn_reverse": { const t = find(); if (t.status !== "approved") throw err("conflict", "Only an approved transaction can be reversed.", 409); t.can.reverse = false; return out(setStatus(t, "reversed", "reverse", String(body.reason))); }
        case "trial_balance": return out({ as_of: null, rows: [], debit: "0", credit: "0", balanced: true });
        default: throw err("unsupported", "The mock does not implement this accounting action.", 501);
      }
    },
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

/** Synthetic draft invoice extraction for UI tests. Values are made up; nothing here was read from a document. */
export function mockExtraction(id: string, doc: string, over: Partial<ExtractionView> = {}): ExtractionView {
  const f = (key: string, label: string, value: string | null, status: ExtractedField["review_status"] = "extracted", kind = "text", confidence = 0.95, required = true): ExtractedField =>
    ({ id: `f-${key}`, field_key: key, label, kind, required, value, original_value: null, confidence, page_no: 1, bbox: value ? [0.1, 0.1, 0.4, 0.14] : null, source_text: value, method: "rules", review_status: status, flags: null });
  return {
    id, document_id: doc, version_id: "v-1", extraction_no: 1, type_key: "tax_invoice", schema_key: "invoice", status: "draft", provider_key: "rules", model_version: "rules-1", validation: [],
    summary: null, created_at: "2026-03-01T10:00:00Z", approved_at: null, row_version: 1,
    fields: [f("invoice_number", "Invoice number", "INV-1001"), f("invoice_date", "Invoice date", "2026-02-15", "extracted", "date"), f("total", "Total", "115.000", "needs_review", "money", 0.55)],
    line_items: [], line_fields: [{ key: "description", label: "Description", kind: "text" }, { key: "amount", label: "Amount", kind: "money" }],
    classification: { id: "c-1", type_key: "tax_invoice", kind_key: "purchase_invoice", confidence: 0.92, method: "rules", evidence: null, alternatives: null, needs_manual: false, provider_key: "rules", review_status: "suggested", created_at: "2026-03-01T10:00:00Z" },
    workspace_suggestions: [], metadata_suggestions: [], split_proposal: null, unresolved: { fields: 1, line_items: 0, transactions: 0 }, ...over,
  };
}

/** Synthetic awaiting-approval transaction for UI tests. Every number is made up and nothing here is calculated. */
export function mockAcctTxn(id: string, over: Partial<AcctTxn> = {}): AcctTxn {
  const line = (n: number, code: string, name: string, debit: string, credit: string) => ({ id: `jl${n}`, line_no: n, account_id: `acc-${code}`, account_code: code, account_name: name, debit, credit, base_debit: debit, base_credit: credit, currency: "OMR", description: null, reference: null, tax_code_id: null, contact_id: null, source: "generated" as const });
  return {
    id, number: `TX-${id.replace(/\D/g, "").padStart(6, "0") || "000001"}`, txn_type: "purchase_invoice", type_label: "Purchase invoice", status: "awaiting_approval", source_document_id: null, extraction_id: null,
    transaction_date: "2025-03-14", due_date: null, doc_number: "INV-77", contact_id: "ct-1", contact_suggestions: [], settlement_account_id: null, counter_account_id: null, currency: "OMR",
    exchange_rate: null, rate_source: null, rate_date: null, prices_include_tax: false, net_amount: "100.000000", tax_amount: "5.000000", gross_amount: "105.000000", base_gross_amount: "105.000000",
    narration: null, journal_mode: "generated", duplicate_status: "none", correction_note: null, rejection_reason: null, row_version: 1,
    lines: [{ id: "l1", line_no: 1, description: "Office paper", account_id: "acc-6100", account_code: "6100", account_name: "General expenses", account_source: "role_default", entry_side: null, quantity: null, unit_price: null, discount: "0", net_amount: "100.000000", tax_amount: "5.000000", gross_amount: "105.000000", tax_code_id: "tx-std", tax_code: "OM_STD", dimensions: {}, calc: {} }],
    journal: { id: "j1", number: null, journal_type: "purchases", journal_date: "2025-03-14", status: "draft", currency: "OMR", base_currency: "OMR", exchange_rate: null, total_debit: "105.000000", total_credit: "105.000000", narration: null, lines: [line(1, "6100", "General expenses", "100.000000", "0"), line(2, "1300", "Input tax", "5.000000", "0"), line(3, "2100", "Accounts payable", "0", "105.000000")] },
    debit_total: "105.000000", credit_total: "105.000000", base_debit_total: "105.000000", base_credit_total: "105.000000", balanced: true, issues: [], blocking_count: 0,
    trail: [{ action: "create", from_status: null, to_status: "draft", actor_type: "user", actor: "Preparer", note: null, created_at: "2026-03-01T10:00:00Z" }, { action: "submit", from_status: "draft", to_status: "awaiting_approval", actor_type: "user", actor: "Preparer", note: null, created_at: "2026-03-01T10:05:00Z" }],
    duplicates: [], document: null, contact: { id: "ct-1", display_name: "Gulf Office Supplies", kind: "supplier", tax_number: "OM123" }, people: { prepared_by: "Preparer" },
    can: { edit: false, submit: false, review: false, approve: true, reject: true, request_correction: true, revise: false, reverse: false },
    policy: { separate_preparer_approver: true, levels: 1, distinct_levels: true, approval_limit: null }, ...over,
  };
}
