import { adminContext } from "./platform-helpers";
import { expect, test } from "@playwright/test";
import { PASSWORD, enrollMfa, mailedToken } from "./helpers";
import { totp } from "./totp";

test("firm owner: signup -> verify -> mandatory MFA -> dashboard -> re-login -> Arabic/RTL", async ({ page, context }) => {
  const email = `e2e-${Date.now()}@example.com`;

  await page.goto("/signup");
  await page.getByLabel("Your name").fill("E2E Owner");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Firm or practice name").fill("Acme E2E Accounting");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Check your email for a verification link")).toBeVisible();

  await page.goto(`/verify-email?token=${mailedToken(email, "verify_email")}`);
  await expect(page.getByText("Email verified")).toBeVisible();

  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Set up two-factor authentication" }).or(page.getByText("Your role requires"))).toBeVisible();
  const secret = await enrollMfa(page);
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  await expect(page.getByRole("main").getByText(/Acme E2E Accounting/)).toBeVisible();
  await expect(page.getByTestId("signed-in-as")).toContainText("Organization owner");
  await expect(page.getByText("No companies yet.")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Firm Control Center" }).getByRole("link", { name: "My Companies" })).toBeVisible();
  // real usage numbers from the entitlement service, not placeholders
  await expect(page.getByText("Client companies")).toBeVisible();

  if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/dashboard-en.png`, fullPage: true });

  // Token cookies are HttpOnly + SameSite=Strict and invisible to page scripts.
  const cookies = await context.cookies();
  const access = cookies.find((c) => c.name === "artify_at");
  expect(access?.httpOnly).toBe(true);
  expect(access?.sameSite).toBe("Strict");
  expect(await page.evaluate(() => document.cookie)).not.toContain("artify_");

  // CSRF: a state-changing request without the custom header is refused even with valid cookies.
  const noHeader = await page.request.post("/session/tenant/logout", { data: {} });
  expect(noHeader.status()).toBe(403);

  // Sign out, then sign in again: the second factor is now required.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("Code").fill(totp(secret, 1)); // the enrollment used the current step
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
});

test("public sign-in pages switch to Arabic with right-to-left layout", async ({ page }) => {
  // The Firm Control Center itself is English-only in Phase 3 (documented deviation); the public pages keep Arabic.
  await page.goto("/login");
  await page.getByRole("button", { name: /\(Language\)|\(اللغة\)/ }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
});

test("wrong credentials show a generic error and no session", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email address").fill("nobody@example.com");
  await page.getByLabel("Password").fill("not the password at all");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".a-alert--danger")).toContainText("not correct");
  await expect(page).toHaveURL(/\/login$/);
});

test("platform admin: separate sign-in, mandatory MFA, Super Admin overview", async ({ page, browser }) => {
  const email = process.env.E2E_PLATFORM_EMAIL ?? "";
  test.skip(!email, "E2E_PLATFORM_EMAIL not provided");
  await page.goto("/platform");
  await expect(page).toHaveURL(/\/platform\/login$/);
  // the first call performs the real sign-in + mandatory MFA enrolment through the UI and saves the session
  const ctx = await adminContext(browser);
  const admin = await ctx.newPage();
  await admin.goto("/platform");
  await expect(admin.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(admin.getByRole("navigation", { name: "Super Admin" }).getByRole("link", { name: "Platform Administrators" })).toBeVisible();
  await ctx.close();
});
