"use client";
import { useState, type ReactNode } from "react";
import { Alert, Button, Dialog } from "@artify/ui";
import { Field, useToast } from "@/components/kit";
import { ApiProblem } from "@/lib/client";
import { errorText, tapi } from "@/lib/tenant";

export interface Node {
  id: string; parent_id: string | null; node_type: "section" | "folder"; key: string; name: string; display_name: string | null; description: string;
  position: number; depth: number; is_active: boolean; archived_at: string | null; label: string; can?: Record<string, boolean>;
}
export interface Category {
  id: string; key: string; name: string; description: string; document_kind: string; accounting_domain: string; processing_type: string;
  allowed_file_types: string[]; node_id: string | null; is_active: boolean; archived_at: string | null;
}
export interface FieldOption { id: string; value: string; label: string; archived_at: string | null }
export interface CField {
  id: string; key: string; label: string; description: string; data_type: string; required: boolean; validation: Record<string, unknown>;
  display_order: number; is_active: boolean; archived_at: string | null; reference_target: string | null; options: FieldOption[]; category_ids: string[]; node_ids: string[];
}
export interface NamingRule { id: string; name: string; pattern: string; description: string; category_id: string | null; is_active: boolean; archived_at: string | null }
export interface FilingRule {
  id: string; name: string; description: string; priority: number; conditions: { field: string; operator: string; value?: unknown }[];
  target_node_id: string; target_category_id: string | null; is_active: boolean; archived_at: string | null;
}
export interface Workspace {
  company_id: string; version: number; workspace_state: string; access_mode: "open" | "restricted"; applied_template: { template_id: string; version: number } | null;
  nodes: Node[]; categories: Category[]; fields: CField[]; naming_rules: NamingRule[]; filing_rules: FilingRule[];
  capabilities: { configure: boolean; manage_permissions: boolean; templates: boolean };
}
export interface Kinds { items: { key: string; name: string; description: string; accounting_domain: string; processing_type: string }[]; field_types: string[]; naming_tokens: Record<string, string> }
export interface Ctx { orgId: string; companyId: string; ws: Workspace; reload: () => void; editable: boolean }

export const KIND_LABEL = (kinds: Kinds | null, key: string) => kinds?.items.find((k) => k.key === key)?.name ?? key;

/** Runs a change against the API with the loaded version, shows a toast, reloads. A stale version reloads and explains. */
export function useChange(ctx: Ctx) {
  const toast = useToast();
  return async function change<T = unknown>(method: string, path: string, body: Record<string, unknown> | undefined, ok: string): Promise<T | null> {
    try {
      const payload = method === "DELETE" ? undefined : { ...(body ?? {}), base_version: ctx.ws.version };
      const url = `/companies/${ctx.companyId}/workspace${path}${method === "DELETE" ? `?base_version=${ctx.ws.version}` : ""}`;
      const out = await tapi<T>(ctx.orgId, url, { method, body: payload });
      toast(ok);
      ctx.reload();
      return out;
    } catch (err) {
      if (err instanceof ApiProblem && err.problem.code === "workspace_version_conflict") {
        toast("Someone else changed this workspace. It was reloaded; please repeat your change.");
        ctx.reload();
        return null;
      }
      throw err;
    }
  };
}

/** Modal form with busy/error handling. `onSubmit` throws to show the message inside the dialog. */
export function FormDialog({ title, submit, danger = false, onClose, onSubmit, children, disabled = false }: {
  title: string; submit: string; danger?: boolean; onClose: () => void; onSubmit: () => Promise<void>; children: ReactNode; disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Dialog open title={title} onClose={onClose}>
      <form className="grid gap-4" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr(null);
        try { await onSubmit(); onClose(); } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
      }}>
        {children}
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <div className="a-actions" style={{ justifyContent: "flex-end" }}>
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" className={danger ? "a-btn--danger" : ""} disabled={busy || disabled}>{busy ? "Working…" : submit}</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function NodeSelect({ nodes, value, onChange, label, allowEmpty, emptyLabel = "—", exclude, hint }: {
  nodes: Node[]; value: string; onChange: (v: string) => void; label: string; allowEmpty?: boolean; emptyLabel?: string; exclude?: Set<string>; hint?: string;
}) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const path = (n: Node): string => (n.parent_id && byId.get(n.parent_id) ? `${path(byId.get(n.parent_id)!)} / ${n.label}` : n.label);
  const opts = nodes.filter((n) => !n.archived_at && !exclude?.has(n.id)).map((n) => ({ id: n.id, text: path(n) })).sort((a, b) => a.text.localeCompare(b.text));
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
          {allowEmpty ? <option value="">{emptyLabel}</option> : null}
          {opts.map((o) => <option key={o.id} value={o.id}>{o.text}</option>)}
        </select>
      )}
    </Field>
  );
}

export const readOnlyNote = (editable: boolean) => (editable ? null : <Alert tone="info">This company is inactive or archived, or your role is read-only here: changes are disabled.</Alert>);
