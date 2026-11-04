"use client";
import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { Schemas } from "@artify/api-client";
import { ApiProblem, bff } from "@/lib/client";

export type S = Schemas;
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Calls the platform API through the BFF (HttpOnly cookies; never a token in page JavaScript). */
export function papi<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  return bff<T>(`/bff/platform${path}`, init);
}

export function errorText(err: unknown): string {
  if (err instanceof ApiProblem) {
    const p = err.problem;
    if (err.status === 403 && p.code === "forbidden") return "Your role does not permit this.";
    if (p.errors?.length) return p.errors.map((e) => `${e.loc.slice(1).join(".")}: ${e.msg}`).join("; ");
    return p.detail ?? p.code ?? "Request failed.";
  }
  return "The request could not be completed. Check your connection and try again.";
}

/** GET with loading / error state. A 401 (session ended) sends the administrator back to sign-in.
 *  Loading is derived (the request key differs from the answered key), so no state is set synchronously in the effect. */
export function useApi<T>(path: string | null): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const router = useRouter();
  const [tick, setTick] = useState(0);
  const [res, setRes] = useState<{ key: string; data: T | null; error: string | null } | null>(null);
  const key = path === null ? null : `${path}#${tick}`;
  useEffect(() => {
    if (path === null || key === null) return;
    let live = true;
    papi<T>(path)
      .then((d) => live && setRes({ key, data: d, error: null }))
      .catch((err) => {
        if (!live) return;
        if (err instanceof ApiProblem && err.status === 401) router.replace("/platform/login");
        setRes((prev) => ({ key, data: prev?.data ?? null, error: errorText(err) }));
      });
    return () => {
      live = false;
    };
  }, [path, key, router]);
  const answered = res !== null && res.key === key;
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: res?.data ?? null, error: answered ? res.error : null, loading: key !== null && !answered, reload };
}

/** Filters, sorting and paging live in the URL: shareable, survives reload, no client storage. */
export function useListState(defaults: Record<string, string> = {}) {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const get = (k: string) => sp.get(k) ?? defaults[k] ?? "";
  const set = (patch: Record<string, string | null>) => {
    const q = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") q.delete(k);
      else q.set(k, v);
    }
    if (!("offset" in patch)) q.delete("offset");
    const s = q.toString();
    router.replace(s ? `${pathname}?${s}` : pathname);
  };
  const query = (keys: string[], extra: Record<string, string> = {}) => {
    const q = new URLSearchParams();
    for (const k of keys) {
      const v = get(k);
      if (v) q.set(k, v);
    }
    for (const [k, v] of Object.entries(extra)) if (v) q.set(k, v);
    const s = q.toString();
    return s ? `?${s}` : "";
  };
  return { get, set, query };
}

const dateFmt = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
const dayFmt = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" });
export const fmtDateTime = (v?: string | null) => (v ? `${dateFmt.format(new Date(v))} UTC` : "—");
export const fmtDate = (v?: string | null) => (v ? dayFmt.format(new Date(v)) : "—");
export const fmtNumber = (v?: number | null) => (v === null || v === undefined ? "—" : new Intl.NumberFormat("en-GB").format(v));
/** Money arrives as exact decimal strings; keep them as strings and only add the currency code. */
export const fmtMoney = (amount?: string | null, currency?: string | null) => (amount ? `${amount} ${currency ?? ""}`.trim() : "—");
export function fmtBytes(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}
export const label = (v: string) => v.replace(/[._]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
export const STATUS_TONE: Record<string, Tone> = {
  active: "success", trialing: "info", past_due: "warning", suspended: "danger", cancelled: "neutral", expired: "neutral",
  inactive: "neutral", retired: "neutral", closed: "neutral", draft: "neutral", open: "info", paid: "success", void: "neutral",
  requested: "warning", revoked: "neutral", denied: "danger", disabled: "danger", pending: "warning", accepted: "success",
  ok: "success", approaching: "warning", exceeded: "danger", degraded: "warning", down: "danger", not_configured: "neutral",
  unknown: "neutral", not_metered: "neutral", flag: "neutral", success: "success", failure: "danger", denied_: "danger",
};

export const ROLE_LABEL: Record<string, string> = {
  platform_owner: "Platform Owner", platform_admin: "Platform Administrator", platform_support: "Support Administrator",
  platform_billing: "Finance Administrator", platform_readonly: "Read-only Administrator", platform_auditor: "Auditor",
};
