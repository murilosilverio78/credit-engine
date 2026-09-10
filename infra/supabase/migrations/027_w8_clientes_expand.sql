-- =============================================================
-- Migration 027 — W8 etapa 1a (expand): cadastro de clientes e
-- log de snapshots de cliente. Dual-write, sem nenhum leitor.
--
-- Execução MANUAL no Supabase SQL Editor, fora da janela de ingestão.
-- Idempotente: pode ser reexecutada.
-- Referência: docs/estrutura-dados-clientes.md (v2) + seção 17 (ajustes etapa 1).
--
-- Fora desta migration (etapa 1b, 028+): clientes_historico,
-- materialização cadastral, sinais financeiros, faturamento anual,
-- sanções, pessoas/vínculos e views de limite aprovado.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 0. Pré-condições: o schema de produção diverge das migrations
--    do repositório. Abortar se o que a W8 assume não existir.
-- -------------------------------------------------------------
DO $$
DECLARE
  v_esperados TEXT[] := ARRAY[
    'brasil_api', 'pessoa_juridica', 'contratos', 'recursos_recebidos',
    'acordos_leniencia', 'ceis', 'cnep', 'cepim', 'web_research'
  ];
  v_sem_enum   TEXT[];
  v_sem_config TEXT[];
BEGIN
  SELECT array_agg(v) INTO v_sem_enum
  FROM unnest(v_esperados) AS v
  WHERE v NOT IN (SELECT unnest(enum_range(NULL::component_type))::text);

  IF v_sem_enum IS NOT NULL THEN
    RAISE EXCEPTION 'component_type sem os valores: %', v_sem_enum;
  END IF;

  SELECT array_agg(v) INTO v_sem_config
  FROM unnest(v_esperados) AS v
  WHERE v NOT IN (SELECT component::text FROM component_config);

  IF v_sem_config IS NOT NULL THEN
    RAISE EXCEPTION 'component_config sem linhas para: %', v_sem_config;
  END IF;
END $$;

-- -------------------------------------------------------------
-- 1. Escopo semântico no catálogo de componentes (spec §5)
-- -------------------------------------------------------------
ALTER TABLE component_config
  ADD COLUMN IF NOT EXISTS data_scope VARCHAR(20) NOT NULL DEFAULT 'OPERACAO',
  ADD COLUMN IF NOT EXISTS empty_result_authoritative BOOLEAN NOT NULL DEFAULT FALSE;

