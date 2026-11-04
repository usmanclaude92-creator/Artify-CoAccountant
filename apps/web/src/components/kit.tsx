"use client";
import Link from "next/link";
import { createContext, useCallback, useContext, useId, useState, type ReactNode } from "react";
import { Alert, Button, Dialog, StatusChip } from "@artify/ui";
import { STATUS_TONE, errorText, label } from "@/lib/platform";

/* ---------------------------------------------------------------- toasts */
interface Toast { id: number; tone: "success" | "danger" | "info"; text: string }
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const notify = useCallback((text: string, tone: Toast["tone"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);
  return (
    <ToastCtx.Provider value={notify}>
      {children}
      <div className="a-toasts" aria-live="polite" aria-atomic="false">
        {toasts.map((t) => <Alert key={t.id} tone={t.tone}>{t.text}</Alert>)}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------------------------------------------------------------- page chrome */
export interface Crumb { label: string; href?: string }
export function PageHeader({ title, crumbs = [], actions, home = { label: "Super Admin", href: "/platform" } }: { title: string; crumbs?: Crumb[]; actions?: ReactNode; home?: Crumb & { href: string } }) {
  return (
    <div className="grid gap-2">
      <nav aria-label="Breadcrumb" className="a-crumbs">
        <ol>
          <li><Link className="a-link" href={home.href}>{home.label}</Link></li>
          {crumbs.map((c) => <li key={c.label}>{c.href ? <Link className="a-link" href={c.href}>{c.label}</Link> : <span aria-current="page">{c.label}</span>}</li>)}
        </ol>
      </nav>
      <div className="a-pagehead">
        <h1>{title}</h1>
        {actions ? <div className="a-actions">{actions}</div> : null}
      </div>
    </div>
  );
}

export function Status({ value, text }: { value: string; text?: string }) {
  return <StatusChip tone={STATUS_TONE[value] ?? "neutral"}>{text ?? label(value)}</StatusChip>;
}

export function Loading({ what = "data" }: { what?: string }) {
  return <p role="status" className="a-hint">Loading {what}…</p>;
}
export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="grid gap-2">
      <Alert tone="danger">{message}</Alert>
      {onRetry ? <div><Button variant="secondary" onClick={onRetry}>Try again</Button></div> : null}
    </div>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <p className="a-hint" style={{ padding: "1rem 0" }}>{children}</p>;
}

export function Kpi({ label: l, value, note, available = true }: { label: string; value?: ReactNode; note?: string; available?: boolean }) {
  return (
    <div className={`a-kpi ${available ? "" : "a-kpi--na"}`}>
      <span className="a-kpi__label">{l}</span>
      <span className="a-kpi__value">{available ? value : "Not available"}</span>
      {note ? <span className="a-hint">{note}</span> : null}
    </div>
  );
}

export function Meter({ percent, status }: { percent: number | null; status: string }) {
  if (percent === null) return <span className="a-hint">—</span>;
  const tone = status === "exceeded" ? "danger" : status === "approaching" ? "warning" : "";
  return (
    <div className={`a-meter ${tone ? `a-meter--${tone}` : ""}`} role="img" aria-label={`${percent}% of limit used`}>
      <span style={{ inlineSize: `${Math.min(100, percent)}%` }} />
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="a-tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" id={`tab-${t.id}`} aria-selected={active === t.id} aria-controls={`panel-${t.id}`} onClick={() => onChange(t.id)}>{t.label}</button>
      ))}
    </div>
  );
}

export function Field({ label: l, children, hint }: { label: string; children: (id: string) => ReactNode; hint?: string }) {
  const id = useId();
  return (
    <div className="a-field">
      <label htmlFor={id}>{l}</label>
      {children(id)}
      {hint ? <span className="a-hint">{hint}</span> : null}
    </div>
  );
}

export function SelectFilter({ label: l, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <Field label={l}>
      {(id) => (
        <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">All</option>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )}
    </Field>
  );
}

export function SearchBox({ value, onSubmit, placeholder = "Search" }: { value: string; onSubmit: (v: string) => void; placeholder?: string }) {
  const [draft, setDraft] = useState(value);
  return (
    <form role="search" onSubmit={(e) => { e.preventDefault(); onSubmit(draft.trim()); }} className="a-toolbar">
      <Field label="Search">{(id) => <input id={id} type="search" value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)} />}</Field>
      <Button type="submit" variant="secondary">Search</Button>
    </form>
  );
}

