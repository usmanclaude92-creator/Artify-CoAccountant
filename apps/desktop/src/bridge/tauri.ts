import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { toAppError } from "../lib/errors";
import type { Bridge } from "./types";

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  try {
    return await invoke<T>(cmd, args);
  } catch (e) {
    throw toAppError(e);
  }
}

/** The real bridge: every method is exactly one allow-listed Rust command. No tokens, paths or SQL pass through here. */
export const tauriBridge: Bridge = {
  appInfo: () => call("app_info"),
  authLogin: (email, password) => call("auth_login", { email, password }),
  authMfa: (mfaToken, code) => call("auth_mfa", { mfaToken, code }),
  authRestore: () => call("auth_restore"),
  authLogout: () => call("auth_logout"),
  organizations: () => call("organizations"),
  companies: (org) => call("companies", { org }),
  companySelect: (org, company) => call("company_select", { org, company }),
  companyFavorite: (company, favorite) => call("company_favorite", { company, favorite }),
  companyAssignments: (org, company) => call("company_assignments", { org, company }),
  workspaceGet: (org, company) => call("workspace_get", { org, company }),
  workspaceRefresh: (org, company) => call("workspace_refresh", { org, company }),
  settingsGet: () => call("settings_get"),
  settingsSet: (key, value) => call("settings_set", { key, value }),
  storagePick: () => call("storage_pick"),
  storageSetRoot: (path) => call("storage_set_root", { path }),
  storageStatus: () => call("storage_status"),
  companyDir: (org, company) => call("company_dir", { org, company }),
  deviceEnsure: (org) => call("device_ensure", { org }),
  devicesList: (org) => call("devices_list", { org }),
  deviceRevoke: (org, deviceId) => call("device_revoke", { org, deviceId }),
  syncStatus: (company) => call("sync_status", { company }),
  notificationsList: () => call("notifications_list"),
  notificationsRead: () => call("notifications_read"),
  diagnosticsExport: () => call("diagnostics_export"),
  updateCheck: () => call("update_check"),
  importPick: () => call("import_pick"),
  importUnstage: (id) => call("import_unstage", { id }),
  queueAdd: (org, company, items) => call("queue_add", { org, company, items }),
  queueList: (company) => call("queue_list", { company }),
  queueRun: () => call("queue_run"),
  queueRetry: (id) => call("queue_retry", { id }),
  queueCancel: (id) => call("queue_cancel", { id }),
  queueRemove: (id) => call("queue_remove", { id }),
  queueClearFinished: (company) => call("queue_clear_finished", { company }),
  uploadConfig: (org, company) => call("upload_config", { org, company }),
  docsList: (org, company, query) => call("docs_list", { org, company, query }),
  docsCounts: (org, company) => call("docs_counts", { org, company }),
  docsGet: (org, company, doc) => call("docs_get", { org, company, doc }),
  docsEvents: (org, company, doc) => call("docs_events", { org, company, doc }),
  docsPatch: (org, company, doc, patch) => call("docs_patch", { org, company, doc, patch }),
  docsMove: (org, company, doc, node, category, baseRowVersion) => call("docs_move", { org, company, doc, node, category, baseRowVersion }),
  docsAction: (org, company, doc, action) => call("docs_action", { org, company, doc, action, body: null }),
  docsRestoreVersion: (org, company, doc, version) => call("docs_restore_version", { org, company, doc, version }),
  docsSuggest: (org, company, body) => call("docs_suggest", { org, company, body }),
  docsDuplicates: (org, company, sha256) => call("docs_duplicates", { org, company, sha256 }),
  docsPreview: (org, company, doc, version) => call("docs_preview", { org, company, doc, version }),
  docsSaveCopy: (org, company, doc, version) => call("docs_save_copy", { org, company, doc, version }),
  docsOpen: (org, company, doc, version) => call("docs_open", { org, company, doc, version }),
  mirrorSync: (org, company) => call("mirror_sync", { org, company }),
  mirrorOpen: (org, company, node) => call("mirror_open", { org, company, node }),
  intelStart: (org, company, doc, kind, languages, reason) => call("intel_start", { org, company, doc, kind, languages, reason }),
  intelLatest: (org, company, doc) => call("intel_latest", { org, company, doc }),
  intelQueue: (org, company) => call("intel_queue", { org, company }),
  intelJobs: (org, company, status) => call("intel_jobs", { org, company, status }),
  intelJobAction: (org, company, job, action) => call("intel_job_action", { org, company, job, action }),
  intelExtraction: (org, company, extraction) => call("intel_extraction", { org, company, extraction }),
  intelOcr: (org, company, doc, pageFrom, pageTo) => call("intel_ocr", { org, company, doc, pageFrom, pageTo }),
  intelReview: (org, company, extraction, kind, target, action, payload, reason) => call("intel_review", { org, company, extraction, kind, target, action, payload, reason }),
  intelAddTransaction: (org, company, extraction, data) => call("intel_add_transaction", { org, company, extraction, data }),
  intelStatement: (org, company, extraction, patch) => call("intel_statement", { org, company, extraction, patch }),
  intelClassification: (org, company, doc, action, typeKey) => call("intel_classification", { org, company, doc, action, typeKey }),
  intelDecide: (org, company, extraction, action, acknowledge, note) => call("intel_decide", { org, company, extraction, action, acknowledge, note }),
  intelAccept: (org, company, extraction, kind, suggestion, value) => call("intel_accept", { org, company, extraction, kind, suggestion, value }),
  intelSplit: (org, company, extraction, approve) => call("intel_split", { org, company, extraction, approve }),
  intelPageImage: (org, company, doc, page) => call("intel_page_image", { org, company, doc, page }),
  acct: <T,>(org: string, company: string, op: string, args?: { ids?: string[]; query?: Record<string, string>; body?: unknown }) =>
    call<T>("acct_call", { org, company, op, ids: args?.ids ?? [], query: Object.entries(args?.query ?? {}), body: args?.body ?? null }),
  bk: <T,>(org: string, company: string, op: string, args?: { ids?: string[]; query?: Record<string, string>; body?: unknown }) =>
    call<T>("bk_call", { org, company, op, ids: args?.ids ?? [], query: Object.entries(args?.query ?? {}), body: args?.body ?? null }),
  xl: <T,>(org: string, company: string, op: string, args?: { ids?: string[]; query?: Record<string, string>; body?: unknown }) =>
    call<T>("xl_call", { org, company, op, ids: args?.ids ?? [], query: Object.entries(args?.query ?? {}), body: args?.body ?? null }),
  xlUploadPick: (org, company, meta) => call("xl_upload_pick", { org, company, meta }),
  xlExportSave: (org, company, exportId, suggestedName) => call("xl_export_save", { org, company, exportId, suggestedName }),
  on: async (event, handler) => listen(event, (e) => handler(e.payload)),
};
