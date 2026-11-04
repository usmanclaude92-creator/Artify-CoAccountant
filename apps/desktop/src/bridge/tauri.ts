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
  filesPick: () => call("files_pick"),
  importsQueue: (org, company, items) => call("imports_queue", { org, company, items }),
  importsList: (company) => call("imports_list", { company }),
  importsProcess: () => call("imports_process"),
  importCancel: (org, id) => call("import_cancel", { org, id }),
  importRetry: (id, o) => call("import_retry", { id, duplicatePolicy: o?.duplicatePolicy ?? null, nodeId: o?.nodeId ?? null, categoryId: o?.categoryId ?? null }),
  importRemove: (id) => call("import_remove", { id }),
  documentsOp: (org, company, op, args) => call("documents_op", { org, company, op, args: args ?? null }),
  documentPreview: (org, company, doc, version) => call("document_preview", { org, company, doc, version: version ?? null }),
  documentDownload: (org, company, doc, version) => call("document_download", { org, company, doc, version: version ?? null }),
  documentOpen: (org, company, doc, version) => call("document_open", { org, company, doc, version: version ?? null }),
  mirrorStatus: (company) => call("mirror_status", { company }),
  mirrorReconcile: (org, company) => call("mirror_reconcile", { org, company }),
  mirrorSync: (org, company) => call("mirror_sync", { org, company }),
  on: async (event, handler) => listen(event, (e) => handler(e.payload)),
};
