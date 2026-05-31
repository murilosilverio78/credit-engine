import { expect, test } from "./helpers/fixtures";
import { createOperation } from "./helpers/api";
import { env } from "./helpers/env";
import { apiPost, skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 4 - Nova operação e gate de elegibilidade", () => {
  test("4.1 - criação de operação válida", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const created = await createOperation(apiDiretor, {
      cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
      contrato_saldo: 800000,
      prazo_dias: 60,
      valor_solicitado: 10000,
    });
    expect(created.operation_id).toBeTruthy();
  });

  test("4.2 - validação de CNPJ", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations/new");
    await diretorPage.getByTestId("op-cnpj").fill("11111111111111");
    await diretorPage.getByTestId("op-submit").click();
    await expect(diretorPage.getByTestId("op-error")).toContainText("Informe um CNPJ válido.");
  });

  test("4.3 - gate de elegibilidade — valor abaixo do mínimo", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiPost(apiDiretor, "/api/v1/operations/", {
      cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
      valor_solicitado: 9999,
    });
    expect(response.status()).toBe(422);
    expect(await response.text()).toMatch(/ELIGIBILITY_FAILED|valor/i);
  });

  test("4.4 - gate de elegibilidade — excede 70% do saldo", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiPost(apiDiretor, "/api/v1/operations/", {
      cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
      contrato_saldo: 800000,
      valor_solicitado: 600000,
    });
    expect(response.status()).toBe(422);
    expect(await response.text()).toMatch(/70|limite/i);
  });

  test("4.5 - gate de elegibilidade — prazo abaixo do mínimo", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiPost(apiDiretor, "/api/v1/operations/", {
      cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
      prazo_dias: 59,
      valor_solicitado: 10000,
    });
    expect(response.status()).toBe(422);
    expect(await response.text()).toMatch(/prazo/i);
  });

  test("4.6 - limite exato (70%) é aceito", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiPost(apiDiretor, "/api/v1/operations/", {
      cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
      contrato_saldo: 800000,
      valor_solicitado: 560000,
    });
    expect(response.status()).toBe(201);
  });

  test("4.7 - campos opcionais", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiPost(apiDiretor, "/api/v1/operations/", {
      cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
      valor_solicitado: 10000,
    });
    expect(response.status()).toBe(201);
  });

  test("4.8 - prazo deve ser inteiro positivo", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations/new");
    await diretorPage.getByTestId("op-cnpj").fill("03.012.610/0001-01");
    await diretorPage.getByTestId("op-prazo").fill("0");
    await diretorPage.getByTestId("op-submit").click();
    await expect(diretorPage.getByTestId("op-error")).toContainText("Informe um prazo inteiro maior que zero.");
  });
});
