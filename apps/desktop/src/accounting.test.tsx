import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { createMockBridge, mockAcctTxn, mockDoc, mockExtraction, type MockBridge, type MockOptions } from "./bridge/mock";
import type { AcctAccount, AcctContact, AcctTaxCode } from "./bridge/types";

const accounts: AcctAccount[] = [
  { id: "acc-6100", code: "6100", name: "General expenses", account_type: "expense", normal_balance: "debit", is_active: true, is_postable: true },
  { id: "acc-1300", code: "1300", name: "Input tax", account_type: "tax", normal_balance: "debit", is_active: true, is_postable: true },
  { id: "acc-2100", code: "2100", name: "Accounts payable", account_type: "payable", normal_balance: "credit", is_active: true, is_postable: true },
  { id: "acc-1120", code: "1120", name: "Main bank", account_type: "bank", normal_balance: "debit", is_active: true, is_postable: true },
];
const taxCodes: AcctTaxCode[] = [{ id: "tx-std", code: "OM_STD", name: "Standard", treatment: "standard", direction: "both", is_active: true, verification_status: "unverified", rates: [{ rate: "5", effective_from: "2021-04-16", effective_to: null }] }];
const contacts: AcctContact[] = [{ id: "ct-1", kind: "supplier", legal_name: "Gulf Office Supplies LLC", display_name: "Gulf Office Supplies", tax_number: "OM123", is_active: true }];
const base: MockOptions = { acctAccounts: accounts, acctTaxCodes: taxCodes, acctContacts: contacts };

async function boot(opts: MockOptions = {}): Promise<{ bridge: MockBridge; user: ReturnType<typeof userEvent.setup> }> {
  const bridge = createMockBridge({ restore: true, ...base, ...opts });
  const user = userEvent.setup();
  render(<App bridge={bridge} />);
  await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
  await screen.findByRole("heading", { name: "Workspace", level: 1 });
  await user.click(await screen.findByRole("button", { name: /^Accounting/ }));
  return { bridge, user };
}
async function openFirst(user: ReturnType<typeof userEvent.setup>, tab?: string) {
  if (tab) await user.click(await screen.findByRole("tab", { name: new RegExp(tab) }));
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await screen.findByRole("heading", { name: /^TX-/ });
}

