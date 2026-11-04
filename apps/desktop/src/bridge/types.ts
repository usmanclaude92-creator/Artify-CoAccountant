/** Shapes crossing the Rust <-> UI boundary. The Rust side owns them (apps/desktop/core, src-tauri/src/commands.rs). */

export interface AppError {
  code: string;
  message: string;
  status?: number | null;
  retryable: boolean;
  detail?: unknown;
}

export interface AppInfo {
  name: string; version: string; build: string; os: string; api_configured: boolean; api_base: string | null;
  db_schema_version: number; db_recovered_from: string | null; data_dir: string; update_configured: boolean;
}
export interface Membership { organization_id: string; organization_name: string; organization_status: string; organization_role: string | null; company_roles: Record<string, string> }
export interface Identity { user_id: string; email: string; display_name: string | null; memberships: Membership[] }
export type LoginResult = { status: "ok"; identity: Identity } | { status: "mfa_required"; mfa_token: string } | { status: "mfa_enrollment_required" };
export interface Organization { id: string; display_name: string; type?: string; country?: string; default_currency?: string; status?: string }
export interface Listing<T> { items: T[]; stale: boolean }
export interface CompanyView { id: string; status: "active" | "suspended" | "archived"; favorite: boolean; last_opened_at: string | null; name: string; code: string; country: string; base_currency: string }
export interface CompanyDetail extends CompanyView { legal_name?: string | null }
export interface Assignment { user_id: string; display_name: string; assignment_type: string; active: boolean }

export interface WsNode {
  id: string; parent_id: string | null; node_type: "section" | "folder"; key: string; name: string; display_name: string | null; description: string;
  position: number; depth: number; is_active: boolean; archived_at: string | null; label?: string; can?: Record<string, boolean>;
}
export interface WsCategory { id: string; name: string; document_kind: string; node_id: string | null; is_active: boolean; archived_at: string | null }
export interface WorkspaceData { company_id: string; version: number; workspace_state: string; access_mode?: string; nodes: WsNode[]; categories: WsCategory[] }
export interface WorkspaceView { company_id: string; version: number; fetched_at: string; data: WorkspaceData; stale: boolean; last_success_at: string | null; last_error: string | null }
export type WsChange = { kind: "added" | "moved" | "archived" | "restored" | "removed"; id: string; name: string } | { kind: "renamed"; id: string; from: string; to: string };
export interface RefreshOutcome { changed: boolean; version: number; summary: string | null; changes: WsChange[] }

export interface RootReport { path: string; exists: boolean; is_dir: boolean; writable: boolean; free_bytes: number | null; status: "ok" | "missing" | "not_a_directory" | "not_writable" | "low_space"; warnings: string[] }
export interface LocalDevice { device_id: string; name: string; app_version: string; registered_at: string; last_seen_at: string | null; status: string }
export interface ServerDevice { id: string; platform: string; name: string; app_version: string | null; status: string; registered_at?: string | null; last_seen_at?: string | null }
export interface SyncStatus {
  connectivity: { state: "unknown" | "online" | "offline"; last_checked_at: string | null }; api_configured: boolean;
  company: { last_success_at: string | null; last_attempt_at: string | null; last_error: string | null; last_version: number | null } | null;
  cached_workspace_version: number | null; pending: { pending: number; in_progress: number; failed: number };
}
export interface EventRow { id: number; ts: string; level: string; kind: string; message: string; read: boolean }
export interface UpdateStatus { configured: boolean; available: boolean; version: string | null; message: string }
export type Settings = Record<"theme" | "language" | "start_on_login" | "default_company_id" | "notifications" | "update_channel", string>;

export type Unlisten = () => void;
export interface Bridge {
  appInfo(): Promise<AppInfo>;
  authLogin(email: string, password: string): Promise<LoginResult>;
  authMfa(mfaToken: string, code: string): Promise<Identity>;
  authRestore(): Promise<Identity | null>;
  authLogout(): Promise<void>;
  organizations(): Promise<Listing<Organization>>;
  companies(org: string): Promise<Listing<CompanyView>>;
  companySelect(org: string, company: string): Promise<CompanyDetail>;
  companyFavorite(company: string, favorite: boolean): Promise<void>;
  companyAssignments(org: string, company: string): Promise<Assignment[]>;
  workspaceGet(org: string, company: string): Promise<WorkspaceView>;
  workspaceRefresh(org: string, company: string): Promise<RefreshOutcome>;
  settingsGet(): Promise<Settings>;
  settingsSet(key: string, value: string): Promise<void>;
  storagePick(): Promise<string | null>;
  storageSetRoot(path: string): Promise<RootReport>;
  storageStatus(): Promise<RootReport | null>;
  companyDir(org: string, company: string): Promise<string>;
  deviceEnsure(org: string): Promise<LocalDevice>;
  devicesList(org: string): Promise<ServerDevice[]>;
  deviceRevoke(org: string, deviceId: string): Promise<void>;
  syncStatus(company: string | null): Promise<SyncStatus>;
  notificationsList(): Promise<EventRow[]>;
  notificationsRead(): Promise<void>;
  diagnosticsExport(): Promise<string>;
  updateCheck(): Promise<UpdateStatus>;
  /** Backend events: workspace-updated (RefreshOutcome), session-expired, sync-error (AppError). */
  on(event: "workspace-updated" | "session-expired" | "sync-error", handler: (payload: unknown) => void): Promise<Unlisten>;
}
