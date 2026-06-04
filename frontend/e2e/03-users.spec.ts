import { expect, test } from "./helpers/fixtures";
import { apiPost, skipIfNoCredentials, uniqueEmail } from "./helpers/test-data";

test.describe("Módulo 3 - Gestão de usuários", () => {
  test("3.1 - criar novo usuário", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const email = uniqueEmail("user-ui");
    await diretorPage.goto("/settings/users");
    await diretorPage.getByTestId("user-name").fill("Usuário E2E");
    await diretorPage.getByTestId("user-email").fill(email);
    await diretorPage.getByTestId("user-role").selectOption("analista");
    await diretorPage.getByTestId("user-password").fill("Temp123456!");
    await diretorPage.getByTestId("user-submit").click();
    await expect(diretorPage.getByTestId("user-message-success")).toContainText(`Usuário criado. Um email de confirmação foi enviado para ${email}.`, { timeout: 45000 });
    await expect(diretorPage.getByTestId("user-name")).toHaveValue("");
    await expect(diretorPage.getByTestId("user-email")).toHaveValue("");
  });

  test("3.2 - email duplicado", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const email = uniqueEmail("duplicate");
    const payload = { email, name: "Duplicado E2E", password: "Temp123456!", role: "analista" };
    expect((await apiPost(apiDiretor, "/api/v1/auth/register", payload)).status()).toBe(201);
    const duplicate = await apiPost(apiDiretor, "/api/v1/auth/register", payload);
    expect(duplicate.status()).toBe(409);
    expect(await duplicate.text()).toMatch(/Email já cadastrado/);
  });

  test("3.3 - papel inválido", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiPost(apiDiretor, "/api/v1/auth/register", {
      email: uniqueEmail("role"),
      name: "Role Inválido",
      password: "Temp123456!",
      role: "root",
    });
    expect(response.status()).toBe(422);
  });

  test("3.4 - fluxo completo de onboarding", async ({ apiDiretor, page }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    testInfo.skip(true, "Requires reading the email confirmation link from the mailbox used in the test environment.");
    const email = uniqueEmail("onboarding");
    await apiPost(apiDiretor, "/api/v1/auth/register", { email, name: "Onboarding", password: "Temp123456!", role: "analista" });
    await page.goto("/login");
  });
});
