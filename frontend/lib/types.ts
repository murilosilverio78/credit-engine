export type Rating = "A" | "B" | "C" | "D" | "E";

export type OperationStatus =
  | "pending"
  | "processing"
  | "aguardando_relatorio"
  | "reprovada_triagem"
  | "cotacao_encerrada"
  | "completed"
  | "failed"
  | "error"          // alias legado — unificado para 'failed' no backend (PR-4)
  | "manual_review"
  | "approved"
  | "rejected"
  | "escalated";

export type FunilEstagio =
  | "LISTA_ESPERA"
  | "ENQUADRADA"
  | "DOCUMENTADA"
  | "QUALIFICADA"
  | "ENCERRADA";

export type OverrideType = "taxa";

export type UserRole = "analista" | "gerente" | "diretor";
export type Alcada = UserRole;

export interface ContratoVerificado {
  numero?: string | null;
  uasg?: string | null;
  valor_global?: number | null;
  vigencia_inicio?: string | null;
  vigencia_fim?: string | null;
  orgao?: string | null;
  origem?: "Comprasnet";
  cnpj_reconferido?: boolean;
}

export interface ContratosVerificados {
  adicionais: ContratoVerificado[];
  contratos: Record<string, unknown>[];
  contratos_ativos_verificados: number;
  valor_total_ativo_verificado: number;
  total_contratos_verificados: number;
  contratos_encerrados_verificados: number;
  orgaos_contratantes_verificados: string[];
  nota: string | null;
}

export interface Operation {
  id: string;
  operation_id?: string | null;
  cnpj: string;
  razao_social: string | null;
  status: OperationStatus;
  rating: Rating | null;
  score: number | null;
  taxa_sugerida: number | null;
  taxa_breakdown?: Record<string, unknown> | null;
  valor_solicitado?: number | null;
  valor_enquadrado?: number | null;
  valor_operacao_relatorio?: number | null;
  saldo_vincendo?: number | null;
  prazo_dias?: number | null;
  prazo_final_meses?: number | null;
  fonte_prazo_vincendo?: string | null;
  prazo_vincendo_meses?: number | null;
  prazo_vincendo_indisponivel?: boolean | null;
  contrato_saldo?: number | null;
  uasg?: string | null;
  margem_disponivel?: number | null;
  origem_dados?: "API_BROADFACTOR" | "MANUAL" | null;
  cotacao_id?: string | null;
  estagio?: FunilEstagio | null;
  estagio_motivo?: string | null;
  n_documentos?: number | null;
  tipos_documento?: string[] | null;
  pricing_skipped_reason?: string | null;
  dado_cadastral_degradado?: boolean | null;
  contratos_verificados?: ContratosVerificados | null;
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
  alcada_required: UserRole;
  status?: "pending" | "approved" | "rejected";
  score_no_momento: number | null;
  requested_at: string;
  created_at: string;
}

export interface TaxaOverrideValidation {
  approved: boolean;
  alcada_required: UserRole;
  motivo: "delta_excedido" | "margem_insuficiente" | null;
  margem_resultante: number;
  taxa_minima_sua_alcada: number;
  taxa_minima_proximo_nivel: number | null;
}

export interface OverrideCreateResult {
  override: Override;
  validation: TaxaOverrideValidation;
}

export interface PropostaInput {
  cnpj: string;
  origem_dados: "API_BROADFACTOR" | "MANUAL";
  cotacao_id?: string;
  valor_solicitado?: number;
  contrato_id?: string;
  uasg?: string;
  contrato_saldo?: number;
  margem_disponivel?: number;
  prazo_dias?: number;
  prazo_vincendo_meses?: number;
  source?: string;
}

export interface OverrideInput {
  override_type: OverrideType;
  previous_value: unknown;
  new_value: unknown;
  justificativa: string;
}

export interface OverrideReviewInput {
  decision: "approved" | "rejected";
  review_comment?: string | null;
}

export interface PaginatedOperations {
  items: Operation[];
  total: number;
  limit: number;
  offset: number;
  estagios?: Partial<Record<FunilEstagio, number>>;
}

export type MotivoFunilTipo = "criterio" | "indisponibilidade";

export interface MotivoFunil {
  codigo: string;
  rotulo: string;
  detalhe: string;
  tipo: MotivoFunilTipo;
}

export interface RelatorioFunil {
  gerado: true;
  rating: Rating | null;
  score: number | null;
  taxa_sugerida: number | null;
  operation_id: string;
}

