import type { APIRequestContext } from "@playwright/test";

import { createOperation, waitForStatus } from "./api";
import { env } from "./env";

export async function ensureCompletedOperation(requestContext: APIRequestContext) {
  const { operation_id } = await createOperation(requestContext, {
    cnpj: env("E2E_CNPJ_VALIDO", "03012610000101"),
  });

  await waitForStatus(
    requestContext,
    operation_id,
    ["completed", "manual_review", "approved", "rejected", "escalated"],
    360_000,
  );

  return operation_id;
}
