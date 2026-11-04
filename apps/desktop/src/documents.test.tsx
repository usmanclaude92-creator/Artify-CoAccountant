import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it } from "vitest";
import { App } from "./App";
import { createMockBridge, node, type MockBridge, type MockOptions } from "./bridge/mock";
import type { DocumentRow, WorkspaceData, WsField } from "./bridge/types";

const CO = "c-alpha";
const nodes = [
  node("n-in", null, "Invoices", { can: { view: true, upload: true } }),
  node("n-ro", null, "Contracts (read only)", { can: { view: true, upload: false } }),
  node("n-sub", "n-in", "Q1", { depth: 2, can: { view: true, upload: true } }),
];
const field = (extra: Partial<WsField>): WsField => ({ id: "f1", key: "invoice_no", label: "Invoice number", data_type: "short_text", required: false, is_active: true, archived_at: null, options: [], category_ids: [], node_ids: [], ...extra });
const ws = (fields: WsField[] = []): Record<string, WorkspaceData> => ({ [CO]: { company_id: CO, version: 3, workspace_state: "configured", nodes, categories: [{ id: "cat1", name: "Sales invoice", document_kind: "sales_invoice", node_id: "n-in", is_active: true, archived_at: null }], fields } });
const doc = (extra: Partial<DocumentRow> = {}): DocumentRow => ({ id: "d1", node_id: "n-in", category_id: null, display_name: "March invoice", original_filename: "march.pdf", extension: "pdf", status: "active", current_version_number: 1, source: "desktop", legal_hold: false, created_by_name: "Test Accountant", created_at: "2026-03-01T10:00:00Z", updated_at: "2026-03-01T10:00:00Z", archived_at: null, row_version: 1, mime_type: "application/pdf", size_bytes: 2048, sha256: "march.pdf:2048", scan_status: "clean", can: { view: true, download: true, edit: true, move: true, archive: true, delete: false, purge: false }, ...extra });

async function boot(opts: MockOptions = {}): Promise<{ bridge: MockBridge; user: ReturnType<typeof userEvent.setup> }> {
  const bridge = createMockBridge({ restore: true, workspaces: ws(), pick: [{ token: "t", name: "inv-001.pdf", size: 4096 }], ...opts });
  const user = userEvent.setup();
  render(<App bridge={bridge} />);
  await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
  await screen.findByRole("heading", { name: "Workspace", level: 1 });
  return { bridge, user };
}
const go = async (user: ReturnType<typeof userEvent.setup>, label: string) => user.click(await screen.findByRole("button", { name: new RegExp(`^${label}`) }));
async function chooseAndQueue(user: ReturnType<typeof userEvent.setup>, folder = "Invoices") {
  await go(user, "Import Documents");
  await user.click(await screen.findByRole("button", { name: "Choose files…" }));
  await screen.findByRole("heading", { name: "inv-001.pdf" });
  await user.selectOptions(screen.getByLabelText("Destination folder"), folder);
  await user.click(screen.getByRole("button", { name: /to the upload queue/ }));
}

beforeAll(() => {
  URL.createObjectURL = () => "blob:mock";
  URL.revokeObjectURL = () => undefined;
});

