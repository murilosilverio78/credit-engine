import type {
  BrasilApiCompany,
  Component,
  ComponentToggleResult,
  HealthStatus,
  OperationCreated,
  OperationDetails,
  Override,
  OverrideInput,
  OverrideReviewInput,
  PaginatedOperations,
  PropostaInput,
  UploadDocumentType,
  UploadResult,
  UploadTask,
} from "@/lib/types";

const BASE = process.env.NEXT_PUBLIC_API_URL;

if (!BASE) {
  throw new Error("NEXT_PUBLIC_API_URL is not configured");
}

class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }

  return response.json() as Promise<T>;
}

export function getAdminOperations(limit = 20, offset = 0) {
  return request<PaginatedOperations>(
    `/api/v1/admin/operations?limit=${limit}&offset=${offset}`,
  );
}

export function createOperation(payload: PropostaInput) {
  return request<OperationCreated>("/api/v1/operations/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getOperation(operationId: string) {
  return request<OperationDetails>(`/api/v1/operations/${operationId}`);
}

export function getComponents() {
  return request<Component[]>("/api/v1/components/");
}

export function toggleComponent(component: string, enabled: boolean) {
  const params = new URLSearchParams({ enabled: String(enabled) });
  return request<ComponentToggleResult>(
    `/api/v1/components/${encodeURIComponent(component)}/toggle?${params}`,
    { method: "PATCH" },
  );
}

export function getPendingOverrides() {
  return request<Override[]>("/api/v1/overrides/pending");
}

export function getOperationOverrides(operationId: string) {
  return request<Override[]>(
    `/api/v1/overrides/operations/${operationId}/overrides`,
  );
}

export function createOverride(operationId: string, payload: OverrideInput) {
  return request<Override>(
    `/api/v1/overrides/operations/${operationId}/override`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function reviewOverride(
  operationId: string,
  overrideId: string,
  payload: OverrideReviewInput,
) {
  return request<Override>(
    `/api/v1/overrides/operations/${operationId}/override/${overrideId}/review`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function getHealth() {
  return request<HealthStatus>("/health");
}

export function getOperationUploads(operationId: string) {
  return request<UploadTask[]>(
    `/api/v1/uploads/pending?operation_id=${encodeURIComponent(operationId)}`,
  );
}

export function uploadCertificate(
  token: string,
  documentType: UploadDocumentType,
  file: File,
) {
  const body = new FormData();
  body.append("file", file);
  body.append("document_type", documentType);
  return request<UploadResult>(`/api/v1/uploads/${encodeURIComponent(token)}`, {
    method: "POST",
    body,
  });
}

export async function getCompanyByCnpj(cnpj: string) {
  const response = await fetch(`https://brasilapi.com.br/api/cnpj/v1/${cnpj}`);

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }

  return response.json() as Promise<BrasilApiCompany>;
}

export { ApiError };
