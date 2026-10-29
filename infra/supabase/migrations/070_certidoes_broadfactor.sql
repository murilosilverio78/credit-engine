-- Cache deterministico e lineage de certidoes capturadas da Broadfactor.
-- Aplicar manualmente antes do deploy do consumidor desta tabela.

CREATE TABLE IF NOT EXISTS certidoes_broadfactor (
  tipo TEXT NOT NULL CHECK (tipo IN ('CND_FEDERAL', 'CND_ESTADUAL', 'CND_MUNICIPAL')),
  cnpj TEXT NOT NULL CHECK (cnpj ~ '^[0-9]{14}$'),
  sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  resultado TEXT NULL CHECK (resultado IN ('negativa', 'positiva_com_efeitos_negativa', 'positiva')),
  data_emissao DATE NULL,
  data_validade DATE NULL,
  codigo_controle TEXT NULL,
  orgao_emissor TEXT NULL,
  uf TEXT NULL CHECK (uf IS NULL OR uf ~ '^[A-Z]{2}$'),
  municipio TEXT NULL,
  valida BOOLEAN NOT NULL DEFAULT FALSE,
  fonte_extracao TEXT NOT NULL CHECK (fonte_extracao IN ('deterministico', 'modelo', 'indisponivel')),
  confianca TEXT NOT NULL CHECK (confianca IN ('ALTA', 'MEDIA', 'BAIXA')),
  erro TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tipo, cnpj, sha256)
);

CREATE INDEX IF NOT EXISTS idx_certidoes_broadfactor_cnpj_tipo_validade
  ON certidoes_broadfactor (cnpj, tipo, data_validade DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS certidoes_broadfactor_origens (
  tipo TEXT NOT NULL,
  cnpj TEXT NOT NULL CHECK (cnpj ~ '^[0-9]{14}$'),
  sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  documento_cliente_id UUID NOT NULL REFERENCES documentos_cliente(id) ON DELETE CASCADE,
  cotacao_id TEXT NOT NULL,
  capturado_em TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tipo, cnpj, sha256, documento_cliente_id),
  FOREIGN KEY (tipo, cnpj, sha256)
    REFERENCES certidoes_broadfactor (tipo, cnpj, sha256)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_certidoes_broadfactor_origens_cotacao
  ON certidoes_broadfactor_origens (cotacao_id);

DROP TRIGGER IF EXISTS trg_certidoes_broadfactor_updated_at ON certidoes_broadfactor;
CREATE TRIGGER trg_certidoes_broadfactor_updated_at
  BEFORE UPDATE ON certidoes_broadfactor
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE certidoes_broadfactor ENABLE ROW LEVEL SECURITY;
ALTER TABLE certidoes_broadfactor_origens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_certidoes_broadfactor" ON certidoes_broadfactor;
CREATE POLICY "service_role_all_certidoes_broadfactor"
  ON certidoes_broadfactor FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP POLICY IF EXISTS "service_role_all_certidoes_broadfactor_origens" ON certidoes_broadfactor_origens;
CREATE POLICY "service_role_all_certidoes_broadfactor_origens"
  ON certidoes_broadfactor_origens FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

NOTIFY pgrst, 'reload schema';
