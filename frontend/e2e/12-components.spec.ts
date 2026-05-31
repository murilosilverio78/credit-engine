import { expect, test } from "./helpers/fixtures";
import { createOperation, waitForStatus } from "./helpers/api";
import { env } from "./helpers/env";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 12 - Componentes", () => {
  test("12.1 - listagem de componentes", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/components");
    await expect(diretorPage.getByTestId("component-row").first()).toBeVisible();
  });

  test("12.2 - componentes de roadmap inativos", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/components");
    for (const component of ["serasa_pj", "boa_vista", "serpro"]) {
      const row = diretorPage.getByTestId("component-row").filter({ hasText: component });
      await expect(row).toHaveClass(/opacity-40/);
      await expect(row.getByTestId("component-toggle")).toBeDisabled();
    }
  });

  test("12.3 - habilitar/desabilitar componente", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/components");
    const toggle = diretorPage.getByTestId("component-row").filter({ hasNotText: "serasa_pj" }).first().getByTestId("component-toggle");
    const checkedBefore = await toggle.getAttribute("data-state");
    await toggle.click();
    await expect(toggle).not.toHaveAttribute("data-state", checkedBefore ?? "");
  });

  test("12.4 - efeito do toggle no pipeline @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const components = await (await apiDiretor.get("/api/v1/components/")).json() as Array<{ component: string; enabled: boolean }>;
    const target = components.find((component) => component.component === "cepim");
    testInfo.skip(!target, "Component cepim not available.");
    await apiDiretor.patch("/api/v1/components/cepim/toggle?enabled=false");
    const { operation_id } = await createOperation(apiDiretor, { cnpj: env("E2E_CNPJ_VALIDO", "03012610000101") });
    const operation = await waitForStatus(apiDiretor, operation_id, ["completed", "manual_review"], 360_000) as { components?: Array<{ component: string }> };
    expect((operation.components ?? []).some((component) => component.component === "cepim")).toBe(false);
    await apiDiretor.patch(`/api/v1/components/cepim/toggle?enabled=${target.enabled}`);
  });
});
