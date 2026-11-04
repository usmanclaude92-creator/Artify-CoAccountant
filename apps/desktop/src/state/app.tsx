import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Bridge, CompanyDetail, Identity, Organization, Settings, Staged } from "../bridge/types";
import { describe, isSessionExpired } from "../lib/errors";

export type Page = "dashboard" | "companies" | "workspace" | "documents" | "import" | "queue" | "archived" | "processing" | "accounting" | "reports" | "sync" | "notifications" | "settings";

export interface AppCtx {
  bridge: Bridge;
  identity: Identity;
  org: Organization | null;
  orgs: Organization[];
  company: CompanyDetail | null;
  page: Page;
  settings: Settings | null;
  notice: string | null;
  /** organizations and settings have been loaded */
  ready: boolean;
  go: (p: Page) => void;
  chooseOrg: (o: Organization) => void;
  openCompany: (companyId: string) => Promise<boolean>;
  closeCompany: () => void;
  setSetting: (key: string, value: string) => Promise<void>;
  signOut: (message?: string) => Promise<void>;
  /** Route any error: a dead session returns to sign-in, everything else is shown as a notice. */
  fail: (e: unknown) => string;
  dismissNotice: () => void;
  showNotice: (m: string) => void;
  /** files chosen or dropped but not queued yet (opaque tokens from the Rust side) */
  pendingFiles: Staged[];
  addPendingFiles: (f: Staged[]) => void;
  removePendingFile: (token: string) => void;
  clearPendingFiles: () => void;
}
const Ctx = createContext<AppCtx | null>(null);
export function useApp(): AppCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp outside provider");
  return c;
}

export function Session({ bridge, identity, onSignedOut, children }: { bridge: Bridge; identity: Identity; onSignedOut: (message?: string) => void; children: ReactNode }) {
  const [orgs, setOrgs] = useState<Organization[] | null>(null);
  const [org, setOrg] = useState<Organization | null>(null);
  const [company, setCompany] = useState<CompanyDetail | null>(null);
  const [page, setPage] = useState<Page>("companies");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingFiles, setPendingFiles] = useState<Staged[]>([]);
  const ended = useRef(false);

  const signOut = useCallback(async (message?: string) => {
    if (ended.current) return;
    ended.current = true;
    await bridge.authLogout().catch(() => undefined);
    onSignedOut(message);
  }, [bridge, onSignedOut]);

  const fail = useCallback((e: unknown): string => {
    if (isSessionExpired(e)) {
      ended.current = true;
      onSignedOut("Your session has ended. Please sign in again.");
      return "";
    }
    const m = describe(e);
    setNotice(m);
    return m;
  }, [onSignedOut]);

  useEffect(() => {
    let live = true;
    Promise.all([bridge.organizations(), bridge.settingsGet()])
      .then(([o, s]) => {
        if (!live) return;
        setOrgs(o.items);
        setSettings(s);
        if (o.items.length === 1) setOrg(o.items[0] ?? null);
      })
      .catch((e) => live && fail(e));
    return () => { live = false; };
  }, [bridge, fail]);

  useEffect(() => {
    const offs: Promise<() => void>[] = [bridge.on("session-expired", () => fail({ code: "session_expired", message: "", retryable: false }))];
    return () => { offs.forEach((p) => p.then((f) => f()).catch(() => undefined)); };
  }, [bridge, fail]);

  // files dropped on the window are staged by the Rust side; the user then chooses where they go
  useEffect(() => {
    const off = bridge.on("files-dropped", (p) => {
      const files = (p as Staged[]) ?? [];
      if (files.length === 0) return;
      setPendingFiles((cur) => [...cur, ...files]);
      setPage("import");
    });
    return () => { void off.then((f) => f()).catch(() => undefined); };
  }, [bridge]);

  // keep this installation registered/active for the chosen organization (heartbeat); a revoked device ends the session
  useEffect(() => {
    if (!org) return;
    bridge.deviceEnsure(org.id).catch((e) => {
      const code = (e as { code?: string })?.code;
      if (code === "device_revoked") void signOut("This device has been revoked. Contact your administrator.");
      else if (code !== "network_unavailable" && code !== "timeout") fail(e);
    });
  }, [bridge, org, fail, signOut]);

  const ctx = useMemo<AppCtx>(() => ({
    bridge, identity, org, orgs: orgs ?? [], company, page, settings, notice, ready: orgs !== null && settings !== null,
    go: (p) => { setNotice(null); setPage(p); },
    chooseOrg: (o) => { setOrg(o); setCompany(null); setPendingFiles([]); setPage("companies"); },
    openCompany: async (id) => {
      if (!org) return false;
      try {
        setCompany(await bridge.companySelect(org.id, id));
        setPage("workspace");
        return true;
      } catch (e) {
        fail(e);
        return false;
      }
    },
    closeCompany: () => { setCompany(null); setPendingFiles([]); },
    setSetting: async (k, v) => {
      await bridge.settingsSet(k, v);
      setSettings((s) => (s ? { ...s, [k]: v } : s));
    },
    signOut, fail, dismissNotice: () => setNotice(null), showNotice: setNotice,
    pendingFiles,
    addPendingFiles: (f) => setPendingFiles((cur) => [...cur, ...f]),
    removePendingFile: (t) => setPendingFiles((cur) => cur.filter((x) => x.token !== t)),
    clearPendingFiles: () => setPendingFiles([]),
  }), [bridge, identity, org, orgs, company, page, settings, notice, signOut, fail, pendingFiles]);

  return <Ctx.Provider value={ctx}>{children}</Ctx.Provider>;
}