export interface PendenciaFunil {
  codigo: string;
  rotulo: string;
}

export interface FunnelItem {
  id: string;
  operation_id: string | null;
  cotacao_id: string;
  cnpj: string;
  razao_social: string | null;
  source: string;
  created_at: string;
  valor_solicitado: number | null;
  margem_disponivel: number | null;
  saldo_vincendo: number | null;
  valor_enquadrado: number | null;
  tipo: string | null;
  data_expiracao: string | null;
  estagio: FunilEstagio;
  estagio_max: FunilEstagio | null;
  estagio_atualizado_em: string | null;
  estagio_motivo: string | null;
  motivos: MotivoFunil[];
  n_documentos: number | null;
  tipos_documento: string[] | null;
  pendencias: PendenciaFunil[];
  score_flags: string[];
  operation_status?: OperationStatus | null;
  pendencia_coleta?: boolean;
  relatorio: RelatorioFunil | null;
}

export interface FunnelSummary {
  total_fila: number | null;
  estagios: Partial<Record<FunilEstagio, number>> | null;
  relatorios_gerados: number | null;
}

export interface PaginatedFunnel extends FunnelSummary {
  items: FunnelItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ManualAnalysis extends Operation {
  ambiente: "PRODUCAO" | "TESTE" | string;
}

export interface PaginatedManualAnalyses {
  items: ManualAnalysis[];
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
  score_reprocessamento?: ScoreReprocessingAudit | null;
}

export interface ScoreReprocessingValue {
  rating: Rating | null;
  score: number | null;
  taxa_sugerida: number | null;
}

export interface ScoreReprocessingAudit {
  created_at: string;
  new_value: ScoreReprocessingValue | null;
  payload: {
    archived_version_id?: string;
    error?: string;
    status: "completed" | "failed";
  };
  previous_value: ScoreReprocessingValue | null;
}

export interface ScoreReprocessingAccepted {
  message: string;
  operation_id: string;
  previous_value: ScoreReprocessingValue;
  status: "accepted";
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

export type UploadDocumentType = "cndt_tst" | "cnd_federal" | "fgts";

export interface UploadTask {
  id: string;
  operation_id: string;
  document_type: UploadDocumentType;
  token: string;
  status: "pending" | "completed" | "expired" | "failed";
  completed_at: string | null;
  error_message?: string;
  expires_at: string;
}

export interface UploadResult {
  status: "uploaded";
  operation_id: string;
  pipeline_resumed: boolean;
  uploads_remaining: number;
}

export interface UploadResetResult {
  operation_id: string;
  status: "pending";
}

export interface UploadResumeResult {
  operation_id: string;
  status: "resume_requested";
}

export interface AlcadaConfig {
  role: UserRole;
  max_valor: number;
  max_rating: Rating;
  pode_override: boolean;
  override_max_valor: number | null;
  override_max_rating: Rating | null;
  pode_aprovar_escalada: boolean;
  updated_at?: string | null;
}

export interface ApprovalActionInput {
  justificativa?: string | null;
}

export interface EscaladaPendente {
  id: string;
  operation_id: string;
  cnpj: string;
  razao_social: string | null;
  rating: Rating | null;
  score: number | null;
  valor_solicitado: number | null;
  requested_by: string | null;
  requested_by_name: string | null;
  requested_role: UserRole | null;
  requested_at: string;
  justificativa: string | null;
}

export interface AuditTrailItem {
  id?: string;
  action: string;
  actor_id: string | null;
  actor_type: string | null;
  override_reason: string | null;
  previous_value: unknown;
  new_value: unknown;
  payload?: unknown;
  created_at: string;
}

export interface PricingParameter {
  key: string;
  value: number;
  label: string;
  unit: string;
  grupo: "estrutura_capital" | "custos_operacionais" | "risco_credito" | string;
  updated_by: string | null;
  updated_at: string;
}

export interface EligibilityParameter {
  key: string;
  value: number;
  label: string;
  unit: "BRL" | "decimal" | "meses" | "dias" | "minutos" | string;
  grupo: "elegibilidade" | "operacional" | string;
  updated_by: string | null;
  updated_at: string;
}

export interface PricingMatrixRow {
  rating: Rating;
  pd_mult: number;
  lgd_mult: number;
  bond_cobertura: number;
  bond_premio_aa: number | null;
  recusa: boolean;
  perfil: string | null;
  ordem: number;
  updated_by: string | null;
  updated_at: string;
}
