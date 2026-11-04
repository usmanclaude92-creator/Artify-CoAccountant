import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { useEffect, useId, useRef } from "react";

export function Button({
  variant = "primary",
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  return <button {...rest} className={`a-btn a-btn--${variant} ${className}`.trim()} />;
}

export function TextField({
  label,
  hint,
  error,
  ...input
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; error?: string | null }) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  return (
    <div className="a-field">
      <label htmlFor={id}>{label}</label>
      <input
        {...input}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errId].filter(Boolean).join(" ") || undefined}
      />
      {hint ? (
        <span id={hintId} className="a-hint">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={errId} className="a-error">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function Alert({
  tone = "info",
  children,
}: {
  tone?: "danger" | "success" | "warning" | "info";
  children: ReactNode;
}) {
  // role=alert interrupts assistive tech: reserve it for errors. Others are polite status text.
  return (
    <div className={`a-alert a-alert--${tone}`} role={tone === "danger" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`a-panel ${className}`.trim()}>{children}</section>;
}

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

/** Status text with colour AND a text label (never colour alone, WCAG 1.4.1). */
export function StatusChip({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`a-chip a-chip--${tone}`}>{children}</span>;
}

/** Modal dialog on the native <dialog> element: focus is trapped, Escape closes, focus returns on close. */
export function Dialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  return (
    <dialog ref={ref} className="a-dialog" aria-labelledby={titleId} onClose={onClose} onCancel={onClose}>
      {open ? (
        <div className="a-dialog__body">
          <h2 id={titleId} className="a-dialog__title">
            {title}
          </h2>
          {children}
        </div>
      ) : null}
    </dialog>
  );
}
