import { expect, test } from "./helpers/fixtures";
import { cleanupOrphans, createOperation, getOperation } from "./helpers/api";
import { env } from "./helpers/env";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 19 - Limpeza de operações órfãs", () => {
  test("19.1 - inspecionar órfãs sem alterá-las", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiDiretor.get("/api/v1/admin/orphans");
    expect(response.status()).toBe(200);
    const body = await response.json() as { count: number; operations: unknown[] };
    expect(body).toHaveProperty("count");
    expect(Array.isArray(body.operations)).toBe(true);
  });

  test("19.2 - executar a limpeza", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const result = await cleanupOrphans(apiDiretor) as { cleaned: number; operations: unknown[] };
    expect(result).toHaveProperty("cleaned");
    expect(Array.isArray(result.operations)).toBe(true);
  });

  test("19.3 - operação em processamento normal não é afetada", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const { operation_id } = await createOperation(apiDiretor, { cnpj: env("E2E_CNPJ_VALIDO", "03012610000101") });
    const before = await getOperation(apiDiretor, operation_id) as { status: string };
    await cleanupOrphans(apiDiretor);
    const after = await getOperation(apiDiretor, operation_id) as { status: string };
    expect(after.status).toBe(before.status);
    expect(after.status).not.toBe("error");
  });

  test("19.4 - operação em manual_review não é afetada", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    testInfo.skip(true, "Requires a controlled operation in manual_review.");
    await cleanupOrphans(apiDiretor);
  });

  test("19.5 - restrito a diretor", async ({ apiAnalista }, testInfo) => {
    skipIfNoCredentials(testInfo, "analista");
    const response = await apiAnalista.post("/api/v1/admin/cleanup-orphans");
    expect(response.status()).toBe(403);
  });

  test("19.6 - idempotência", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    await cleanupOrphans(apiDiretor);
    const second = await cleanupOrphans(apiDiretor) as { cleaned: number };
    expect(second.cleaned).toBe(0);
  });
});
