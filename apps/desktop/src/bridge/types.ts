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
export interface WsFieldOption { id: string; value: string; label: string; position: number; archived_at: string | null }
export interface WsField {
  id: string; key: string; label: string; data_type: string; required: boolean; is_active: boolean; archived_at: string | null;
  options: WsFieldOption[]; category_ids: string[]; node_ids: string[]; default_value?: unknown; reference_target?: string | null;
}
export interface WorkspaceData { company_id: string; version: number; workspace_state: string; access_mode?: string; nodes: WsNode[]; categories: WsCategory[]; fields?: WsField[] }
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
export type Settings = Record<"theme" | "language" | "start_on_login" | "default_company_id" | "notifications" | "update_channel" | "mirror_policy", string>;

// ---------------------------------------------------------------- documents (Phase 6)
export interface Staged { token: string; name: string; size: number }
export interface QueueItem {
  token: string; node_id: string; category_id?: string | null; display_name?: string | null; field_values?: Record<string, unknown> | null;
  duplicate_policy?: string | null; document_id?: string | null; change_reason?: string | null; filing_rule_id?: string | null;
}
export type ImportStatus = "waiting" | "preparing" | "uploading" | "verifying" | "completed" | "failed" | "cancelled" | "quarantined";
export interface ImportRow {
  id: string; org_id: string; company_id: string; batch_id: string | null; file_name: string; display_name: string | null; size: number; sha256: string | null;
  node_id: string; category_id: string | null; document_id: string | null; duplicate_policy: string; status: ImportStatus; bytes_sent: number; attempts: number;
  next_retry_at: string | null; error_code: string | null; error_message: string | null; error_detail: unknown; result_document_id: string | null; created_at: string; updated_at: string;
}
export interface Rejected { name: string; code: string; message: string }
export interface QueueResult { queued: ImportRow[]; rejected: Rejected[] }
export interface DocCan { view: boolean; download: boolean; edit: boolean; move: boolean; archive: boolean; delete: boolean; purge: boolean }
export interface DocumentRow {
  id: string; node_id: string; category_id: string | null; display_name: string; original_filename: string; extension: string; status: "active" | "archived" | "deleted";
  current_version_number: number; latest_version_number?: number; source: string; filed_path_snapshot?: string | null; legal_hold: boolean;
  created_by_name: string | null; created_at: string; updated_at: string; archived_at: string | null; row_version: number;
  mime_type: string; size_bytes: number; sha256: string; scan_status: string; can: DocCan; field_values?: Record<string, unknown>; path?: string[]; versions?: DocVersion[];
}
export interface DocVersion { id: string; version_number: number; original_filename: string; extension: string; mime_type: string; size_bytes: number; sha256: string; scan_status: string; change_reason: string | null; uploaded_by_name: string | null; created_at: string; is_current: boolean; purged_at: string | null }
export interface DocEvent { id: string; event_type: string; created_at: string; actor_name: string | null; detail: Record<string, unknown> | null }
export interface DocPage<T> { items: T[]; total: number; limit: number; offset: number }
export interface DocLimits { max_file_size: number; batch_max_files: number; allowed_extensions: string[]; chunk_max_bytes?: number; max_concurrent?: number }
export interface DocCounts { by_node: Record<string, { active: number; archived: number }>; totals: { active: number; archived: number } }
export interface Suggestion { filing: { rule: { id: string; name: string } | null; target_node_id: string | null; category_id: string | null; path: string[] | null; matches: { rule_id: string; name: string }[] }; naming: { suggested_name: string | null; pattern: string | null; missing: string[] }; [k: string]: unknown }
export interface DuplicateCheck { exists: boolean; document: { id: string; display_name: string; status: string; node_id: string; path: string[] } | null }
export interface Preview { mime: string; size: number; sha256: string; data_base64: string }
export interface LocalCopy { path: string; rel_path: string; size: number; already_present: boolean }
export interface MirrorStatus { files: number; missing?: number; root_configured?: boolean; [k: string]: unknown }
export type DocOpName = "limits" | "counts" | "storage" | "list" | "get" | "versions" | "events" | "patch" | "move" | "archive" | "restore" | "delete" | "undelete" | "restore_version" | "suggest" | "check_duplicate";

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
  /** Native file picker: the UI gets opaque tokens, never paths. */
  filesPick(): Promise<Staged[]>;
  importsQueue(org: string, company: string, items: QueueItem[]): Promise<QueueResult>;
  importsList(company: string | null): Promise<ImportRow[]>;
  importsProcess(): Promise<number>;
  importCancel(org: string, id: string): Promise<void>;
  importRetry(id: string, opts?: { duplicatePolicy?: string; nodeId?: string; categoryId?: string | null }): Promise<boolean>;
  importRemove(id: string): Promise<boolean>;
  documentsOp<T = unknown>(org: string, company: string, op: DocOpName, args?: Record<string, unknown>): Promise<T>;
  documentPreview(org: string, company: string, doc: string, version?: number | null): Promise<Preview>;
  documentDownload(org: string, company: string, doc: string, version?: number | null): Promise<LocalCopy>;
  documentOpen(org: string, company: string, doc: string, version?: number | null): Promise<void>;
  mirrorStatus(company: string): Promise<MirrorStatus>;
  mirrorReconcile(org: string, company: string): Promise<unknown>;
  mirrorSync(org: string, company: string): Promise<unknown>;
  /** Backend events: workspace-updated (RefreshOutcome), session-expired, sync-error (AppError), files-dropped (Staged[]), imports-updated. */
  on(event: "workspace-updated" | "session-expired" | "sync-error" | "files-dropped" | "imports-updated", handler: (payload: unknown) => void): Promise<Unlisten>;
}
