import { expect, test } from "@playwright/test";
import { API, apiOwner, mailedToken, signedInOwner } from "./helpers";

test("one person, two organizations: switcher, company selection, accept-invitation page", async ({ page, request }) => {
  const { email } = await signedInOwner(page, "Alpha Accounting");
  await expect(page.getByLabel("Organization", { exact: true })).toHaveValue(/.+/);

  // a company in Alpha, created through the BFF with the browser's own session
  const alphaOrg = await page.getByLabel("Organization", { exact: true }).inputValue();
  const made = await page.request.post(`/bff/orgs/${alphaOrg}/companies`, {
    headers: { "x-artify-csrf": "1", "x-organization-id": alphaOrg },
    data: { name: "Alpha Trading LLC", code: "ALPHA1", country: "OM", base_currency: "OMR" },
  });
  expect(made.status()).toBe(201);

  // another firm (API-only owner) with its own company invites our person as staff
  const beta = await apiOwner(request, "Beta Audit Partners");
  const company = await request.post(`${API}/orgs/${beta.org}/companies`, {
    headers: beta.headers, data: { name: "Beta Holdings", code: "BETA1", country: "OM", base_currency: "OMR" },
  });
  expect(company.status()).toBe(201);
  const betaCompanyId = (await company.json()).id as string;
  const inv = await beta.post(`/orgs/${beta.org}/invitations`, { email, role: "staff" }, beta.headers);
  expect(inv.status()).toBe(201);

  // our signed-in person accepts through the web page (existing users authenticate via the session)
  await page.goto(`/accept-invitation?token=${mailedToken(email, "invitation")}`);
  await page.getByRole("button", { name: "Accept invitation" }).click();
  await expect(page.getByText("Invitation accepted")).toBeVisible();

  await page.goto("/dashboard");
  const orgSelect = page.getByLabel("Organization", { exact: true });
  await expect(orgSelect.locator("option")).toHaveCount(2);
  await expect(page.getByRole("listitem").filter({ hasText: "Alpha Trading LLC" })).toBeVisible();

  // switch to Beta: the URL carries the choice and data reloads. Staff are NOT assigned to any Beta
  // company yet, so Beta's company is invisible to them (company-level isolation inside a firm).
  await orgSelect.selectOption({ label: "Beta Audit Partners" });
  await expect(page).toHaveURL(/org=/);
  await expect(page.getByText("No client companies yet.")).toBeVisible();
  await expect(page.getByText("Beta Holdings")).toHaveCount(0);
  await expect(page.getByText("Alpha Trading LLC")).toHaveCount(0);

  // Beta's owner assigns our person to Beta Holdings; after a reload the company appears
  const myId = (await (await page.request.get(`/bff/me`)).json()).user_id as string;
  const assign = await request.post(`${API}/companies/${betaCompanyId}/assignments`, {
    headers: beta.headers, data: { user_id: myId, assignment_type: "secondary" },
  });
  expect(assign.status()).toBe(201);
  await page.reload();
  await expect(page.getByRole("listitem").filter({ hasText: "Beta Holdings" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Alpha Trading LLC" })).toHaveCount(0);
  // the signed-in person is only *staff* in Beta, so MFA is not demanded there and the role is shown
  await expect(page.getByText("Role: Staff")).toBeVisible();

  // company selection shows details; reload keeps the selection (state lives in the URL)
  await page.getByLabel("Company", { exact: true }).selectOption({ label: "Beta Holdings" });
  await expect(page.getByTestId("company-details")).toContainText("BETA1");
  await page.reload();
  await expect(page.getByTestId("company-details")).toContainText("Beta Holdings");

  // switching organization clears the company selection (a company belongs to one organization)
  await orgSelect.selectOption({ label: "Alpha Accounting" });
  await expect(page).not.toHaveURL(/company=/);
  await expect(page.getByTestId("company-details")).toHaveCount(0);
});
