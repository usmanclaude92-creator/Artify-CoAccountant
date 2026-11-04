"use client";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, Suspense, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Alert, Button } from "@artify/ui";
import { bff } from "@/lib/client";
import { ROLE_LABEL, papi, useApi, type S } from "@/lib/platform";
import { Loading, ErrorBox, ToastProvider } from "./kit";

const MeCtx = createContext<S["PlatformMe"] | null>(null);
/** The signed-in administrator (profile and permission keys). Permissions only shape the UI; the API re-checks each call. */
export const useMe = () => {
  const me = useContext(MeCtx);
  if (!me) throw new Error("useMe outside the platform shell");
  return me;
};
export const useCan = () => {
  const me = useMe();
  return (perm: string) => me.permissions.includes(perm);
};

interface NavItem { label: string; href: string; perm: string; match: (path: string, type: string | null) => boolean }
const NAV: NavItem[] = [
  { label: "Overview", href: "/platform", perm: "platform.dashboard.read", match: (p) => p === "/platform" },
  { label: "Accounting Firms", href: "/platform/organizations?type=accounting_firm", perm: "platform.organizations.read", match: (p, t) => p.startsWith("/platform/organizations") && t === "accounting_firm" },
  { label: "Independent Accountants", href: "/platform/organizations?type=independent_accountant", perm: "platform.organizations.read", match: (p, t) => p.startsWith("/platform/organizations") && t === "independent_accountant" },
  { label: "All Organizations", href: "/platform/organizations", perm: "platform.organizations.read", match: (p, t) => p.startsWith("/platform/organizations") && !t },
  { label: "Client Companies", href: "/platform/companies", perm: "platform.companies.read", match: (p) => p.startsWith("/platform/companies") },
  { label: "Subscription Plans", href: "/platform/plans", perm: "platform.plans.read", match: (p) => p.startsWith("/platform/plans") },
  { label: "Subscriptions", href: "/platform/subscriptions", perm: "platform.subscriptions.read", match: (p) => p.startsWith("/platform/subscriptions") },
  { label: "Billing & Payments", href: "/platform/billing", perm: "platform.billing.read", match: (p) => p.startsWith("/platform/billing") },
  { label: "Usage & Limits", href: "/platform/usage", perm: "platform.usage.read", match: (p) => p.startsWith("/platform/usage") },
  { label: "Platform Administrators", href: "/platform/admins", perm: "platform.admins.read", match: (p) => p.startsWith("/platform/admins") },
  { label: "Support Access", href: "/platform/support", perm: "platform.support.read", match: (p) => p.startsWith("/platform/support") },
  { label: "Platform Activity", href: "/platform/activity", perm: "platform.audit.read", match: (p) => p.startsWith("/platform/activity") },
  { label: "Audit Logs", href: "/platform/audit", perm: "platform.audit.read", match: (p) => p.startsWith("/platform/audit") },
  { label: "System Health", href: "/platform/health", perm: "platform.health.read", match: (p) => p.startsWith("/platform/health") },
  { label: "Platform Settings", href: "/platform/settings", perm: "platform.settings.read", match: (p) => p.startsWith("/platform/settings") },
];

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<S["SearchHit"][] | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (q.trim().length < 2) return;
    const t = setTimeout(() => papi<S["SearchHit"][]>(`/search?q=${encodeURIComponent(q.trim())}`).then((h) => { setHits(h); setOpen(true); }).catch(() => setHits([])), 250);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);
  const shown = q.trim().length >= 2 ? hits : null;
  const href = (h: S["SearchHit"]) =>
    h.kind === "organization" ? `/platform/organizations/${h.id}` : h.kind === "company" ? `/platform/companies?q=${encodeURIComponent(h.title)}` : "/platform/admins";
  return (
    <div className="a-search" role="search" ref={box} onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <label className="a-visually-hidden" htmlFor="global-search">Search organizations, companies and administrators</label>
      <input id="global-search" type="search" placeholder="Search organizations, companies, administrators…" value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => shown && setOpen(true)} autoComplete="off" />
      {open && shown ? (
        <div className="a-popover" role="region" aria-label="Search results" aria-live="polite">
          {shown.length === 0 ? <p className="a-hint" style={{ padding: "0.5rem" }}>No matches.</p> : shown.map((h) => (
            <Link key={`${h.kind}-${h.id}`} href={href(h)} onClick={() => { setOpen(false); setQ(""); }}>
              <strong>{h.title}</strong> <span className="a-hint">· {h.kind}</span>
              {h.subtitle ? <span className="a-hint" style={{ display: "block" }}>{h.subtitle}</span> : null}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Alerts() {
  const can = useCan();
  const { data } = useApi<S["Dashboard"]>(can("platform.dashboard.read") ? "/dashboard" : null);
  const [open, setOpen] = useState(false);
  const alerts = data?.alerts ?? [];
  const total = alerts.reduce((n, a) => n + a.count, 0);
  return (
    <div style={{ position: "relative" }} onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <Button variant="secondary" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen((o) => !o)}>
        Alerts {total ? <span className="a-badge" aria-label={`${total} open`}>{total}</span> : null}
      </Button>
      {open ? (
        <div className="a-popover a-popover--end" role="region" aria-label="Alerts">
          {alerts.length === 0 ? <p className="a-hint" style={{ padding: "0.5rem" }}>Nothing needs attention.</p> : alerts.map((a) => (
            <Link key={a.kind} href={a.href} onClick={() => setOpen(false)}>
              <strong>{a.count}</strong> — {a.message}
              <span className="a-hint" style={{ display: "block" }}>{a.severity}</span>
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Frame({ me, children }: { me: S["PlatformMe"]; children: ReactNode }) {
  const pathname = usePathname();
  const type = useSearchParams().get("type");
  const router = useRouter();
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const menu = menuFor === pathname; // the drawer closes itself on navigation: it is only "open" for the page that opened it
  const [profile, setProfile] = useState(false);
  const items = NAV.filter((n) => me.permissions.includes(n.perm));
  const signOut = async () => {
    await bff("/session/platform/logout", { method: "POST", body: {} }).catch(() => undefined);
    router.replace("/platform/login");
  };
  return (
    <div className="a-shell">
      <aside className="a-sidebar" id="platform-nav" data-open={menu}>
        <div className="a-brand">Artify<small>Super Admin</small></div>
        <nav aria-label="Super Admin">
          <ul className="a-nav" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {items.map((n) => (
              <li key={n.label}><Link href={n.href} aria-current={n.match(pathname, type) ? "page" : undefined}>{n.label}</Link></li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="a-content">
        <header className="a-topbar">
          <Button variant="secondary" className="a-menu-button" aria-expanded={menu} aria-controls="platform-nav" onClick={() => setMenuFor(menu ? null : pathname)}>Menu</Button>
          <GlobalSearch />
          <span className="a-topbar__spacer" />
          <Alerts />
          <div style={{ position: "relative" }} onKeyDown={(e) => e.key === "Escape" && setProfile(false)}>
            <Button variant="secondary" aria-expanded={profile} aria-haspopup="true" onClick={() => setProfile((p) => !p)}>{me.display_name}</Button>
            {profile ? (
              <div className="a-popover a-popover--end" role="region" aria-label="Administrator profile">
                <dl className="a-dl" style={{ padding: "0.5rem" }}>
                  <dt>Name</dt><dd>{me.display_name}</dd>
                  <dt>E-mail</dt><dd>{me.email}</dd>
                  <dt>Role</dt><dd>{ROLE_LABEL[me.role] ?? me.role}</dd>
                  <dt>Permissions</dt><dd>{me.permissions.length}</dd>
                </dl>
                <button type="button" className="a-item" onClick={signOut}>Sign out</button>
              </div>
            ) : null}
          </div>
        </header>
        <main id="main" className="a-main">{children}</main>
      </div>
    </div>
  );
}

export function PlatformShell({ children }: { children: ReactNode }) {
  const { data: me, error, loading, reload } = useApi<S["PlatformMe"]>("/me");
  const router = useRouter();
  useEffect(() => {
    if (error && /permit|authenticat|session/i.test(error)) router.replace("/platform/login");
  }, [error, router]);
  if (loading && !me) return <main id="main" className="a-main"><Loading what="Super Admin" /></main>;
  if (!me) return <main id="main" className="a-main"><ErrorBox message={error ?? "Could not load your profile."} onRetry={reload} /><Alert tone="info">Sign in again if your session ended.</Alert></main>;
  return (
    <MeCtx.Provider value={me}>
      <ToastProvider>
        <Suspense fallback={null}>
          <Frame me={me}>
            <Suspense fallback={<Loading />}>{children}</Suspense>
          </Frame>
        </Suspense>
      </ToastProvider>
    </MeCtx.Provider>
  );
}
