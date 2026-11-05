import { useEffect, useState } from "react";
import { Alert, Button } from "@artify/ui";
import { Companies } from "./screens/companies";
import { Dashboard, Notifications, Placeholder, Sync } from "./screens/misc";
import { Settings } from "./screens/settings";
import { Workspace } from "./screens/workspace";
import { Documents, ImportDocuments, UploadQueue } from "./screens/documents";
import { ProcessingQueue, Review } from "./screens/intelligence";
import { Brand } from "./screens/auth";
import { useApp, type Page } from "./state/app";

const NAV: { page: Page; label: string; future?: boolean }[] = [
  { page: "dashboard", label: "Dashboard" }, { page: "companies", label: "My Companies" }, { page: "workspace", label: "Workspace" },
  { page: "documents", label: "All Documents" }, { page: "import", label: "Import Documents" }, { page: "queue", label: "Upload Queue" }, { page: "archive", label: "Archived Documents" },
  { page: "processing", label: "Processing Queue" },
  { page: "accounting", label: "Accounting", future: true }, { page: "reports", label: "Reports", future: true },
  { page: "sync", label: "Synchronization" }, { page: "notifications", label: "Notifications" }, { page: "settings", label: "Settings" },
];

export function Shell() {
  const { page, reviewDoc, go, org, orgs, chooseOrg, company, identity, notice, dismissNotice, bridge } = useApp();
  const [unread, setUnread] = useState(0);
  const [conn, setConn] = useState<string>("unknown");
  useEffect(() => {
    let live = true;
    const tick = () => { bridge.syncStatus(company?.id ?? null).then((s) => { if (live) setConn(s.connectivity.state); }).catch(() => undefined); bridge.notificationsList().then((n) => live && setUnread(n.filter((x) => !x.read).length)).catch(() => undefined); };
    tick();
    const t = setInterval(tick, 30_000);
    return () => { live = false; clearInterval(t); };
  }, [bridge, company, page]);
  return (
    <div className="d-shell">
      <a className="a-skip" href="#main">Skip to content</a>
      <aside className="d-side" aria-label="Main">
        <Brand />
        <nav><ul>{NAV.map((n) => (
          <li key={n.page}><button type="button" className="d-nav" aria-current={page === n.page || (n.page === "processing" && page === "review") ? "page" : undefined} onClick={() => go(n.page)}>
            <span>{n.label}</span>{n.future ? <span className="d-soon">Soon</span> : n.page === "notifications" && unread > 0 ? <span className="d-badge" aria-label={`${unread} unread`}>{unread}</span> : null}
          </button></li>))}</ul></nav>
      </aside>
      <div className="d-main">
        <header className="d-top">
          <div className="d-top__ctx">
            {orgs.length > 1 ? (
              <label className="d-inline"><span className="a-visually-hidden">Organization</span>
                <select value={org?.id} onChange={(e) => { const o = orgs.find((x) => x.id === e.target.value); if (o) chooseOrg(o); }}>{orgs.map((o) => <option key={o.id} value={o.id}>{o.display_name}</option>)}</select></label>
            ) : <strong>{org?.display_name}</strong>}
            <span aria-hidden="true">›</span>
            <span>{company ? company.name : "No company open"}</span>
          </div>
          <div className="d-top__right">
            <span className={`d-conn d-conn--${conn}`} role="status">{conn === "online" ? "● Online" : conn === "offline" ? "● Offline" : "● Not checked"}</span>
            <span className="a-hint">{identity.display_name ?? identity.email}</span>
          </div>
        </header>
        <main id="main" className="d-content" tabIndex={-1}>
          {notice ? <Alert tone="warning">{notice} <Button variant="secondary" className="a-btn--sm" onClick={dismissNotice}>Dismiss</Button></Alert> : null}
          {page === "dashboard" ? <Dashboard /> : page === "companies" ? <Companies /> : page === "workspace" ? <Workspace key={company?.id ?? "none"} /> : page === "documents" ? <Documents mode="active" key="docs" /> : page === "archive" ? <Documents mode="archived" key="arch" /> : page === "import" ? <ImportDocuments /> : page === "queue" ? <UploadQueue /> : page === "processing" ? <ProcessingQueue /> : page === "review" ? <Review key={reviewDoc ?? "none"} /> : page === "sync" ? <Sync /> : page === "notifications" ? <Notifications /> : page === "settings" ? <Settings /> : <Placeholder page={page} />}
        </main>
      </div>
    </div>
  );
}
