"use client";
import Link from "next/link";
import { Suspense, useEffect, useEffectEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { LocaleSwitch } from "@/components/LocaleSwitch";
import { useI18n } from "@/i18n";
import { ApiProblem, bff, problemMessage } from "@/lib/client";

type Me = Schemas["MeOut"];
type Company = Schemas["CompanyOut"];
type Entitlement = Schemas["EntitlementOut"];

function Dashboard() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const [me, setMe] = useState<Me | null>(null);
  const [companies, setCompanies] = useState<Company[] | null>(null);
  const [limits, setLimits] = useState<Entitlement[]>([]);
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The active organization/company live in the URL: shareable, survives reloads, no client storage.
  const orgParam = params.get("org");
  const companyParam = params.get("company") ?? "";
  const orgId = me ? (me.memberships.find((m) => m.organization_id === orgParam)?.organization_id ?? me.memberships[0]?.organization_id) : undefined;

  function select(next: { org?: string; company?: string }) {
    const q = new URLSearchParams(params.toString());
    if (next.org !== undefined) {
      q.set("org", next.org);
      q.delete("company"); // a company belongs to exactly one organization
    }
    if (next.company !== undefined) {
      if (next.company) q.set("company", next.company);
      else q.delete("company");
    }
    router.replace(`/dashboard?${q.toString()}`);
  }

  // Reads the *current* language without making it a dependency: switching language must not refetch.
  const showError = useEffectEvent((err: unknown) => setError(problemMessage(err, t.errors)));

  useEffect(() => {
    bff<Me>("/bff/me")
      .then(setMe)
      .catch((err) => {
        if (err instanceof ApiProblem && err.status === 401) router.replace("/login");
        else showError(err);
      });
  }, [router]);

  useEffect(() => {
    if (!orgId) return;
    let cancelled = false;
    (async () => {
      setCompanies(null);
      setMfaRequired(false);
      try {
        const list = await bff<Company[]>(`/bff/orgs/${orgId}/companies`, { org: orgId });
        const lim = await bff<Entitlement[]>(`/bff/orgs/${orgId}/entitlements`, { org: orgId }).catch(() => []);
        if (!cancelled) {
          setCompanies(list);
          setLimits(lim);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiProblem && err.problem.code === "mfa_required") setMfaRequired(true);
        else showError(err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  async function signOut() {
    await bff("/session/tenant/logout", { method: "POST", body: {} });
    router.replace("/login");
  }

  const membership = me?.memberships.find((m) => m.organization_id === orgId);
  const roleLabels = membership
    ? (membership.organization_role ? [membership.organization_role] : Object.values(membership.company_roles)).map((r) => t.roles[r] ?? r).join(", ")
    : "";
  const selected = companies?.find((c) => c.id === companyParam);
  const hard = limits.filter((l) => l.enforcement === "hard" && l.current !== null);

  return (
    <main id="main" className="mx-auto grid max-w-4xl gap-6 px-4 py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">{t.dashboardTitle}</h1>
        <div className="flex items-center gap-4">
          <LocaleSwitch />
          <Button variant="secondary" onClick={signOut}>
            {t.signOut}
          </Button>
        </div>
      </header>

      {me ? (
        <div className="flex flex-wrap gap-4">
          <div className="a-field">
            <label htmlFor="org-switch">{t.activeOrganization}</label>
            <select id="org-switch" value={orgId ?? ""} onChange={(e) => select({ org: e.target.value })}>
              {me.memberships.map((m) => (
                <option key={m.organization_id} value={m.organization_id}>
                  {m.organization_name}
                </option>
              ))}
            </select>
          </div>
          <div className="a-field">
            <label htmlFor="company-switch">{t.activeCompany}</label>
            <select id="company-switch" value={companyParam} onChange={(e) => select({ company: e.target.value })} disabled={!companies}>
              <option value="">{t.allCompanies}</option>
              {(companies ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      ) : null}

      {error ? <Alert tone="danger">{error}</Alert> : null}
      {mfaRequired ? (
        <Alert tone="warning">
          {t.mfaNeeded}{" "}
          <Link className="a-link" href="/security/mfa">
            {t.setUpMfa}
          </Link>
        </Alert>
      ) : null}
      {!me ? <p>{t.loading}</p> : null}

      {membership ? (
        <Panel>
          <h2 className="mb-2 font-semibold">{membership.organization_name}</h2>
          <p className="a-hint">
            {t.role}: {roleLabels}
          </p>
        </Panel>
      ) : null}

      {selected ? (
        <Panel>
          <h2 className="mb-2 font-semibold">{t.companyDetails}</h2>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm" data-testid="company-details">
            <dt className="a-hint">{t.activeCompany}</dt>
            <dd>{selected.name}</dd>
            <dt className="a-hint">Code</dt>
            <dd>{selected.code}</dd>
            <dt className="a-hint">{t.baseCurrency}</dt>
            <dd>{selected.base_currency}</dd>
            <dt className="a-hint">{t.status}</dt>
            <dd>{selected.status}</dd>
          </dl>
        </Panel>
      ) : null}

      {companies ? (
        <Panel>
          <h2 className="mb-2 font-semibold">{t.companies}</h2>
          {companies.length === 0 ? (
            <p className="a-hint">{t.noCompanies}</p>
          ) : (
            <ul>
              {companies.map((c) => (
                <li key={c.id}>
                  {c.name} <span className="a-hint">({c.code})</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      ) : null}

      {hard.length > 0 ? (
        <Panel>
          <h2 className="mb-2 font-semibold">{t.usage}</h2>
          <table className="a-table">
            <tbody>
              {hard.map((l) => (
                <tr key={l.key}>
                  <td>{t.entitlements[l.key] ?? l.key}</td>
                  <td className="num">
                    <bdi>{l.current}</bdi> {t.usageOf} <bdi>{l.unlimited ? t.unlimited : l.limit}</bdi>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}
    </main>
  );
}

export default function DashboardPage() {
  const { t } = useI18n();
  return (
    <Suspense fallback={<p className="p-8">{t.loading}</p>}>
      <Dashboard />
    </Suspense>
  );
}
