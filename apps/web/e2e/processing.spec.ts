import { expect, test } from "@playwright/test";
import { signedInOwner } from "./helpers";

test("the Document Processing page shows real (empty) counts and the honest provider state", async ({ page }) => {
  test.setTimeout(120_000);
  await signedInOwner(page, "Processing Page Firm");
  await page.getByRole("link", { name: "Document Processing" }).click();
  await expect(page.getByRole("heading", { name: "Document Processing", level: 1 })).toBeVisible();
  await expect(page.getByText("No documents have been processed yet.")).toBeVisible();
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();
  await expect(page.getByText("No failed jobs.")).toBeVisible();
  // the rules provider is local and always available; the external AI provider is not configured on a fresh install
  const providers = page.getByRole("table", { name: "Providers" });
  await expect(providers.getByRole("row", { name: /understanding rules .*Configured/i })).toBeVisible();
  await expect(providers.getByRole("row", { name: /http_json .*Not configured/i })).toBeVisible();
  await expect(providers.getByRole("button", { name: /allow/i })).toBeDisabled();
});
