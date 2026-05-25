-- =============================================================
-- Migration: 001_initial_schema
-- AntecipaGov Credit Engine
-- =============================================================

-- Extensões
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================
-- ENUM TYPES
-- =============================================================

CREATE TYPE operation_status AS ENUM (
  'pending',           -- proposta recebida, análise não iniciada
  'running',           -- análise em andamento
  'waiting_upload',    -- aguardando upload de certidão manual
  'completed',         -- análise concluída com decisão
  'expired',           -- expirou sem conclusão
  'error'              -- erro irrecuperável
);

CREATE TYPE rating_value AS ENUM ('A', 'B', 'C', 'D', 'E');

CREATE TYPE component_status AS ENUM (
  'pending',
  'running',
  'completed',
  'failed',
  'skipped',
  'waiting_upload'
);

CREATE TYPE component_type AS ENUM (
  'brasil_api',
  'portal_transparencia',
  'cndt_tst',
  'cnd_federal',
  'fgts',
  'serasa_pj',
  'boa_vista',
  'serpro',
  'web_research',
  'score_engine'
);

CREATE TYPE document_type AS ENUM (
  'cnd_federal',
  'fgts',
  'cndt_tst',
  'certidao_estadual',
  'certidao_municipal',
  'contrato_social',
  'balanco',
  'outro'
);

CREATE TYPE action_type AS ENUM (
  'operation_created',
  'component_started',
  'component_completed',
  'component_failed',
  'upload_requested',
  'upload_received',
  'decision_generated',
  'override_applied',
  'operation_expired',
  'operation_error'
);

-- =============================================================
-- OPERAÇÕES
-- =============================================================

CREATE TABLE operations (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cnpj              VARCHAR(14) NOT NULL,
  razao_social      VARCHAR(255),
  status            operation_status NOT NULL DEFAULT 'pending',

  -- Dados da proposta
  valor_solicitado  NUMERIC(15,2),
  contrato_id       VARCHAR(100),         -- ID do contrato no Portal AntecipaGov
  contrato_saldo    NUMERIC(15,2),        -- Saldo disponível do contrato
  prazo_dias        INTEGER,

  -- Resultado da análise
  rating            rating_value,
  score             NUMERIC(5,2),         -- 0-100
  taxa_sugerida     NUMERIC(6,4),         -- ex: 0.0185 = 1,85% a.m.
  limite_aprovado   NUMERIC(15,2),
  parecer           TEXT,                 -- texto gerado pelo Opus

  -- Controle
  source            VARCHAR(50) DEFAULT 'frontend_mvp', -- frontend_mvp | marketplace | api
  created_by        UUID,                 -- user_id do analista (se aplicável)
  completed_at      TIMESTAMPTZ,
  expires_at        TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '48 hours'),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_operations_cnpj ON operations(cnpj);
CREATE INDEX idx_operations_status ON operations(status);
CREATE INDEX idx_operations_created_at ON operations(created_at DESC);

-- =============================================================
-- SNAPSHOTS DE COMPONENTES
-- =============================================================

CREATE TABLE component_snapshots (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  operation_id    UUID NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  component       component_type NOT NULL,
  status          component_status NOT NULL DEFAULT 'pending',

  -- Resultado
  raw_result      JSONB,                  -- resposta bruta da fonte
  parsed_result   JSONB,                  -- dados extraídos e normalizados
  score_contrib   NUMERIC(5,2),           -- contribuição ao score final (0-100)
  error_message   TEXT,

  -- Metadados de execução
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  duration_ms     INTEGER,
  retry_count     INTEGER DEFAULT 0,
  cost_usd        NUMERIC(10,6),          -- custo da consulta (2captcha, LLM tokens etc)

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE(operation_id, component)
);

CREATE INDEX idx_snapshots_operation_id ON component_snapshots(operation_id);
CREATE INDEX idx_snapshots_component ON component_snapshots(component);
CREATE INDEX idx_snapshots_status ON component_snapshots(status);

-- =============================================================
-- DOCUMENTOS (certidões, uploads)
-- =============================================================

