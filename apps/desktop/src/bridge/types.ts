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

// ---------------------------------------------------------------- document intelligence (Phase 7)
export type Box = [number, number, number, number]; // normalised x0,y0,x1,y1 (0..1) on the processed page image
export type ReviewStatus = "extracted" | "needs_review" | "confirmed" | "corrected" | "missing" | "unreadable" | "rejected";
export interface ProcessingJob {
  id: string; document_id: string; version_no: number | null; kind: string; status: string; languages: string[] | null; pages_total: number | null; pages_done: number | null;
  attempt_count: number; max_attempts: number; error_code: string | null; error_message: string | null; reason: string | null; created_at: string; finished_at: string | null; progress: number;
}
export interface ClassificationView {
  id: string; type_key: string; kind_key: string | null; confidence: number; method: string; evidence: unknown; alternatives: { type_key: string; confidence: number }[] | null;
  needs_manual: boolean; provider_key: string; review_status: string; created_at: string;
}
export interface IntelLatest { job: ProcessingJob | null; classification: ClassificationView | null; extraction_id: string | null }
export interface ReviewQueueItem { id: string; document_id: string; display_name: string; type_key: string; type_name: string; summary: Record<string, unknown> | null; confidence: number | null; created_at: string }
export interface ExtractedField {
  id: string; field_key: string; label: string; kind: string; required: boolean; value: string | null; original_value: string | null; confidence: number | null;
  page_no: number | null; bbox: Box | null; source_text: string | null; method: string; review_status: ReviewStatus; flags: string[] | null;
}
export interface ExtractedLine { id: string; line_no: number; page_no: number | null; bbox: Box | null; source_text: string | null; values: Record<string, string | null>; confidence: number | null; review_status: ReviewStatus; flags: string[] | null }
export interface StatementTx {
  id: string; row_no: number; page_no: number | null; bbox: Box | null; source_text: string | null; tx_date: string | null; value_date: string | null; description: string | null; reference: string | null;
  debit: string | null; credit: string | null; balance: string | null; currency: string | null; tx_type: string | null; confidence: number | null; review_status: ReviewStatus; manual: boolean; flags: string[] | null;
}
export interface StatementHeader {
  id: string; bank_name: string | null; account_holder: string | null; account_name: string | null; account_number_masked: string | null; iban_masked: string | null; currency: string | null;
  period_start: string | null; period_end: string | null; opening_balance: string | null; closing_balance: string | null; validation: Validation | null;
}
export interface ValidationIssue { code: string; severity: "error" | "warning" | "info"; message: string; data?: Record<string, unknown> }
export type Validation = ValidationIssue[];
export interface WorkspaceSuggestion { id: string; rank: number; node_id: string | null; node_label: string | null; category_id: string | null; category_name: string | null; confidence: number | null; method: string; evidence: unknown; status: string; you_can_file_here: boolean }
export interface MetadataSuggestion { id: string; field_id: string; field_key: string; label: string; value: unknown; confidence: number | null; evidence: unknown; problems: string[] | null; status: string }
export interface SplitProposal { id: string; segments: { from_page: number; to_page: number; type_key?: string | null }[]; evidence: unknown; status: string }
export interface ExtractionView {
  id: string; document_id: string; version_id: string; extraction_no: number; type_key: string; schema_key: string; status: string; provider_key: string; model_version: string | null;
  validation: Validation | null; summary: Record<string, unknown> | null; created_at: string; approved_at: string | null; row_version: number;
  fields: ExtractedField[]; line_items: ExtractedLine[]; line_fields: { key: string; label: string; kind: string }[]; classification: ClassificationView | null;
  statement?: StatementHeader; transactions?: StatementTx[];
  workspace_suggestions: WorkspaceSuggestion[]; metadata_suggestions: MetadataSuggestion[]; split_proposal: SplitProposal | null; unresolved: { fields: number; line_items: number; transactions: number };
}
export interface OcrBlock { no: number; text: string; confidence: number | null; bbox: Box }
export interface OcrPage { page_no: number; width: number; height: number; source: string; is_blank: boolean; avg_confidence: number | null; rotation_applied: number | null; deskew_angle: number | null; quality: unknown; text: string; blocks: OcrBlock[] }
export interface OcrResult { id: string; provider_key: string; engine_version: string | null; languages: string[] | null; page_count: number; avg_confidence: number | null; pages: OcrPage[] }
export interface PageImage { page: number; data_base64: string }
export type ReviewKind = "field" | "line-items" | "transactions";

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
  // document intelligence: the server decides everything; AI output is a suggestion until an accountant approves
  intelStart(org: string, company: string, doc: string, kind: "full" | "ocr" | "classify" | "extract", languages: string[], reason: string | null): Promise<ProcessingJob>;
  intelLatest(org: string, company: string, doc: string): Promise<IntelLatest>;
  intelQueue(org: string, company: string): Promise<{ items: ReviewQueueItem[]; total: number }>;
  intelJobs(org: string, company: string, status: string | null): Promise<{ items: ProcessingJob[]; total: number }>;
  intelJobAction(org: string, company: string, job: string, action: "cancel" | "retry"): Promise<ProcessingJob>;
  intelExtraction(org: string, company: string, extraction: string): Promise<ExtractionView>;
  intelOcr(org: string, company: string, doc: string, pageFrom: number, pageTo: number): Promise<OcrResult>;
  intelReview(org: string, company: string, extraction: string, kind: ReviewKind, target: string, action: "correct" | "confirm" | "reject" | "missing" | "unreadable", payload: Record<string, unknown>, reason: string | null): Promise<unknown>;
  intelAddTransaction(org: string, company: string, extraction: string, data: Record<string, unknown>): Promise<StatementTx>;
  intelStatement(org: string, company: string, extraction: string, patch: Record<string, unknown>): Promise<unknown>;
  intelClassification(org: string, company: string, doc: string, action: "confirm" | "correct", typeKey: string | null): Promise<unknown>;
  intelDecide(org: string, company: string, extraction: string, action: "approve" | "reject", acknowledge: boolean, note: string | null): Promise<unknown>;
  intelAccept(org: string, company: string, extraction: string, kind: "workspace" | "metadata", suggestion: string, value: unknown): Promise<unknown>;
  intelSplit(org: string, company: string, extraction: string, approve: boolean): Promise<unknown>;
  intelPageImage(org: string, company: string, doc: string, page: number): Promise<PageImage>;
  /** Backend events: workspace-updated (RefreshOutcome), session-expired, sync-error (AppError), queue-changed, queue-progress, documents-changed, files-dropped (StagedFile[]). */
  on(event: "workspace-updated" | "session-expired" | "sync-error" | "queue-changed" | "queue-progress" | "documents-changed" | "processing-changed" | "files-dropped", handler: (payload: unknown) => void): Promise<Unlisten>;
}
