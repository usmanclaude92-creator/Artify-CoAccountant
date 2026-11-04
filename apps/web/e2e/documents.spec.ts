import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { signedInOwner } from "./helpers";

/** Documents are imported by the desktop app; here we store real files through the same API (ASCII text passes the BFF
 *  unchanged) and check that the Control Center reports them honestly. */
test("Control Center shows stored documents, storage usage and failed uploads", async ({ page }) => {
  await signedInOwner(page, "Docs View Firm");
  const org = await page.getByLabel("Organization", { exact: true }).inputValue();
  const h = { "x-artify-csrf": "1", "x-organization-id": org };
  const company = await (await page.request.post(`/bff/orgs/${org}/companies`, { headers: h, data: { name: "Docs Trading LLC", code: "DOCS1", country: "OM", base_currency: "OMR" } })).json();
  const node = (await (await page.request.post(`/bff/companies/${company.id}/workspace/nodes`, { headers: h, data: { name: "Inbox" } })).json()).item;

  async function upload(filename: string, content: string): Promise<number> {
    const data = Buffer.from(content, "ascii");
    const init = await page.request.post(`/bff/companies/${company.id}/uploads`, {
      headers: { ...h, "idempotency-key": `e2e-${filename}-${Date.now()}` },
      data: { filename, size: data.length, sha256: createHash("sha256").update(data).digest("hex"), node_id: node.id },
    });
    expect(init.status()).toBe(201);
    const session = await init.json();
    const put = await page.request.put(`/bff/companies/${company.id}/uploads/${session.id}/content?offset=0`, { headers: { ...h, "content-type": "application/octet-stream" }, data });
    expect(put.status()).toBe(200);
    return (await page.request.post(`/bff/companies/${company.id}/uploads/${session.id}/complete`, { headers: h, data: {} })).status();
  }
  expect(await upload("note.txt", "Synthetic note for the Control Center view\n")).toBe(200);
  expect(await upload("bad.pdf", "this is not a pdf at all")).toBe(422); // rejected by content inspection, recorded as failed

  await page.goto(`/documents?org=${org}`);
  await expect(page.getByRole("heading", { name: "Documents & Storage", level: 1 })).toBeVisible();
  const row = page.getByRole("row", { name: /Docs Trading LLC/ });
  await expect(row).toContainText("1"); // one stored document
  await expect(row).toContainText("43 B");
  await expect(page.getByText("How storage is counted")).toBeVisible();

  await page.goto(`/companies/${company.id}?org=${org}`);
  await page.getByRole("tab", { name: "Documents" }).click();
  await expect(page.getByRole("row", { name: /note\.txt/ })).toContainText("Inbox");
  await expect(page.getByText("Active documents")).toBeVisible();
  await expect(page.getByText("bad.pdf")).toBeVisible(); // failed uploads panel (managers only)
  await expect(page.getByRole("link", { name: "Documents & Storage" })).toBeVisible();
});
