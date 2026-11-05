import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { createMockBridge, mockDoc, mockExtraction, type MockBridge, type MockOptions } from "./bridge/mock";

async function boot(opts: MockOptions = {}): Promise<{ bridge: MockBridge; user: ReturnType<typeof userEvent.setup> }> {
  const bridge = createMockBridge({ restore: true, ...opts });
  const user = userEvent.setup();
  render(<App bridge={bridge} />);
  return { bridge, user };
}
const nav = async (user: ReturnType<typeof userEvent.setup>, label: string) => user.click(await screen.findByRole("button", { name: new RegExp(`^${label}`) }));
async function openReview(opts: MockOptions) {
  const ctx = await boot(opts);
  await ctx.user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
  await screen.findByRole("heading", { name: "Workspace", level: 1 });
  await nav(ctx.user, "Processing Queue");
  await ctx.user.click(await screen.findByRole("button", { name: "Review" }));
  await screen.findByRole("heading", { name: "Review extracted data" });
  return ctx;
}
const docs = () => [mockDoc("d1", "a0", "Invoice 1001.pdf", 4096)];

describe("processing queue and review (Phase 7)", () => {
  it("shows an honest empty queue", async () => {
    const { user } = await boot({ documents: docs() });
    await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
    await nav(user, "Processing Queue");
    expect(await screen.findByText(/No documents are waiting for review/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing is approved until an accountant reviews/)).toBeInTheDocument();
  });

  it("labels values as suggestions, blocks approval until every flagged item is resolved, then approves under the person's action", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { bridge, user } = await openReview({ documents: docs(), extractions: [mockExtraction("e1", "d1")] });
    expect(screen.getAllByText(/Suggested by the system/).length).toBeGreaterThan(0);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/1 item still needs review/)).toBeInTheDocument();
    // correct the flagged total: the original reading stays visible
    const total = screen.getByLabelText(/^Total/);
    await user.clear(total);
    await user.type(total, "115.500");
    await user.click(screen.getByRole("button", { name: "Save Total" }));
    expect(await screen.findByText("Corrected by you · 55%")).toBeInTheDocument();
    expect(screen.getByText("Originally read as: 115.000")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved.")).toBeInTheDocument();
    expect(bridge.calls).toContain("intelDecide:approve");
    expect(bridge.state.extractions[0]?.status).toBe("approved");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("lets the person mark a field unreadable or missing and reject the whole extraction", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { bridge, user } = await openReview({ documents: docs(), extractions: [mockExtraction("e1", "d1")] });
    await user.click(screen.getByRole("button", { name: "Mark Total unreadable" }));
    expect(await screen.findByText("Marked unreadable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Mark Invoice date missing" }));
    expect(await screen.findByText("Marked missing")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Rejected.")).toBeInTheDocument();
    expect(bridge.state.extractions[0]?.status).toBe("rejected");
  });

  it("requires acknowledging validation errors before approving", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const ex = mockExtraction("e1", "d1", { unresolved: { fields: 0, line_items: 0, transactions: 0 }, validation: { ok: false, issues: [{ code: "totals_mismatch", severity: "error", message: "Subtotal plus tax does not equal the total.", difference: "0.500" }] } });
    ex.fields.forEach((f) => { f.review_status = "confirmed"; });
    const { user } = await openReview({ documents: docs(), extractions: [ex] });
    expect(screen.getByText(/Subtotal plus tax does not equal the total/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    await user.click(screen.getByLabelText(/I have reviewed the validation errors/));
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("reviews bank statement rows, adds a missing row by hand and never adjusts amounts itself", async () => {
    const tx = (n: number, debit: string | null, credit: string | null, balance: string) => ({ id: `t${n}`, row_no: n, page_no: 1, bbox: [0.1, 0.1 * n, 0.9, 0.1 * n + 0.04] as [number, number, number, number], source_text: `row ${n}`, tx_date: `2026-03-0${n}`, value_date: null, description: `Payment ${n}`, reference: null, debit, credit, balance, currency: "OMR", tx_type: null, confidence: 0.9, review_status: "needs_review" as const, manual: false, flags: null });
    const ex = mockExtraction("e2", "d1", {
      type_key: "bank_statement", schema_key: "bank_statement", fields: [], unresolved: { fields: 0, line_items: 0, transactions: 2 },
      statement: { id: "s1", bank_name: "Test Bank", account_holder: null, account_name: null, account_number_masked: "****1234", iban_masked: null, currency: "OMR", period_start: "2026-03-01", period_end: "2026-03-31", opening_balance: "100.000", closing_balance: "160.000", validation: null },
      transactions: [tx(1, "40.000", null, "60.000"), tx(2, null, "100.000", "160.000")],
      validation: { ok: false, issues: [{ code: "balance_break", severity: "warning", message: "Row 2: the balance does not follow from the row above.", row_no: 2, difference: "5.000" }] },
    });
    const { bridge, user } = await openReview({ documents: docs(), extractions: [ex] });
    expect(screen.getByText("****1234")).toBeInTheDocument();
    expect(screen.getByText(/the balance does not follow from the row above/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm row 1" }));
    await waitFor(() => expect(bridge.state.extractions[0]?.transactions?.[0]?.review_status).toBe("confirmed"));
    await user.click(screen.getByRole("button", { name: "Edit row 2" }));
    const credit = within(screen.getByRole("table", { name: "Transactions" })).getByLabelText("Credit");
    await user.clear(credit);
    await user.type(credit, "95.000");
    await user.click(screen.getByRole("button", { name: "Save row" }));
    await waitFor(() => expect(bridge.state.extractions[0]?.transactions?.[1]?.credit).toBe("95.000"));
    expect(bridge.state.extractions[0]?.transactions?.[1]?.balance).toBe("160.000"); // the balance column is untouched
    const form = screen.getByRole("form", { name: "Add a missing row" });
    await user.type(within(form).getByLabelText("Description"), "Bank fee");
    await user.type(within(form).getByLabelText("Debit"), "2.000");
    await user.click(within(form).getByRole("button", { name: "Add row" }));
    expect(await screen.findByText(/Bank fee \(added by hand\)/)).toBeInTheDocument();
  });

  it("offers filing suggestions only as an explicit action and keeps earlier results when re-running", async () => {
    const ex = mockExtraction("e1", "d1", {
      workspace_suggestions: [{ id: "w1", rank: 1, node_id: "a1", node_label: "Receivables", category_id: null, category_name: null, confidence: 0.8, method: "rules", evidence: null, status: "pending", you_can_file_here: true }],
    });
    const { bridge, user } = await openReview({ documents: docs(), extractions: [ex] });
    expect(screen.getByText("Suggested location")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Move here" }));
    await waitFor(() => expect(bridge.state.extractions[0]?.workspace_suggestions[0]?.status).toBe("accepted"));
    await user.click(screen.getByRole("button", { name: "Re-run extraction" }));
    expect(await screen.findByText(/Earlier results are kept/)).toBeInTheDocument();
    expect(bridge.state.jobs.at(-1)?.kind).toBe("extract");
  });

  it("shows a clear starting point when nothing has been read yet", async () => {
    const { user } = await boot({ documents: docs() });
    await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
    await nav(user, "All Documents");
    await user.click(await screen.findByRole("button", { name: "Invoice 1001.pdf" }));
    await user.click(await screen.findByRole("button", { name: /Read with OCR/ }));
    expect(await screen.findByText("This document has no extracted data yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Read with OCR" }));
    expect(await screen.findByText(/Started: full read/)).toBeInTheDocument();
  });
});
