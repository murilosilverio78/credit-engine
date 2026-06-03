import { expect, test } from "./helpers/fixtures";
import { restoreManualComponents } from "./helpers/api";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 6 - Precificação determinística @slow", () => {
  let operation: Record<string, unknown>;

  test.afterAll(async ({ apiDiretor }) => {
    await restoreManualComponents(apiDiretor);
  });

  test.beforeAll(async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    operation = await ensureCompletedOperation(apiDiretor);
  });

  test("6.1 - taxa coerente com o rating", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const matrix = await (await apiDiretor.get("/api/v1/pricing/matrix")).json() as Array<{ rating: string; recusa: boolean; pd_mult: number }>;
    const editable = matrix.filter((row) => !row.recusa);
    expect(editable.map((row) => row.rating)).toEqual(expect.arrayContaining(["A", "B", "C", "D"]));
    expect(editable.find((row) => row.rating === "A")!.pd_mult).toBeLessThan(editable.find((row) => row.rating === "D")!.pd_mult);
  });

  test("6.2 - breakdown da taxa visível", async ({}, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const breakdown = operation.taxa_breakdown as Record<string, unknown> | undefined;
    expect(Object.keys(breakdown ?? {}).length).toBeGreaterThan(0);
  });

  test("6.3 - fee SERPRO por faixa", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const params = await (await apiDiretor.get("/api/v1/pricing/parameters")).json() as Array<{ key: string; value: number }>;
    expect(params.some((param) => param.key.toLowerCase().includes("serpro"))).toBe(true);
  });

  test("6.4 - waterfall mensal converge por fluxo de caixa", async ({}, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const breakdown = operation.taxa_breakdown as Record<string, unknown> | undefined;
    const taxa = operation.taxa_sugerida as number | undefined;
    expect(taxa).toBeGreaterThan(0);
    expect(breakdown).toBeTruthy();
    expect(breakdown?.fluxo_caixa).toEqual(expect.any(Array));
    const detalhes = breakdown?.detalhes as Record<string, number> | undefined;
    expect(Math.abs(Number(detalhes?.residual_fluxo_rs ?? Number.NaN))).toBeLessThan(1);
    expect(Number(detalhes?.total_receita_rs ?? 0)).toBeGreaterThan(0);
  });
});
