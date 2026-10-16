-- Preserva documentos Broadfactor enquanto a cotacao ainda esta ativa.

CREATE TABLE IF NOT EXISTS documentos_cliente (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cnpj TEXT NOT NULL CHECK (cnpj ~ '^[0-9]{14}$'),
  cotacao_id TEXT NOT NULL,
  operation_id UUID NULL REFERENCES operations(id),
  broadfactor_documento_id TEXT NOT NULL,
  tipo TEXT NOT NULL,
  dono TEXT NOT NULL,
  dono_tipo TEXT NOT NULL CHECK (dono_tipo IN ('EMPRESA', 'SOCIO')),
  nome_arquivo TEXT,
  mime TEXT,
  tamanho_bytes BIGINT,
  sha256 TEXT CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
  storage_key TEXT,
  status TEXT NOT NULL CHECK (status IN ('CAPTURADO', 'FALHA', 'INDISPONIVEL', 'IGNORADO_TAMANHO')),
  erro TEXT,
  tentativas INTEGER NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
  capturado_em TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (cotacao_id, broadfactor_documento_id)
);

CREATE INDEX IF NOT EXISTS idx_documentos_cliente_cnpj_tipo
  ON documentos_cliente (cnpj, tipo);
CREATE INDEX IF NOT EXISTS idx_documentos_cliente_sha256
  ON documentos_cliente (sha256);

DROP TRIGGER IF EXISTS trg_documentos_cliente_updated_at ON documentos_cliente;
CREATE TRIGGER trg_documentos_cliente_updated_at
  BEFORE UPDATE ON documentos_cliente
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TABLE IF NOT EXISTS documentos_captura_config (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  tipos_capturados TEXT[] NOT NULL,
  tamanho_max_bytes BIGINT NOT NULL CHECK (tamanho_max_bytes > 0),
  max_tentativas INTEGER NOT NULL CHECK (max_tentativas > 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO documentos_captura_config (
  singleton, tipos_capturados, tamanho_max_bytes, max_tentativas
) VALUES (
  TRUE,
  ARRAY[
    'ENDIVIDAMENTO_EMPRESA', 'FATURAMENTO', 'ULTIMO_BALANCO',
    'PENULTIMO_BALANCO', 'DRE', 'ULTIMO_IMPOSTO_RENDA', 'CONTRATO_SOCIAL'
  ],
  26214400,
  3
) ON CONFLICT (singleton) DO NOTHING;

ALTER TABLE documentos_cliente ENABLE ROW LEVEL SECURITY;
ALTER TABLE documentos_captura_config ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_documentos_cliente" ON documentos_cliente;
CREATE POLICY "service_role_all_documentos_cliente"
  ON documentos_cliente FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_all_documentos_captura_config" ON documentos_captura_config;
CREATE POLICY "service_role_all_documentos_captura_config"
  ON documentos_captura_config FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

NOTIFY pgrst, 'reload schema';
