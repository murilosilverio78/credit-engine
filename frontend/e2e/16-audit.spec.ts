import { expect, test } from "./helpers/fixtures";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 16 - Auditoria e governança", () => {
  test("16.1 - trilha de auditoria registra decisões", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const id = await ensureCompletedOperation(apiDiretor);
    await apiDiretor.post(`/api/v1/operations/${id}/approve`, { data: {} });
    const pricingAudit = await (await apiDiretor.get("/api/v1/pricing/audit")).json();
    expect(Array.isArray(pricingAudit)).toBe(true);
  });

  test("16.2 - imutabilidade da auditoria", async ({}, testInfo) => {
    testInfo.skip(true, "Requires direct database permissions; not exposed through the public API.");
  });

  test("16.3 - score_contrib por componente", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const id = await ensureCompletedOperation(apiDiretor);
    const operation = await (await apiDiretor.get(`/api/v1/operations/${id}`)).json() as { components?: Array<{ score_contrib: number | null }> };
    const contributions = (operation.components ?? []).map((component) => component.score_contrib).filter((value) => value !== null);
    expect(contributions.length).toBeGreaterThan(0);
  });
});
