"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiProblem, bff } from "@/lib/client";
import { errorText } from "@/lib/platform";

export { errorText, fmtBytes, fmtDate, fmtDateTime, fmtNumber, fmtMoney, label, useListState, STATUS_TONE } from "@/lib/platform";
export type { Page, S } from "@/lib/platform";

/** Calls the customer API through the BFF (HttpOnly cookies). `org` becomes the X-Organization-Id claim, which the
 *  API validates against the caller's memberships on every request. */
export function tapi<T = unknown>(org: string | undefined, path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  return bff<T>(`/bff${path}`, { ...init, org });
}

/** GET with derived loading/error state (no state set synchronously in the effect). 401 -> sign in again. */
export function useTenantApi<T>(org: string | undefined, path: string | null): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const router = useRouter();
  const [tick, setTick] = useState(0);
  const [res, setRes] = useState<{ key: string; data: T | null; error: string | null } | null>(null);
  const key = path === null ? null : `${org ?? ""}|${path}#${tick}`;
  useEffect(() => {
    if (path === null || key === null) return;
    let live = true;
    tapi<T>(org, path)
      .then((d) => live && setRes({ key, data: d, error: null }))
      .catch((err) => {
        if (!live) return;
        if (err instanceof ApiProblem && err.status === 401) router.replace("/login");
        setRes((prev) => ({ key, data: prev?.data ?? null, error: errorText(err) }));
      });
    return () => {
      live = false;
    };
  }, [org, path, key, router]);
  const answered = res !== null && res.key === key;
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: res?.data ?? null, error: answered ? res.error : null, loading: key !== null && !answered, reload };
}

export const ORG_ROLE_LABEL: Record<string, string> = {
  firm_owner: "Organization owner", practice_admin: "Organization administrator", practice_manager: "Practice manager",
  accountant: "Accountant", assistant_accountant: "Assistant accountant", staff: "Staff", document_operator: "Document operator",
  read_only: "Read-only user", company_owner: "Company owner", company_admin: "Company administrator", company_employee: "Company employee",
};
