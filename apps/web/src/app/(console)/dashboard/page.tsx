"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { LocaleSwitch } from "@/components/LocaleSwitch";
import { useI18n } from "@/i18n";
import { ApiProblem, bff, problemMessage } from "@/lib/client";

type Me = Schemas["MeOut"];
type Company = Schemas["CompanyOut"];
type Entitlement = Schemas["EntitlementOut"];

export default function DashboardPage() {
  const { t } = useI18n();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [companies, setCompanies] = useState<Company[] | null>(null);
  const [limits, setLimits] = useState<Entitlement[]>([]);
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const profile = await bff<Me>("/bff/me");
        setMe(profile);
        const org = profile.memberships[0]?.organization_id;
        if (!org) return;
        try {
          setCompanies(await bff<Company[]>(`/bff/orgs/${org}/companies`, { org }));
          setLimits(await bff<Entitlement[]>(`/bff/orgs/${org}/entitlements`, { org }).catch(() => []));
        } catch (err) {
          if (err instanceof ApiProblem && err.problem.code === "mfa_required") setMfaRequired(true);
          else throw err;
        }
      } catch (err) {
        if (err instanceof ApiProblem && err.status === 401) router.replace("/login");
        else setError(problemMessage(err, t.errors));
      }
    })();
  }, [router, t.errors]);

  async function signOut() {
    await bff("/session/tenant/logout", { method: "POST", body: {} });
    router.replace("/login");
  }

  const hard = limits.filter((l) => l.enforcement === "hard" && l.current !== null);
  return (
    <main id="main" className="mx-auto grid max-w-4xl gap-6 px-4 py-8">
      <header className="flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">{t.dashboardTitle}</h1>
        <div className="flex items-center gap-4">
          <LocaleSwitch />
          <Button variant="secondary" onClick={signOut}>{t.signOut}</Button>
        </div>
      </header>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      {mfaRequired ? (
        <Alert tone="warning">
          {t.mfaNeeded} <Link className="a-link" href="/security/mfa">{t.setUpMfa}</Link>
        </Alert>
      ) : null}
      {!me ? <p>{t.loading}</p> : null}
      {me ? (
        <Panel>
          <h2 className="mb-2 font-semibold">{t.organizations}</h2>
          <table className="a-table">
            <thead><tr><th>{t.organizations}</th><th>{t.role}</th></tr></thead>
            <tbody>
              {me.memberships.map((m) => (
                <tr key={m.organization_id}>
                  <td>{m.organization_name}</td>
                  <td>{(m.organization_role ? [m.organization_role] : Object.values(m.company_roles)).map((r) => t.roles[r] ?? r).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}
      {companies ? (
        <Panel>
          <h2 className="mb-2 font-semibold">{t.companies}</h2>
          {companies.length === 0 ? (
            <p className="a-hint">{t.noCompanies}</p>
          ) : (
            <ul>{companies.map((c) => <li key={c.id}>{c.name} <span className="a-hint">({c.code})</span></li>)}</ul>
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
                  <td className="num"><bdi>{l.current}</bdi> {t.usageOf} <bdi>{l.unlimited ? t.unlimited : l.limit}</bdi></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}
    </main>
  );
}
