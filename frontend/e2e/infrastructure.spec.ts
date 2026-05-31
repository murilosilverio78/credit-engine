import { expect, test } from "@playwright/test";

test("playwright infrastructure is configured", async ({}, testInfo) => {
  expect(testInfo.project.name).toBe("chromium");
});
