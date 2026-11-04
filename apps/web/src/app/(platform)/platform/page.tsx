"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Schemas } from "@artify/api-client";
import { Alert, Button, Panel } from "@artify/ui";
import { LocaleSwitch } from "@/components/LocaleSwitch";
import { useI18n } from "@/i18n";
import { ApiProblem, bff } from "@/lib/client";

type Org = Schemas["OrgSummary"];

export default function PlatformHome() {
  const { t } = useI18n();
  const router = useRouter();
  const [orgs, setOrgs] = useState<Org[] | null>(null);
  useEffect(() => {
    bff<Org[]>("/bff/platform/organizations")
      .then(setOrgs)
      .catch((err) => {
        if (err instanceof ApiProblem && [401, 403].includes(err.status)) router.replace("/platform/login");
      });
  }, [router]);
  return (
    <main id="main" className="mx-auto grid max-w-4xl gap-6 px-4 py-8">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t.platformName}</h1>
        <div className="flex items-center gap-4">
          <LocaleSwitch />
          <Button variant="secondary" onClick={async () => { await bff("/session/platform/logout", { method: "POST", body: {} }); router.replace("/platform/login"); }}>
            {t.signOut}
          </Button>
        </div>
      </header>
      <Alert tone="info">{t.platformNote}</Alert>
      <Panel>
        <h2 className="mb-2 font-semibold">{t.platformOrgs}</h2>
        {!orgs ? <p>{t.loading}</p> : (
          <table className="a-table">
            <thead><tr><th>Name</th><th>Type</th><th>Country</th><th>Status</th></tr></thead>
            <tbody>{orgs.map((o) => <tr key={o.id}><td>{o.display_name}</td><td>{o.type}</td><td>{o.country}</td><td>{o.status}</td></tr>)}</tbody>
          </table>
        )}
      </Panel>
    </main>
  );
}
