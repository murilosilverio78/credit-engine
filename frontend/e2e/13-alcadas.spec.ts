import { expect, test } from "./helpers/fixtures";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 13 - Alçadas", () => {
  test("13.1 - visualização das alçadas", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/alcadas");
    await expect(diretorPage.getByTestId("alcada-row")).toHaveCount(3);
  });

  test("13.2 - alteração de alçada", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/alcadas");
    await diretorPage.getByRole("button", { name: "Editar" }).first().click();
    await diretorPage.getByTestId("alcada-justificativa").first().fill("Alteração de teste E2E");
    await expect(diretorPage.getByTestId("alcada-save").first()).toBeEnabled();
  });

  test("13.3 - alteração só por diretor", async ({ apiAnalista }, testInfo) => {
    skipIfNoCredentials(testInfo, "analista");
    const response = await apiAnalista.patch("/api/v1/alcadas/analista", {
      data: { justificativa: "Tentativa bloqueada E2E", max_valor: 1 },
    });
    expect(response.status()).toBe(403);
  });

  test("13.4 - auditoria de alçadas", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiDiretor.get("/api/v1/alcadas/audit");
    expect(response.status()).toBe(200);
    expect(Array.isArray(await response.json())).toBe(true);
  });

  test("13.5 - efeito da alçada na decisão", async ({}, testInfo) => {
    testInfo.skip(true, "Requires mutating role thresholds and a controlled completed operation.");
  });
});