describe("accounting (Phase 8)", () => {
  it("shows an honest empty state and never invents transactions", async () => {
    await boot();
    expect(await screen.findByText(/No transactions here/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is approved until an authorised person approves it/i)).toBeInTheDocument();
  });

  it("lists transactions with a text status and the number of blocking items", async () => {
    const blocked = mockAcctTxn("tx-2", { issues: [{ code: "missing_account_mapping", severity: "error", blocking: true, message: "Line 1 has no account.", data: {} }], blocking_count: 1, balanced: false });
    await boot({ acctTransactions: [mockAcctTxn("tx-1"), blocked] });
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1] as HTMLElement).getByText("Awaiting approval")).toBeInTheDocument();
    expect(screen.getByText("1 blocking")).toBeInTheDocument();
  });

  it("shows the source, the proposed journal with totals and a text balance indicator, then approves under the person's action", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { bridge, user } = await boot({ acctTransactions: [mockAcctTxn("tx-1")] });
    await openFirst(user);
    expect(screen.getByText("Journal balanced")).toBeInTheDocument();
    expect(screen.getByText("No blocking items")).toBeInTheDocument();
    const journal = screen.getByRole("table", { name: "Proposed journal lines" });
    expect(within(journal).getByText(/6100 · General expenses/)).toBeInTheDocument();
    expect(within(journal).getByText(/Total — balanced/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved.")).toBeInTheDocument();
    expect(bridge.calls).toContain("acct:txn_approve");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText(/It can no longer be edited/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Approval history" })).toHaveTextContent("Approved");
  });

  it("does not offer approval to the preparer and lets the server's refusal reach the screen", async () => {
    const own = mockAcctTxn("tx-1", { can: { edit: false, submit: false, review: false, approve: false, reject: false, request_correction: false, revise: false, reverse: false } });
    const { user } = await boot({ acctTransactions: [own] });
    await openFirst(user);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText(/You cannot approve this transaction/)).toBeInTheDocument();
  });

  it("blocks approval while a blocking item exists and shows the server's message", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const t = mockAcctTxn("tx-1", { issues: [{ code: "period_closed", severity: "error", blocking: true, message: "The accounting period 2025-03 is closed.", data: {} }], blocking_count: 1 });
    const { user } = await boot({ acctTransactions: [t] });
    await openFirst(user);
    expect(screen.getByText("1 item block approval")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Validation results" })).getByText("Blocks approval")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect((await screen.findAllByText(/This transaction cannot be approved: The accounting period 2025-03 is closed\./)).length).toBeGreaterThan(0);
  });

  it("makes an unbalanced journal visible, lets the accountant fix it, and only then allows submission", async () => {
    const draft = mockAcctTxn("tx-1", {
      status: "draft", balanced: false, debit_total: "105.000000", credit_total: "100.000000",
      issues: [{ code: "journal_unbalanced", severity: "error", blocking: true, message: "Debits and credits differ by 5.000000.", data: {} }], blocking_count: 1,
      can: { edit: true, submit: false, review: false, approve: false, reject: false, request_correction: false, revise: false, reverse: false },
    });
    const { bridge, user } = await boot({ acctTransactions: [draft] });
    await openFirst(user, "Drafts");
    expect(screen.getByText("Journal NOT balanced")).toBeInTheDocument();
    expect(screen.getByText(/Total — NOT balanced/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit for approval" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit journal lines" }));
    const credit3 = screen.getByLabelText("Journal credit 3");
    await user.clear(credit3);
    await user.type(credit3, "105.000000");
    await user.click(screen.getByRole("button", { name: "Save journal" }));
    expect(await screen.findByText("Journal balanced")).toBeInTheDocument();
    expect(bridge.calls).toContain("acct:txn_journal_lines");
    await user.click(await screen.findByRole("button", { name: "Submit for approval" }));
    expect(await screen.findByText("Submitted for approval.")).toBeInTheDocument();
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument();
  });

  it("requires an acknowledgement and a written reason before approving documented differences", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const t = mockAcctTxn("tx-1", { issues: [{ code: "tax_mismatch", severity: "error", blocking: false, message: "Line 1: the tax on the document (5.500) differs from the calculation (5.000).", data: {} }] });
    const { user } = await boot({ acctTransactions: [t] });
    await openFirst(user);
    expect(within(screen.getByRole("list", { name: "Validation results" })).getByText("Needs your acknowledgement")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    await user.click(screen.getByLabelText(/I have reviewed the differences/));
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled(); // a reason is still needed
    await user.type(screen.getByLabelText("Note or reason"), "Supplier rounds VAT up; checked the paper invoice");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved.")).toBeInTheDocument();
  });

  it("rejects with a reason, reopens for correction and keeps the history", async () => {
    const { user } = await boot({ acctTransactions: [mockAcctTxn("tx-1")] });
    await openFirst(user);
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    await user.type(screen.getByLabelText("Note or reason"), "Wrong supplier");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Rejected.")).toBeInTheDocument();
    expect(screen.getByText(/Rejected: Wrong supplier/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reopen for correction" }));
    expect(await screen.findByText("Reopened for correction.")).toBeInTheDocument();
    const history = screen.getByRole("list", { name: "Approval history" });
    expect(history).toHaveTextContent("Rejected");
    expect(history).toHaveTextContent("Reopened for correction");
  });

  it("reverses an approved transaction only with a date and a reason", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const ap = mockAcctTxn("tx-1", { status: "approved", can: { edit: false, submit: false, review: false, approve: false, reject: false, request_correction: false, revise: false, reverse: true } });
    const { user } = await boot({ acctTransactions: [ap] });
    await openFirst(user, "Approved");
    expect(screen.getByRole("button", { name: "Reverse" })).toBeDisabled();
    await user.type(screen.getByLabelText("Reversal date"), "2025-03-20");
    await user.type(screen.getByLabelText("Note or reason"), "Entered twice");
    await user.click(screen.getByRole("button", { name: "Reverse" }));
    expect(await screen.findByText(/Reversed\. The original and the reversal both stay in the history\./)).toBeInTheDocument();
  });

  it("suggests existing customers or suppliers but links one only when the accountant confirms", async () => {
    const draft = mockAcctTxn("tx-1", {
      status: "draft", contact_id: null, contact: null, can: { edit: true, submit: false, review: false, approve: false, reject: false, request_correction: false, revise: false, reverse: false },
      contact_suggestions: [{ contact_id: "ct-1", strength: "name_similar", score: 0.9, display_name: "Gulf Office Supplies", kind: "supplier" }],
      issues: [{ code: "contact_required", severity: "error", blocking: true, message: "Choose the supplier before approval.", data: {} }], blocking_count: 1,
    });
    const { user } = await boot({ acctTransactions: [draft] });
    await openFirst(user, "Drafts");
    expect(screen.getByText(/Not chosen yet\. Nothing is linked automatically\./)).toBeInTheDocument();
    expect(screen.getByText(/Similar name — check carefully/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use Gulf Office Supplies" }));
    await waitFor(() => expect(screen.getByText("Gulf Office Supplies", { selector: "strong" })).toBeInTheDocument());
    expect(screen.queryByText(/Choose the supplier before approval/)).not.toBeInTheDocument();
  });

  it("shows the evidence behind a duplicate warning and never removes anything", async () => {
    const dup = mockAcctTxn("tx-1", {
      status: "draft", duplicate_status: "exact", can: { edit: true, submit: false, review: false, approve: false, reject: false, request_correction: false, revise: false, reverse: false },
      duplicates: [{ id: "m1", level: "exact", signals: { same_document_number: true, same_amount_and_currency: true, days_apart: 0 }, decision: null, note: null, matched_id: "tx-0", matched_number: "TX-000009", matched_doc_number: "INV-77", matched_status: "approved", matched_gross: "105.000000", matched_date: "2025-03-14" }],
    });
    const { user } = await boot({ acctTransactions: [dup] });
    await openFirst(user, "Drafts");
    expect(screen.getByText(/Looks identical to TX-000009/)).toBeInTheDocument();
    expect(screen.getByText(/Evidence: same document number, same amount and currency/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not a duplicate" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "It is a duplicate" })).toBeInTheDocument();
  });

  it("offers to prepare an accounting transaction only from an approved extraction", async () => {
    const approved = mockExtraction("e1", "d1", { status: "approved", unresolved: { fields: 0, line_items: 0, transactions: 0 } });
    const bridge = createMockBridge({ restore: true, ...base, documents: [mockDoc("d1", "a0", "Invoice.pdf")], extractions: [approved] });
    const user = userEvent.setup();
    render(<App bridge={bridge} />);
    await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
    await screen.findByRole("heading", { name: "Workspace", level: 1 });
    await user.click(await screen.findByRole("button", { name: /^Processing Queue/ }));
    // an approved extraction is no longer in the review queue; open it from the document instead
    await user.click(await screen.findByRole("button", { name: /^All Documents/ }));
    await user.click(await screen.findByRole("button", { name: "Invoice.pdf" }));
    await user.click(await screen.findByRole("button", { name: /Read with OCR/ }));
    await user.click(await screen.findByRole("button", { name: "Prepare accounting transaction" }));
    expect(await screen.findByRole("heading", { name: /^TX-/ })).toBeInTheDocument();
    expect(bridge.calls).toContain("acct:from_extraction");
  });

  it("refuses to prepare a transaction from an unapproved extraction (server rule)", async () => {
    const bridge = createMockBridge({ restore: true, ...base, extractions: [mockExtraction("e1", "d1")] });
    await bridge.authLogin("a@b.c", "correct");
    await expect(bridge.acct("o", "c", "from_extraction", { ids: ["e1"] })).rejects.toMatchObject({ code: "conflict" });
  });
});
