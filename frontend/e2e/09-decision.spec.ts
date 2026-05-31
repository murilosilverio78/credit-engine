import { expect, test } from "./helpers/fixtures";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 9 - Detalhe da operação e decisão", () => {
  let operationId: string;

  test.beforeAll(async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    operationId = await ensureCompletedOperation(apiDiretor);
  });

  test("9.1 - visão completa", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}`);
    await expect(diretorPage.getByTestId("detail-status")).toBeVisible();
    await expect(diretorPage.getByTestId("snapshot-row").first()).toBeVisible();
    await expect(diretorPage.getByTestId("detail-score")).toBeVisible();
    await expect(diretorPage.getByTestId("detail-taxa")).toBeVisible();
    await expect(diretorPage.getByTestId("detail-limite")).toBeVisible();
  });

  test("9.2 - botões de decisão conforme alçada", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}`);
    await expect(diretorPage.getByTestId("action-approve").or(diretorPage.getByTestId("action-escalate"))).toBeVisible();
  });

  test("9.3 - aprovar operação", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const id = await ensureCompletedOperation(apiDiretor);
    const response = await apiDiretor.post(`/api/v1/operations/${id}/approve`, { data: {} });
    expect(response.status()).toBe(200);
    const operation = await (await apiDiretor.get(`/api/v1/operations/${id}`)).json() as { status: string };
    expect(operation.status).toBe("approved");
  });

  test("9.4 - rejeitar exige justificativa", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const id = await ensureCompletedOperation(apiDiretor);
    const response = await apiDiretor.post(`/api/v1/operations/${id}/reject`, { data: { justificativa: "curta" } });
    expect(response.status()).toBe(400);
  });

  test("9.5 - rejeitar com justificativa", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const id = await ensureCompletedOperation(apiDiretor);
    const response = await apiDiretor.post(`/api/v1/operations/${id}/reject`, { data: { justificativa: "Justificativa válida E2E" } });
    expect(response.status()).toBe(200);
    const operation = await (await apiDiretor.get(`/api/v1/operations/${id}`)).json() as { status: string };
    expect(operation.status).toBe("rejected");
  });

  test("9.6 - escalar operação acima da alçada", async ({ analistaPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "analista", "diretor");
    testInfo.skip(true, "Requires a completed operation exceeding the analyst approval threshold.");
    await analistaPage.goto(`/operations/${operationId}`);
  });

  test("9.7 - decisão só em operação concluída", async ({ apiDiretor, diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const created = await apiDiretor.post("/api/v1/operations/", { data: { cnpj: "03012610000101", source: "playwright_e2e" } });
    const { operation_id } = await created.json() as { operation_id: string };
    await diretorPage.goto(`/operations/${operation_id}`);
    await expect(diretorPage.getByTestId("action-approve")).toHaveCount(0);
  });

  test("9.8 - polling durante processamento", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}`);
    await expect(diretorPage.getByTestId("snapshot-row").first()).toBeVisible();
  });
});