DO $$ BEGIN
  ALTER TABLE component_config
    ADD CONSTRAINT component_config_data_scope_check
    CHECK (data_scope IN (
      'CLIENTE', 'CNPJ_RAIZ', 'COTACAO', 'CONTRATO', 'DOCUMENTO', 'OPERACAO'
    ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Classificação inicial (spec §1.3). Reexecução restaura estes valores.
UPDATE component_config SET data_scope = 'CLIENTE'
 WHERE component::text IN (
   'brasil_api', 'pessoa_juridica', 'contratos', 'recursos_recebidos',
   'acordos_leniencia', 'ceis', 'cnep', 'cepim', 'web_research'
 );

UPDATE component_config SET data_scope = 'CONTRATO'
 WHERE component::text IN ('contrato_extracao', 'contratos_comprasnet');

UPDATE component_config SET data_scope = 'DOCUMENTO'
 WHERE component::text IN ('cnd_federal', 'cndt_tst', 'fgts');

-- score_engine e componentes legados/desabilitados permanecem OPERACAO.
-- empty_result_authoritative permanece FALSE para todos (ver seção 17).

-- -------------------------------------------------------------
-- 2. clientes (spec §6.1)
--    Ajuste: CHECK aceita CNPJ alfanumérico (IN RFB 2.229/2024,
--    emissão iniciada em 31/07/2026). Sem índice trigram nesta etapa.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clientes (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cnpj                      VARCHAR(14) NOT NULL UNIQUE,
  cnpj_raiz                 VARCHAR(8)
                            GENERATED ALWAYS AS (LEFT(cnpj, 8)) STORED,

  razao_social              TEXT,
  nome_fantasia             TEXT,
  situacao_cadastral        VARCHAR(30),
  data_situacao_cadastral   DATE,
  data_abertura             DATE,
  natureza_juridica         TEXT,
  regime_tributario         VARCHAR(30),
  porte                     VARCHAR(30),
  unidade                   VARCHAR(20),
  cnae_principal_codigo     VARCHAR(10),
  cnae_principal_descricao  TEXT,
  municipio                 TEXT,
  uf                        CHAR(2),
  capital_social            NUMERIC(15,2),

  cadastro_revision         BIGINT NOT NULL DEFAULT 0,
  field_provenance          JSONB NOT NULL DEFAULT '{}'::jsonb,
  primeiro_contato_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ultima_coleta_cadastral   TIMESTAMPTZ,

  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT clientes_cnpj_check CHECK (cnpj ~ '^[0-9A-Z]{12}[0-9]{2}$'),
  CONSTRAINT clientes_unidade_check
    CHECK (unidade IS NULL OR unidade IN ('HEAD_OFFICE', 'BRANCH')),
  CONSTRAINT clientes_uf_check CHECK (uf IS NULL OR uf ~ '^[A-Z]{2}$'),
  CONSTRAINT clientes_capital_check
    CHECK (capital_social IS NULL OR capital_social >= 0)
);

CREATE INDEX IF NOT EXISTS idx_clientes_cnpj_raiz ON clientes (cnpj_raiz);

DROP TRIGGER IF EXISTS trg_clientes_updated_at ON clientes;
CREATE TRIGGER trg_clientes_updated_at
  BEFORE UPDATE ON clientes
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- -------------------------------------------------------------
-- 3. cliente_snapshots (spec §6.3) — log de observações
--    Ajustes (seção 17):
--      - collected_at fornecido pela aplicação (sem DEFAULT);
--      - sem FK para cotacoes_broadfactor (cotação manual não existe lá);
--      - degradado/degradacao_motivo: resultado válido mas incompleto
--        nunca vira vigente;
--      - fonte e payload_hash para medir mudança e divergência;
--      - status restrito a estados terminais.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cliente_snapshots (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id             UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  component              component_type NOT NULL,
  collection_key         UUID NOT NULL,

  status                 component_status NOT NULL,
  result_state           VARCHAR(10) NOT NULL,
  degradado              BOOLEAN NOT NULL DEFAULT FALSE,
  degradacao_motivo      TEXT,
  fonte                  VARCHAR(30),
  raw_result             JSONB,
  parsed_result          JSONB,
  payload_hash           CHAR(64),

  collected_at           TIMESTAMPTZ NOT NULL,
  valid_until            TIMESTAMPTZ,
  is_current_usable      BOOLEAN NOT NULL DEFAULT FALSE,

  source_operation_id    UUID REFERENCES operations(id) ON DELETE SET NULL,
  source_cotacao_id      VARCHAR(100),
  error_message          TEXT,
  duration_ms            INTEGER,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT cliente_snapshots_status_check
    CHECK (status IN ('completed', 'failed')),
  CONSTRAINT cliente_snapshots_result_state_check
    CHECK (result_state IN ('OK', 'EMPTY', 'ERROR')),
  -- status = desfecho da execução no pipeline; result_state = classificação
  -- semântica. completed + ERROR é intencional: registra falha silenciosa
  -- (ex.: HTTP 200 com corpo vazio tratado como sucesso pelo pipeline).
  CONSTRAINT cliente_snapshots_failed_is_error_check
    CHECK (status <> 'failed' OR result_state = 'ERROR'),
  CONSTRAINT cliente_snapshots_error_check
    CHECK (result_state <> 'ERROR' OR error_message IS NOT NULL),
  CONSTRAINT cliente_snapshots_degradado_check
    CHECK (NOT degradado OR degradacao_motivo IS NOT NULL),
  CONSTRAINT cliente_snapshots_current_check
    CHECK (
      NOT is_current_usable
      OR (status = 'completed'
          AND result_state IN ('OK', 'EMPTY')
          AND NOT degradado)
    ),
  CONSTRAINT cliente_snapshots_validity_check
    CHECK (valid_until IS NULL OR valid_until >= collected_at),
  CONSTRAINT cliente_snapshots_duration_check
    CHECK (duration_ms IS NULL OR duration_ms >= 0),
  CONSTRAINT cliente_snapshots_hash_check
    CHECK (payload_hash IS NULL OR payload_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cliente_snapshots_collection_unique
    UNIQUE (cliente_id, component, collection_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cliente_snapshots_current_usable
  ON cliente_snapshots (cliente_id, component)
  WHERE is_current_usable = TRUE;

CREATE INDEX IF NOT EXISTS idx_cliente_snapshots_history
  ON cliente_snapshots (cliente_id, component, collected_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_cliente_snapshots_source_operation
  ON cliente_snapshots (source_operation_id)
  WHERE source_operation_id IS NOT NULL;

-- -------------------------------------------------------------
-- 4. Vínculos (spec §6.4 e §6.9). Nullable; nada lê nesta etapa.
--    cliente_cadastro_revision fica para a etapa 1b.
-- -------------------------------------------------------------
ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS cliente_id UUID
  REFERENCES clientes(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_operations_cliente
  ON operations (cliente_id)
  WHERE cliente_id IS NOT NULL;

ALTER TABLE component_snapshots
  ADD COLUMN IF NOT EXISTS source_cliente_snapshot_id UUID
  REFERENCES cliente_snapshots(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_component_snapshots_cliente_source
  ON component_snapshots (source_cliente_snapshot_id)
  WHERE source_cliente_snapshot_id IS NOT NULL;

-- -------------------------------------------------------------
-- 5. RPC: vincular operação ao cliente (atômico e idempotente)
--    Retorna NULL em out_cliente_id para documento não-PJ.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION vincular_cliente_operacao(p_operation_id UUID)
RETURNS TABLE (out_cliente_id UUID, out_criado BOOLEAN, out_vinculado BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  v_cnpj    TEXT;
  v_atual   UUID;
  v_cliente UUID;
  v_criado  BOOLEAN := FALSE;
BEGIN
  SELECT upper(regexp_replace(COALESCE(o.cnpj, ''), '[^0-9A-Za-z]', '', 'g')),
         o.cliente_id
    INTO v_cnpj, v_atual
    FROM operations o
   WHERE o.id = p_operation_id
   FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'operacao % inexistente', p_operation_id;
  END IF;

  IF v_atual IS NOT NULL THEN
    RETURN QUERY SELECT v_atual, FALSE, FALSE;
    RETURN;
  END IF;

  IF v_cnpj !~ '^[0-9A-Z]{12}[0-9]{2}$' THEN
    RETURN QUERY SELECT NULL::UUID, FALSE, FALSE;
    RETURN;
  END IF;

  INSERT INTO clientes (cnpj) VALUES (v_cnpj)
  ON CONFLICT (cnpj) DO NOTHING
  RETURNING id INTO v_cliente;

  IF v_cliente IS NULL THEN
    SELECT c.id INTO v_cliente FROM clientes c WHERE c.cnpj = v_cnpj;
  ELSE
    v_criado := TRUE;
  END IF;

  UPDATE operations SET cliente_id = v_cliente WHERE id = p_operation_id;

  RETURN QUERY SELECT v_cliente, v_criado, TRUE;
END;
$$;

-- -------------------------------------------------------------
-- 6. RPC: registrar observação e trocar o vigente atomicamente
--    - rejeita componente cujo data_scope não seja CLIENTE;
--    - rejeita operação de origem que não pertença ao cliente;
--    - retry com a mesma collection_key não duplica;
--    - só promove completed + OK (ou EMPTY autoritativo) + não degradado;
--    - observação mais antiga não substitui a vigente;
--    - valid_until = collected_at + cache_ttl_hours; TTL nulo/zero
--      nasce vencido (NULL significaria "nunca vence").
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION registrar_cliente_snapshot(
  p_cliente_id          UUID,
  p_component           TEXT,
  p_collection_key      UUID,
  p_status              TEXT,
  p_result_state        TEXT,
  p_collected_at        TIMESTAMPTZ,
  p_parsed_result       JSONB   DEFAULT NULL,
  p_raw_result          JSONB   DEFAULT NULL,
  p_payload_hash        TEXT    DEFAULT NULL,
  p_fonte               TEXT    DEFAULT NULL,
  p_degradado           BOOLEAN DEFAULT FALSE,
  p_degradacao_motivo   TEXT    DEFAULT NULL,
  p_source_operation_id UUID    DEFAULT NULL,
  p_source_cotacao_id   TEXT    DEFAULT NULL,
  p_error_message       TEXT    DEFAULT NULL,
  p_duration_ms         INTEGER DEFAULT NULL
)
RETURNS TABLE (out_snapshot_id UUID, out_inserido BOOLEAN, out_promovido BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  v_component   component_type := p_component::component_type;
  v_scope       TEXT;
  v_empty_auth  BOOLEAN;
  v_ttl         INTEGER;
  v_valid_until TIMESTAMPTZ;
  v_id          UUID;
  v_atual_id    UUID;
  v_atual_col   TIMESTAMPTZ;
  v_elegivel    BOOLEAN;
BEGIN
  SELECT cc.data_scope, cc.empty_result_authoritative, cc.cache_ttl_hours
    INTO v_scope, v_empty_auth, v_ttl
    FROM component_config cc
   WHERE cc.component = v_component;

  IF v_scope IS DISTINCT FROM 'CLIENTE' THEN
    RAISE EXCEPTION 'componente % com escopo % nao gera cliente_snapshot',
      p_component, COALESCE(v_scope, 'NULL');
  END IF;

  IF p_source_operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM operations o
     WHERE o.id = p_source_operation_id
       AND o.cliente_id = p_cliente_id
  ) THEN
    RAISE EXCEPTION 'operacao de origem % nao pertence ao cliente %',
      p_source_operation_id, p_cliente_id;
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(p_cliente_id::text || ':' || p_component, 0)
  );

  v_valid_until := CASE
    WHEN v_ttl IS NULL OR v_ttl <= 0 THEN p_collected_at
    ELSE p_collected_at + make_interval(hours => v_ttl)
  END;

  INSERT INTO cliente_snapshots (
    cliente_id, component, collection_key, status, result_state,
    degradado, degradacao_motivo, fonte, raw_result, parsed_result,
    payload_hash, collected_at, valid_until, source_operation_id,
    source_cotacao_id, error_message, duration_ms
  ) VALUES (
    p_cliente_id, v_component, p_collection_key,
    p_status::component_status, p_result_state,
    COALESCE(p_degradado, FALSE), p_degradacao_motivo, p_fonte, p_raw_result,
    p_parsed_result, p_payload_hash, p_collected_at, v_valid_until,
    p_source_operation_id, p_source_cotacao_id, p_error_message, p_duration_ms
  )
  ON CONFLICT (cliente_id, component, collection_key) DO NOTHING
  RETURNING id INTO v_id;

  IF v_id IS NULL THEN
    SELECT cs.id INTO v_id
      FROM cliente_snapshots cs
     WHERE cs.cliente_id = p_cliente_id
       AND cs.component = v_component
       AND cs.collection_key = p_collection_key;
    RETURN QUERY SELECT v_id, FALSE, FALSE;
    RETURN;
  END IF;

  v_elegivel := p_status = 'completed'
    AND NOT COALESCE(p_degradado, FALSE)
    AND (p_result_state = 'OK'
         OR (p_result_state = 'EMPTY' AND v_empty_auth));

  IF NOT v_elegivel THEN
    RETURN QUERY SELECT v_id, TRUE, FALSE;
    RETURN;
  END IF;

  SELECT cs.id, cs.collected_at INTO v_atual_id, v_atual_col
    FROM cliente_snapshots cs
   WHERE cs.cliente_id = p_cliente_id
     AND cs.component = v_component
     AND cs.is_current_usable;

  IF v_atual_id IS NOT NULL AND v_atual_col > p_collected_at THEN
    RETURN QUERY SELECT v_id, TRUE, FALSE;
    RETURN;
  END IF;

  IF v_atual_id IS NOT NULL THEN
    UPDATE cliente_snapshots SET is_current_usable = FALSE WHERE id = v_atual_id;
  END IF;
  UPDATE cliente_snapshots SET is_current_usable = TRUE WHERE id = v_id;

  RETURN QUERY SELECT v_id, TRUE, TRUE;
END;
$$;

-- -------------------------------------------------------------
-- 7. Segurança: RLS + policy service_role; RPCs fora do alcance
--    das chaves anon/authenticated via PostgREST.
-- -------------------------------------------------------------
ALTER TABLE clientes ENABLE ROW LEVEL SECURITY;
ALTER TABLE cliente_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all" ON clientes;
CREATE POLICY "service_role_all" ON clientes
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP POLICY IF EXISTS "service_role_all" ON cliente_snapshots;
CREATE POLICY "service_role_all" ON cliente_snapshots
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

REVOKE ALL ON FUNCTION vincular_cliente_operacao(UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION vincular_cliente_operacao(UUID) TO service_role;

REVOKE ALL ON FUNCTION registrar_cliente_snapshot(
  UUID, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ, JSONB, JSONB, TEXT, TEXT,
  BOOLEAN, TEXT, UUID, TEXT, TEXT, INTEGER
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION registrar_cliente_snapshot(
  UUID, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ, JSONB, JSONB, TEXT, TEXT,
  BOOLEAN, TEXT, UUID, TEXT, TEXT, INTEGER
) TO service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';
