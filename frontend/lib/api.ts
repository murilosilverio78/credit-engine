import type {
  AlcadaConfig,
  ApprovalActionInput,
  AuditTrailItem,
  BrasilApiCompany,
  Component,
  ComponentToggleResult,
  EscaladaPendente,
  HealthStatus,
  OperationCreated,
  OperationDetails,
  Override,
  OverrideInput,
  OverrideReviewInput,
  PaginatedOperations,
  PropostaInput,
  UploadDocumentType,
  UploadResetResult,
  UploadResumeResult,
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
    credentials: "include",
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
  const formData = new FormData();
  formData.append("file", file);
  formData.append("document_type", documentType);
  return request<UploadResult>(`/api/v1/uploads/${encodeURIComponent(token)}`, {
    method: "POST",
    body: formData,
  });
}

export function removeCertificateUpload(token: string) {
  return request<UploadResetResult>(
    `/api/v1/uploads/${encodeURIComponent(token)}`,
    { method: "DELETE" },
  );
}

export function resumeAfterUploads(operationId: string) {
  return request<UploadResumeResult>(
    `/api/v1/uploads/operations/${encodeURIComponent(operationId)}/resume`,
    { method: "POST" },
  );
}

export function getAlcadas() {
  return request<AlcadaConfig[]>("/api/v1/alcadas");
}

export function updateAlcada(
  role: string,
  payload: Partial<AlcadaConfig> & { justificativa: string },
) {
  return request<AlcadaConfig>(`/api/v1/alcadas/${encodeURIComponent(role)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function getAlcadaAuditTrail() {
  return request<AuditTrailItem[]>("/api/v1/alcadas/audit");
}

export function approveOperation(
  operationId: string,
  payload: ApprovalActionInput = {},
) {
  return request(`/api/v1/operations/${operationId}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function rejectOperation(
  operationId: string,
  payload: ApprovalActionInput,
) {
  return request(`/api/v1/operations/${operationId}/reject`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function escalateOperation(
  operationId: string,
  payload: ApprovalActionInput = {},
) {
  return request(`/api/v1/operations/${operationId}/escalate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPendingEscaladas() {
  return request<EscaladaPendente[]>("/api/v1/escaladas/pendentes");
}

export function resolveEscalation(
  operationId: string,
  payload: {
    approval_id: string;
    action: "escalation_approved" | "escalation_rejected";
    justificativa: string;
  },
) {
  return request(`/api/v1/operations/${operationId}/resolve-escalation`, {
    method: "POST",
    body: JSON.stringify(payload),
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
