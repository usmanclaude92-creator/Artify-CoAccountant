import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { enrollMfa, PASSWORD } from "./helpers";
import { totp } from "./totp";

/** The e2e platform administrator's TOTP secret is shared between spec files through this file (the database is fresh per run). */
const SECRET_FILE = `${process.env.E2E_API_LOG ?? "/tmp/e2e"}.platform-secret`;
const readSecret = () => { try { return readFileSync(SECRET_FILE, "utf8").trim(); } catch { return ""; } };

export async function platformSignIn(page: Page, email: string, password = PASSWORD, secretFile = SECRET_FILE): Promise<string> {
  await page.goto("/platform/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  const enroll = page.getByRole("button", { name: "Start setup" });
  const code = page.getByLabel("Code");
  await enroll.or(code).first().waitFor();
  let secret = secretFile === SECRET_FILE ? readSecret() : "";
  if (await enroll.isVisible()) {
    secret = await enrollMfa(page);
    mkdirSync(dirname(secretFile), { recursive: true });
    if (secretFile === SECRET_FILE) writeFileSync(secretFile, secret);
    await page.getByRole("button", { name: "Continue" }).click();
  } else {
    await code.fill(totp(secret, 1)); // +1 step: the enrolment code of this window may not be replayed
    await page.getByRole("button", { name: "Verify" }).click();
  }
  await expect(page).toHaveURL(/\/platform$/);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  return secret;
}

export const OWNER_EMAIL = () => process.env.E2E_PLATFORM_EMAIL ?? "";

const STATE_FILE = `${process.env.E2E_API_LOG ?? "/tmp/e2e"}.platform-state.json`;

/** A browser context already signed in as the e2e platform administrator. MFA codes cannot be replayed within a
 *  30-second window (by design), so the first call signs in with the real UI and later calls reuse that session. */
export async function adminContext(browser: Browser, opts: { colorScheme?: "light" | "dark" } = {}): Promise<BrowserContext> {
  if (existsSync(STATE_FILE)) return browser.newContext({ storageState: STATE_FILE, ...opts });
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  await platformSignIn(page, OWNER_EMAIL());
  await ctx.storageState({ path: STATE_FILE });
  await page.close();
  return ctx;
}
