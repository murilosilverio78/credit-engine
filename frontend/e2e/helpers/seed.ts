import type { APIRequestContext } from "@playwright/test";

import {
  createOperation,
  disableManualComponents,
  getManualComponentsState,
  type OperationPayload,
  restoreManualComponents,
  waitForStatus,
} from "./api";
import { env } from "./env";

type CompletedOperation = {
  operation_id: string;
  status?: string;
  taxa_sugerida?: number | string | null;
  [key: string]: unknown;
};

export async function ensureCompletedOperation(
  requestContext: APIRequestContext,
  overrides: Partial<OperationPayload> = {},
) {
  const original = await getManualComponentsState(requestContext);
  try {
    await disableManualComponents(requestContext);
    const { operation_id } = await createOperation(requestContext, {
      cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
      valor_solicitado: 500_000,
      contrato_saldo: 800_000,
      prazo_dias: 180,
      ...overrides,
    });
    const operation = await waitForStatus(requestContext, operation_id, ["completed"], 360_000) as CompletedOperation;
    return { ...operation, operation_id };
  } finally {
    await restoreManualComponents(requestContext, original);
  }
}