CREATE TABLE documents (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  operation_id    UUID NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  document_type   document_type NOT NULL,

  -- Storage
  storage_key     VARCHAR(500) NOT NULL,  -- chave no R2/S3
  filename        VARCHAR(255),
  mime_type       VARCHAR(100),
  file_size_bytes INTEGER,

  -- Validade da certidão
  valid_until     DATE,
  issuer          VARCHAR(255),

  -- Upload
  uploaded_by     UUID,                   -- user_id do analista
  upload_source   VARCHAR(50),            -- manual | automated
  upload_ip       INET,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_documents_operation_id ON documents(operation_id);
CREATE INDEX idx_documents_type ON documents(document_type);

-- =============================================================
-- TAREFAS DE UPLOAD PENDENTES (human-in-the-loop)
-- =============================================================

CREATE TABLE upload_tasks (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  operation_id    UUID NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  document_type   document_type NOT NULL,

  -- Controle
  token           VARCHAR(100) UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
  status          VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending | completed | expired
  notified_at     TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_upload_tasks_operation_id ON upload_tasks(operation_id);
CREATE INDEX idx_upload_tasks_token ON upload_tasks(token);
CREATE INDEX idx_upload_tasks_status ON upload_tasks(status);

-- =============================================================
-- AUDIT TRAIL (append-only, imutável)
-- =============================================================

CREATE TABLE audit_trail (
  id              BIGSERIAL PRIMARY KEY,
  operation_id    UUID REFERENCES operations(id),
  action          action_type NOT NULL,
  actor_id        UUID,                   -- user_id ou NULL para sistema
  actor_type      VARCHAR(20) DEFAULT 'system', -- system | analyst | admin

  -- Payload
  payload         JSONB,                  -- dados relevantes da ação
  ip_address      INET,
  user_agent      TEXT,

  -- Override específico
  override_reason TEXT,                   -- preenchido apenas em override_applied
  previous_value  JSONB,                  -- valor antes do override
  new_value       JSONB,                  -- valor após o override

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_operation_id ON audit_trail(operation_id);
CREATE INDEX idx_audit_action ON audit_trail(action);
CREATE INDEX idx_audit_created_at ON audit_trail(created_at DESC);

-- Impedir deleção e update (append-only)
CREATE RULE audit_no_delete AS ON DELETE TO audit_trail DO INSTEAD NOTHING;
CREATE RULE audit_no_update AS ON UPDATE TO audit_trail DO INSTEAD NOTHING;

-- =============================================================
-- CONFIGURAÇÃO DE COMPONENTES (feature flags)
-- =============================================================

CREATE TABLE component_config (
  component       component_type PRIMARY KEY,
  enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  timeout_seconds INTEGER NOT NULL DEFAULT 30,
  max_retries     INTEGER NOT NULL DEFAULT 3,
  cache_ttl_hours INTEGER NOT NULL DEFAULT 24,  -- cache por CNPJ
  weight          NUMERIC(4,2),                  -- peso no score (0-100)
  description     TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by      UUID
);

-- Seed: configuração inicial dos componentes
INSERT INTO component_config (component, enabled, timeout_seconds, max_retries, cache_ttl_hours, weight, description) VALUES
  ('brasil_api',          TRUE,  15,  3, 24,   NULL, 'Dados cadastrais via BrasilAPI'),
  ('portal_transparencia',TRUE,  20,  3, 12,   NULL, 'Contratos administrativos via Portal da Transparência'),
  ('cndt_tst',            TRUE,  60,  2, 24,   NULL, 'Certidão Negativa de Débitos Trabalhistas (TST) via 2captcha'),
  ('cnd_federal',         TRUE,  0,   0, 24,   NULL, 'Certidão Negativa Federal - upload manual'),
  ('fgts',                TRUE,  0,   0, 24,   NULL, 'Certificado de Regularidade FGTS - upload manual'),
  ('serasa_pj',           FALSE, 10,  3, 24,   NULL, 'Relatório Avançado PJ Serasa Experian (roadmap)'),
  ('boa_vista',           FALSE, 10,  3, 24,   NULL, 'Consulta Boa Vista SCPC (roadmap)'),
  ('serpro',              FALSE, 10,  3, 24,   NULL, 'Consulta SERPRO / Receita Federal (roadmap)'),
  ('web_research',        TRUE,  120, 1, 48,   NULL, 'Pesquisa de reputação via Claude Sonnet + WebSearch'),
  ('score_engine',        TRUE,  180, 1, 0,    NULL, 'Geração de score e relatório via Claude Opus');

-- =============================================================
-- CACHE DE CNPJ (evitar consultas duplicadas)
-- =============================================================

CREATE TABLE cnpj_cache (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cnpj            VARCHAR(14) NOT NULL,
  component       component_type NOT NULL,
  result          JSONB NOT NULL,
  expires_at      TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE(cnpj, component)
);

CREATE INDEX idx_cnpj_cache_lookup ON cnpj_cache(cnpj, component, expires_at);

-- =============================================================
-- TRIGGERS: updated_at automático
-- =============================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_operations_updated_at
  BEFORE UPDATE ON operations
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_snapshots_updated_at
  BEFORE UPDATE ON component_snapshots
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =============================================================
-- ROW LEVEL SECURITY (base)
-- =============================================================

ALTER TABLE operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE component_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_trail ENABLE ROW LEVEL SECURITY;

-- Service role tem acesso total (backend usa service role key)
CREATE POLICY "service_role_all" ON operations FOR ALL TO service_role USING (TRUE);
CREATE POLICY "service_role_all" ON component_snapshots FOR ALL TO service_role USING (TRUE);
CREATE POLICY "service_role_all" ON documents FOR ALL TO service_role USING (TRUE);
CREATE POLICY "service_role_all" ON audit_trail FOR ALL TO service_role USING (TRUE);
