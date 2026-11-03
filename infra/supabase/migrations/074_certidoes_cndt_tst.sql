-- Cache e lineage da CNDT emitida pelo serviço TST no VPS brasileiro.
-- NÃO aplicar automaticamente: esta migration deve ser revisada/aplicada antes
-- de habilitar CNDT_PROXY_URL na Railway.

CREATE TABLE IF NOT EXISTS certidoes_cndt_tst_cache (
  cnpj TEXT PRIMARY KEY CHECK (cnpj ~ '^[0-9]{14}$'),
  sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  storage_key TEXT NOT NULL,
  filename TEXT NULL,
  resultado TEXT NOT NULL CHECK (resultado IN ('negativa', 'positiva_com_efeitos_negativa', 'positiva')),
  data_emissao DATE NULL,
  data_validade DATE NOT NULL,
  numero_certidao TEXT NULL,
  orgao_emissor TEXT NOT NULL DEFAULT 'Tribunal Superior do Trabalho',
  valida BOOLEAN NOT NULL DEFAULT FALSE,
  emitida_em TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_certidoes_cndt_tst_validade
  ON certidoes_cndt_tst_cache (data_validade DESC);

CREATE TABLE IF NOT EXISTS certidoes_cndt_tst_lineage (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  cnpj TEXT NOT NULL CHECK (cnpj ~ '^[0-9]{14}$'),
  sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  cache_hit BOOLEAN NOT NULL,
  fonte TEXT NOT NULL CHECK (fonte = 'tst'),
  collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_certidoes_cndt_tst_lineage_operation
  ON certidoes_cndt_tst_lineage (operation_id, collected_at DESC);

ALTER TABLE certidoes_cndt_tst_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE certidoes_cndt_tst_lineage ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_certidoes_cndt_tst_cache" ON certidoes_cndt_tst_cache;
CREATE POLICY "service_role_all_certidoes_cndt_tst_cache"
  ON certidoes_cndt_tst_cache FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP POLICY IF EXISTS "service_role_all_certidoes_cndt_tst_lineage" ON certidoes_cndt_tst_lineage;
CREATE POLICY "service_role_all_certidoes_cndt_tst_lineage"
  ON certidoes_cndt_tst_lineage FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

NOTIFY pgrst, 'reload schema';
