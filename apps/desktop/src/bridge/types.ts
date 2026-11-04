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

// ---------------------------------------------------------------- documents (Phase 6)
export interface StagedFile { id: string; name: string; size: number; extension: string; sha256: string | null; problem: string | null }
export type QueueStatus = "preparing" | "waiting" | "uploading" | "verification_pending" | "completed" | "failed" | "cancelled" | "quarantined";
export interface QueueItem {
  id: string; org_id: string; company_id: string; file_name: string; display_name: string | null; size: number; status: QueueStatus; bytes_sent: number;
  attempts: number; node_id: string | null; category_id: string | null; target_document_id: string | null; error_code: string | null; error_message: string | null;
  duplicates: { id: string; display_name: string; node_id?: string }[] | null; result_document_id: string | null; can_retry: boolean; created_at: string; updated_at: string;
}
export interface QueueAddItem {
  staged_id: string; node_id?: string | null; category_id?: string | null; display_name?: string | null; metadata?: Record<string, unknown>;
  filing_rule_id?: string | null; duplicate_policy?: "block" | "allow_copy"; target_document_id?: string | null; change_reason?: string | null; batch_local?: string | null;
}
export interface RunSummary { completed: number; failed: number; quarantined: number; cancelled: number; waiting: number; stopped: string | null }
export interface UploadTarget { id: string; parent_id: string | null; node_type: string; key: string; label: string; position: number; depth: number }
export interface UploadCategory { id: string; key: string; name: string; document_kind: string; node_id: string | null; allowed_file_types: string[] }
export interface UploadConfig { company_id: string; version: number; targets: UploadTarget[]; categories: UploadCategory[]; fields: { key: string; label: string; data_type: string; required: boolean }[] }
export interface DocSummary {
  id: string; node_id: string; category_id: string | null; display_name: string; original_filename: string; extension: string; mime_type: string; size_bytes: number;
  content_sha256: string; current_version_no: number; status: "active" | "archived" | "deleted"; retention_hold: boolean; created_by_name: string | null;
  created_at: string; updated_at: string; row_version: number; previewable: boolean;
}
export interface DocVersion { version_no: number; original_filename: string; size_bytes: number; sha256: string; uploaded_by_name: string | null; uploaded_at: string; change_reason: string | null; restored_from_version: number | null; scan_status: string }
export interface DocDetail extends DocSummary {
  versions: DocVersion[]; metadata: { key: string; label: string; data_type: string; value: unknown; applies: boolean }[];
  duplicates: { id: string; display_name: string }[]; location: string[]; can: Record<string, boolean>; scan_status: string; node_archived: boolean;
}
export interface DocPage { items: DocSummary[]; total: number; limit: number; offset: number }
export interface DocQuery { node_id?: string; include_subfolders?: boolean; status?: "active" | "archived" | "deleted"; q?: string; extension?: string; sort?: string; order?: string; limit?: number; offset?: number }
export interface DocCounts { nodes: Record<string, { own_active: number; active: number; archived: number; deleted: number }>; totals: { active: number; archived: number; bytes: number } }
export interface DocEvent { id: string; event_type: string; actor_name: string | null; created_at: string; summary: Record<string, unknown> }
export interface Preview { mime: string; size: number; data_base64: string; name: string }
export interface SavedCopy { path: string; created: boolean; name: string }
export interface MirrorReport { created: number; renamed: number; unchanged: number; conflicts: string[]; too_long: string[]; orphaned: number }

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
  // documents: files are picked or dropped in Rust; the interface only ever holds opaque staged ids, never paths
  importPick(): Promise<StagedFile[]>;
  importUnstage(id: string): Promise<void>;
  queueAdd(org: string, company: string, items: QueueAddItem[]): Promise<QueueItem[]>;
  queueList(company: string | null): Promise<QueueItem[]>;
  queueRun(): Promise<RunSummary>;
  queueRetry(id: string): Promise<QueueItem>;
  queueCancel(id: string): Promise<QueueItem>;
  queueRemove(id: string): Promise<boolean>;
  queueClearFinished(company: string | null): Promise<number>;
  uploadConfig(org: string, company: string): Promise<UploadConfig>;
  docsList(org: string, company: string, query: DocQuery): Promise<DocPage>;
  docsCounts(org: string, company: string): Promise<DocCounts>;
  docsGet(org: string, company: string, doc: string): Promise<DocDetail>;
  docsEvents(org: string, company: string, doc: string | null): Promise<{ items: DocEvent[] }>;
  docsPatch(org: string, company: string, doc: string, patch: { display_name?: string; metadata?: Record<string, unknown>; base_row_version?: number }): Promise<DocDetail>;
  docsMove(org: string, company: string, doc: string, node: string, category: string | null, baseRowVersion: number | null): Promise<DocDetail>;
  docsAction(org: string, company: string, doc: string, action: "archive" | "restore" | "delete" | "undelete" | "duplicate" | "purge"): Promise<unknown>;
  docsRestoreVersion(org: string, company: string, doc: string, version: number): Promise<DocDetail>;
  docsSuggest(org: string, company: string, body: { filename: string; category_id?: string | null; node_id?: string | null }): Promise<{ filing: { node_id: string | null; category_id: string | null; rule_name: string | null }; naming: { suggested_name: string } }>;
  docsDuplicates(org: string, company: string, sha256: string): Promise<{ duplicates: { id: string; display_name: string }[] }>;
  docsPreview(org: string, company: string, doc: string, version: number | null): Promise<Preview>;
  docsSaveCopy(org: string, company: string, doc: string, version: number | null): Promise<SavedCopy>;
  docsOpen(org: string, company: string, doc: string, version: number | null): Promise<SavedCopy>;
  mirrorSync(org: string, company: string): Promise<MirrorReport>;
  mirrorOpen(org: string, company: string, node: string | null): Promise<string>;
  /** Backend events: workspace-updated (RefreshOutcome), session-expired, sync-error (AppError), queue-changed, queue-progress, documents-changed, files-dropped (StagedFile[]). */
  on(event: "workspace-updated" | "session-expired" | "sync-error" | "queue-changed" | "queue-progress" | "documents-changed" | "files-dropped", handler: (payload: unknown) => void): Promise<Unlisten>;
}
