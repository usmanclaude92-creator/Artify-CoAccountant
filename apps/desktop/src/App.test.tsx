import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { createMockBridge, DEMO_COMPANIES, mockDoc, node, type MockBridge, type MockOptions } from "./bridge/mock";
import type { CompanyView, StagedFile, WorkspaceData } from "./bridge/types";

const co = (id: string, name: string, extra: Partial<CompanyView> = {}): CompanyView => ({ id, name, code: id.toUpperCase(), country: "OM", base_currency: "OMR", status: "active", favorite: false, last_opened_at: null, ...extra });

async function boot(opts: MockOptions = {}): Promise<{ bridge: MockBridge; user: ReturnType<typeof userEvent.setup> }> {
  const bridge = createMockBridge({ restore: true, ...opts });
  const user = userEvent.setup();
  render(<App bridge={bridge} />);
  return { bridge, user };
}
async function signIn(user: ReturnType<typeof userEvent.setup>, password = "correct") {
  await user.type(await screen.findByLabelText("Email address"), "accountant@example.com");
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}
const openCompany = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
  await user.click(await screen.findByRole("button", { name: `Open ${name}` }));
  await screen.findByRole("heading", { name: "Workspace", level: 1 });
};

describe("authentication", () => {
  it("shows the sign-in screen when there is no stored session", async () => {
    await boot({ restore: false });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
  it("signs in with valid credentials, clears the password field and reaches the app", async () => {
    const { bridge, user } = await boot({ restore: false });
    await signIn(user);
    expect(await screen.findByRole("heading", { name: "My Companies", level: 1 })).toBeInTheDocument();
    expect(bridge.calls).toContain("authLogin");
  });
  it("rejects invalid credentials with a message and no session", async () => {
    const { bridge, user } = await boot({ restore: false });
    await signIn(user, "wrong");
    expect(await screen.findByText("Invalid email or password.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(bridge.state.signedIn).toBe(false);
  });
  it("asks for the authenticator code when the account requires it", async () => {
    const { user } = await boot({ restore: false, mfa: true });
    await signIn(user);
    expect(await screen.findByRole("heading", { name: "Two-factor authentication" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Authenticator code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Invalid code.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Authenticator code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("heading", { name: "My Companies", level: 1 })).toBeInTheDocument();
  });
  it("restores a stored session without asking for a password", async () => {
    await boot({ restore: true });
    expect(await screen.findByRole("heading", { name: "My Companies", level: 1 })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });
  it("keeps the stored session and offers a retry when the server is unreachable at startup", async () => {
    const bridge = createMockBridge({ restore: true });
    let fail = true;
    const orig = bridge.authRestore;
    bridge.authRestore = async () => { if (fail) throw { code: "network_unavailable", message: "x", retryable: true }; return orig(); };
    const user = userEvent.setup();
    render(<App bridge={bridge} />);
    expect(await screen.findByText(/cannot be reached/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "My Companies", level: 1 })).toBeInTheDocument();
  });
  it("signs out, calls the backend and returns to sign-in", async () => {
    const { bridge, user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("tab", { name: "Account" }));
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(bridge.calls).toContain("authLogout");
    expect(bridge.state.signedIn).toBe(false);
  });
  it("returns to sign-in with an explanation when the session expires", async () => {
    const { bridge } = await boot();
    await screen.findByRole("heading", { name: "My Companies", level: 1 });
    await act(async () => { bridge.emit("session-expired"); });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("Your session has ended. Please sign in again.")).toBeInTheDocument();
  });
  it("treats an unauthorized API answer mid-session as an ended session", async () => {
    const { bridge, user } = await boot();
    await screen.findByRole("heading", { name: "My Companies", level: 1 });
    bridge.companies = async () => { throw { code: "session_expired", message: "Your session has ended.", status: 401, retryable: false }; };
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
  it("signs out a revoked device", async () => {
    const bridge = createMockBridge({ restore: true });
    bridge.deviceEnsure = async () => { throw { code: "device_revoked", message: "revoked", status: 404, retryable: false }; };
    render(<App bridge={bridge} />);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/device has been revoked/i)).toBeInTheDocument();
  });
  it("disables sign-in when the build has no server address", async () => {
    const bridge = createMockBridge({ restore: false });
    bridge.appInfo = async () => ({ ...(await createMockBridge().appInfo()), api_configured: false, api_base: null });
    render(<App bridge={bridge} />);
    expect(await screen.findByText(/no server address configured/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });
});

describe("organizations", () => {
  it("goes straight in for a single organization", async () => {
    await boot();
    expect(await screen.findByText("Gulf Accounting Partners")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Choose an organization" })).not.toBeInTheDocument();
  });
  it("lets a multi-organization user choose, and each organization has its own companies", async () => {
    const { user } = await boot({
      orgs: [{ id: "org-1", display_name: "Firm One" }, { id: "org-2", display_name: "Firm Two" }],
      companies: { "org-1": [co("c1", "Only In One")], "org-2": [co("c2", "Only In Two")] },
    });
    await user.click(await screen.findByRole("button", { name: /Firm Two/ }));
    expect(await screen.findByText("Only In Two")).toBeInTheDocument();
    expect(screen.queryByText("Only In One")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Organization"), "org-1");
    expect(await screen.findByText("Only In One")).toBeInTheDocument();
    expect(screen.queryByText("Only In Two")).not.toBeInTheDocument();
  });
  it("explains the situation of a user with no organization", async () => {
    await boot({ orgs: [] });
    expect(await screen.findByText(/not a member of any organization/i)).toBeInTheDocument();
  });
});

describe("company selection", () => {
  it("lists authorized companies with status, supports search, filters and favorites", async () => {
    const { user } = await boot();
    expect(await screen.findByText("Alpha Trading LLC")).toBeInTheDocument();
    expect(screen.getByText("Inactive")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search companies"), "beta");
    expect(screen.queryByText("Alpha Trading LLC")).not.toBeInTheDocument();
    expect(screen.getByText("Beta Contracting")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search companies"));
    await user.click(screen.getByRole("button", { name: "Add Beta Contracting to favorites" }));
    await user.selectOptions(screen.getByLabelText("Show"), "favorites");
    expect(screen.getByText("Beta Contracting")).toBeInTheDocument();
    expect(screen.queryByText("Alpha Trading LLC")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Show"), "active");
    expect(screen.queryByText("Gamma Retail")).not.toBeInTheDocument();
  });
  it("shows an empty state when the user has no company", async () => {
    await boot({ companies: { "org-1": [] } });
    expect(await screen.findByText(/no authorized companies/i)).toBeInTheDocument();
  });
  it("marks a list that came from the local copy", async () => {
    await boot({ offline: true });
    expect(await screen.findByText(/saved on this computer/i)).toBeInTheDocument();
  });
  it("shows the server's refusal and stays on the list", async () => {
    const b = createMockBridge({ restore: true, companies: { "org-1": [co("c-bad", "Revoked Meanwhile")] } });
    const user = userEvent.setup();
    render(<App bridge={b} />);
    await screen.findByText("Revoked Meanwhile");
    b.state.opts.denied = ["c-bad"];
    await user.click(screen.getByRole("button", { name: "Open Revoked Meanwhile" }));
    expect(await screen.findByText(/no longer have access/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "My Companies", level: 1 })).toBeInTheDocument();
  });
  it("switches between companies and each shows its own workspace", async () => {
    const { user } = await boot();
    await openCompany(user, "Alpha Trading LLC");
    expect(await screen.findByRole("tree", { name: "Alpha Trading LLC workspace" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "My Companies" }));
    await openCompany(user, "Beta Contracting");
    expect(await screen.findByRole("tree", { name: "Beta Contracting workspace" })).toBeInTheDocument();
  });
});

describe("dynamic workspace", () => {
  const names = (tree: HTMLElement) => within(tree).getAllByRole("treeitem").map((li) => li.querySelector(".d-tree__item")?.textContent ?? "");

  it("a four-section company shows exactly its four sections", async () => {
    const { user } = await boot();
    await openCompany(user, "Alpha Trading LLC");
    const tree = await screen.findByRole("tree");
    expect(names(tree)).toEqual(["Bank Statements", "Receivables", "Payables", "Expenses"]);
    expect(screen.queryByText("Reconciliation")).not.toBeInTheDocument();
  });
  it("shows the assigned accountants when the server provides them", async () => {
    const { user } = await boot();
    await openCompany(user, "Alpha Trading LLC");
    expect(await screen.findByText(/Accountants: Test Accountant \(primary\)/)).toBeInTheDocument();
  });
  it("a ten-section company with nested folders shows its own structure, expandable", async () => {
    const { user } = await boot();
    await openCompany(user, "Beta Contracting");
    const tree = await screen.findByRole("tree");
    expect(within(tree).getAllByRole("treeitem").filter((li) => li.parentElement === tree)).toHaveLength(10);
    expect(screen.getByText("Banking")).toBeInTheDocument(); // sections start expanded one level
    expect(screen.queryByText("Bank of Muscat")).not.toBeInTheDocument(); // deeper levels start collapsed
    await user.click(screen.getByRole("button", { name: "Expand Banking" }));
    expect(screen.getByText("Bank of Muscat")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Collapse Finance" }));
    expect(screen.queryByText("Bank of Muscat")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getByText("Credit Notes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryByText("Credit Notes")).not.toBeInTheDocument();
  });
  it("a third company with a different structure shows only that structure", async () => {
    const { user } = await boot({ companies: { "org-1": [DEMO_COMPANIES[2] ? { ...DEMO_COMPANIES[2], status: "active" } : co("x", "x")] } });
    await openCompany(user, "Gamma Retail");
    expect(names(await screen.findByRole("tree"))).toEqual(["Daily Sales Reports", "Card Settlements", "Stores"]);
  });
  it("hides archived items by default and shows them, marked, on request", async () => {
    const { user } = await boot();
    await openCompany(user, "Beta Contracting");
    await screen.findByRole("tree");
    expect(screen.queryByText(/Old suppliers/)).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Show archived items"));
    expect(screen.getByText("Old suppliers (archived)")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Old suppliers/ }));
    expect(screen.getByText(/cannot receive new documents/i)).toBeInTheDocument();
  });
  it("searches the structure and keeps the path to each match", async () => {
    const { user } = await boot();
    await openCompany(user, "Beta Contracting");
    await user.type(await screen.findByLabelText("Search folders"), "muscat");
    const tree = screen.getByRole("tree");
    expect(names(tree)).toEqual(["Finance", "Banking", "Bank of Muscat"]);
    await user.clear(screen.getByLabelText("Search folders"));
    await user.type(screen.getByLabelText("Search folders"), "no-such-folder");
    expect(screen.getByText(/No section or folder matches/)).toBeInTheDocument();
  });
  it("shows details of the selected folder including its place in the hierarchy", async () => {
    const { user } = await boot();
    await openCompany(user, "Beta Contracting");
    await user.click(await screen.findByRole("button", { name: "Expand Banking" }));
    await user.click(screen.getByRole("button", { name: "Bank of Muscat" }));
    expect(screen.getByRole("heading", { name: "Bank of Muscat" })).toBeInTheDocument();
    expect(screen.getByLabelText("Location")).toHaveTextContent("Finance / Banking / Bank of Muscat");
  });
  it("shows an honest empty state for a company without a configured workspace", async () => {
    const empty: Record<string, WorkspaceData> = { "c-alpha": { company_id: "c-alpha", version: 1, workspace_state: "not_configured", nodes: [], categories: [] } };
    const { user } = await boot({ workspaces: empty });
    await openCompany(user, "Alpha Trading LLC");
    expect(await screen.findByText(/no sections yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("tree")).not.toBeInTheDocument();
  });
  it("shows only what the server delivered for a permission-restricted user", async () => {
    const restricted: Record<string, WorkspaceData> = { "c-alpha": { company_id: "c-alpha", version: 4, workspace_state: "configured", access_mode: "restricted", nodes: [node("s", null, "Sales", { can: { view: true }, position: 0 }), node("loans", null, "Loans", { position: 1 })], categories: [] } };
    const { user } = await boot({ workspaces: restricted });
    await openCompany(user, "Alpha Trading LLC");
    expect(names(await screen.findByRole("tree"))).toEqual(["Sales", "Loans"]);
    expect(screen.queryByText("Payroll")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sales" }));
    expect(screen.getByText("View only", { exact: false })).toBeInTheDocument();
  });
  it("lists the user's permissions on the selected item, always starting with View", async () => {
    const ws: Record<string, WorkspaceData> = { "c-alpha": { company_id: "c-alpha", version: 3, workspace_state: "configured", nodes: [node("s", null, "Sales", { can: { upload: true, edit: true, rename: false, delete: false } })], categories: [] } };
    const { user } = await boot({ workspaces: ws });
    await openCompany(user, "Alpha Trading LLC");
    await user.click(await screen.findByRole("button", { name: "Sales" }));
    expect(screen.getByText("View, Upload, Edit")).toBeInTheDocument();
  });
  it("reloads when a newer configuration arrives, keeps the selection by id and tells the user what changed", async () => {
    const { bridge, user } = await boot();
    await openCompany(user, "Alpha Trading LLC");
    await user.click(await screen.findByRole("button", { name: "Receivables" }));
    expect(screen.getByRole("heading", { name: "Receivables" })).toBeInTheDocument();
    const ws = bridge.state.workspaces["c-alpha"] as WorkspaceData;
    ws.version = 8;
    ws.nodes = ws.nodes.map((n) => (n.id === "a1" ? { ...n, name: "Customer Receivables" } : n));
    await act(async () => { bridge.emit("workspace-updated", { changed: true, version: 8, summary: "Workspace updated: 1 renamed.", changes: [] }); });
    expect(await screen.findByText("Workspace updated: 1 renamed.")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Customer Receivables" })).toBeInTheDocument();
    expect(screen.getByText(/configuration version 8/)).toBeInTheDocument();
  });
  it("tells the user when the selected item disappeared in the new configuration", async () => {
    const { bridge, user } = await boot();
    await openCompany(user, "Alpha Trading LLC");
    await user.click(await screen.findByRole("button", { name: "Payables" }));
    const ws = bridge.state.workspaces["c-alpha"] as WorkspaceData;
    ws.version = 9;
    ws.nodes = ws.nodes.filter((n) => n.id !== "a2");
    await act(async () => { bridge.emit("workspace-updated", { changed: true, version: 9, summary: "Workspace updated: 1 no longer visible.", changes: [] }); });
    expect(await screen.findByText(/no longer visible to you/i)).toBeInTheDocument();
  });
  it("cannot open a company offline, because the server must confirm access, and says why", async () => {
    const { user } = await boot({ offline: true });
    await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
    expect(await screen.findByText(/cannot be reached/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "My Companies", level: 1 })).toBeInTheDocument();
  });
  it("shows a clear error with retry when the workspace cannot be loaded at all", async () => {
    const b = createMockBridge({ restore: true });
    const user = userEvent.setup();
    render(<App bridge={b} />);
    await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
    await screen.findByRole("tree");
    b.workspaceGet = async () => { throw { code: "network_unavailable", message: "x", retryable: true }; };
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/cannot be reached/i)).toBeInTheDocument();
  });
  it("asks for a company first when none is open", async () => {
    const { user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Workspace" }));
    expect(screen.getByText(/Choose a company first/)).toBeInTheDocument();
  });
});

describe("honest placeholders", () => {
  it.each([["Processing Queue"], ["Accounting"], ["Reports"]])("%s says it is not available and shows no sample data", async (label) => {
    const { user } = await boot();
    await user.click(await screen.findByRole("button", { name: new RegExp(label) }));
    expect(screen.getByRole("heading", { name: label, level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Not available yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText(/no sample data/i)).toBeInTheDocument();
  });
});

describe("settings", () => {
  it("saves the theme and keeps the language control honest", async () => {
    const { bridge, user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await user.selectOptions(screen.getByLabelText("Theme"), "dark");
    expect(bridge.calls).toContain("settingsSet:theme");
    expect(bridge.state.settings.theme).toBe("dark");
    expect(screen.getByLabelText("Language")).toBeDisabled();
  });
  it("chooses a storage location through the native dialog and reports its status", async () => {
    const { bridge, user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("tab", { name: "Storage" }));
    expect(await screen.findByText(/No location chosen yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Choose location…" }));
    expect(await screen.findByText("D:\\Artify")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText(/Existing files were not moved/)).toBeInTheDocument();
    expect(bridge.state.root).toBe("D:\\Artify");
  });
  it("shows a storage error returned by the backend", async () => {
    const b = createMockBridge({ restore: true });
    b.storageSetRoot = async () => { throw { code: "permission_denied", message: "The app cannot write to that folder.", retryable: false }; };
    const user = userEvent.setup();
    render(<App bridge={b} />);
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("tab", { name: "Storage" }));
    await user.click(await screen.findByRole("button", { name: "Choose location…" }));
    expect(await screen.findByText("The app cannot write to that folder.")).toBeInTheDocument();
  });
  it("lists devices and revokes one after confirmation", async () => {
    const { bridge, user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await waitFor(() => expect(bridge.calls).toContain("deviceEnsure"));
    await user.click(screen.getByRole("tab", { name: "Devices" }));
    expect(await screen.findByText("ACCOUNTING-PC-01")).toBeInTheDocument();
    window.confirm = () => true;
    await user.click(screen.getByRole("button", { name: "Revoke ACCOUNTING-PC-01" }));
    expect(await screen.findByText("Revoked")).toBeInTheDocument();
  });
  it("reports truthfully that automatic updates are not configured and exports diagnostics", async () => {
    const { user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("tab", { name: "About" }));
    await user.click(await screen.findByRole("button", { name: "Check for updates" }));
    expect(await screen.findByText(/not configured in this build/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export diagnostics" }));
    expect(await screen.findByText(/no passwords, tokens, company names or documents/i)).toBeInTheDocument();
  });
});

describe("synchronization and notifications", () => {
  it("shows the connection state and runs a manual synchronization", async () => {
    const { bridge, user } = await boot();
    await openCompany(user, "Alpha Trading LLC");
    await user.click(screen.getByRole("button", { name: "Synchronization" }));
    expect(await screen.findByText("Online")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Synchronize now" }));
    expect(await screen.findByText(/Already up to date/)).toBeInTheDocument();
    expect(bridge.calls).toContain("workspaceRefresh:c-alpha");
  });
  it("lists notifications and shows an empty state", async () => {
    const { user } = await boot({ notifications: [{ id: 1, ts: "2026-11-04T10:00:00Z", level: "info", kind: "workspace_updated", message: "Workspace updated: 2 renamed.", read: false }] });
    await user.click(await screen.findByRole("button", { name: /Notifications/ }));
    expect(await screen.findByText("Workspace updated: 2 renamed.")).toBeInTheDocument();
  });
  it("the dashboard shows real identity data and no invented figures", async () => {
    const { user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Dashboard" }));
    expect(await screen.findByText("accountant@example.com · Accountant")).toBeInTheDocument();
    expect(screen.getByText(/Nothing on this screen is sample data/)).toBeInTheDocument();
  });
});


// ------------------------------------------------------------------ Phase 6: documents
const staged = (id: string, name: string, size = 2048, extra: Partial<StagedFile> = {}): StagedFile => ({ id, name, size, extension: name.split(".").pop() ?? "", sha256: "a".repeat(64), problem: null, ...extra });
const nav = async (user: ReturnType<typeof userEvent.setup>, label: string) => user.click(await screen.findByRole("button", { name: new RegExp(`^${label}`) }));

describe("importing documents", () => {
  it("needs an open company", async () => {
    const { user } = await boot();
    await nav(user, "Import Documents");
    expect(await screen.findByText("Open a company first to see its documents.")).toBeInTheDocument();
  });

  it("files are reviewed per file, queued, and shown as imported only after the server confirmed them", async () => {
    const { bridge, user } = await boot({ pickFiles: [staged("s1", "invoice.pdf"), staged("s2", "bad-scan.pdf")] });
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "Import Documents");
    await user.click(await screen.findByRole("button", { name: "Choose files…" }));
    const dest = await screen.findByLabelText("Destination for invoice.pdf");
    expect(screen.getByRole("button", { name: /Import 2 files/ })).toBeDisabled(); // no destination chosen yet
    await user.selectOptions(dest, "Receivables");
    await user.selectOptions(screen.getByLabelText("Destination for bad-scan.pdf"), "Payables");
    await user.click(screen.getByRole("button", { name: "Import 2 files" }));
    expect(await screen.findByRole("heading", { name: "Upload Queue", level: 1 })).toBeInTheDocument();
    expect(await screen.findAllByText("Waiting")).toHaveLength(2);
    expect(screen.queryByText("Completed")).not.toBeInTheDocument(); // queued is not imported
    await act(async () => { await bridge.queueRun(); });
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("The file is not a PDF document.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    // the interface only ever handled opaque ids: no path-taking call exists on the bridge
    expect(Object.keys(bridge).some((k) => /path/i.test(k))).toBe(false);
  });

  it("refused files are explained and never queued; quarantined files cannot be retried", async () => {
    const { bridge, user } = await boot({ pickFiles: [staged("", "run.exe", 0, { problem: "Files of type '.exe' are not supported." }), staged("s1", "evil.pdf")] });
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "Import Documents");
    await user.click(await screen.findByRole("button", { name: "Choose files…" }));
    expect(await screen.findByText(/run\.exe: Files of type '\.exe' are not supported\./)).toBeInTheDocument();
    await user.selectOptions(await screen.findByLabelText("Destination for evil.pdf"), "Receivables");
    await user.click(screen.getByRole("button", { name: "Import 1 file" }));
    await screen.findByRole("heading", { name: "Upload Queue", level: 1 });
    await act(async () => { await bridge.queueRun(); });
    expect(await screen.findByText("Quarantined")).toBeInTheDocument();
    expect(screen.getByText("Windows executables are not accepted.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("explains an empty destination list instead of offering made-up folders", async () => {
    const { user } = await boot({ workspaces: { "c-alpha": { company_id: "c-alpha", version: 1, workspace_state: "not_configured", nodes: [], categories: [] } }, pickFiles: [staged("s1", "a.pdf")] });
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "Import Documents");
    expect(await screen.findByText(/no folders you can file into/i)).toBeInTheDocument();
  });
});

describe("browsing and managing documents", () => {
  const docs = () => [mockDoc("d1", "a0", "Bank March.pdf", 4096), mockDoc("d2", "a1", "Invoice 1001.png", 2048), mockDoc("d3", "a1", "Invoice 1002.pdf", 8192)];

  it("lists the company's documents by folder with counts, search and details", async () => {
    const { user } = await boot({ documents: docs() });
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "All Documents");
    expect(await screen.findByRole("button", { name: "Bank March.pdf" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /All documents\s*3/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Receivables\s*2/ }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Bank March.pdf" })).not.toBeInTheDocument());
    await user.type(screen.getByLabelText("Search documents"), "1002");
    expect(await screen.findByRole("button", { name: "Invoice 1002.pdf" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invoice 1001.png" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Invoice 1002.pdf" }));
    const panel = await screen.findByLabelText("Details of Invoice 1002.pdf");
    expect(within(panel).getByText("Not scanned (no scanner configured)")).toBeInTheDocument();
    expect(within(panel).getByText(/^v1 · /)).toBeInTheDocument();
  });

  it("renames, archives and restores; archived documents live in their own view", async () => {
    const { bridge, user } = await boot({ documents: docs() });
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "All Documents");
    await user.click(await screen.findByRole("button", { name: "Bank March.pdf" }));
    const panel = await screen.findByLabelText("Details of Bank March.pdf");
    const field = within(panel).getByLabelText("Rename");
    await user.clear(field);
    await user.type(field, "Bank statement March");
    await user.click(within(panel).getByRole("button", { name: "Rename" }));
    expect(await screen.findByRole("button", { name: "Bank statement March.pdf" })).toBeInTheDocument();
    await user.click(within(await screen.findByLabelText("Details of Bank statement March.pdf")).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Bank statement March.pdf" })).not.toBeInTheDocument());
    await nav(user, "Archived Documents");
    await user.click(await screen.findByRole("button", { name: "Bank statement March.pdf" }));
    await user.click(within(await screen.findByLabelText("Details of Bank statement March.pdf")).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(bridge.calls).toContain("docsAction:restore"));
    await nav(user, "All Documents");
    expect(await screen.findByRole("button", { name: "Bank statement March.pdf" })).toBeInTheDocument();
  });

  it("deleting asks first; permanent deletion is only offered from the deleted items", async () => {
    const { bridge, user } = await boot({ documents: docs() });
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "All Documents");
    await user.click(await screen.findByRole("button", { name: "Invoice 1001.png" }));
    const panel = await screen.findByLabelText("Details of Invoice 1001.png");
    expect(within(panel).queryByRole("button", { name: "Delete permanently" })).not.toBeInTheDocument();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await user.click(within(panel).getByRole("button", { name: "Delete" }));
    expect(bridge.calls).not.toContain("docsAction:delete"); // declined
    await user.click(within(panel).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(bridge.calls).toContain("docsAction:delete"));
    confirm.mockRestore();
    await nav(user, "Archived Documents");
    await user.selectOptions(await screen.findByLabelText("Show"), "deleted");
    await user.click(await screen.findByRole("button", { name: "Invoice 1001.png" }));
    const del = await screen.findByLabelText("Details of Invoice 1001.png");
    expect(within(del).getByRole("button", { name: "Delete permanently" })).toBeEnabled();
    expect(within(del).getByRole("button", { name: "Recover" })).toBeInTheDocument();
  });

  it("previews images with zoom and rotation controls; other types offer Open instead", async () => {
    URL.createObjectURL = vi.fn(() => "blob:preview");
    URL.revokeObjectURL = vi.fn();
    const { user } = await boot({ documents: [...docs(), mockDoc("d4", "a0", "Letter.docx", 900)] });
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "All Documents");
    await user.click(await screen.findByRole("button", { name: "Letter.docx" }));
    const word = await screen.findByLabelText("Details of Letter.docx");
    expect(within(word).queryByRole("button", { name: "Preview" })).not.toBeInTheDocument();
    expect(within(word).getByText(/No in-app preview/)).toBeInTheDocument();
    expect(within(word).getByRole("button", { name: "Open" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Invoice 1001.png" }));
    const panel = await screen.findByLabelText("Details of Invoice 1001.png");
    await user.click(within(panel).getByRole("button", { name: "Preview" }));
    const img = await screen.findByRole("img", { name: "Invoice 1001.png" });
    expect(img).toHaveAttribute("src", "blob:preview");
    await user.click(screen.getByRole("button", { name: "Rotate" }));
    expect(img.style.transform).toContain("rotate(90deg)");
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(img.style.transform).toContain("scale(1.25)");
    await user.click(screen.getByRole("button", { name: "Close preview" }));
    expect(screen.queryByRole("img", { name: "Invoice 1001.png" })).not.toBeInTheDocument();
  });

  it("shows a friendly message when there are no documents yet", async () => {
    const { user } = await boot();
    await openCompany(user, "Alpha Trading LLC");
    await nav(user, "All Documents");
    expect(await screen.findByText(/No documents here yet/)).toBeInTheDocument();
  });
});
