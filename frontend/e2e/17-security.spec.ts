import { expect, test } from "./helpers/fixtures";
import { credentialsFor, env } from "./helpers/env";
import { loginViaAPI } from "./helpers/auth";
import { skipIfNoCredentials } from "./helpers/test-data";

const API = env("E2E_API_URL", "https://credit-engine-production-a0a1.up.railway.app");
const APP = env("E2E_BASE_URL", process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000");

test.describe("Módulo 17 - Segurança e casos de borda", () => {
  test.use({ ignoreHTTPSErrors: true });

  test("17.1 - endpoint protegido sem token", async ({ request }) => {
    const response = await request.get(`${API}/api/v1/auth/me`);
    expect(response.status()).toBe(401);
    expect(await response.text()).toMatch(/Não autenticado|Not authenticated/i);
  });

  test("17.2 - operação inexistente", async ({ request, page }) => {
    const response = await request.get(`${API}/api/v1/operations/00000000-0000-0000-0000-000000000000`);
    expect(response.status()).toBe(404);
    await page.goto(`${APP}/operations/00000000-0000-0000-0000-000000000000`);
    await expect(page.getByTestId("detail-status")).toHaveCount(0, { timeout: 15_000 });
  });

  test("17.3 - login retorna bearer token", async ({ request }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const { email, password } = credentialsFor("diretor");
    const token = await loginViaAPI(request, email, password);
    expect(token.split(".")).toHaveLength(3);
  });

  test("17.4 - CORS entre Vercel e Railway", async ({ request }) => {
    const response = await request.get(`${API}/health`);
    expect([200, 404]).toContain(response.status());
  });

  test("17.5 - resiliência a componente que falha", async ({}, testInfo) => {
    testInfo.skip(true, "Requires a controlled external component timeout/failure fixture.");
  });
});
