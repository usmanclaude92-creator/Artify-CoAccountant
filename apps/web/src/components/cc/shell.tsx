"use client";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, Suspense, useContext, useState, type ReactNode } from "react";
import { Alert, Button } from "@artify/ui";
import type { Schemas } from "@artify/api-client";
import { bff } from "@/lib/client";
import { ORG_ROLE_LABEL, useTenantApi } from "@/lib/tenant";
import { ErrorBox, Loading, PageHeader, ToastProvider, type Crumb } from "@/components/kit";

type Me = Schemas["MeOut"];

interface OrgCtx {
  me: Me;
  orgId: string;
  orgName: string;
  role: string | null;
  permissions: string[];
  can: (perm: string) => boolean;
  /** Builds an in-app link that keeps the selected organization in the URL. */
  href: (path: string) => string;
  mfaRequired: boolean;
}
const Ctx = createContext<OrgCtx | null>(null);
/** The signed-in user, the selected organization and the permission keys the API granted for it.
 *  Permissions only shape the UI (hide what cannot work); the API re-checks every call. */
export function useOrg(): OrgCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useOrg outside the Control Center shell");
  return c;
}

/** Page header with the Control Center home crumb. */
export function CcHeader({ title, crumbs, actions }: { title: string; crumbs?: Crumb[]; actions?: ReactNode }) {
  const { href } = useOrg();
  return <PageHeader title={title} crumbs={crumbs} actions={actions} home={{ label: "Control Center", href: href("/dashboard") }} />;
}

interface NavItem { label: string; path: string; perm?: string; any?: string[] }
const NAV: NavItem[] = [
  { label: "Dashboard", path: "/dashboard" },
  { label: "My Companies", path: "/companies", perm: "companies.list" },
  { label: "Workspace Templates", path: "/workspace-templates", perm: "companies.list" },
  { label: "Documents & Storage", path: "/documents", perm: "storage.manage" },
  { label: "Document Processing", path: "/processing", perm: "processing.monitor" },
  { label: "Excel & Reports", path: "/excel", perm: "excel.monitor" },
  { label: "Daily Closing", path: "/bookkeeping", perm: "bookkeeping.monitor" },
  { label: "Team Members", path: "/team", perm: "members.read" },
  { label: "Accountants", path: "/accountants", perm: "members.read" },
  { label: "Client Assignments", path: "/assignments", perm: "members.read" },
  { label: "Invitations", path: "/invitations", perm: "invitations.manage" },
  { label: "Subscription & Usage", path: "/subscription", perm: "entitlements.read" },
  { label: "Activity & Audit History", path: "/activity", perm: "audit.read" },
  { label: "Organization Settings", path: "/settings", perm: "organization.read" },
  { label: "Support Access", path: "/support-access", perm: "support_grants.manage" },
  { label: "My Profile", path: "/profile" },
];

function Frame({ ctx, children }: { ctx: OrgCtx; children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const menu = menuFor === pathname; // the drawer closes itself on navigation
  const items = NAV.filter((n) => !n.perm || ctx.can(n.perm));
  const switchOrg = (id: string) => router.replace(`/dashboard?org=${id}`);
  const signOut = async () => {
    await bff("/session/tenant/logout", { method: "POST", body: {} }).catch(() => undefined);
    router.replace("/login");
  };
  return (
    <div className="a-shell">
      <aside className="a-sidebar" id="cc-nav" data-open={menu}>
        <div className="a-brand">Artify<small>Firm Control Center</small></div>
        <nav aria-label="Firm Control Center">
          <ul className="a-nav" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {items.map((n) => (
              <li key={n.path}>
                <Link href={ctx.href(n.path)} aria-current={pathname === n.path || pathname.startsWith(`${n.path}/`) ? "page" : undefined}>{n.label}</Link>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="a-content">
        <header className="a-topbar">
          <Button variant="secondary" className="a-menu-button" aria-expanded={menu} aria-controls="cc-nav" onClick={() => setMenuFor(menu ? null : pathname)}>Menu</Button>
          <div className="a-field" style={{ flexDirection: "row", alignItems: "center", gap: "0.5rem" }}>
            <label htmlFor="org-switch" className="a-hint">Organization</label>
            <select id="org-switch" value={ctx.orgId} onChange={(e) => switchOrg(e.target.value)}>
              {ctx.me.memberships.map((m) => <option key={m.organization_id} value={m.organization_id}>{m.organization_name}</option>)}
            </select>
          </div>
          <span className="a-topbar__spacer" />
          <span className="a-hint" data-testid="signed-in-as">{ctx.me.display_name} · {ctx.role ? (ORG_ROLE_LABEL[ctx.role] ?? ctx.role) : "Company access"}</span>
          <Button variant="secondary" onClick={signOut}>Sign out</Button>
        </header>
        <main id="main" className="a-main">
          {ctx.mfaRequired ? (
            <Alert tone="warning">
              Your role requires two-factor authentication before it can be used. <Link className="a-link" href="/security/mfa">Set up two-factor authentication</Link>
            </Alert>
          ) : null}
          {children}
        </main>
      </div>
    </div>
  );
}

function Inner({ children }: { children: ReactNode }) {
  const params = useSearchParams();
  const requested = params.get("org");
  const base = useTenantApi<Me>(undefined, "/me");
  const orgId = base.data
    ? (base.data.memberships.find((m) => m.organization_id === requested)?.organization_id ?? base.data.memberships[0]?.organization_id)
    : undefined;
  // Permissions are organization-specific: ask the API for them with the organization claim.
  const scoped = useTenantApi<Me>(orgId, orgId ? "/me" : null);
  const roles = useTenantApi<Schemas["RoleOut"][]>(undefined, "/roles");
  if (base.error && !base.data) return <main id="main" className="a-main"><ErrorBox message={base.error} onRetry={base.reload} /></main>;
  if (!base.data) return <main id="main" className="a-main"><Loading what="your workspace" /></main>;
  if (!orgId) {
    return (
      <main id="main" className="a-main">
        <Alert tone="info">You are not a member of any organization yet. Accept an invitation to join one.</Alert>
      </main>
    );
  }
  if (!scoped.data) return <main id="main" className="a-main">{scoped.error ? <ErrorBox message={scoped.error} onRetry={scoped.reload} /> : <Loading what="your workspace" />}</main>;
  const membership = base.data.memberships.find((m) => m.organization_id === orgId)!;
  const permissions = scoped.data.permissions ?? [];
  const ctx: OrgCtx = {
    me: base.data,
    orgId,
    orgName: membership.organization_name,
    role: membership.organization_role,
    permissions,
    can: (p) => permissions.includes(p),
    href: (path) => `${path}${path.includes("?") ? "&" : "?"}org=${orgId}`,
    // The API refuses privileged roles until the session has completed two-factor authentication.
    mfaRequired: !scoped.data.session_mfa_verified && (roles.data?.find((r) => r.key === membership.organization_role)?.mfa_required ?? false),
  };
  return (
    <Ctx.Provider value={ctx}>
      <ToastProvider>
        <Frame ctx={ctx}><Suspense fallback={<Loading />}>{children}</Suspense></Frame>
      </ToastProvider>
    </Ctx.Provider>
  );
}

export function ControlCenterShell({ children }: { children: ReactNode }) {
  return <Suspense fallback={<main id="main" className="a-main"><Loading what="your workspace" /></main>}><Inner>{children}</Inner></Suspense>;
}
