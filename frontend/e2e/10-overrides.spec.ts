import { expect, test } from "./helpers/fixtures";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 10 - Overrides @slow", () => {
  let operationId: string;

  test.beforeAll(async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    operationId = (await ensureCompletedOperation(apiDiretor)).operation_id;
  });

  test("10.1 - solicitar override de rating", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}`);
    await diretorPage.getByRole("heading", { name: "Solicitar override" }).scrollIntoViewIfNeeded();
    await expect(diretorPage.getByRole("heading", { name: "Solicitar override" })).toBeVisible();
  });

  test("10.2 - tipos de override", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}`);
    const select = diretorPage.locator('select[name="override_type"]');
    await expect(select).toBeVisible();
    await expect(select.locator("option")).toHaveCount(5);
  });

  test("10.3 - override dentro da alçada", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiDiretor.post(`/api/v1/overrides/operations/${operationId}/override`, {
      data: { justificativa: "Override válido E2E", new_value: "B", override_type: "rating", previous_value: "A", requested_by: "playwright" },
    });
    expect([200, 201]).toContain(response.status());
  });

  test("10.4 - segregação de funções", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const created = await apiDiretor.post(`/api/v1/overrides/operations/${operationId}/override`, {
      data: { justificativa: "Segregação E2E", new_value: "B", override_type: "rating", previous_value: "A", requested_by: "same-user" },
    });
    testInfo.skip(!created.ok(), "Could not create override fixture.");
    const override = await created.json() as { id: string };
    const review = await apiDiretor.post(`/api/v1/overrides/operations/${operationId}/override/${override.id}/review`, {
      data: { decision: "approved", reviewed_by: "same-user" },
    });
    expect(review.status()).toBe(400);
  });

  test("10.5 - revisão de override por outro usuário @slow", async ({ apiDiretor, apiAnalista }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    skipIfNoCredentials(testInfo, "analista");

    // Create override as analista
    const analista = await (await apiAnalista.get("/api/v1/auth/me")).json() as { id: string };
    const created = await apiAnalista.post(`/api/v1/overrides/operations/${operationId}/override`, {
      data: {
        justificativa: "Revisão E2E",
        new_value: "E",
        override_type: "rating",
        previous_value: "B",
        requested_by: analista.id,
      },
    });
    if (!created.ok()) {
      testInfo.skip(true, `Could not create override: ${await created.text()}`);
      return;
    }
    const override = await created.json() as { id: string };

    // Review as diretor (different user)
    const diretor = await (await apiDiretor.get("/api/v1/auth/me")).json() as { id: string };
    const review = await apiDiretor.post(
      `/api/v1/overrides/operations/${operationId}/override/${override.id}/review`,
      { data: { decision: "approved", reviewed_by: diretor.id } },
    );
    expect(review.status()).toBe(200);
  });

  test("10.6 - fila de overrides pendentes", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/overrides");
    await expect(diretorPage.getByTestId("override-row").first().or(diretorPage.getByText("Nenhum override pendente"))).toBeVisible();
  });
});
