import { expect, test } from "./helpers/fixtures";
import { credentialsFor, optionalEnv } from "./helpers/env";
import { loginViaUI } from "./helpers/auth";
import { apiPost, skipIfNoCredentials, uniqueEmail } from "./helpers/test-data";

test.describe("Módulo 1 - Autenticação", () => {
  test.skip(!optionalEnv("E2E_BASE_URL"), "Set E2E_BASE_URL or run the frontend locally for UI auth tests.");

  function skipIfNoFrontend(testInfo: Parameters<typeof skipIfNoCredentials>[0]) {
    testInfo.skip(!optionalEnv("E2E_BASE_URL"), "Set E2E_BASE_URL or run the frontend locally for UI auth tests.");
  }

  test("1.1 - login com credenciais válidas", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    skipIfNoCredentials(testInfo, "diretor");
    const { email, password } = credentialsFor("diretor");
    await loginViaUI(page, email, password);
    await expect(page).toHaveURL(/\/operations/);
    const session = await page.context().cookies();
    expect(session.some((cookie) => cookie.name === "session" && cookie.httpOnly)).toBe(true);
    await expect(page.getByText(email).or(page.getByText(/Credit Engine/))).toBeVisible();
  });

  test("1.2 - login com senha incorreta", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    skipIfNoCredentials(testInfo, "diretor");
    const { email } = credentialsFor("diretor");
    await page.goto("/login");
    await page.getByTestId("login-email").fill(email);
    await page.getByTestId("login-password").fill("senha-incorreta");
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("login-error")).toContainText("Email ou senha inválidos");
    await expect(page).toHaveURL(/\/login/);
  });

  test("1.3 - login com email não confirmado", async ({ page, apiDiretor }, testInfo) => {
    skipIfNoFrontend(testInfo);
    skipIfNoCredentials(testInfo, "diretor");
    const email = uniqueEmail("unverified");
    const password = "Temp123456!";
    await apiPost(apiDiretor, "/api/v1/auth/register", { email, name: "Unverified E2E", password, role: "analista" });
    await page.goto("/login");
    await page.getByTestId("login-email").fill(email);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("login-error")).toContainText(/email.*não confirmado/i);
    await expect(page.getByTestId("login-resend")).toBeVisible();
  });

  test("1.4 - reenvio de confirmação", async ({ page, apiDiretor }, testInfo) => {
    skipIfNoFrontend(testInfo);
    skipIfNoCredentials(testInfo, "diretor");
    const email = uniqueEmail("resend");
    const password = "Temp123456!";
    await apiPost(apiDiretor, "/api/v1/auth/register", { email, name: "Resend E2E", password, role: "analista" });
    await page.goto("/login");
    await page.getByTestId("login-email").fill(email);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();
    await page.getByTestId("login-resend").click();
    await expect(page.getByText("Novo link enviado")).toBeVisible();
  });

  test("1.5 - confirmação de email", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    const token = optionalEnv("E2E_CONFIRMATION_TOKEN");
    testInfo.skip(!token, "Provide E2E_CONFIRMATION_TOKEN to validate a real confirmation link.");
    await page.goto(`/auth/confirm-email?token=${token}`);
    await expect(page.getByTestId("confirm-status")).toContainText("Email confirmado");
    await expect(page.getByTestId("confirm-goto-login")).toBeVisible();
  });

  test("1.6 - token de confirmação inválido ou expirado", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    await page.goto("/auth/confirm-email?token=invalido123");
    await expect(page.getByTestId("confirm-status")).toContainText("Link inválido ou expirado");
    await expect(page.getByTestId("confirm-goto-login")).toBeVisible();
  });

  test("1.7 - confirmação sem token", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    await page.goto("/auth/confirm-email");
    await expect(page.getByTestId("confirm-status")).toContainText("Link inválido ou expirado");
  });

  test("1.8 - botão de login desabilitado com campos vazios", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    await page.goto("/login");
    await expect(page.getByTestId("login-submit")).toBeDisabled();
    await page.getByTestId("login-email").fill("user@example.com");
    await expect(page.getByTestId("login-submit")).toBeDisabled();
    await page.getByTestId("login-password").fill("secret");
    await expect(page.getByTestId("login-submit")).toBeEnabled();
  });

  test("1.9 - logout", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    skipIfNoCredentials(testInfo, "diretor");
    const { email, password } = credentialsFor("diretor");
    await loginViaUI(page, email, password);
    await page.getByRole("button", { name: "Sair" }).click();
    await expect(page).toHaveURL(/\/login/);
    expect((await page.context().cookies()).some((cookie) => cookie.name === "session")).toBe(false);
  });

  test("1.10 - acesso a rota protegida sem sessão", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    skipIfNoCredentials(testInfo, "diretor");
    await page.goto("/operations");
    await expect(page).toHaveURL(/\/login\?next=/);
    const { email, password } = credentialsFor("diretor");
    await page.getByTestId("login-email").fill(email);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();
    await expect(page).toHaveURL(/\/operations/);
  });

  test("1.11 - sessão expirada", async ({ page }, testInfo) => {
    skipIfNoFrontend(testInfo);
    await page.context().addCookies([{
      domain: new URL(test.info().config.projects[0].use.baseURL as string).hostname,
      expires: Math.floor(Date.now() / 1000) - 60,
      httpOnly: true,
      name: "session",
      path: "/",
      sameSite: "Lax",
      secure: true,
      value: "expired",
    }]);
    await page.goto("/operations");
    await expect(page).toHaveURL(/\/login/);
  });

});
