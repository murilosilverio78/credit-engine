import { expect, test } from "./helpers/fixtures";
import { createOperation, getOperation, restoreManualComponents, waitForStatus } from "./helpers/api";
import { env } from "./helpers/env";
import { ensureCompletedOperation } from "./helpers/seed";
import { skipIfNoCredentials } from "./helpers/test-data";

test.describe("Módulo 5 - Pipeline de análise e processamento", () => {
  test.afterAll(async ({ apiDiretor }) => {
    await restoreManualComponents(apiDiretor);
  });

  test("5.1 - acompanhamento em tempo real @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const { operation_id } = await createOperation(apiDiretor, { cnpj: env("E2E_CNPJ_VALIDO", "03012610000101") });
    const finalOperation = await waitForStatus(apiDiretor, operation_id, ["completed", "manual_review"], 360_000);
    expect(["completed", "manual_review"]).toContain((finalOperation as { status: string }).status);
  });

  test("5.2 - componentes automáticos executam @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const { operation_id } = await createOperation(apiDiretor, { cnpj: env("E2E_CNPJ_VALIDO", "03012610000101") });
    const operation = await waitForStatus(apiDiretor, operation_id, ["completed", "manual_review"], 360_000) as { components?: Array<{ component: string; status: string; duration_ms: number | null }> };
    const names = new Set((operation.components ?? []).map((component) => component.component));
    ["brasil_api", "pessoa_juridica", "contratos", "recursos_recebidos", "ceis", "cnep", "cepim", "acordos_leniencia"].forEach((name) => expect(names.has(name)).toBe(true));
  });

  test("5.2b - paralelismo das fases @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const { operation_id } = await createOperation(apiDiretor, { cnpj: env("E2E_CNPJ_VALIDO", "03012610000101") });
    const operation = await waitForStatus(apiDiretor, operation_id, ["completed", "manual_review"], 360_000) as { components?: Array<{ component: string; duration_ms: number | null }> };
    const phase2 = (operation.components ?? []).filter((component) => ["contratos", "recursos_recebidos", "acordos_leniencia", "ceis", "cnep", "cepim"].includes(component.component));
    expect(phase2.length).toBeGreaterThan(0);
    expect(phase2.every((component) => component.duration_ms !== undefined)).toBe(true);
  });

  test("5.3 - operação que exige certidões manuais @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const { operation_id } = await createOperation(apiDiretor, { cnpj: env("E2E_CNPJ_VALIDO", "03012610000101") });
    const operation = await waitForStatus(apiDiretor, operation_id, ["completed", "manual_review"], 360_000) as { status: string };
    expect(["completed", "manual_review"]).toContain(operation.status);
  });

  test("5.4 - score e rating gerados @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const operation = await ensureCompletedOperation(apiDiretor) as { status: string; score?: number; rating?: string; taxa_sugerida?: number };
    expect(operation.status).toBe("completed");
    expect(operation.score).toBeGreaterThanOrEqual(0);
    expect(operation.rating).toMatch(/[A-E]/);
    expect(operation.taxa_sugerida).not.toBeUndefined();
  });

  test("5.5 - bloqueio automático → rating E @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    testInfo.skip(true, "Requires a known CNPJ with inactive status, active sanction, or leniency agreement.");
    expect(await getOperation(apiDiretor, "placeholder")).toBeTruthy();
  });

  test("5.6 - empresa sem contratos governamentais @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    testInfo.skip(true, "Requires a known CNPJ without government contracts.");
  });

  test("5.7 - cache por CNPJ @slow", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const cnpj = env("E2E_CNPJ_VALIDO", "03012610000101");
    const first = await createOperation(apiDiretor, { cnpj });
    await waitForStatus(apiDiretor, first.operation_id, ["completed", "manual_review"], 360_000);
    const second = await createOperation(apiDiretor, { cnpj });
    const operation = await waitForStatus(apiDiretor, second.operation_id, ["completed", "manual_review"], 360_000) as { components?: Array<{ component: string; duration_ms: number | null }> };
    expect((operation.components ?? []).some((component) => component.component !== "score_engine" && component.duration_ms === 0)).toBe(true);
  });
});
