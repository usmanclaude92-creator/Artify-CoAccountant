import { useEffect, useState } from "react";
import type { WorkspaceData } from "../bridge/types";
import { useApp } from "./app";

/** The open company's workspace as delivered to this user (structure, categories, fields). Nothing here is hard-coded. */
export function useWs(): { data: WorkspaceData | null; stale: boolean; error: string | null } {
  const { bridge, org, company, fail } = useApp();
  const [data, setData] = useState<WorkspaceData | null>(null);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const orgId = org?.id;
  const companyId = company?.id;
  useEffect(() => {
    if (!orgId || !companyId) return;
    let live = true;
    const load = () => bridge.workspaceGet(orgId, companyId)
      .then((v) => { if (live) { setData(v.data); setStale(v.stale); setError(null); } })
      .catch((e) => { if (live) { const m = fail(e); if (m) setError(m); } });
    void load();
    const off = bridge.on("workspace-updated", () => { void load(); });
    return () => { live = false; void off.then((f) => f()).catch(() => undefined); };
  }, [bridge, orgId, companyId, fail]);
  return { data, stale, error };
}
