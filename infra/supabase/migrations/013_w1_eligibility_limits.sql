-- Migration 013: W1 eligibility limits and ingestion discard audit

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS eligibility_parameters (
  key         VARCHAR(60) PRIMARY KEY,
  value       NUMERIC(15,6) NOT NULL,
  label       TEXT NOT NULL,
  unit        VARCHAR(20) NOT NULL,
  grupo       VARCHAR(40) NOT NULL DEFAULT 'elegibilidade',
  updated_by  UUID,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE eligibility_parameters ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "service_role_all" ON eligibility_parameters
    FOR ALL TO service_role USING (TRUE);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

INSERT INTO eligibility_parameters (key, value, label, unit) VALUES
  ('ticket_minimo',            10000,   'Ticket minimo',                 'BRL'),
  ('ticket_maximo',            5000000, 'Ticket maximo',                 'BRL'),
  ('pct_max_margem',           0.50,    'Percentual maximo da margem',   'decimal'),
  ('prazo_padrao_meses',       12,      'Prazo padrao',                  'meses'),
  ('dias_minimos_expiracao',   5,       'Dias minimos ate expiracao',   'dias'),
  ('prazo_minimo_dias',        60,      'Prazo minimo',                  'dias'),
  ('cnpj_idade_minima_meses',  12,      'Idade minima do CNPJ',          'meses')
ON CONFLICT (key) DO NOTHING;

ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS origem_dados VARCHAR(20),
  ADD COLUMN IF NOT EXISTS cotacao_id VARCHAR(100),
  ADD COLUMN IF NOT EXISTS margem_disponivel NUMERIC(15,2),
  ADD COLUMN IF NOT EXISTS valor_enquadrado NUMERIC(15,2),
  ADD COLUMN IF NOT EXISTS prazo_vincendo_meses INTEGER,
  ADD COLUMN IF NOT EXISTS prazo_final_meses INTEGER,
  ADD COLUMN IF NOT EXISTS prazo_vincendo_indisponivel BOOLEAN NOT NULL DEFAULT FALSE;

DO $$ BEGIN
  ALTER TABLE operations
    ADD CONSTRAINT operations_origem_dados_check
    CHECK (origem_dados IS NULL OR origem_dados IN ('API_BROADFACTOR', 'MANUAL'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_operations_cotacao_id
  ON operations(cotacao_id)
  WHERE cotacao_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS descartes_ingestao (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cotacao_id          VARCHAR(100),
  cnpj                VARCHAR(14) NOT NULL,
  valor_solicitado    NUMERIC(15,2),
  margem_disponivel   NUMERIC(15,2),
  valor_enquadrado    NUMERIC(15,2),
  motivo              TEXT NOT NULL,
  estagio             VARCHAR(50) NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_descartes_ingestao_cotacao_id
  ON descartes_ingestao(cotacao_id)
  WHERE cotacao_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_descartes_ingestao_created_at
  ON descartes_ingestao(created_at DESC);

ALTER TABLE descartes_ingestao ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "service_role_all" ON descartes_ingestao
    FOR ALL TO service_role USING (TRUE);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

NOTIFY pgrst, 'reload schema';
