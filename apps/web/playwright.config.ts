import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  outputDir: process.env.E2E_OUTPUT_DIR ?? "test-results",
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3100",
    // The sandbox ships a pre-installed Chromium; CI installs its own via `playwright install`.
    launchOptions: process.env.E2E_CHROMIUM_PATH ? { executablePath: process.env.E2E_CHROMIUM_PATH } : {},
  },
});