describe("import documents", () => {
  it("offers only folders the server says the user may upload to, and never shows a path", async () => {
    const { user } = await boot();
    await go(user, "Import Documents");
    await user.click(await screen.findByRole("button", { name: "Choose files…" }));
    const select = await screen.findByLabelText("Destination folder");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options.some((o) => o?.includes("Invoices"))).toBe(true);
    expect(options.some((o) => o?.includes("Q1"))).toBe(true);
    expect(options.some((o) => o?.includes("read only"))).toBe(false);
    expect(document.body.textContent).not.toMatch(/[A-Z]:\\/);
  });
  it("needs a destination before anything is queued, then queues and completes only after the cloud confirms", async () => {
    const { bridge, user } = await boot();
    await go(user, "Import Documents");
    await user.click(await screen.findByRole("button", { name: "Choose files…" }));
    await screen.findByRole("heading", { name: "inv-001.pdf" });
    expect(screen.getByRole("button", { name: /to the upload queue/ })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Destination folder"), "Invoices");
    await user.click(screen.getByRole("button", { name: /to the upload queue/ }));
    expect(await screen.findByText(/1 file added to the upload queue/)).toBeInTheDocument();
    expect(bridge.state.imports[0]?.status === "waiting" || bridge.state.imports[0]?.status === "completed").toBe(true);
    await user.click(screen.getByRole("button", { name: "Open the upload queue" }));
    await user.click(await screen.findByRole("checkbox", { name: "Show finished" }));
    expect(await screen.findByText("Stored in the cloud")).toBeInTheDocument();
    expect(bridge.state.documents).toHaveLength(1);
  });
  it("requires required details and keeps money as typed text", async () => {
    const { bridge, user } = await boot({ workspaces: ws([field({ required: true }), field({ id: "f2", key: "total", label: "Total", data_type: "decimal" })]) });
    await go(user, "Import Documents");
    await user.click(await screen.findByRole("button", { name: "Choose files…" }));
    await screen.findByRole("heading", { name: "inv-001.pdf" });
    await user.selectOptions(screen.getByLabelText("Destination folder"), "Invoices");
    await user.type(screen.getByLabelText("Total"), "1250.500");
    await user.click(screen.getByRole("button", { name: /to the upload queue/ }));
    expect(await screen.findByText(/please fill in Invoice number/)).toBeInTheDocument();
    expect(bridge.state.imports).toHaveLength(0);
  });
  it("tells the user which files were refused and why", async () => {
    const { user } = await boot({ pick: [{ token: "t", name: "setup.exe", size: 10 }] });
    await go(user, "Import Documents");
    await user.click(await screen.findByRole("button", { name: "Choose files…" }));
    await screen.findByRole("heading", { name: "setup.exe" });
    await user.selectOptions(screen.getByLabelText("Destination folder"), "Invoices");
    await user.click(screen.getByRole("button", { name: /to the upload queue/ }));
    expect(await screen.findByText(/This file type is not allowed/)).toBeInTheDocument();
  });
  it("a file dropped on the window lands on the import screen", async () => {
    const { bridge } = await boot();
    act(() => bridge.emit("files-dropped", [{ token: "drop-1", name: "scan.png", size: 99 }]));
    expect(await screen.findByRole("heading", { name: "scan.png" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Import Documents", level: 1 })).toBeInTheDocument();
  });
});

describe("upload queue", () => {
  it("keeps a file when the cloud cannot be reached and says so", async () => {
    const { bridge, user } = await boot();
    bridge.state.opts.offline = true; // the connection drops after the company was opened
    await chooseAndQueue(user);
    await user.click(await screen.findByRole("button", { name: "Open the upload queue" }));
    await user.click(await screen.findByRole("button", { name: "Process now" }));
    expect(await screen.findByText(/kept and will continue when the connection returns/)).toBeInTheDocument();
    expect(bridge.state.documents).toHaveLength(0);
    expect(bridge.state.imports[0]?.status).toBe("waiting");
  });
  it("asks what to do with a duplicate and finishes after the choice", async () => {
    const { bridge, user } = await boot({ documents: [doc({ display_name: "Existing", sha256: "inv-001.pdf:4096" })] });
    await chooseAndQueue(user);
    await user.click(await screen.findByRole("button", { name: "Open the upload queue" }));
    await user.click(await screen.findByRole("button", { name: "Process now" }));
    expect(await screen.findByText(/identical content already exists/)).toBeInTheDocument();
    expect(bridge.state.documents).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Add it anyway" }));
    await user.click(await screen.findByRole("button", { name: "Process now" }));
    await user.click(await screen.findByRole("checkbox", { name: "Show finished" }));
    await waitFor(() => expect(bridge.state.documents).toHaveLength(2));
  });
  it("explains a reached limit without suggesting anything was deleted", async () => {
    const { user } = await boot({ maxDocuments: 0 });
    await chooseAndQueue(user);
    await user.click(await screen.findByRole("button", { name: "Open the upload queue" }));
    await user.click(await screen.findByRole("button", { name: "Process now" }));
    expect(await screen.findByText(/nothing was deleted/)).toBeInTheDocument();
  });
  it("lets the user cancel and remove an entry", async () => {
    const { bridge, user } = await boot();
    bridge.state.opts.offline = true;
    await chooseAndQueue(user);
    await user.click(await screen.findByRole("button", { name: "Open the upload queue" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(bridge.state.imports[0]?.status).toBe("cancelled");
    await user.click(await screen.findByRole("checkbox", { name: "Show finished" }));
    await user.click(await screen.findByRole("button", { name: "Remove from list" }));
    expect(bridge.state.imports).toHaveLength(0);
  });
});

describe("document list and viewer", () => {
  it("lists the documents, shows only the actions the server allows and previews an image", async () => {
    const png = doc({ id: "d2", display_name: "Receipt photo", extension: "png", mime_type: "image/png", can: { view: true, download: true, edit: false, move: false, archive: false, delete: false, purge: false } });
    const { bridge, user } = await boot({ documents: [png] });
    await go(user, "All Documents");
    await user.click(await screen.findByRole("button", { name: "Receipt photo" }));
    expect(await screen.findByRole("img", { name: "Receipt photo" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rename" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
    expect(bridge.calls).toContain("documentPreview");
  });
  it("opens documents the app cannot preview with the default application", async () => {
    const xlsx = doc({ id: "d3", display_name: "Ledger", extension: "xlsx", mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    const { bridge, user } = await boot({ documents: [xlsx] });
    await go(user, "All Documents");
    await user.click(await screen.findByRole("button", { name: "Ledger" }));
    expect(await screen.findByText(/cannot be previewed inside the app/)).toBeInTheDocument();
    expect(bridge.calls).not.toContain("documentPreview");
    await user.click(screen.getAllByRole("button", { name: "Open in default app" })[0]!);
    await waitFor(() => expect(bridge.state.opened).toEqual(["d3"]));
  });
  it("archives a document and shows it under Archived Documents", async () => {
    const { bridge, user } = await boot({ documents: [doc()] });
    await go(user, "All Documents");
    await user.click(await screen.findByRole("button", { name: "March invoice" }));
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => expect(bridge.state.documents[0]?.status).toBe("archived"));
    await go(user, "Archived Documents");
    expect(await screen.findByRole("button", { name: "March invoice" })).toBeInTheDocument();
  });
  it("says so when there is nothing to show instead of showing sample data", async () => {
    const { user } = await boot();
    await go(user, "All Documents");
    expect(await screen.findByText(/no documents here yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("local copies", () => {
  it("saves the policy and keeps the buttons honest when no storage location is chosen", async () => {
    const { bridge, user } = await boot();
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await user.click(await screen.findByRole("tab", { name: "Storage" }));
    await user.selectOptions(await screen.findByLabelText("Keep local copies of"), "recent");
    await waitFor(() => expect(bridge.state.settings.mirror_policy).toBe("recent"));
    expect(screen.getByRole("button", { name: "Match folders to the cloud" })).toBeDisabled();
    expect(screen.getByText(/Choose a storage location first/)).toBeInTheDocument();
  });
});
