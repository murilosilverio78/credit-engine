-- Migration 015: persist raw Broadfactor quotes and ingestion state

CREATE TABLE IF NOT EXISTS cotacoes_broadfactor (
  cotacao_id          VARCHAR(100) PRIMARY KEY,
  cnpj                VARCHAR(14) NOT NULL,
  nome_fornecedor     TEXT,
  valor_solicitado    NUMERIC(15,2),
  margem_disponivel   NUMERIC(15,2),
  saldo_vincendo      NUMERIC(15,2),
  valor_enquadrado    NUMERIC(15,2),
  tipo                VARCHAR(30),
  data_cotacao        DATE,
  data_expiracao      DATE,
  operation_id        UUID,
  status_ingestao     VARCHAR(30) NOT NULL,
  payload_bruto       JSONB,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cotacoes_broadfactor_cnpj
  ON cotacoes_broadfactor(cnpj);

CREATE INDEX IF NOT EXISTS idx_cotacoes_broadfactor_data_expiracao
  ON cotacoes_broadfactor(data_expiracao);

CREATE INDEX IF NOT EXISTS idx_cotacoes_broadfactor_status_ingestao
  ON cotacoes_broadfactor(status_ingestao);

ALTER TABLE cotacoes_broadfactor ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "service_role_all" ON cotacoes_broadfactor
    FOR ALL TO service_role USING (TRUE);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

NOTIFY pgrst, 'reload schema';
