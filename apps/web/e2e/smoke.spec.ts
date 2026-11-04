import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { totp } from "./totp";

const PASSWORD = "correct horse battery staple";

/** The API runs with the console mailer in local mode; the log line carries the e-mailed token. */
function mailedToken(email: string, template: string): string {
  const log = readFileSync(process.env.E2E_API_LOG ?? "/dev/null", "utf8");
  const re = new RegExp(`MAIL to=${email.replace(/[.+]/g, "\\$&")} template=${template} context=\\{'token': '([^']+)'\\}`, "g");
  const all = [...log.matchAll(re)];
  const last = all.at(-1);
  if (!last?.[1]) throw new Error(`no ${template} mail for ${email} in API log`);
  return last[1];
}

async function enrollMfa(page: Page): Promise<string> {
  await page.getByRole("button", { name: "Start setup" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent())?.trim() ?? "";
  expect(secret.length).toBeGreaterThan(10);
  await page.getByLabel("Code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm and enable" }).click();
  await expect(page.getByTestId("recovery-codes").locator("li")).toHaveCount(10);
  return secret;
}

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

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("cell", { name: "Acme E2E Accounting" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Firm owner" })).toBeVisible();
  await expect(page.getByText("No client companies yet.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Client companies" })).toBeVisible();

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
  await expect(page).toHaveURL(/\/dashboard$/);

  // Arabic + RTL
  await page.getByRole("button", { name: "Language" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.getByRole("heading", { name: "نظرة عامة" })).toBeVisible();
  // Bidi correctness: in RTL the first logical figure ("used") must sit to the RIGHT of the limit.
  const [used, limit] = await page.locator("td.num").first().locator("bdi").all();
  const usedBox = await used!.boundingBox();
  const limitBox = await limit!.boundingBox();
  expect(usedBox!.x).toBeGreaterThan(limitBox!.x);
  if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/dashboard-ar.png`, fullPage: true });
});

test("wrong credentials show a generic error and no session", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email address").fill("nobody@example.com");
  await page.getByLabel("Password").fill("not the password at all");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".a-alert--danger")).toContainText("not correct");
  await expect(page).toHaveURL(/\/login$/);
});

test("platform admin: separate sign-in, mandatory MFA, metadata-only console", async ({ page }) => {
  const email = process.env.E2E_PLATFORM_EMAIL ?? "";
  test.skip(!email, "E2E_PLATFORM_EMAIL not provided");
  await page.goto("/platform");
  await expect(page).toHaveURL(/\/platform\/login$/);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await enrollMfa(page);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(/\/platform$/);
  await expect(page.getByText("Customer documents and accounting data are not accessible")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Acme E2E Accounting" }).first()).toBeVisible();
});
