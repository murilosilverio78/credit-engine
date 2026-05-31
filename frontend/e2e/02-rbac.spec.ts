import { expect, test } from "./helpers/fixtures";
import { apiPost, skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 2 - Controle de acesso por papel (RBAC)", () => {
  test("2.1 - diretor acessa configurações", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/alcadas");
    await expect(diretorPage.getByTestId("alcada-row").first()).toBeVisible();
    await diretorPage.goto("/settings/users");
    await expect(diretorPage.getByTestId("user-submit")).toBeVisible();
    await expect(diretorPage.getByTestId("nav-users")).toBeVisible();
    await expect(diretorPage.getByTestId("nav-alcadas")).toBeVisible();
  });

  test("2.2 - não-diretor bloqueado em alçadas", async ({ analistaPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "analista");
    await analistaPage.goto("/settings/alcadas");
    await expect(analistaPage).toHaveURL(/\/forbidden/);
  });

  test("2.3 - não-diretor não vê item de menu de gestão", async ({ analistaPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "analista");
    await analistaPage.goto("/operations");
    await expect(analistaPage.getByTestId("nav-users")).toHaveCount(0);
    await expect(analistaPage.getByTestId("nav-alcadas")).toHaveCount(0);
  });

  test("2.4 - criação de usuário só por diretor (backend)", async ({ apiAnalista }, testInfo) => {
    skipIfNoCredentials(testInfo, "analista");
    const response = await apiPost(apiAnalista, "/api/v1/auth/register", {
      email: `rbac.${Date.now()}@example.com`,
      name: "RBAC E2E",
      password: "Temp123456!",
      role: "analista",
    });
    expect(response.status()).toBe(403);
    expect(await response.text()).toMatch(/Acesso negado/);
  });
});
