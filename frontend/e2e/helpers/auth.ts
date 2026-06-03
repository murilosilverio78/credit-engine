import fs from "node:fs/promises";
import path from "node:path";

import { chromium, expect, type APIRequestContext, type Page } from "@playwright/test";

import { credentialsFor, env, type E2ERole, loadE2EEnv } from "./env";

const authDir = path.join(process.cwd(), "e2e", ".auth");
const storageStateCache = new Map<E2ERole, string>();

export async function loginViaUI(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/operations(?:$|[/?#])/, { timeout: 30_000 });
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem("auth_token")))
    .not.toBeNull();
}

export async function loginViaAPI(
  request: APIRequestContext,
  email: string,
  password: string,
) {
  loadE2EEnv();
  const response = await request.post(`${env("E2E_API_URL")}/api/v1/auth/login`, {
    data: { email, password },
  });

  if (!response.ok()) {
    throw new Error(`API login failed with HTTP ${response.status()}: ${await response.text()}`);
  }

  const data = (await response.json()) as { access_token?: string };
  if (!data.access_token) {
    throw new Error("API login response did not include an access_token.");
  }

  return data.access_token;
}

export async function storageStateFor(role: E2ERole) {
  loadE2EEnv();
  if (storageStateCache.has(role)) {
    return storageStateCache.get(role)!;
  }

  await fs.mkdir(authDir, { recursive: true });
  const storageStatePath = path.join(authDir, `${role}.json`);

  try {
    await fs.access(storageStatePath);
    storageStateCache.set(role, storageStatePath);
    return storageStatePath;
  } catch {
    // Cache miss; generate below.
  }

  const { email, password } = credentialsFor(role);
  const browser = await chromium.launch();
  const context = await browser.newContext({
    baseURL: env("E2E_BASE_URL", "http://localhost:3000"),
  });
  const page = await context.newPage();

  try {
    await loginViaUI(page, email, password);
    await context.storageState({ path: storageStatePath });
  } finally {
    await browser.close();
  }

  storageStateCache.set(role, storageStatePath);
  return storageStatePath;
}
