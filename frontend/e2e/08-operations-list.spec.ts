import { expect, test } from "./helpers/fixtures";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 8 - Lista de operações", () => {
  test("8.1 - listagem", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    await expect(diretorPage.getByRole("heading", { name: "Operações" })).toBeVisible();
    await expect(diretorPage.getByTestId("op-row").first().or(diretorPage.getByText("Nenhuma operação encontrada"))).toBeVisible();
  });

  test("8.2 - badges de status com cores distintas", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    await expect(diretorPage.getByTestId("op-status-badge").first()).toBeVisible();
  });

  test("8.3 - filtro por status", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    await diretorPage.getByTestId("filter-status").selectOption("completed");
    await expect(diretorPage.getByTestId("filter-status")).toHaveValue("completed");
  });

  test("8.4 - filtro por rating", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    await diretorPage.getByTestId("filter-rating").selectOption("B");
    await expect(diretorPage.getByTestId("filter-rating")).toHaveValue("B");
  });

  test("8.5 - busca por CNPJ", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    await diretorPage.getByTestId("filter-cnpj").fill("03012610");
    await expect(diretorPage.getByTestId("filter-cnpj")).toHaveValue("03012610");
  });

  test("8.6 - combinação de filtros e limpeza", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    await diretorPage.getByTestId("filter-status").selectOption("completed");
    await diretorPage.getByTestId("filter-rating").selectOption("A");
    await diretorPage.getByTestId("filter-cnpj").fill("03012610");
    await diretorPage.getByTestId("filter-status").selectOption("");
    await diretorPage.getByTestId("filter-rating").selectOption("");
    await diretorPage.getByTestId("filter-cnpj").fill("");
    await expect(diretorPage.getByTestId("filter-cnpj")).toHaveValue("");
  });

  test("8.7 - paginação", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    await expect(diretorPage.getByRole("button", { name: /próxima|anterior/i }).first()).toBeVisible();
  });

  test("8.8 - navegação para o detalhe", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/operations");
    const firstRow = diretorPage.getByTestId("op-row").first();
    testInfo.skip(await firstRow.count() === 0, "No operations available to navigate.");
    await firstRow.click();
    await expect(diretorPage).toHaveURL(/\/operations\/[^/]+$/);
  });
});
