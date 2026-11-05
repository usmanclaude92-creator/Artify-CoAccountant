import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { createMockBridge, mockXlTemplate, type MockBridge, type MockOptions } from "./bridge/mock";
import type { BkIssue } from "./bridge/types";

async function boot(opts: MockOptions = {}, tab = "Today"): Promise<{ bridge: MockBridge; user: ReturnType<typeof userEvent.setup> }> {
  const bridge = createMockBridge({ restore: true, ...opts });
  const user = userEvent.setup();
  render(<App bridge={bridge} />);
  await user.click(await screen.findByRole("button", { name: "Open Alpha Trading LLC" }));
  await screen.findByRole("heading", { name: "Workspace", level: 1 });
  await user.click(await screen.findByRole("button", { name: /^Daily Bookkeeping/ }));
  await screen.findByRole("heading", { name: "Daily Bookkeeping", level: 1 });
  if (tab !== "Today") await user.click(screen.getByRole("tab", { name: tab }));
  return { bridge, user };
}
const issue = (code: string, severity: BkIssue["severity"], message: string): BkIssue => ({ code, severity, message, subject_type: "day", subject_id: null, fingerprint: `${code}:day:-`, data: {} });
const WARN = issue("TXN_DRAFT_OPEN", "warning", "PI-0003 is still a draft.");
const ERR = issue("TXN_AWAITING_APPROVAL", "error", "PI-0002 is awaiting approval.");
const CRIT = issue("DOC_QUARANTINED", "critical", "1 document(s) are quarantined.");

async function startAndValidate(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: "Closing" }));
  await user.click(await screen.findByRole("button", { name: /^Start closing for/ }));
  await user.click(await screen.findByRole("button", { name: "Validate day" }));
}

