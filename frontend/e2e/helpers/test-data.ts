import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";

import { hasCredentials, optionalEnv, type E2ERole } from "./env";

export function skipIfNoCredentials(testInfo: TestInfo, ...roles: E2ERole[]) {
  const missing = roles.filter((role) => !hasCredentials(role));
  if (missing.length) {
    testInfo.skip(true, `Missing E2E credentials for: ${missing.join(", ")}`);
  }
}

export function skipIfNoEnv(testInfo: TestInfo, ...names: string[]) {
  const missing = names.filter((name) => !optionalEnv(name));
  if (missing.length) {
    testInfo.skip(true, `Missing E2E env vars: ${missing.join(", ")}`);
  }
}

export function uniqueEmail(prefix: string) {
  return `${prefix}.${Date.now()}.${Math.random().toString(36).slice(2)}@example.test`;
}

export async function expectStatus(responsePromise: Promise<{ status: () => number }>, status: number) {
  const response = await responsePromise;
  expect(response.status()).toBe(status);
  return response;
}

export async function apiGet(request: APIRequestContext, path: string) {
  return request.get(path);
}

export async function apiPost(request: APIRequestContext, path: string, data?: unknown) {
  return request.post(path, { data });
}

export async function apiPatch(request: APIRequestContext, path: string, data?: unknown) {
  return request.patch(path, { data });
}

export async function gotoAndExpect(page: Page, path: string, testId?: string) {
  await page.goto(path);
  if (testId) {
    await expect(page.getByTestId(testId).first()).toBeVisible();
  }
}
