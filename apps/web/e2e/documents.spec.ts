import { expect, test } from "@playwright/test";
import { signedInOwner } from "./helpers";

test("the Documents & Storage and Document Processing pages show real usage, the honest scanner and provider state, and run a read-only storage check", async ({ page }) => {
  test.setTimeout(120_000);
  await signedInOwner(page, "Docs Page Firm");
  await page.getByRole("link", { name: "Documents & Storage" }).click();
  await expect(page.getByRole("heading", { name: "Documents & Storage", level: 1 })).toBeVisible();
  // a fresh organization has stored nothing; the plan limit comes from the platform, not from the page
  await expect(page.getByText(/^0 B of /)).toBeVisible();
  await expect(page.getByText("Malware scanning is not active on this installation.")).toBeVisible();
  await expect(page.getByText("Local development storage")).toBeVisible();
  await expect(page.getByText("No failed uploads.")).toBeVisible();
  await page.getByRole("button", { name: "Run storage check" }).click();
  const result = page.getByLabel("Storage check result");
  await expect(result).toBeVisible();
  await expect(result.getByText("Missing from the store").locator("xpath=following-sibling::dd[1]")).toHaveText("0");
  await expect(result.getByText("Stray objects").locator("xpath=following-sibling::dd[1]")).toHaveText("0");
  // Phase 7: the Document Processing page (same firm: the signup endpoint allows 10 per IP per hour and the suite already uses them)
  await page.getByRole("link", { name: "Document Processing" }).click();
  await expect(page.getByRole("heading", { name: "Document Processing", level: 1 })).toBeVisible();
  await expect(page.getByText("No documents have been processed yet.")).toBeVisible();
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();
  await expect(page.getByText("No failed jobs.")).toBeVisible();
  const providers = page.getByRole("table", { name: "Providers" });
  await expect(providers.getByRole("row", { name: /understanding rules .*Configured/i })).toBeVisible();
  await expect(providers.getByRole("row", { name: /http_json .*Not configured/i })).toBeVisible();
  await expect(providers.getByRole("button", { name: /allow/i })).toBeDisabled();
  // Phase 9: the Excel & Reports overview (same firm again): read-only, no downloads, honest empty states
  await page.getByRole("link", { name: "Excel & Reports" }).click();
  await expect(page.getByRole("heading", { name: "Excel Templates & Reports", level: 1 })).toBeVisible();
  await expect(page.getByText("No templates have been added yet.")).toBeVisible();
  await expect(page.getByText("No exports yet.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Excel usage" }).getByText("Excel exports this month")).toBeVisible();
  await expect(page.getByRole("link", { name: /download/i })).toHaveCount(0);
});
