CREATE TABLE IF NOT EXISTS documentos_extracoes (
  sha256 TEXT PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  familia TEXT NOT NULL,
  schema_versao INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('EXTRAIDO','PARCIAL','ILEGIVEL','IGNORADO_DERIVADO','FALHA')),
  ano_referencia INTEGER,
  payload JSONB NOT NULL DEFAULT '{}'::JSONB,
  modelo TEXT,
  tokens_entrada INTEGER,
  tokens_saida INTEGER,
  custo_usd NUMERIC(10,4),
  erro TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
DROP TRIGGER IF EXISTS trg_documentos_extracoes_updated_at ON documentos_extracoes;
CREATE TRIGGER trg_documentos_extracoes_updated_at BEFORE UPDATE ON documentos_extracoes
FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TABLE IF NOT EXISTS documentos_financeiros_config (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  max_paginas_extracao INTEGER NOT NULL DEFAULT 40 CHECK (max_paginas_extracao > 0),
  max_custo_usd_operacao NUMERIC(10,4) NOT NULL DEFAULT 1.00,
  balanco_validade_meses INTEGER NOT NULL DEFAULT 18,
  faturamento_defasagem_meses INTEGER NOT NULL DEFAULT 3,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO documentos_financeiros_config (singleton) VALUES (TRUE) ON CONFLICT (singleton) DO NOTHING;

INSERT INTO component_config (component, enabled, timeout_seconds, max_retries, cache_ttl_hours, weight, description, data_scope)
VALUES ('documentos_financeiros', TRUE, 180, 0, 24, NULL, 'Extracao estruturada de documentos financeiros do cliente', 'CLIENTE')
ON CONFLICT (component) DO UPDATE SET data_scope = EXCLUDED.data_scope, cache_ttl_hours = EXCLUDED.cache_ttl_hours;

ALTER TABLE documentos_extracoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE documentos_financeiros_config ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_role_all_documentos_extracoes" ON documentos_extracoes;
CREATE POLICY "service_role_all_documentos_extracoes" ON documentos_extracoes FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_all_documentos_financeiros_config" ON documentos_financeiros_config;
CREATE POLICY "service_role_all_documentos_financeiros_config" ON documentos_financeiros_config FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
NOTIFY pgrst, 'reload schema';
