import { request, type APIRequestContext } from "@playwright/test";

import { loginViaAPI } from "./auth";
import { credentialsFor, env, type E2ERole, loadE2EEnv } from "./env";

export type OperationPayload = {
  cnpj: string;
  contrato_id?: string;
  contrato_saldo?: number;
  prazo_dias?: number;
  source?: string;
  valor_solicitado?: number;
};

export const MANUAL_COMPONENTS = ["cnd_federal", "fgts", "cndt_tst"] as const;

export type ManualComponent = (typeof MANUAL_COMPONENTS)[number];
export type ManualComponentsState = Partial<Record<ManualComponent, boolean>>;

export async function apiContext(role: E2ERole) {
  loadE2EEnv();
  const baseURL = env("E2E_API_URL");
  const unauthenticated = await request.newContext({ baseURL });
  const { email, password } = credentialsFor(role);
  const cookie = await loginViaAPI(unauthenticated, email, password);
  await unauthenticated.dispose();

  return request.newContext({
    baseURL,
    extraHTTPHeaders: {
      Cookie: cookie,
    },
  });
}

export async function createOperation(
  requestContext: APIRequestContext,
  payload: OperationPayload,
) {
  const response = await requestContext.post("/api/v1/operations/", {
    data: {
      source: "playwright_e2e",
      ...payload,
    },
  });

  if (!response.ok()) {
    throw new Error(`Create operation failed with HTTP ${response.status()}: ${await response.text()}`);
  }

  return response.json() as Promise<{ operation_id: string }>;
}

export async function getOperation(requestContext: APIRequestContext, id: string) {
  const response = await requestContext.get(`/api/v1/operations/${id}`);
  if (!response.ok()) {
    throw new Error(`Get operation failed with HTTP ${response.status()}: ${await response.text()}`);
  }
  return response.json();
}

export async function waitForStatus(
  requestContext: APIRequestContext,
  id: string,
  statuses: string[],
  timeoutMs = 360_000,
) {
  const deadline = Date.now() + timeoutMs;
  let lastOperation: unknown;

  while (Date.now() < deadline) {
    lastOperation = await getOperation(requestContext, id);
    const status = (lastOperation as { status?: string }).status;
    if (status && statuses.includes(status)) {
      return lastOperation;
    }

    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }

  throw new Error(
    `Operation ${id} did not reach ${statuses.join(", ")} before timeout. Last payload: ${JSON.stringify(lastOperation)}`,
  );
}

export async function getManualComponentsState(requestContext: APIRequestContext) {
  const response = await requestContext.get("/api/v1/components/");
  if (!response.ok()) {
    throw new Error(`Get components failed with HTTP ${response.status()}: ${await response.text()}`);
  }

  const data = await response.json();
  const list = Array.isArray(data) ? data : (data.components ?? data.data ?? []);
  const state: ManualComponentsState = {};

  for (const item of list as Array<{ component?: string; enabled?: boolean }>) {
    if (MANUAL_COMPONENTS.includes(item.component as ManualComponent)) {
      state[item.component as ManualComponent] = Boolean(item.enabled);
    }
  }

  return state;
}

export async function disableManualComponents(requestContext: APIRequestContext) {
  for (const component of MANUAL_COMPONENTS) {
    const response = await requestContext.patch(`/api/v1/components/${component}/toggle?enabled=false`);
    if (!response.ok()) {
      throw new Error(`Disable ${component} failed with HTTP ${response.status()}: ${await response.text()}`);
    }
  }
}

export async function restoreManualComponents(
  requestContext: APIRequestContext,
  state?: ManualComponentsState,
) {
  for (const component of MANUAL_COMPONENTS) {
    const enabled = state && component in state ? state[component] : true;
    const response = await requestContext.patch(`/api/v1/components/${component}/toggle?enabled=${enabled}`);
    if (!response.ok()) {
      throw new Error(`Restore ${component} failed with HTTP ${response.status()}: ${await response.text()}`);
    }
  }
}

export async function cleanupOrphans(requestContext: APIRequestContext) {
  const response = await requestContext.post("/api/v1/admin/cleanup-orphans");
  if (!response.ok()) {
    throw new Error(`Cleanup orphans failed with HTTP ${response.status()}: ${await response.text()}`);
  }
  return response.json();
}
