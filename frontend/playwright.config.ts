import { defineConfig, devices } from "@playwright/test";

import { loadE2EEnv } from "./e2e/helpers/env";

loadE2EEnv();

export default defineConfig({
  expect: {
    timeout: 10_000,
  },
  forbidOnly: Boolean(process.env.CI),
  fullyParallel: false,
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  reporter: [["html"], ["list"]],
  testDir: "./e2e",
  timeout: 360_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3000",
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  workers: process.env.CI ? 2 : 1,
});
