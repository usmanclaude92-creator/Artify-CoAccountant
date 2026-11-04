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
  on: async (event, handler) => listen(event, (e) => handler(e.payload)),
};
