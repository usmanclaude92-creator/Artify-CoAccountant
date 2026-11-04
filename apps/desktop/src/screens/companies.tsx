import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Panel, StatusChip } from "@artify/ui";
import type { CompanyView, Organization } from "../bridge/types";
import { fmtDateTime, ROLE_LABEL } from "../lib/format";
import { useApp } from "../state/app";

export function OrgPicker() {
  const { orgs, chooseOrg, identity, signOut } = useApp();
  const role = (id: string) => ROLE_LABEL[identity.memberships.find((m) => m.organization_id === id)?.organization_role ?? ""] ?? "Member";
  return (
    <main className="d-login" id="main">
      <Panel className="d-login__card">
        <h1>Choose an organization</h1>
        {orgs.length === 0 ? (
          <Alert tone="info">You are not a member of any organization yet. Accept an invitation from your administrator, then sign in again.</Alert>
        ) : (
          <ul className="d-list" aria-label="Organizations">
            {orgs.map((o: Organization) => (
              <li key={o.id}><button type="button" className="d-list__item" onClick={() => chooseOrg(o)}><strong>{o.display_name}</strong><span className="a-hint">{role(o.id)}</span></button></li>
            ))}
          </ul>
        )}
        <Button variant="secondary" onClick={() => void signOut()}>Sign out</Button>
      </Panel>
    </main>
  );
}

type Filter = "all" | "active" | "favorites" | "recent";

export function Companies() {
  const { bridge, org, company, openCompany, fail, settings } = useApp();
  const [items, setItems] = useState<CompanyView[] | null>(null);
  const [stale, setStale] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!org) return;
    let live = true;
    bridge.companies(org.id).then((l) => { if (live) { setItems(l.items); setStale(l.stale); } }).catch((e) => live && fail(e));
    return () => { live = false; };
  }, [bridge, org, fail, tick]);

  // optional default company: opened once when the list first loads and nothing is open yet
  const defaultId = settings?.default_company_id ?? "";
  const autoDone = useRef(false);
  useEffect(() => {
    if (autoDone.current || !items || company || !defaultId) return;
    autoDone.current = true;
    if (items.some((c) => c.id === defaultId && c.status === "active")) void openCompany(defaultId);
  }, [items, company, defaultId, openCompany]);

  const shown = useMemo(() => {
    if (!items) return [];
    const needle = q.trim().toLowerCase();
    let list = items.filter((c) => !needle || [c.name, c.code, c.country].some((v) => v.toLowerCase().includes(needle)));
    if (filter === "active") list = list.filter((c) => c.status === "active");
    if (filter === "favorites") list = list.filter((c) => c.favorite);
    if (filter === "recent") list = list.filter((c) => c.last_opened_at).sort((a, b) => (b.last_opened_at ?? "").localeCompare(a.last_opened_at ?? "")).slice(0, 5);
    return list;
  }, [items, q, filter]);

  if (!org) return null;
  const open = async (id: string) => { setBusy(id); await openCompany(id); setBusy(null); setTick((t) => t + 1); };
  const fav = async (c: CompanyView) => { try { await bridge.companyFavorite(c.id, !c.favorite); setItems((l) => l?.map((x) => (x.id === c.id ? { ...x, favorite: !x.favorite } : x)) ?? l); } catch (e) { fail(e); } };

  return (
    <section aria-labelledby="h-companies" className="d-page">
      <header className="d-page__head"><h1 id="h-companies">My Companies</h1><Button variant="secondary" onClick={() => setTick((t) => t + 1)}>Refresh</Button></header>
      {stale ? <Alert tone="warning">The server could not be reached. This is the list saved on this computer; it may be out of date.</Alert> : null}
      <div className="d-toolbar" role="search">
        <div className="a-field"><label htmlFor="co-search">Search companies</label><input id="co-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, code or country" /></div>
        <div className="a-field"><label htmlFor="co-filter">Show</label>
          <select id="co-filter" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
            <option value="all">All companies</option><option value="active">Active only</option><option value="favorites">Favorites</option><option value="recent">Recently opened</option>
          </select>
        </div>
      </div>
      {items === null ? <p role="status">Loading companies…</p> : shown.length === 0 ? (
        <Panel><p>{items.length === 0 ? "You have no authorized companies in this organization yet. Ask your administrator to assign you to a company." : "No company matches your search."}</p></Panel>
      ) : (
        <div className="a-tablewrap">
          <table className="a-table">
            <caption className="a-visually-hidden">Companies you can access</caption>
            <thead><tr><th scope="col">Company</th><th scope="col">Code</th><th scope="col">Country</th><th scope="col">Currency</th><th scope="col">Status</th><th scope="col">Last opened</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.id} aria-current={company?.id === c.id}>
                  <th scope="row">{c.name}{company?.id === c.id ? <span className="a-hint"> (open)</span> : null}</th>
                  <td>{c.code}</td><td>{c.country}</td><td>{c.base_currency}</td>
                  <td><StatusChip tone={c.status === "active" ? "success" : c.status === "suspended" ? "warning" : "neutral"}>{c.status === "suspended" ? "Inactive" : c.status[0]?.toUpperCase() + c.status.slice(1)}</StatusChip></td>
                  <td>{fmtDateTime(c.last_opened_at)}</td>
                  <td className="d-actions">
                    <Button className="a-btn--sm" disabled={busy === c.id} onClick={() => void open(c.id)} aria-label={`Open ${c.name}`}>Open</Button>
                    <Button variant="secondary" className="a-btn--sm" aria-pressed={c.favorite} onClick={() => void fav(c)} aria-label={`${c.favorite ? "Remove" : "Add"} ${c.name} ${c.favorite ? "from" : "to"} favorites`}>{c.favorite ? "★ Favorite" : "☆ Favorite"}</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="a-hint">Opening a company is always confirmed by the server. Company logos are not available yet.</p>
    </section>
  );
}
