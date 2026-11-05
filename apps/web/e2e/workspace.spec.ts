import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signedInOwner } from "./helpers";

test("workspace builder: build, customize, template, permissions, history; two companies stay independent", async ({ page }) => {
  test.setTimeout(240_000);
  await signedInOwner(page, "Workspace Test Firm");
  const org = await page.getByLabel("Organization", { exact: true }).inputValue();
  const dialog = page.getByRole("dialog");
  const scan = async (label: string) => {
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
      expect(r.violations.map((v) => `${v.id}: ${v.help}`), `${label} (${scheme})`).toEqual([]);
    }
    await page.emulateMedia({ colorScheme: "light" });
  };

  // two companies
  for (const [name = "", code = ""] of [["Alpha Trading", "ALPHA"], ["Beta Contracting", "BETA"]]) {
    await page.goto(`/companies/new?org=${org}`);
    await page.getByLabel("Company display name *").fill(name);
    await page.getByLabel("Company code *").fill(code);
    await page.getByLabel("Country (ISO 3166 code) *").fill("OM");
    for (let i = 0; i < 4; i++) {
      if (i === 1) await page.getByLabel("Base currency (ISO 4217 code) *").fill("OMR");
      await page.getByRole("button", { name: "Next" }).click();
    }
    await page.getByRole("button", { name: "Create company" }).click();
    await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  }
  const open = async (name: string) => {
    await page.goto(`/companies?org=${org}`);
    await page.getByRole("link", { name }).first().click();
    await page.getByRole("tab", { name: "Configuration" }).click();
    await page.getByRole("link", { name: "Open the workspace builder" }).click();
    await expect(page.getByRole("heading", { name: `${name} · Workspace`, level: 1 })).toBeVisible();
  };

  // ---- Alpha: empty state, then build by hand ---------------------------------------------------
  await open("Alpha Trading");
  await expect(page.getByText("This company has no sections yet")).toBeVisible();
  await scan("empty workspace builder");
  await page.getByRole("button", { name: "Add section" }).click();
  await dialog.getByLabel("Name").fill("Finance");
  await dialog.getByRole("button", { name: "Create" }).click();
  await expect(dialog).toHaveCount(0);
  const tree = page.getByRole("list", { name: "Workspace structure" });
  await expect(tree.getByRole("button", { name: "Finance", exact: true })).toBeVisible();
  await tree.getByRole("button", { name: "Finance", exact: true }).click();
  await page.getByRole("button", { name: "Add subfolder" }).click();
  await dialog.getByLabel("Name").fill("Bank Statements");
  await dialog.getByRole("button", { name: "Create" }).click();
  await expect(tree.getByRole("button", { name: "Bank Statements", exact: true })).toBeVisible();
  // invalid names are refused by the server with a clear message
  await tree.getByRole("button", { name: "Finance", exact: true }).click();
  await page.getByRole("button", { name: "Add subfolder" }).click();
  await dialog.getByLabel("Name").fill("bad/name");
  await dialog.getByRole("button", { name: "Create" }).click();
  await expect(dialog.getByRole("alert").filter({ hasText: /cannot contain/i })).toBeVisible(); // the hint under the field also says "Cannot contain": match the error alert only
  await dialog.getByRole("button", { name: "Cancel" }).click();
  // rename keeps the item
  await tree.getByRole("button", { name: "Finance", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Finance & Banking");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(tree.getByRole("button", { name: "Finance & Banking", exact: true })).toBeVisible();
  await expect(page.getByText("C:\\")).toHaveCount(0);
  await expect(page.locator("pre")).toContainText("Finance & Banking/Bank Statements/");
  // archive asks for confirmation and shows impact
  await tree.getByRole("button", { name: "Bank Statements", exact: true }).click();
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  await expect(dialog.getByText("Nothing is deleted")).toBeVisible();
  await scan("archive dialog");
  await dialog.getByRole("button", { name: "Archive" }).click();
  await expect(tree.getByRole("button", { name: "Bank Statements", exact: true })).toHaveCount(0);
  await page.getByLabel("Show archived items").check();
  await tree.getByRole("button", { name: /Bank Statements \(archived\)/ }).click();
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await dialog.getByRole("button", { name: "Restore" }).click();
  await expect(tree.getByRole("button", { name: "Bank Statements", exact: true })).toBeVisible();
  await scan("structure with items");

  // ---- category with a document-kind mapping ---------------------------------------------------
  await page.getByRole("tab", { name: "Categories" }).click();
  await page.getByRole("button", { name: "Add category" }).click();
  await dialog.getByLabel("Category name").fill("Muscat Bank Monthly Statements");
  await dialog.getByLabel("Artify document kind (required)").selectOption({ label: "Bank statement" });
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: /Bank statement · banking · statement extraction/ })).toBeVisible();

  // ---- custom field + naming rule preview -----------------------------------------------------
  await page.getByRole("tab", { name: "Custom fields" }).click();
  await page.getByRole("button", { name: "Add field" }).click();
  await dialog.getByLabel("Field name").fill("Bank");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "bank", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Naming rules" }).click();
  await page.getByRole("button", { name: "Add naming rule" }).click();
  await dialog.getByLabel("Rule name").fill("Statements");
  await dialog.getByLabel("Pattern").fill("{Company}_{Bank}_{Period}");
  await dialog.getByLabel("Sample values (one per line, name=value)").fill("Bank=Bank of Muscat\nDocumentDate=2026-03-04");
  await dialog.getByRole("button", { name: "Preview" }).click();
  await expect(dialog.locator("strong.a-mono")).toHaveText(/Alpha_Trading_Bank_of_Muscat_2026-03/);
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "{Company}_{Bank}_{Period}" })).toBeVisible();

  // ---- filing rule with a dry-run test ---------------------------------------------------------
  await page.getByRole("tab", { name: "Filing rules" }).click();
  await page.getByRole("button", { name: "Add filing rule" }).click();
  await dialog.getByLabel("Rule name").fill("Bank statements");
  await dialog.getByLabel("Value").selectOption({ label: "Bank statement" });
  await dialog.getByLabel("File into").selectOption({ label: "Finance & Banking / Bank Statements" });
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "Bank statements" }).first()).toBeVisible();
  await page.getByLabel("Document kind").selectOption({ label: "Bank statement" });
  await page.getByRole("button", { name: "Test", exact: true }).click();
  await expect(page.getByText(/Winner: Bank statements/)).toBeVisible();
  await scan("filing rules");

  // ---- permissions: restricted mode ------------------------------------------------------------
  await page.getByRole("tab", { name: "Permissions" }).click();
  await page.getByRole("button", { name: "Switch to restricted" }).click();
  await expect(page.getByText("Restricted", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Switch to open" }).click();
  await scan("permissions");

  // ---- history shows every change, with restore preview ----------------------------------------
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByRole("cell", { name: "node created" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Restore this version" }).last().click();
  await dialog.getByRole("button", { name: "Preview the effect first" }).click();
  await expect(dialog.getByText(/Would archive/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();

  // ---- Beta is untouched and gets a template; Alpha stays unchanged -----------------------------
  await open("Beta Contracting");
  await expect(page.getByText("This company has no sections yet")).toBeVisible();
  await page.getByRole("tab", { name: "Templates" }).click();
  await expect(page.getByText("Construction", { exact: true })).toBeVisible();
  await scan("template gallery");
  await page.getByRole("listitem").filter({ hasText: "Construction" }).getByRole("button", { name: "Preview & apply" }).click();
  await dialog.getByRole("button", { name: "Check against my plan limits" }).click();
  await expect(dialog.getByText("Fits your plan.")).toBeVisible();
  await dialog.getByRole("button", { name: "Apply template" }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("tab", { name: "Structure" }).click();
  const betaTree = page.getByRole("list", { name: "Workspace structure" });
  await expect(betaTree.getByRole("button", { name: "Projects", exact: true })).toBeVisible();
  await expect(betaTree.getByRole("button", { name: "Finance & Banking", exact: true })).toHaveCount(0);
  await open("Alpha Trading");
  const alphaTree = page.getByRole("list", { name: "Workspace structure" });
  await expect(alphaTree.getByRole("button", { name: "Finance & Banking", exact: true })).toBeVisible();
  await expect(alphaTree.getByRole("button", { name: "Projects", exact: true })).toHaveCount(0);

  // ---- keyboard: the tree is operable without a mouse -------------------------------------------
  await alphaTree.getByRole("button", { name: "Finance & Banking", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: /Section: Finance & Banking/ })).toBeVisible();
  await page.getByRole("link", { name: "Workspace Templates" }).click();
  await expect(page.getByRole("heading", { name: "Workspace Templates", level: 1 })).toBeVisible();
  await scan("workspace templates page");
});
