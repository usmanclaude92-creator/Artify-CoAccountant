import { expect, test } from "@playwright/test";
import { signedInOwner } from "./helpers";

test("the Documents & Storage page shows real usage, the honest scanner state, and runs a read-only storage check", async ({ page }) => {
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
});
