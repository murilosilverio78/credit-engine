import { expect, test } from "./helpers/fixtures";
import { restoreManualComponents } from "./helpers/api";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 7 - Limite aprovado @slow", () => {
  test.afterAll(async ({ apiDiretor }) => {
    await restoreManualComponents(apiDiretor);
  });

  async function completedLimit(apiDiretor: Parameters<typeof ensureCompletedOperation>[0], payload: Record<string, unknown>) {
    return ensureCompletedOperation(apiDiretor, payload) as Promise<{ limite_aprovado?: number; rating?: string; status: string }>;
  }

  test("7.1 - limite calculado pelo saldo @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const operation = await completedLimit(apiDiretor, { contrato_saldo: 800000, valor_solicitado: 500000 });
    expect(operation.status).toBe("completed");
    expect(operation.limite_aprovado).toBeLessThanOrEqual(500000);
  });

  test("7.2 - limite capado pelo teto @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const operation = await completedLimit(apiDiretor, { contrato_saldo: 600000, valor_solicitado: 420000 });
    expect(operation.status).toBe("completed");
    expect(operation.limite_aprovado).toBeLessThanOrEqual(420000);
  });

  test("7.3 - limite por rating mais baixo", async ({}, testInfo) => {
    testInfo.skip(true, "Requires deterministic fixtures producing rating C and D.");
  });

  test("7.4 - fallback sem saldo @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const operation = await completedLimit(apiDiretor, { valor_solicitado: 10000 });
    expect(operation.status).toBe("completed");
    expect(operation.limite_aprovado).not.toBeUndefined();
  });

  test("7.5 - rating E → limite zero", async ({}, testInfo) => {
    testInfo.skip(true, "Requires a known CNPJ that deterministically produces rating E.");
  });
});
