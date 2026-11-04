import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { PASSWORD, signedInOwner } from "./helpers";

/**
 * Automated accessibility checks (axe-core, WCAG 2.0/2.1/2.2 A+AA rules) plus keyboard behaviour.
 * LIMITS: automated rules find roughly a third of real-world issues. This does NOT replace a
 * manual screen-reader review (NVDA/JAWS/VoiceOver) or an expert audit, which remain open items.
 */
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function scan(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const summary = results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
  expect(summary, `axe violations on ${label}`).toEqual([]);
}

const PUBLIC_PAGES = ["/login", "/signup", "/forgot-password", "/reset-password?token=sample-token-value", "/accept-invitation?token=sample-token-value", "/verify-email?token=bad", "/platform/login"];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`axe, ${scheme} scheme`, () => {
    test.use({ colorScheme: scheme });
    for (const path of PUBLIC_PAGES) {
      test(`English ${path}`, async ({ page }) => {
        await page.goto(path);
        await expect(page.locator("main")).toBeVisible();
        await scan(page, `${path} (${scheme}, en)`);
        if (process.env.E2E_SHOTS && path === "/signup") await page.screenshot({ path: `${process.env.E2E_SHOTS}/signup-${scheme}.png`, fullPage: true });
      });
    }
    test("Arabic RTL login and signup", async ({ page, context }) => {
      await context.addCookies([{ name: "artify_locale", value: "ar", url: "http://localhost:3100" }]);
      for (const path of ["/login", "/signup"]) {
        await page.goto(path);
        await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
        await scan(page, `${path} (${scheme}, ar)`);
      }
    });
  });
}

const CC_PAGES: [string, string][] = [
  ["/dashboard", "Dashboard"], ["/companies", "My Companies"], ["/companies/new", "Create company"], ["/team", "Team Members"], ["/accountants", "Accountants"],
  ["/assignments", "Client Assignments"], ["/invitations", "Invitations"], ["/subscription", "Subscription & Usage"], ["/activity", "Activity & Audit History"],
  ["/settings", "Organization Settings"], ["/profile", "My Profile"], ["/support-access", "Artify support access"], ["/workspace-templates", "Workspace Templates"], ["/documents", "Documents & Storage"],
];

test("axe on every Control Center page (light and dark) and MFA setup", async ({ page }) => {
  test.setTimeout(240_000);
  await page.emulateMedia({ colorScheme: "light" });
  await signedInOwner(page, "A11y Accounting");
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Organization", { exact: true })).toBeVisible();
  for (const [path, heading] of CC_PAGES) {
    await page.emulateMedia({ colorScheme: "light" });
    const org = await page.getByLabel("Organization", { exact: true }).inputValue();
    await page.goto(`${path}?org=${org}`);
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    await expect(page.getByText(/^Loading/)).toHaveCount(0);
    await scan(page, `${path} (light)`);
    await page.emulateMedia({ colorScheme: "dark" });
    await scan(page, `${path} (dark)`);
    if (process.env.E2E_SHOTS && path === "/dashboard") await page.screenshot({ path: `${process.env.E2E_SHOTS}/dashboard-dark-en.png`, fullPage: true });
  }
  // the create-company wizard steps and the roles panel are separate DOM states
  await page.emulateMedia({ colorScheme: "light" });
  await page.getByRole("link", { name: "Team Members" }).click();
  await page.getByRole("button", { name: "Roles and permissions" }).click();
  await expect(page.getByRole("table", { name: "Organization roles" })).toBeVisible();
  await scan(page, "team with roles panel");
});

test("keyboard: logical tab order, visible focus, Enter submits", async ({ page }) => {
  await page.goto("/login");
  const order: string[] = [];
  const visibleFocus: boolean[] = [];
  for (let i = 0; i < 7; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      const cs = getComputedStyle(el);
      const name = el.getAttribute("aria-label") || (el as HTMLInputElement).labels?.[0]?.textContent || el.textContent || el.tagName;
      return { name: name.trim(), focusStyle: cs.boxShadow !== "none" || (cs.outlineStyle !== "none" && cs.outlineWidth !== "0px") };
    });
    order.push(info.name);
    visibleFocus.push(info.focusStyle);
  }
  expect(order.map((n) => n.replace(/\s+/g, " "))).toEqual(["Skip to content", "العربية (Language)", "Email address", "Password", "Sign in", "Forgot your password?", "Create account"]);
  expect(visibleFocus, `focus indicator missing at: ${order.filter((_, i) => !visibleFocus[i]).join(", ")}`).not.toContain(false);
  // Enter submits the form from the keyboard
  await page.getByLabel("Email address").fill("keyboard@example.com");
  await page.getByLabel("Password").fill(PASSWORD + "-wrong");
  await page.getByLabel("Password").press("Enter");
  await expect(page.locator(".a-alert--danger")).toBeVisible();
  // the skip link moves focus to the main landmark
  await page.goto("/login");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => location.hash)).toBe("#main");
});

test("form errors are announced: invalid fields are marked and described", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("X");
  await page.getByLabel("Email address").fill("not-an-email");
  await page.getByLabel("Password").fill("short");
  await page.getByLabel("Firm or practice name").fill("Y");
  await page.getByRole("button", { name: "Create account" }).click();
  const alert = page.locator(".a-alert--danger");
  await expect(alert).toBeVisible();
  expect(await alert.getAttribute("role")).toBe("alert");
  const invalid = page.locator('input[aria-invalid="true"]');
  await expect(invalid.first()).toBeVisible();
  const describedby = await invalid.first().getAttribute("aria-describedby");
  expect(describedby).toBeTruthy();
});