describe("Daily bookkeeping workbench (Phase 10)", () => {
  it("shows the day from the server, in the company's time zone, with text status for every finding", async () => {
    await boot({ bkIssues: [WARN, ERR] });
    expect(await screen.findByLabelText("Day overview")).toBeInTheDocument();
    expect(screen.getByText(/Approved transactions not yet in an Excel export/)).toBeInTheDocument();
    expect(screen.getByText(/counted in the company's time zone \(Asia\/Muscat\)/)).toBeInTheDocument();
    const findings = await screen.findByRole("list", { name: "Findings" });
    expect(within(findings).getByText("Warning")).toBeInTheDocument();
    expect(within(findings).getByText("Error")).toBeInTheDocument();
    expect(within(findings).getByText(/PI-0002 is awaiting approval/)).toBeInTheDocument();
    expect(screen.getByText(/Document counts here and in validation cover the whole company/)).toBeInTheDocument();
    expect(await screen.findByText("Invoice 1001.pdf")).toBeInTheDocument();
    expect(await screen.findByText("PI-0001")).toBeInTheDocument();
  });

  it("walks the closing stages: a day with an unresolved error cannot be submitted until it is acknowledged with an explanation", async () => {
    const { user } = await boot({ bkIssues: [ERR] });
    await startAndValidate(user);
    expect(await screen.findAllByText("Awaiting review")).not.toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    expect(await screen.findByText(/errors that nobody has fixed or acknowledged/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Explanation for/), "short");
    await user.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText(/explanation of at least 10 characters/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText(/Explanation for/));
    await user.type(screen.getByLabelText(/Explanation for/), "Approver is away until Thursday.");
    await user.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(screen.getAllByText("Passed with warnings").length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    expect(await screen.findByRole("button", { name: "Approve and close" })).toBeInTheDocument();
  });

  it("a critical failure blocks submission and needs an explained override", async () => {
    const { user, bridge } = await boot({ bkIssues: [CRIT] });
    await startAndValidate(user);
    expect(await screen.findByText("Critical")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    expect(await screen.findByText(/unresolved critical problem/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).not.toBeInTheDocument(); // a critical issue is overridden, not acknowledged
    await user.type(screen.getByLabelText(/Explanation for/), "Quarantined file is a test upload that is being deleted.");
    await user.click(screen.getByRole("button", { name: "Override" }));
    await waitFor(() => expect(bridge.calls).toContain("bk:exception_override"));
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    expect(await screen.findByRole("button", { name: "Approve and close" })).toBeInTheDocument();
  });

  it("the person who submitted cannot approve and close; the server's refusal is shown unchanged", async () => {
    const { user } = await boot();
    await startAndValidate(user);
    await user.click(await screen.findByRole("button", { name: "Submit for review" }));
    expect(await screen.findByText(/A different person must approve and close it/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve and close" }));
    expect(await screen.findByText(/cannot approve and close it/)).toBeInTheDocument();
    expect(screen.queryByText("Closed")).not.toBeInTheDocument();
  });

  it("a second person closes the day, then reopening needs a real reason and starts a linked closing", async () => {
    const { user, bridge } = await boot({ bkSubmittedByOther: true });
    await startAndValidate(user);
    await user.click(await screen.findByRole("button", { name: "Submit for review" }));
    await user.click(await screen.findByRole("button", { name: "Approve and close" }));
    expect(await screen.findByText(/Closed by Test Accountant/)).toBeInTheDocument();
    expect(screen.getByText(/Snapshot #1/)).toBeInTheDocument();
    const reopen = screen.getByRole("button", { name: "Reopen this day" });
    expect(reopen).toBeDisabled();
    await user.type(screen.getByLabelText("Reason for reopening"), "Late supplier invoice arrived");
    await user.click(reopen);
    expect(await screen.findByText(/replaces an earlier one that was reopened/)).toBeInTheDocument();
    expect(bridge.calls).toContain("bk:closing_reopen");
    expect(screen.queryByRole("button", { name: "Reopen this day" })).not.toBeInTheDocument();
  });

  it("a daily Excel report comes from an active template; a repeat must be confirmed and explained", async () => {
    const { user } = await boot({ xlTemplates: [mockXlTemplate("t-1")] });
    await user.click(screen.getByRole("tab", { name: "Closing" }));
    await user.click(await screen.findByRole("button", { name: /^Start closing for/ }));
    const create = await screen.findByRole("button", { name: "Create Excel report" });
    expect(create).toBeDisabled();
    await screen.findByRole("option", { name: mockXlTemplate("t-1").name }); // templates load after the panel appears
    await user.selectOptions(screen.getByLabelText("Template"), "t-1");
    await user.click(screen.getByRole("button", { name: "Create Excel report" }));
    const list = await screen.findByRole("list", { name: "Daily reports" });
    expect(within(list).getByText(/template v1, mapping 1\) — completed — approved only/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create Excel report" })); // the same report again, unconfirmed
    expect(await screen.findByText(/already exported with this template/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("This is a repeat"));
    expect(screen.getByRole("button", { name: "Create Excel report" })).toBeDisabled(); // needs a reason
    await user.type(screen.getByLabelText("Reason"), "Sent again to the client");
    await user.click(screen.getByRole("button", { name: "Create Excel report" }));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Daily reports" })).getByText(/— repeat/)).toBeInTheDocument());
  });

  it("checklists are copied per company; automatic items cannot be ticked by hand", async () => {
    const { user } = await boot({ bkChecklists: [{ id: "cl-0", name: "Day-end", items: [{ id: "i-auto", title: "All processed", mandatory: true, assigned_role: null, auto_rule: "documents_processed", description: null, done: false }, { id: "i-man", title: "Count cash", mandatory: false, assigned_role: "accountant", auto_rule: null, description: null, done: false }] }] }, "Checklists");
    expect(await screen.findByLabelText("All processed", { exact: false })).toBeDisabled();
    expect(screen.getByText(/Automatic — not met/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Count cash", { exact: false }));
    await waitFor(() => expect(screen.getByLabelText("Count cash", { exact: false })).toBeChecked());
    await user.click(screen.getByRole("button", { name: /Copy the default “Daily bookkeeping” checklist/ }));
    expect(await screen.findByText(/It is now this company's own/)).toBeInTheDocument();
    expect(screen.getByText(/never affects another company/)).toBeInTheDocument();
  });

  it("tasks can be created and moved along; approving your own review request is refused by the server", async () => {
    const { user } = await boot({ bkTasks: [{ id: "tk-1", title: "Chase bank statement", description: null, status: "awaiting_review", priority: "normal", business_date: null, due_date: null, requires_review: true, blocks_closing: true, assignee_id: null, assignee_name: "Sara", overdue: true, row_version: 1 }] }, "Tasks");
    expect(await screen.findByText("Chase bank statement")).toBeInTheDocument();
    expect(screen.getByText("Overdue")).toBeInTheDocument();
    expect(screen.getByText("Blocks closing")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/cannot approve or reject it/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Title"), "Call the supplier");
    await user.click(screen.getByRole("button", { name: "Create task" }));
    expect(await screen.findByText("Call the supplier")).toBeInTheDocument();
  });

  it("the report states its basis and says plainly that cash and bank figures are not a reconciliation", async () => {
    await boot({}, "Daily report");
    expect(await screen.findByText(/Approved and posted transactions only/)).toBeInTheDocument();
    expect(screen.getByText(/Journal totals of the day \(base currency\): debit 210\.00, credit 210\.00/)).toBeInTheDocument();
    expect((await screen.findAllByText(/Not a bank reconciliation/)).length).toBeGreaterThan(0);
    expect(await screen.findByText("1,150.30".replace(",", "") )).toBeInTheDocument();
  });

  it("period readiness lists the blocking days and promises nothing it does not do", async () => {
    await boot({}, "Period readiness");
    expect(await screen.findByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText(/1 day\(s\) with activity are not closed/)).toBeInTheDocument();
    expect(screen.getByText(/2 approved transaction\(s\) not yet exported/)).toBeInTheDocument();
    expect(screen.getByText(/does not lock the period, prepare a VAT return or approve anything/)).toBeInTheDocument();
  });

  it("the interface only names allow-listed operations: it has no way to send a path or a company of its own", async () => {
    const { bridge } = await boot({});
    await screen.findByLabelText("Day overview");
    expect(bridge.calls.filter((c) => c.startsWith("bk:")).every((c) => /^bk:[a-z_]+$/.test(c))).toBe(true);
  });
});
