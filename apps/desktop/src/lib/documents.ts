import type { DocumentRow, ImportRow, ImportStatus, WsField, WsNode } from "../bridge/types";

/** Fields that apply to a category/folder: a field with no applicability rows applies everywhere, otherwise only to the
 *  listed categories or to the listed folders and their subfolders. The server validates again; this only decides what to ask. */
export function applicableFields(fields: WsField[] | undefined, categoryId: string | null, nodeChain: string[]): WsField[] {
  return (fields ?? []).filter((f) => {
    if (f.archived_at || !f.is_active) return false;
    if (f.category_ids.length === 0 && f.node_ids.length === 0) return true;
    return (categoryId !== null && f.category_ids.includes(categoryId)) || f.node_ids.some((n) => nodeChain.includes(n));
  });
}

/** ids from the root down to the node, following parent_id through the delivered nodes only */
export function nodeChain(nodes: WsNode[], nodeId: string): string[] {
  const by = new Map(nodes.map((n) => [n.id, n]));
  const out: string[] = [];
  let cur = by.get(nodeId);
  while (cur && !out.includes(cur.id)) {
    out.unshift(cur.id);
    cur = cur.parent_id ? by.get(cur.parent_id) : undefined;
  }
  return out;
}

/** Folders the user may upload to, according to what the server delivered (`can.upload`); nothing is assumed. */
export const uploadTargets = (nodes: WsNode[]): WsNode[] => nodes.filter((n) => !n.archived_at && n.is_active && n.can?.upload === true);

export type ViewerKind = "image" | "pdf" | "none";
const IMAGE = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff"]);
/** What the in-app viewer can show. Anything else is opened with the operating system's default application. */
export function viewerKind(mime: string, extension: string): ViewerKind {
  const m = mime.toLowerCase();
  if (m === "application/pdf" || extension.toLowerCase() === "pdf") return "pdf";
  // TIFF is not rendered by the web view; it goes to the default application
  if (IMAGE.has(m) && m !== "image/tiff") return "image";
  return "none";
}

export const IMPORT_STATUS_LABEL: Record<ImportStatus, string> = {
  waiting: "Waiting", preparing: "Preparing", uploading: "Uploading", verifying: "Verifying",
  completed: "Stored in the cloud", failed: "Failed", cancelled: "Cancelled", quarantined: "Blocked",
};
export const isActiveImport = (s: ImportStatus): boolean => s === "waiting" || s === "preparing" || s === "uploading" || s === "verifying";
export const isFinishedImport = (s: ImportStatus): boolean => s === "completed" || s === "cancelled";

/** Plain-language explanations for the codes the backend and the core return for a file. */
export function importProblem(code: string | null, fallback: string | null): string {
  switch (code) {
    case "network_unavailable": return "The cloud could not be reached. The file is kept and will continue when the connection returns.";
    case "timeout": return "The cloud took too long to answer. The file is kept and will be retried.";
    case "duplicate_detected": return "A document with identical content already exists in this company. Choose whether to keep both or skip this file.";
    case "unsupported_type": return "This file type is not allowed.";
    case "file_too_large": return "This file is larger than your plan or the platform allows.";
    case "empty_file": return "This file is empty.";
    case "content_mismatch": return "The content of this file does not match its type.";
    case "storage_quota": case "entitlement_exceeded": return "Your plan's storage or document limit is reached. Contact your administrator; nothing was deleted.";
    case "quarantined": case "infected": return "This file was blocked by the safety checks and was not stored.";
    case "source_missing": return "The original file was moved or deleted on this computer, so it cannot be uploaded.";
    case "target_unavailable": return "The destination folder is no longer available. Choose another folder and retry.";
    case "upload_expired": return "The upload took too long and expired. It will start again.";
    case "forbidden": return "You do not have permission to add documents to this folder.";
    default: return fallback ?? "The upload did not finish.";
  }
}

/** Retry-able without the user choosing something else. */
export const needsDecision = (r: ImportRow): boolean => r.error_code === "duplicate_detected" || r.error_code === "target_unavailable";

export function fmtPct(sent: number, size: number): number {
  if (size <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((sent / size) * 100)));
}

export const docStatusLabel = (d: Pick<DocumentRow, "status">): string => (d.status === "archived" ? "Archived" : d.status === "deleted" ? "Removed" : "Active");

/** Keys are the event types the API records (document_events.event_type). */
export const EVENT_LABEL: Record<string, string> = {
  uploaded: "Added", version_created: "New version added", version_restored: "Earlier version made current", renamed: "Renamed", moved: "Moved to another folder",
  metadata_changed: "Details changed", archived: "Archived", restored: "Restored", deleted: "Removed", undeleted: "Brought back", purged: "Permanently deleted",
  downloaded: "Downloaded or opened", previewed: "Previewed", legal_hold_changed: "Legal hold changed", duplicate_kept: "Duplicate kept as a separate document", quarantined: "Blocked by the safety checks",
};
export const eventLabel = (t: string): string => EVENT_LABEL[t] ?? t.replace(/_/g, " ");
