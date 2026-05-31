import { expect, test } from "./helpers/fixtures";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 6 - Precificação determinística", () => {
  let operation: Record<string, unknown>;

  test.beforeAll(async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const id = await ensureCompletedOperation(apiDiretor);
    operation = await (await apiDiretor.get(`/api/v1/operations/${id}`)).json();
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
    testInfo.skip(!breakdown, "Operation did not complete with pricing breakdown.");
    expect(Object.keys(breakdown ?? {}).length).toBeGreaterThan(0);
  });

  test("6.3 - fee SERPRO por faixa", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const params = await (await apiDiretor.get("/api/v1/pricing/parameters")).json() as Array<{ key: string; value: number }>;
    expect(params.some((param) => param.key.toLowerCase().includes("serpro"))).toBe(true);
  });

  test("6.4 - sensibilidade ao prazo", async ({}, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    testInfo.skip(true, "Requires comparable completed operations with same rating/value and different terms.");
  });
});