/* ---------------------------------------------------------------- data table */
export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  sortKey?: string;
  numeric?: boolean;
}
export function DataTable<T>({
  caption, columns, rows, loading, error, onRetry, empty, rowKey, total, limit, offset, onPage, sort, order, onSort,
}: {
  caption: string; columns: Column<T>[]; rows: T[] | undefined; loading: boolean; error: string | null; onRetry?: () => void;
  empty: string; rowKey: (r: T) => string; total?: number; limit?: number; offset?: number; onPage?: (offset: number) => void;
  sort?: string; order?: string; onSort?: (key: string) => void;
}) {
  if (error) return <ErrorBox message={error} onRetry={onRetry} />;
  if (!rows && loading) return <Loading />;
  const from = total === 0 || !rows?.length ? 0 : (offset ?? 0) + 1;
  const to = (offset ?? 0) + (rows?.length ?? 0);
  return (
    <div aria-busy={loading}>
      <div className="a-tablewrap">
        <table className="a-table">
          <caption className="a-visually-hidden">{caption}</caption>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} scope="col" className={c.numeric ? "a-num" : undefined}
                  aria-sort={c.sortKey && sort === c.sortKey ? (order === "asc" ? "ascending" : "descending") : undefined}>
                  {c.sortKey && onSort ? <button type="button" onClick={() => onSort(c.sortKey!)}>{c.header}</button> : c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows?.map((r) => <tr key={rowKey(r)}>{columns.map((c) => <td key={c.key} className={c.numeric ? "a-num" : undefined}>{c.render(r)}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
      {rows && rows.length === 0 ? <Empty>{empty}</Empty> : null}
      {total !== undefined && limit !== undefined && onPage ? (
        <nav className="a-pager" aria-label={`${caption} pagination`}>
          <span role="status">{total === 0 ? "No results" : `Showing ${from}–${to} of ${total}`}</span>
          <span className="a-actions">
            <Button variant="secondary" className="a-btn--sm" disabled={(offset ?? 0) === 0} onClick={() => onPage(Math.max(0, (offset ?? 0) - limit))}>Previous</Button>
            <Button variant="secondary" className="a-btn--sm" disabled={to >= total} onClick={() => onPage((offset ?? 0) + limit)}>Next</Button>
          </span>
        </nav>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- confirmation dialog for sensitive actions */
export function ActionDialog({
  open, title, description, submitLabel, danger = false, confirmWord, needsReason = true, minReason = 10, reasonLabel = "Reason (recorded in the audit log)",
  children, onSubmit, onClose,
}: {
  open: boolean; title: string; description?: ReactNode; submitLabel: string; danger?: boolean; confirmWord?: string; needsReason?: boolean; minReason?: number;
  reasonLabel?: string; children?: ReactNode; onSubmit: (v: { reason: string; confirm: string }) => Promise<void | false>; onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const reasonOk = !needsReason || reason.trim().length >= minReason;
  const confirmOk = !confirmWord || confirm.trim().toLowerCase() === confirmWord.toLowerCase();
  const close = () => { setReason(""); setConfirm(""); setErr(null); onClose(); };
  return (
    <Dialog open={open} title={title} onClose={close}>
      <form
        className="grid gap-4"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try { const r = await onSubmit({ reason: reason.trim(), confirm: confirm.trim() }); if (r !== false) { setReason(""); setConfirm(""); } }
          catch (ex) { setErr(errorText(ex)); }
          finally { setBusy(false); }
        }}
      >
        {description ? <div className="a-hint">{description}</div> : null}
        {children}
        {needsReason ? (
          <Field label={reasonLabel} hint={`At least ${minReason} characters.`}>
            {(id) => <textarea id={id} value={reason} onChange={(e) => setReason(e.target.value)} required minLength={minReason} maxLength={500} />}
          </Field>
        ) : null}
        {confirmWord ? (
          <Field label={`Type “${confirmWord}” to confirm`}>
            {(id) => <input id={id} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" spellCheck={false} />}
          </Field>
        ) : null}
        {err ? <Alert tone="danger">{err}</Alert> : null}
        <div className="a-actions" style={{ justifyContent: "flex-end" }}>
          <Button type="button" variant="secondary" onClick={close}>Cancel</Button>
          <Button type="submit" className={danger ? "a-btn--danger" : ""} disabled={busy || !reasonOk || !confirmOk}>{busy ? "Working…" : submitLabel}</Button>
        </div>
      </form>
    </Dialog>
  );
}
