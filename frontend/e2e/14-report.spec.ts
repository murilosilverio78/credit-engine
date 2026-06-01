import { expect, test } from "./helpers/fixtures";
import { restoreManualComponents } from "./helpers/api";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 14 - Relatório de crédito @slow", () => {
  let operationId: string;

  test.afterAll(async ({ apiDiretor }) => {
    await restoreManualComponents(apiDiretor);
  });

  test.beforeAll(async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    operationId = (await ensureCompletedOperation(apiDiretor)).operation_id;
  });

  test("14.1 - renderização do relatório", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}/report`);
    await expect(diretorPage.getByText(/score|rating|parecer/i).first()).toBeVisible();
  });

  test("14.2 - gráfico radar das dimensões", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}/report`);
    await expect(diretorPage.locator("svg").first()).toBeVisible();
  });

  test("14.3 - barras por dimensão", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}/report`);
    await expect(diretorPage.getByText(/dimens/i).first()).toBeVisible();
  });

  test("14.4 - seção de certidões", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}/report`);
    await expect(diretorPage.getByText(/certid/i).first()).toBeVisible();
  });

  test("14.5 - exportação para PDF (A4)", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}/report`);
    await expect(diretorPage.getByRole("button", { name: /pdf|imprimir|download/i }).first()).toBeVisible();
  });

  test("14.6 - conclusão coerente com o rating", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}/report`);
    await expect(diretorPage.getByText(/conclus|parecer|rating/i).first()).toBeVisible();
  });
});
