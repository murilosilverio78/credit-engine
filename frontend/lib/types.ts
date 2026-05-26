export type Rating = "A" | "B" | "C" | "D" | "E";

export type OperationStatus =
  | "pending"
  | "processing"
  | "completed"
  | "failed"
  | "manual_review";

export type OverrideType =
  | "rating"
  | "score"
  | "taxa"
  | "limite"
  | "status_operacao";

export type Alcada = "analyst" | "manager" | "committee";

export interface Operation {
  id: string;
  cnpj: string;
  razao_social: string | null;
  status: OperationStatus;
  rating: Rating | null;
  score: number | null;
  taxa_sugerida: number | null;
  source: string;
  created_at: string;
  limite_aprovado?: number | null;
}

export interface Component {
  component: string;
  enabled: boolean;
  timeout_seconds: number;
  max_retries: number;
  cache_ttl_hours: number;
  weight: number | null;
  description: string;
  updated_at: string;
  updated_by: string | null;
}

export interface Override {
  id: string;
  operation_id: string;
  cnpj: string;
  razao_social: string | null;
  override_type: OverrideType;
  previous_value: unknown;
  new_value: unknown;
  justificativa: string;
  alcada_required: Alcada;
  status?: "pending" | "approved" | "rejected";
  score_no_momento: number | null;
  requested_at: string;
  created_at: string;
}

export interface PropostaInput {
  cnpj: string;
  valor_solicitado?: number;
  contrato_id?: string;
  prazo_dias?: number;
  source?: string;
}

export interface OverrideInput {
  override_type: OverrideType;
  previous_value: unknown;
  new_value: unknown;
  justificativa: string;
  requested_by?: string;
}

export interface OverrideReviewInput {
  decision: "approved" | "rejected";
  reviewed_by: string;
  review_comment?: string | null;
}

export interface PaginatedOperations {
  items: Operation[];
  total: number;
  limit: number;
  offset: number;
}

export interface OperationCreated {
  operation_id: string;
  cnpj: string;
  status: OperationStatus;
  message: string;
}

export interface OperationDetails extends Operation {
  components?: ComponentSnapshot[];
}

export interface ComponentSnapshot {
  component: string;
  status: string;
  score_contrib: number | null;
  duration_ms: number | null;
  error_message: string | null;
  completed_at: string | null;
  parsed_result: unknown;
}

export interface ComponentToggleResult {
  component: string;
  enabled: boolean;
}

export interface BrasilApiCompany {
  cnpj: string;
  razao_social: string;
  nome_fantasia: string | null;
}

export interface HealthStatus {
  status: string;
  version: string;
}
