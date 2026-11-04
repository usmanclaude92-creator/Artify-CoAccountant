import { expect, test } from "@playwright/test";
import { API, PASSWORD, mailedToken, signedInOwner } from "./helpers";

test("firm owner runs the Control Center: company wizard, lifecycle, invitation, assignment, least-privilege accountant", async ({ page, browser, request }) => {
  test.setTimeout(180_000);
  await signedInOwner(page, "Control Test Firm");
  const org = await page.getByLabel("Organization", { exact: true }).inputValue();

  // ---- company wizard (5 steps) ------------------------------------------------------------
  await page.getByRole("link", { name: "My Companies" }).click();
  await expect(page.getByText("No companies yet.")).toBeVisible();
  await page.getByRole("link", { name: "Create company" }).click();
  await expect(page.getByRole("heading", { name: "Create company", level: 1 })).toBeVisible();
  await page.getByLabel("Company display name *").fill("Gulf Traders LLC");
  await page.getByLabel("Company code *").fill("GULF1");
  await page.getByLabel("Country (ISO 3166 code) *").fill("OM");
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByLabel("Base currency (ISO 4217 code) *").fill("OMR");
  await page.getByLabel("VAT registered").check();
  await page.getByLabel("VAT number").fill("VAT-E2E-1");
  await page.getByLabel("Tax jurisdiction").fill("Sample jurisdiction");
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("No active accountants exist yet")).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Workspace", level: 2 })).toBeVisible();
  await expect(page.getByLabel("Blank workspace — configure it later")).toBeChecked();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Registered (VAT-E2E-1)")).toBeVisible();
  await page.getByRole("button", { name: "Create company" }).click();
  await expect(page.getByRole("heading", { name: "Gulf Traders LLC", level: 1 })).toBeVisible();
  const companyUrl = page.url();

  // ---- accounting settings (company-owned) ---------------------------------------------------
  await page.getByRole("tab", { name: "Accounting settings" }).click();
  await expect(page.getByText("Registered · VAT-E2E-1")).toBeVisible();
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Accounting basis").selectOption("cash");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Cash", { exact: true })).toBeVisible();

  // ---- the configuration record exists but nothing is pre-built ------------------------------
  await page.getByRole("tab", { name: "Configuration" }).click();
  await expect(page.getByText("Not configured")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the workspace builder" })).toBeVisible();

  // ---- lifecycle: deactivate -> activate -> archive -> restore (nothing is deleted) -----------
  const dialog = page.getByRole("dialog");
  for (const [open, submit, expected] of [["Deactivate", "Deactivate", "inactive"], ["Activate", "Activate", ""], ["Archive", "Archive", "archived"], ["Restore", "Restore", ""]] as const) {
    await page.getByRole("button", { name: open, exact: true }).first().click();
    await dialog.getByRole("button", { name: submit, exact: true }).click();
    await expect(dialog).toHaveCount(0);
    if (expected) await expect(page.getByText(`This company is ${expected} and read-only.`)).toBeVisible();
    else await expect(page.getByText(/read-only\./)).toHaveCount(0);
  }
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByRole("cell", { name: "Company status changed" }).first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "Company created" })).toBeVisible();

  // ---- invitations are honest about e-mail --------------------------------------------------
  const stamp = Date.now();
  const invitee = `e2e-acc-${stamp}@example.com`;
  await page.getByRole("link", { name: "Invitations" }).click();
  await page.getByLabel("E-mail").fill(`e2e-cancel-${stamp}@example.com`);
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByText(/No e-mail server is configured|delivered through the configured SMTP/)).toBeVisible();
  await page.getByRole("button", { name: `Cancel invitation for e2e-cancel-${stamp}@example.com` }).click();
  await dialog.getByRole("button", { name: "Cancel invitation" }).click();
  await expect(page.getByRole("row", { name: new RegExp(`e2e-cancel-${stamp}.*Revoked`) })).toBeVisible();
  await page.getByLabel("E-mail").fill(invitee);
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByRole("row", { name: new RegExp(`${invitee}.*Pending`) })).toBeVisible();

  // the invitee accepts with the one-time token (what the e-mail link carries)
  const accepted = await request.post(`${API}/auth/invitations/accept`, {
    data: { token: mailedToken(invitee, "invitation"), display_name: "E2E Accountant", password: PASSWORD },
  });
  expect(accepted.status()).toBeLessThan(300);
  await page.reload();
  await expect(page.getByRole("row", { name: new RegExp(`${invitee}.*Accepted`) })).toBeVisible();

  // an invitee can also decline through the public page; the firm sees it as declined and the link is dead
  const decliner = `e2e-decline-${stamp}@example.com`;
  await page.getByLabel("E-mail").fill(decliner);
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByRole("row", { name: new RegExp(`${decliner}.*Pending`) })).toBeVisible();
  const declinePage = await browser.newPage();
  await declinePage.goto(`/accept-invitation?token=${mailedToken(decliner, "invitation")}`);
  await declinePage.getByRole("button", { name: "Decline invitation" }).click();
  await expect(declinePage.getByText("Invitation declined")).toBeVisible();
  await declinePage.close();
  await page.reload();
  await expect(page.getByRole("row", { name: new RegExp(`${decliner}.*Declined`) })).toBeVisible();

  // ---- team, assignment, accountants overview -------------------------------------------------
  await page.getByRole("link", { name: "Team Members" }).click();
  await expect(page.getByRole("row", { name: /E2E Accountant.*Accountant.*Active/ })).toBeVisible();
  await page.goto(companyUrl);
  await page.getByRole("tab", { name: "Assigned accountants" }).click();
  await page.getByLabel("Accountant", { exact: true }).selectOption({ label: "E2E Accountant" });
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await expect(page.getByRole("row", { name: /E2E Accountant.*Secondary.*Active/ })).toBeVisible();
  await page.getByRole("link", { name: "Client Assignments" }).click();
  await expect(page.getByRole("row", { name: /Gulf Traders LLC.*E2E Accountant/ })).toBeVisible();
  await page.getByRole("link", { name: "Accountants", exact: true }).click();
  await expect(page.getByRole("row", { name: /E2E Accountant.*Gulf Traders LLC/ })).toBeVisible();

  // ---- the accountant sees only what the role and assignment allow --------------------------
  const ctx = await browser.newContext();
  const acc = await ctx.newPage();
  await acc.goto("/login");
  await acc.getByLabel("Email address").fill(invitee);
  await acc.getByLabel("Password").fill(PASSWORD);
  await acc.getByRole("button", { name: "Sign in" }).click();
  await expect(acc.getByTestId("signed-in-as")).toContainText("Accountant");
  await acc.getByRole("link", { name: "My Companies" }).click();
  await expect(acc.getByRole("link", { name: "Gulf Traders LLC" })).toBeVisible();
  await expect(acc.getByRole("link", { name: "Invitations" })).toHaveCount(0);
  await expect(acc.getByRole("link", { name: "Create company" })).toHaveCount(0);
  await acc.goto(`/invitations?org=${org}`);
  await expect(acc.getByText("Your role does not permit this.")).toBeVisible();
  await ctx.close();

  // ---- activity trail shows the privileged changes -------------------------------------------
  await page.getByRole("link", { name: "Activity & Audit History" }).click();
  await expect(page.getByRole("cell", { name: "Company status changed" }).first()).toBeVisible();
});
