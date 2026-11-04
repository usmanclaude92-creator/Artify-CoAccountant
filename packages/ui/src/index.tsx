import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { useId } from "react";

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
