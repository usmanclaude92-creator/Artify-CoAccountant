import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { totp } from "./totp";

export const PASSWORD = "correct horse battery staple";
export const API = process.env.E2E_API_URL ?? "http://localhost:8100/api/v1";

/** The API runs with the console mailer in local mode; the log line carries the e-mailed token. */
export function mailedToken(email: string, template: string): string {
  const log = readFileSync(process.env.E2E_API_LOG ?? "/dev/null", "utf8");
  const re = new RegExp(`MAIL to=${email.replace(/[.+]/g, "\\$&")} template=${template} context=\\{'token': '([^']+)'\\}`, "g");
  const last = [...log.matchAll(re)].at(-1);
  if (!last?.[1]) throw new Error(`no ${template} mail for ${email} in API log`);
  return last[1];
}

export async function enrollMfa(page: Page): Promise<string> {
  await page.getByRole("button", { name: "Start setup" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent())?.trim() ?? "";
  expect(secret.length).toBeGreaterThan(10);
  await page.getByLabel("Code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm and enable" }).click();
  await expect(page.getByTestId("recovery-codes").locator("li")).toHaveCount(10);
  return secret;
}

/** Sign up, verify, sign in and complete mandatory MFA through the real UI. Ends on the dashboard. */
export async function signedInOwner(page: Page, orgName: string): Promise<{ email: string; secret: string }> {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("E2E Owner");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Firm or practice name").fill(orgName);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Check your email for a verification link")).toBeVisible();
  await page.goto(`/verify-email?token=${mailedToken(email, "verify_email")}`);
  await expect(page.getByText("Email verified")).toBeVisible();
  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  const secret = await enrollMfa(page);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  return { email, secret };
}

/** A second, fully API-driven owner (no browser session) used to create a second organization. */
export async function apiOwner(request: APIRequestContext, orgName: string) {
  const email = `e2e-api-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  const post = (path: string, data: unknown, headers: Record<string, string> = {}) =>
    request.post(`${API}${path}`, { data, headers });
  expect((await post("/auth/signup", { email, password: PASSWORD, display_name: "Beta Owner", organization_name: orgName,
    organization_type: "accounting_firm", country: "OM", default_currency: "OMR" })).status()).toBe(202);
  expect((await post("/auth/verify-email", { token: mailedToken(email, "verify_email") })).status()).toBe(204);
  const login = await (await post("/auth/login", { email, password: PASSWORD })).json();
  let h: Record<string, string> = { Authorization: `Bearer ${login.tokens.access_token}` };
  const me = await (await request.get(`${API}/me`, { headers: h })).json();
  const org = me.memberships[0].organization_id as string;
  h = { ...h, "X-Organization-Id": org };
  const secret = (await (await post("/auth/mfa/enroll", {}, h)).json()).secret as string;
  const conf = await (await post("/auth/mfa/confirm", { code: totp(secret) }, h)).json();
  h = { ...h, Authorization: `Bearer ${conf.access_token}` };
  return { email, org, headers: h, post };
}
