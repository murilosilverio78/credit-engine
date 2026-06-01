import { expect, test } from "./helpers/fixtures";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 18 - Parametrização da precificação", () => {
  test("18.1 - acesso restrito ao diretor", async ({ diretorPage, analistaPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor", "analista");
    await diretorPage.goto("/settings/pricing");
    await expect(diretorPage.getByTestId("pricing-param").first()).toBeVisible();
    await analistaPage.goto("/settings/pricing");
    await expect(analistaPage).toHaveURL(/\/forbidden/);
  });

  test("18.2 - listagem dos parâmetros por grupo", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/pricing");
    await expect(diretorPage.getByTestId("pricing-param")).toHaveCount(16);
  });

  test("18.3 - exibição da matriz de rating", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/pricing");
    await expect(diretorPage.getByTestId("pricing-matrix-row")).toHaveCount(5);
    await expect(diretorPage.getByTestId("pricing-matrix-row").filter({ hasText: "E" })).toBeVisible();
  });

  test("18.4 - conversão de percentual amigável", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/pricing");
    await diretorPage.getByRole("button", { name: "Editar" }).first().click();
    const input = diretorPage.locator("input").first();
    await expect(input).toHaveValue(/,/);
  });

  test("18.5 - edição de parâmetro exige justificativa", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const params = await (await apiDiretor.get("/api/v1/pricing/parameters")).json() as Array<{ key: string; value: number }>;
    const response = await apiDiretor.patch(`/api/v1/pricing/parameters/${params[0].key}`, {
      data: { justificativa: "curta", value: params[0].value },
    });
    expect(response.status()).toBe(422);
  });

  test("18.6 - edição de parâmetro escalar", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const params = await (await apiDiretor.get("/api/v1/pricing/parameters")).json() as Array<{ key: string; value: number }>;
    const target = params[0];
    const updated = target.value + 0.0001;
    try {
      const response = await apiDiretor.patch(`/api/v1/pricing/parameters/${target.key}`, {
        data: { justificativa: "Alteração temporária E2E", value: updated },
      });
      expect(response.status()).toBe(200);
    } finally {
      await apiDiretor.patch(`/api/v1/pricing/parameters/${target.key}`, {
        data: { justificativa: "Restauração temporária E2E", value: target.value },
      });
    }
  });

  test("18.7 - edição da matriz de rating (A-D)", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const matrix = await (await apiDiretor.get("/api/v1/pricing/matrix")).json() as Array<{
      bond_cobertura: number;
      bond_premio_aa: number | null;
      lgd_mult: number;
      pd_mult: number;
      perfil: string | null;
      rating: string;
      recusa: boolean;
    }>;
    const target = matrix.find((row) => row.rating === "C" && !row.recusa) ?? matrix.find((row) => !row.recusa);
    if (!target) {
      testInfo.skip(true, "No editable pricing matrix row available.");
      return;
    }
    const updatedPerfil = `${target.perfil ?? "E2E"} teste`;
    try {
      const response = await apiDiretor.patch(`/api/v1/pricing/matrix/${target.rating}`, {
        data: { justificativa: "Alteração temporária E2E", perfil: updatedPerfil },
      });
      expect(response.status()).toBe(200);
    } finally {
      await apiDiretor.patch(`/api/v1/pricing/matrix/${target.rating}`, {
        data: {
          bond_cobertura: target.bond_cobertura,
          bond_premio_aa: target.bond_premio_aa,
          justificativa: "Restauração temporária E2E",
          lgd_mult: target.lgd_mult,
          pd_mult: target.pd_mult,
          perfil: target.perfil,
        },
      });
    }
  });

  test("18.8 - rating E protegido contra edição", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiDiretor.patch("/api/v1/pricing/matrix/E", {
      data: { bond_premio_aa: 0.08, justificativa: "teste deve falhar" },
    });
    expect(response.status()).toBe(422);
    expect(await response.text()).toMatch(/recusa estrutural/i);
  });

  test("18.9 - alteração só por diretor (backend)", async ({ apiAnalista }, testInfo) => {
    skipIfNoCredentials(testInfo, "analista");
    const response = await apiAnalista.patch("/api/v1/pricing/parameters/cdi_mensal", {
      data: { justificativa: "Tentativa bloqueada E2E", value: 0.01 },
    });
    expect(response.status()).toBe(403);
  });

  test("18.10 - efeito da alteração no cálculo", async ({}, testInfo) => {
    testInfo.skip(true, "Requires safely mutating pricing parameters and comparing new operations.");
  });

  test("18.11 - cache de 60 segundos", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/pricing");
    await expect(diretorPage.getByText(/até 60 segundos/i)).toBeVisible();
  });

  test("18.12 - histórico de alterações", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await diretorPage.goto("/settings/pricing");
    await expect(diretorPage.getByText("Histórico")).toBeVisible();
  });

  test("18.13 - coerência com a planilha de origem", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const params = await (await apiDiretor.get("/api/v1/pricing/parameters")).json() as unknown[];
    const matrix = await (await apiDiretor.get("/api/v1/pricing/matrix")).json() as unknown[];
    expect(params.length).toBe(16);
    expect(matrix.length).toBe(5);
  });
});
