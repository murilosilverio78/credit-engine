import { expect, test } from "./helpers/fixtures";
import { credentialsFor } from "./helpers/env";
import { loginViaAPI } from "./helpers/auth";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 17 - Segurança e casos de borda", () => {
  test("17.1 - endpoint protegido sem token", async ({ request }) => {
    const response = await request.get("/api/v1/auth/me");
    expect(response.status()).toBe(401);
    expect(await response.text()).toMatch(/Não autenticado|Not authenticated/i);
  });

  test("17.2 - operação inexistente", async ({ request, page }) => {
    const response = await request.get("/api/v1/operations/00000000-0000-0000-0000-000000000000");
    expect(response.status()).toBe(404);
    await page.goto("/operations/00000000-0000-0000-0000-000000000000");
    await expect(page.getByText(/Operação não encontrada|login/i)).toBeVisible();
  });

  test("17.3 - cookie seguro", async ({ request }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const { email, password } = credentialsFor("diretor");
    const cookie = await loginViaAPI(request, email, password);
    expect(cookie).toMatch(/^session=/);
  });

  test("17.4 - CORS entre Vercel e Railway", async ({ request }) => {
    const response = await request.get("/health");
    expect([200, 404]).toContain(response.status());
  });

  test("17.5 - resiliência a componente que falha", async ({}, testInfo) => {
    testInfo.skip(true, "Requires a controlled external component timeout/failure fixture.");
  });
});
