import { expect, test } from "./helpers/fixtures";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 11 - Escaladas", () => {
  test("11.1 - fila de escaladas", async ({ gerentePage }, testInfo) => {
    skipIfNoCredentials(testInfo, "gerente");
    await gerentePage.goto("/escaladas");
    await expect(gerentePage.getByTestId("escalada-row").first().or(gerentePage.getByText("Nenhuma escalada pendente"))).toBeVisible();
  });

  test("11.2 - resolver escalada — aprovar", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const pending = await (await apiDiretor.get("/api/v1/escaladas/pendentes")).json() as Array<{ id: string; operation_id: string }>;
    testInfo.skip(pending.length === 0, "No pending escalations available.");
    const response = await apiDiretor.post(`/api/v1/operations/${pending[0].operation_id}/resolve-escalation`, {
      data: { action: "escalation_approved", approval_id: pending[0].id, justificativa: "Escalada aprovada E2E" },
    });
    expect(response.status()).toBe(200);
  });

  test("11.3 - resolver escalada — rejeitar exige justificativa", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const pending = await (await apiDiretor.get("/api/v1/escaladas/pendentes")).json() as Array<{ id: string; operation_id: string }>;
    testInfo.skip(pending.length === 0, "No pending escalations available.");
    const response = await apiDiretor.post(`/api/v1/operations/${pending[0].operation_id}/resolve-escalation`, {
      data: { action: "escalation_rejected", approval_id: pending[0].id, justificativa: "curta" },
    });
    expect(response.status()).toBe(400);
  });
});
