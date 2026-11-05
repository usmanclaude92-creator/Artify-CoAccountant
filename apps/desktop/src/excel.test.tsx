import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { createMockBridge, mockXlExport, mockXlTemplate, type MockBridge, type MockOptions } from "./bridge/mock";

async function boot(opts: MockOptions = {}): Promise<{ bridge: MockBridge; user: ReturnType<typeof userEvent.setup> }> {
  const bridge = createMockBridge({ restore: true, ...opts });
  const user = userEvent.setup();
  render(<App bridge={bridge} />);
  await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
  await screen.findByRole("heading", { name: "Workspace", level: 1 });
  await user.click(await screen.findByRole("button", { name: /^Excel & Reports/ }));
  await screen.findByRole("heading", { name: "Excel & Reports", level: 1 });
  return { bridge, user };
}
const tpl = mockXlTemplate("t-1");

describe("Excel templates and reports (Phase 9)", () => {
  it("shows an honest empty state and states the limits of the engine", async () => {
    await boot();
    expect(await screen.findByText(/No templates yet/)).toBeInTheDocument();
    expect(screen.getByText(/Macros are never run/)).toBeInTheDocument();
    expect(screen.getByText(/Excel recalculates them when the file is opened/)).toBeInTheDocument();
  });

  it("lists templates with a text status, mapping and compatibility", async () => {
    await boot({ xlTemplates: [tpl, mockXlTemplate("t-2", { name: "Old sales", status: "inactive", active_mapping_no: null, active_mapping_id: null, risk_level: "warned" })] });
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1] as HTMLElement).getByText("Active")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("#1 active")).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByText("Inactive")).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByText("Some features may not survive")).toBeInTheDocument();
  });

  it("uploads through the native file dialog, never a path from the page, and needs a name first", async () => {
    const { bridge, user } = await boot();
    const choose = await screen.findByRole("button", { name: /Choose Excel file/ });
    expect(choose).toBeDisabled();
    await user.type(screen.getByLabelText("Template name"), "VAT register");
    await user.click(choose);
    expect(await screen.findByText(/Template uploaded/)).toBeInTheDocument();
    expect(bridge.calls).toContain("xlUploadPick");
    expect(await screen.findByText("VAT register")).toBeInTheDocument();
  });

  it("a cancelled file dialog uploads nothing", async () => {
    const { user } = await boot({ xlPickCancel: true });
    await user.type(await screen.findByLabelText("Template name"), "X");
    await user.click(screen.getByRole("button", { name: /Choose Excel file/ }));
    expect((await screen.findAllByText(/No file was chosen/)).length).toBeGreaterThan(0);
    expect(screen.queryByText("X")).not.toBeInTheDocument();
  });

  it("builds a mapping from the file's headings, saves a draft and activates it", async () => {
    const { bridge, user } = await boot({ xlTemplates: [mockXlTemplate("t-1", { status: "draft", active_mapping_id: null, active_mapping_no: null })] });
    await user.click(await screen.findByRole("button", { name: "Mapping for Purchase register" }));
    await screen.findByRole("heading", { name: /^Mapping · Purchase register/ });
    await user.click(screen.getByRole("button", { name: "Add worksheet mapping" }));
    expect(screen.getByLabelText("Header row 1")).toHaveValue(4);
    expect(screen.getByLabelText("First data row 1")).toHaveValue(5);
    await user.click(screen.getByRole("button", { name: "Suggest from headers" })); // only exact heading matches are suggested
    expect(screen.queryByLabelText("Column letter 1.1")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add column" }));
    await user.type(screen.getByLabelText("Column letter 1.1"), "h");
    await user.selectOptions(screen.getByLabelText("Field for column H"), "gross_amount");
    await user.click(screen.getByRole("button", { name: /Save draft/ }));
    expect(await screen.findByText(/Draft saved and valid/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Activate mapping" }));
    expect(await screen.findByText(/Mapping activated/)).toBeInTheDocument();
    expect(bridge.calls).toEqual(expect.arrayContaining(["xl:mapping_save", "xl:mapping_activate"]));
  });

  it("blocks column letters that are not Excel letters", async () => {
    const { user } = await boot({ xlTemplates: [tpl] });
    await user.click(await screen.findByRole("button", { name: "Mapping for Purchase register" }));
    await user.click(await screen.findByRole("button", { name: "Add worksheet mapping" }));
    await user.click(screen.getByRole("button", { name: "Add column" }));
    await user.type(screen.getByLabelText("Column letter 1.1"), "1A");
    expect(screen.getByRole("button", { name: /Save draft/ })).toBeDisabled();
    expect(screen.getByText(/1 to 3 letters/)).toBeInTheDocument();
  });

  it("validates and previews before generating, then offers a save dialog for the verified file", async () => {
    const { bridge, user } = await boot({ xlTemplates: [tpl] });
    await user.click(await screen.findByRole("tab", { name: "Generate export" }));
    await user.click(await screen.findByRole("button", { name: /Validate & preview/ }));
    expect(await screen.findByText(/approved data only/)).toBeInTheDocument();
    expect(screen.getByText("TX-000001")).toBeInTheDocument(); // the preview shows what will be written
    await user.click(screen.getByRole("button", { name: "Generate Excel file" }));
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save as…" }));
    expect(await screen.findByText(/Saved to .*checksum was verified/)).toBeInTheDocument();
    expect(bridge.state.xl.saved).toHaveLength(1);
  });

  it("warns about a repeat export and requires a reason before generating again", async () => {
    const { bridge, user } = await boot({ xlTemplates: [tpl], xlExports: [mockXlExport("e-1", "t-1")] });
    await user.click(await screen.findByRole("tab", { name: "Generate export" }));
    await user.click(await screen.findByRole("button", { name: /Validate & preview/ }));
    expect(await screen.findByText(/already exported with this template/)).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Generate Excel file" });
    expect(go).toBeDisabled();
    await user.click(screen.getByLabelText("I want a repeat export"));
    expect(go).toBeDisabled(); // still needs a reason
    await user.type(screen.getByLabelText("Reason for the repeat export"), "Client asked");
    expect(go).toBeEnabled();
    await user.click(go);
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    expect(bridge.state.xl.exports).toHaveLength(2);
  });

  it("marks unapproved data clearly in the checks", async () => {
    const { user } = await boot({ xlTemplates: [tpl] });
    await user.click(await screen.findByRole("tab", { name: "Generate export" }));
    await user.click(await screen.findByLabelText(/Include unapproved transactions/));
    await user.click(screen.getByRole("button", { name: /Validate & preview/ }));
    expect(await screen.findByText(/INCLUDES UNAPPROVED DATA/)).toBeInTheDocument();
  });

  it("shows export history with data basis and verifies a stored file", async () => {
    const { bridge, user } = await boot({ xlTemplates: [tpl], xlExports: [mockXlExport("e-1", "t-1"), mockXlExport("e-2", "t-1", { status: "failed", filename: null, sha256: null, download_available: false, error_message: "The file store was not available." })] });
    await user.click(await screen.findByRole("tab", { name: "Export history" }));
    expect(await screen.findByText("The file store was not available.")).toBeInTheDocument();
    expect(screen.getAllByText("Approved only").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: /^Verify Purchase_register/ }));
    expect(await screen.findByText(/matches its recorded checksum/)).toBeInTheDocument();
    expect(bridge.calls).toContain("xl:export_verify");
    expect(screen.queryByRole("button", { name: /^Save null/ })).not.toBeInTheDocument(); // a failed export has no file
  });

  it("a re-export needs a reason and creates a new record", async () => {
    const { bridge, user } = await boot({ xlTemplates: [tpl], xlExports: [mockXlExport("e-1", "t-1")] });
    await user.click(await screen.findByRole("tab", { name: "Export history" }));
    const btn = await screen.findByRole("button", { name: /^Re-export/ });
    expect(btn).toBeDisabled();
    await user.type(screen.getByLabelText("Reason for re-export"), "lost the file");
    await user.click(btn);
    expect(await screen.findByText("Re-export created as a new record.")).toBeInTheDocument();
    expect(bridge.state.xl.exports).toHaveLength(2);
  });

  it("runs a standard report and says it is not a statutory filing", async () => {
    const { user } = await boot({ xlTemplates: [tpl] });
    await user.click(await screen.findByRole("tab", { name: "Standard reports" }));
    await user.click(await screen.findByRole("button", { name: "Run report" }));
    expect(await screen.findByText(/not a statutory filing/)).toBeInTheDocument();
  });

  it("builds a custom report from governed fields only", async () => {
    const { bridge, user } = await boot({ xlTemplates: [tpl] });
    await user.click(await screen.findByRole("tab", { name: "Custom reports" }));
    expect(await screen.findByText(/There is no free-form query/)).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save report" });
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText("Report name"), "Big purchases");
    await user.click(await screen.findByLabelText("Gross amount"));
    await user.click(save);
    expect(await screen.findByText("Big purchases")).toBeInTheDocument();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "Run Big purchases" }));
    await waitFor(() => expect(bridge.calls).toContain("xl:custom_run"));
  });
});
