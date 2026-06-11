import { expect, test } from "./helpers/fixtures";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

type OverrideCreateResponse = {
  id?: string;
  override?: {
    id: string;
  };
};

test.describe("Modulo 10 - Overrides @slow", () => {
  let operationId: string;
  let taxaSugerida: number;

  function taxaSugeridaPercent() {
    return taxaSugerida > 1 ? taxaSugerida : taxaSugerida * 100;
  }

  function taxaProposta(deltaPp: number) {
    return Math.max(taxaSugeridaPercent() - deltaPp, 0.01);
  }

  test.beforeAll(async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const operation = await ensureCompletedOperation(apiDiretor);
    operationId = operation.operation_id;
    taxaSugerida = Number(operation.taxa_sugerida);
    expect(Number.isFinite(taxaSugerida)).toBeTruthy();
  });

  test("10.1 - solicitar override de taxa", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}`);
    await diretorPage.getByRole("heading", { name: "Solicitar override" }).scrollIntoViewIfNeeded();
    await expect(diretorPage.getByRole("heading", { name: "Solicitar override" })).toBeVisible();
  });

  test("10.2 - formulario taxa-only", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto(`/operations/${operationId}`);

    await expect(diretorPage.locator('select[name="override_type"]')).toHaveCount(0);
    await expect(diretorPage.locator('input[name="taxa_proposta"]')).toBeVisible();
    await diretorPage.locator('input[name="taxa_proposta"]').fill(taxaProposta(0.1).toFixed(2));
    await expect(diretorPage.locator('textarea[name="justificativa"]')).toBeVisible();
  });

  test("10.3 - override dentro da alcada", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiDiretor.post(`/api/v1/overrides/operations/${operationId}/override`, {
      data: {
        justificativa: "Override valido E2E",
        new_value: taxaProposta(0.1),
        override_type: "taxa",
        previous_value: taxaSugerida,
        requested_by: "playwright",
      },
    });
    expect([200, 201]).toContain(response.status());
  });

  test("10.4 - segregacao de funcoes", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const created = await apiDiretor.post(`/api/v1/overrides/operations/${operationId}/override`, {
      data: {
        justificativa: "Segregacao E2E",
        new_value: taxaProposta(0.1),
        override_type: "taxa",
        previous_value: taxaSugerida,
        requested_by: "same-user",
      },
    });
    testInfo.skip(!created.ok(), "Could not create override fixture.");
    const createdPayload = await created.json() as OverrideCreateResponse;
    const overrideId = createdPayload.override?.id ?? createdPayload.id;
    expect(overrideId).toBeTruthy();

    const review = await apiDiretor.post(`/api/v1/overrides/operations/${operationId}/override/${overrideId}/review`, {
      data: { decision: "approved", reviewed_by: "same-user" },
    });
    expect(review.status()).toBe(400);
  });

  test("10.5 - revisao de override por outro usuario @slow", async ({ apiDiretor, apiAnalista }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    skipIfNoCredentials(testInfo, "analista");

    // Create override as analista.
    const analistaResp = await (await apiAnalista.get("/api/v1/auth/me")).json() as { user: { id: string } };
    const analista = analistaResp.user;
    const created = await apiAnalista.post(`/api/v1/overrides/operations/${operationId}/override`, {
      data: {
        justificativa: "Revisao E2E",
        new_value: taxaProposta(2.0),
        override_type: "taxa",
        previous_value: taxaSugerida,
        requested_by: analista.id,
      },
    });
    if (!created.ok()) {
      testInfo.skip(true, `Could not create override: ${await created.text()}`);
      return;
    }
    const createdPayload = await created.json() as OverrideCreateResponse;
    const overrideId = createdPayload.override?.id ?? createdPayload.id;
    expect(overrideId).toBeTruthy();

    // Review as diretor (different user).
    const diretorResp = await (await apiDiretor.get("/api/v1/auth/me")).json() as { user: { id: string } };
    const diretor = diretorResp.user;
    const review = await apiDiretor.post(
      `/api/v1/overrides/operations/${operationId}/override/${overrideId}/review`,
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
