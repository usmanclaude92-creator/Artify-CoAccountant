import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { API, PASSWORD, apiOwner, mailedToken, signedInOwner } from "./helpers";
import { OWNER_EMAIL, adminContext, platformSignIn } from "./platform-helpers";

const nav = (page: Page) => page.getByRole("navigation", { name: "Super Admin" });
const slugOf = async (page: Page) => (await page.locator("dt:text-is('Slug') + dd").textContent())?.trim() ?? "";

async function scan(page: Page, label: string) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = r.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
  expect(summary, `axe violations on ${label}`).toEqual([]);
}

test("plan -> assign -> customer limit enforced; suspend with typed confirmation; audit trail", async ({ browser, request }) => {
  test.skip(!OWNER_EMAIL(), "E2E_PLATFORM_EMAIL not provided");
  const firm = await apiOwner(request, "Console Limit Firm");
  const mk = (code: string) => request.post(`${API}/orgs/${firm.org}/companies`, { headers: firm.headers, data: { name: `Co ${code}`, code, country: "OM", base_currency: "OMR" } });
  const page = await (await adminContext(browser)).newPage();
  await page.goto("/platform");

  // overview: real counts, and unmeasured things say so instead of showing numbers
  await expect(page.locator(".a-kpi", { hasText: "Organizations" }).first()).toBeVisible();
  for (const name of ["Documents processed", "Storage consumption", "AI processing"]) {
    await expect(page.locator(".a-kpi--na", { hasText: name })).toContainText("Not available");
  }

  // global search finds the organization
  await page.getByLabel("Search organizations, companies and administrators").fill("Console Limit");
  await page.getByRole("link", { name: /Console Limit Firm/ }).first().click();
  await expect(page.getByRole("heading", { name: "Console Limit Firm" })).toBeVisible();
  const slug = await slugOf(page);

  // create a plan through the UI: inactive until activated
  await page.goto("/platform/plans/new");
  await page.getByLabel("Key (lowercase letters, digits, underscore)").fill(`e2e_${Date.now() % 100000}`);
  await page.getByLabel("Name", { exact: true }).fill("E2E Three Companies");
  await page.getByLabel("Price (optional, exact decimal)").fill("25.500");
  await page.getByLabel("limit.companies setting").selectOption("number");
  await page.getByLabel("limit.companies limit").fill("3");
  await page.getByLabel("limit.users setting").selectOption("number"); // anything a plan omits is denied, so be explicit
  await page.getByLabel("limit.users limit").fill("10");
  await page.getByRole("button", { name: "Create plan" }).click();
  await expect(page.getByRole("heading", { name: "E2E Three Companies" })).toBeVisible();
  await expect(page.locator(".a-chip", { hasText: "Inactive" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Activate" }).click();
  await expect(page.locator(".a-chip", { hasText: "Active" }).first()).toBeVisible();
  await page.goto("/platform/plans");
  await expect(page.getByRole("link", { name: "E2E Three Companies" })).toBeVisible();
  await expect(page.getByText("25.500 OMR / monthly")).toBeVisible();

  // assign it from the customer's subscription page
  await page.goto("/platform/subscriptions?q=Console%20Limit");
  await page.getByRole("link", { name: "Console Limit Firm" }).click();
  await page.getByRole("button", { name: "Change plan" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByLabel("New plan version").selectOption({ label: "E2E Three Companies v1" });
  await expect(dlg.getByRole("button", { name: "Change plan" })).toBeDisabled(); // a reason is mandatory
  await dlg.getByLabel(/Reason/).fill("Sales agreed a three company plan");
  await dlg.getByRole("button", { name: "Change plan" }).click();
  await expect(page.getByText("E2E Three Companies (version 1)")).toBeVisible();

  // the customer API now enforces exactly that limit (no deploy, no cache to clear)
  for (const code of ["E1", "E2", "E3"]) expect((await mk(code)).status()).toBe(201);
  const blocked = await mk("E4");
  expect(blocked.status()).toBe(402);
  expect((await blocked.json()).limit).toBe(3);

  // usage page shows it as at the limit
  await page.goto("/platform/usage?q=Console%20Limit");
  await expect(page.getByRole("row", { name: /Console Limit Firm/ })).toContainText("3 / 3");

  // suspend needs a reason AND the typed slug; wrong text keeps the button disabled
  await page.goto("/platform/organizations?q=Console%20Limit");
  await page.getByRole("link", { name: "Console Limit Firm" }).click();
  await page.getByRole("button", { name: "Suspend" }).click();
  const sd = page.getByRole("dialog");
  await sd.getByLabel(/Reason/).fill("Chargeback under investigation");
  await sd.getByLabel(/Type “/).fill("not-the-slug");
  await expect(sd.getByRole("button", { name: "Suspend organization" })).toBeDisabled();
  await sd.getByLabel(/Type “/).fill(slug);
  await sd.getByRole("button", { name: "Suspend organization" }).click();
  await expect(page.locator(".a-chip", { hasText: "Suspended" }).first()).toBeVisible();
  expect((await mk("E5")).status()).toBe(403); // suspended organizations are read-only
  await page.getByRole("button", { name: "Reactivate" }).click();
  await page.getByRole("dialog").getByLabel(/Reason/).fill("Chargeback resolved in our favour");
  await page.getByRole("dialog").getByLabel(/Type “/).fill(slug);
  await page.getByRole("dialog").getByRole("button", { name: "Reactivate" }).click();
  await expect(page.locator(".a-chip", { hasText: "Active" }).first()).toBeVisible();

  // everything above is in the audit log, with the reason
  await page.goto("/platform/audit?action=organization.");
  await expect(page.getByRole("row", { name: /organization.suspended/ })).toContainText("Chargeback under investigation");
  await page.goto("/platform/audit?action=subscription.plan_changed");
  await expect(page.getByRole("row", { name: /subscription.plan_changed/ }).first()).toContainText("Success");
});

test("a support administrator sees a reduced console; the API still refuses what the menu hides", async ({ browser }) => {
  test.skip(!OWNER_EMAIL(), "E2E_PLATFORM_EMAIL not provided");
  const page = await (await adminContext(browser)).newPage();
  await page.goto("/platform/admins");
  await page.getByRole("button", { name: "Invite administrator" }).click();
  const email = `support-e2e-${Date.now()}@example.com`;
  const d = page.getByRole("dialog");
  await d.getByLabel("E-mail address").fill(email);
  await d.getByLabel("Role").selectOption("platform_support");
  await d.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByRole("row", { name: new RegExp(email) })).toContainText("Pending");

  // a second, clean browser context = a different person
  const ctx = await browser.newContext();
  const sp = await ctx.newPage();
  await sp.goto(`/platform/accept-invitation?token=${mailedToken(email, "platform_invitation")}`);
  await sp.getByLabel("Your name").fill("Sam Support");
  await sp.getByLabel("Choose a password").fill(PASSWORD);
  await sp.getByRole("button", { name: "Create account" }).click();
  await expect(sp.getByText("Your account is ready")).toBeVisible();
  await platformSignIn(sp, email, PASSWORD, `${process.env.E2E_API_LOG ?? "/tmp/e2e"}.support-secret`);

  const menu = nav(sp);
  await expect(menu.getByRole("link", { name: "Support Access" })).toBeVisible();
  await expect(menu.getByRole("link", { name: "Accounting Firms" })).toBeVisible();
  for (const hidden of ["Billing & Payments", "Platform Administrators", "Audit Logs", "Platform Settings", "Subscription Plans"]) {
    await expect(menu.getByRole("link", { name: hidden })).toHaveCount(0);
  }
  // deep-linking to a hidden page: the page loads but the API refuses, and the UI says so
  await sp.goto("/platform/billing");
  await expect(sp.getByText("Your role does not permit this.").first()).toBeVisible();
  await sp.goto("/platform/admins");
  await expect(sp.getByText("Your role does not permit this.").first()).toBeVisible();
  const direct = await sp.request.get("/bff/platform/billing/invoices");
  expect(direct.status()).toBe(403);
  await ctx.close();

  // the denied attempts are in the audit log
  await page.goto("/platform/audit?outcome=denied");
  await expect(page.getByRole("row", { name: /authz.denied/ }).first()).toBeVisible();
});

test("customers cannot enter the Super Admin; support access needs the customer's approval", async ({ page, browser }) => {
  test.skip(!OWNER_EMAIL(), "E2E_PLATFORM_EMAIL not provided");
  // a signed-in CUSTOMER is sent away from /platform, and their cookies do not work on platform APIs
  const { email: ownerEmail } = await signedInOwner(page, "Support Flow Firm");
  await page.goto("/platform");
  await expect(page).toHaveURL(/\/platform\/login$/);
  expect((await page.request.get("/bff/platform/me")).status()).toBe(401);

  // the platform administrator asks for access
  const ctx = await adminContext(browser);
  const admin = await ctx.newPage();
  await admin.goto("/platform/support");
  await admin.getByRole("button", { name: "Request access" }).click();
  const rd = admin.getByRole("dialog");
  await rd.getByLabel("Organization").selectOption({ label: "Support Flow Firm" });
  await rd.getByLabel(/Why do you need access/).fill("Customer reported a missing member after an import");
  await rd.getByRole("button", { name: "Send request" }).click();
  await expect(admin.getByRole("row", { name: /Support Flow Firm/ })).toContainText("Requested");
  // nothing is visible until the customer approves
  await expect(admin.getByRole("button", { name: "View members" })).toHaveCount(0);

  // the customer approves in their own console
  await page.goto("/support-access");
  await expect(page.getByText("Customer reported a missing member after an import")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.locator(".a-chip", { hasText: "active" })).toBeVisible();

  // now (and only now) the administrator can look at the member list, and it is recorded for the customer
  await admin.reload();
  await admin.getByRole("button", { name: "View members" }).click();
  await expect(admin.getByRole("cell", { name: ownerEmail })).toBeVisible();
  await page.goto("/dashboard");
  await ctx.close();

  // the customer can end access at any time
  await page.goto("/support-access");
  await page.getByRole("button", { name: "End access now" }).click();
  await expect(page.locator(".a-chip", { hasText: "revoked" })).toBeVisible();
});

test.describe("Super Admin accessibility (axe, light and dark)", () => {
  for (const scheme of ["light", "dark"] as const) {
    test(`pages in the ${scheme} scheme`, async ({ browser }) => {
      test.skip(!OWNER_EMAIL(), "E2E_PLATFORM_EMAIL not provided");
      const page = await (await adminContext(browser, { colorScheme: scheme })).newPage();
      for (const [path, heading] of [
        ["/platform", "Overview"], ["/platform/organizations", "All Organizations"], ["/platform/organizations/new", "New organization"],
        ["/platform/companies", "Client Companies"], ["/platform/plans", "Subscription Plans"], ["/platform/plans/new", "New plan"],
        ["/platform/subscriptions", "Subscriptions"], ["/platform/billing", "Billing & Payments"], ["/platform/usage", "Usage & Limits"],
        ["/platform/admins", "Platform Administrators"], ["/platform/support", "Support Access"], ["/platform/activity", "Platform Activity"],
        ["/platform/audit", "Audit Logs"], ["/platform/health", "System Health"], ["/platform/settings", "Platform Settings"],
      ] as const) {
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
        await expect(page.getByText(/^Loading/)).toHaveCount(0);
        await scan(page, `${path} (${scheme})`);
      }
    });
  }
});
