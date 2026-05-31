import fs from "node:fs/promises";
import path from "node:path";

import { chromium, expect, type APIRequestContext, type Page } from "@playwright/test";

import { credentialsFor, env, type E2ERole, loadE2EEnv } from "./env";

const authDir = path.join(process.cwd(), "e2e", ".auth");
const storageStateCache = new Map<E2ERole, string>();

function cookieHeader(setCookie: string | null) {
  if (!setCookie) {
    throw new Error("Login response did not include a session cookie.");
  }

  const sessionCookie = setCookie
    .split(/,(?=\s*[^;,]+=)/)
    .find((cookie) => cookie.trim().startsWith("session="));

  if (!sessionCookie) {
    throw new Error("Login response did not include the session cookie.");
  }

  return sessionCookie.split(";")[0].trim();
}

export async function loginViaUI(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/operations(?:$|[/?#])/, { timeout: 30_000 });
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

  return cookieHeader(response.headers()["set-cookie"] ?? null);
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
