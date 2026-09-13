-- =============================================================
-- Migration 030 — W8 etapa 1b.2: coletas e sanções por cliente
--
-- Projeta as observações de ceis, cnep, cepim e acordos_leniencia
-- (cliente_snapshots) em duas tabelas: o registro de cada verificação
-- (mesmo sem sanção) e as sanções em si, com detecção de aparecimento e
-- desaparecimento. Continua sem nenhum leitor.
--
-- Execução MANUAL no Supabase SQL Editor. Idempotente.
-- Backfill ao final (seção 9).
--
-- Formato real das fontes confirmado contra a API em 11/09/2026:
--   ceis/cnep: id, tipoSancao.descricaoResumida, orgaoSancionador.nome,
--              dataInicioSancao, dataFimSancao (dd/mm/aaaa), numeroProcesso,
--              pessoa.cnpjFormatado (pode ser CPF: CEIS mistura PF e PJ)
--   cepim:     id, motivo, orgaoSuperior.nome, dataReferencia,
--              pessoaJuridica.cnpjFormatado, convenio.numero
--   leniencia: o worker atual NÃO grava id nem a lista de empresas do acordo;
--              ver seção 7 (tratamento degradado) e a seção 19 da spec.
-- =============================================================

BEGIN;

DO $$ BEGIN
  IF to_regclass('public.cliente_snapshots') IS NULL THEN
    RAISE EXCEPTION 'migration 027 (etapa 1a) não aplicada';
  END IF;
END $$;

-- -------------------------------------------------------------
-- 1. cliente_sancao_coletas (spec §6.7)
--    Uma linha por observação de fonte de sanção, inclusive sem sanção.
--    Ajustes: FK para o snapshot de origem; `pagination_complete` derivado
--    do worker; `source` restrito às 4 fontes conhecidas.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cliente_sancao_coletas (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id           UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  source               VARCHAR(30) NOT NULL,
  collection_key       UUID NOT NULL,
  source_snapshot_id   UUID NOT NULL REFERENCES cliente_snapshots(id) ON DELETE CASCADE,
  result_state         VARCHAR(10) NOT NULL,
  pagination_complete  BOOLEAN NOT NULL DEFAULT FALSE,
  registros_observados INTEGER NOT NULL DEFAULT 0,
  registros_atribuidos INTEGER NOT NULL DEFAULT 0,
  collected_at         TIMESTAMPTZ NOT NULL,
  completed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  error_message        TEXT,

  CONSTRAINT sancao_coleta_source_check
    CHECK (source IN ('CEIS', 'CNEP', 'CEPIM', 'ACORDOS_LENIENCIA')),
  CONSTRAINT sancao_coleta_state_check
    CHECK (result_state IN ('OK', 'EMPTY', 'ERROR')),
  CONSTRAINT sancao_coleta_error_check
    CHECK (result_state <> 'ERROR' OR NOT pagination_complete),
  CONSTRAINT sancao_coleta_counts_check
    CHECK (registros_atribuidos <= registros_observados),
  CONSTRAINT sancao_coleta_unique UNIQUE (cliente_id, source, collection_key)
);

CREATE INDEX IF NOT EXISTS idx_sancao_coletas_cliente
  ON cliente_sancao_coletas (cliente_id, source, collected_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sancao_coletas_snapshot
  ON cliente_sancao_coletas (source_snapshot_id);

-- -------------------------------------------------------------
-- 2. cliente_sancoes (spec §6.7)
--    `identificador` é o id numérico da fonte (estável, confirmado na API).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cliente_sancoes (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id           UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  source               VARCHAR(30) NOT NULL,
  tipo                 VARCHAR(20) NOT NULL,
  identificador        TEXT NOT NULL,

  orgao_sancionador    TEXT,
  descricao            TEXT,
  data_inicio          DATE,
  data_fim             DATE,

  primeira_deteccao    TIMESTAMPTZ NOT NULL,
  ultima_confirmacao   TIMESTAMPTZ NOT NULL,
  ausente_desde        TIMESTAMPTZ,
  last_seen_run_id     UUID REFERENCES cliente_sancao_coletas(id) ON DELETE SET NULL,
  payload              JSONB,

  CONSTRAINT cliente_sancoes_source_check
    CHECK (source IN ('CEIS', 'CNEP', 'CEPIM', 'ACORDOS_LENIENCIA')),
  CONSTRAINT cliente_sancoes_tipo_check
    CHECK (tipo IN ('INIDONEIDADE', 'IMPEDIMENTO', 'IMPEDIMENTO_CONVENIO',
                    'ACORDO_LENIENCIA', 'OUTRA')),
  CONSTRAINT cliente_sancoes_datas_check
    CHECK (data_fim IS NULL OR data_inicio IS NULL OR data_fim >= data_inicio),
  CONSTRAINT cliente_sancoes_unique UNIQUE (cliente_id, source, tipo, identificador)
);

CREATE INDEX IF NOT EXISTS idx_sancoes_cliente_observadas
  ON cliente_sancoes (cliente_id, tipo)
  WHERE ausente_desde IS NULL;

-- -------------------------------------------------------------
-- 3. Utilitários
-- -------------------------------------------------------------
-- Data brasileira (dd/mm/aaaa) tolerante a lixo ("Sem informação", vazio).
CREATE OR REPLACE FUNCTION _w8_data_br(p TEXT) RETURNS DATE
LANGUAGE plpgsql IMMUTABLE SET search_path = public AS $$
BEGIN
  IF p IS NULL OR btrim(p) !~ '^\d{2}/\d{2}/\d{4}$' THEN RETURN NULL; END IF;
  RETURN to_date(btrim(p), 'DD/MM/YYYY');
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION _w8_digitos(p TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE SET search_path = public AS $$
  SELECT nullif(regexp_replace(coalesce(p, ''), '\D', '', 'g'), '')
$$;

-- Classificação do tipo a partir da descrição da fonte.
CREATE OR REPLACE FUNCTION _w8_tipo_sancao(p_source TEXT, p_desc TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE SET search_path = public AS $$
  SELECT CASE
    WHEN p_source = 'CEPIM' THEN 'IMPEDIMENTO_CONVENIO'
    WHEN p_source = 'ACORDOS_LENIENCIA' THEN 'ACORDO_LENIENCIA'
    WHEN lower(coalesce(p_desc, '')) LIKE '%inidone%' THEN 'INIDONEIDADE'
    WHEN lower(coalesce(p_desc, '')) LIKE '%suspens%'
      OR lower(coalesce(p_desc, '')) LIKE '%impediment%'
      OR lower(coalesce(p_desc, '')) LIKE '%proibi%' THEN 'IMPEDIMENTO'
    ELSE 'OUTRA'
  END
$$;

-- -------------------------------------------------------------
-- 4. Extração: registro bruto da fonte → sanção candidata.
--    Só devolve linha quando o CNPJ do registro bate com o do cliente
--    (o CEIS mistura PF e PJ; CPF vem mascarado e nunca casa).
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION _w8_extrair_sancoes(
  p_source TEXT, p_parsed JSONB, p_cnpj TEXT
)
RETURNS TABLE (
  identificador TEXT, tipo TEXT, orgao TEXT, descricao TEXT,
  data_inicio DATE, data_fim DATE, payload JSONB, total_observado INTEGER
)
LANGUAGE plpgsql IMMUTABLE SET search_path = public AS $$
DECLARE
  v_itens JSONB;
BEGIN
  IF p_parsed IS NULL OR jsonb_typeof(p_parsed) <> 'object' THEN RETURN; END IF;

  v_itens := CASE WHEN p_source = 'ACORDOS_LENIENCIA'
                  THEN p_parsed->'acordos' ELSE p_parsed->'registros' END;
  IF jsonb_typeof(v_itens) <> 'array' THEN RETURN; END IF;

  IF p_source IN ('CEIS', 'CNEP') THEN
    RETURN QUERY
    SELECT r->>'id',
           _w8_tipo_sancao(p_source, r#>>'{tipoSancao,descricaoResumida}'),
           r#>>'{orgaoSancionador,nome}',
           coalesce(r#>>'{tipoSancao,descricaoResumida}', r->>'numeroProcesso'),
           _w8_data_br(r->>'dataInicioSancao'),
           _w8_data_br(r->>'dataFimSancao'),
           r,
           jsonb_array_length(v_itens)
      FROM jsonb_array_elements(v_itens) r
     WHERE r->>'id' IS NOT NULL
       AND _w8_digitos(r#>>'{pessoa,cnpjFormatado}') = p_cnpj;

  ELSIF p_source = 'CEPIM' THEN
    RETURN QUERY
    SELECT r->>'id',
           'IMPEDIMENTO_CONVENIO',
           r#>>'{orgaoSuperior,nome}',
           coalesce(r->>'motivo', 'Impedimento de convênio'),
           _w8_data_br(r->>'dataReferencia'),
           NULL::date,
           r,
           jsonb_array_length(v_itens)
      FROM jsonb_array_elements(v_itens) r
     WHERE r->>'id' IS NOT NULL
       AND _w8_digitos(r#>>'{pessoaJuridica,cnpjFormatado}') = p_cnpj;

  ELSIF p_source = 'ACORDOS_LENIENCIA' THEN
    -- O worker atual não grava `id` nem a lista de empresas do acordo.
    -- Sem id, usa-se uma chave derivada (órgão + datas), estável enquanto
    -- esses campos não mudam. A atribuição ao cliente vem da própria
    -- consulta, que é filtrada por CNPJ na origem. Quando o worker passar
    -- a gravar `id` e `empresas`, esta função é substituída (seção 19).
    RETURN QUERY
    SELECT coalesce(r->>'id',
                    md5(coalesce(r->>'orgao', '') || '|' ||
                        coalesce(r->>'data_inicio', '') || '|' ||
                        coalesce(r->>'data_fim', ''))),
           'ACORDO_LENIENCIA',
           r->>'orgao',
           coalesce(r->>'situacao', 'Acordo de leniência'),
           _w8_data_br(r->>'data_inicio'),
           _w8_data_br(r->>'data_fim'),
           r,
           jsonb_array_length(v_itens)
      FROM jsonb_array_elements(v_itens) r;
  END IF;
END $$;

-- -------------------------------------------------------------
-- 5. Projeção de uma observação de sanção (spec §6.7, regras 1 a 4).
--    Tudo em uma transação: coleta, upsert das sanções e marcação de
--    ausências. Idempotente pela collection_key.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION projetar_sancoes_de_snapshot(p_snapshot_id UUID)
RETURNS TABLE (
  out_coleta_id UUID, out_novas INTEGER, out_confirmadas INTEGER,
  out_ausentes INTEGER, out_reaparecidas INTEGER
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  c_map CONSTANT JSONB := '{"ceis":"CEIS","cnep":"CNEP","cepim":"CEPIM",
                            "acordos_leniencia":"ACORDOS_LENIENCIA"}'::jsonb;
  v_snap      cliente_snapshots%ROWTYPE;
  v_source    TEXT;
  v_cnpj      TEXT;
  v_complete  BOOLEAN;
  v_coleta    UUID;
  v_existente UUID;
  v_obs       INTEGER := 0;
  v_atr       INTEGER := 0;
  v_novas     INTEGER := 0;
  v_conf      INTEGER := 0;
  v_reap      INTEGER := 0;
  v_aus       INTEGER := 0;
  r           RECORD;
BEGIN
  SELECT * INTO v_snap FROM cliente_snapshots WHERE id = p_snapshot_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'cliente_snapshot % inexistente', p_snapshot_id;
  END IF;

  v_source := c_map->>(v_snap.component::text);
  IF v_source IS NULL THEN
    RETURN QUERY SELECT NULL::UUID, 0, 0, 0, 0;
    RETURN;
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(v_snap.cliente_id::text || ':sancoes:' || v_source, 0)
  );

  SELECT id INTO v_existente
    FROM cliente_sancao_coletas
   WHERE source_snapshot_id = p_snapshot_id;
  IF v_existente IS NOT NULL THEN
    RETURN QUERY SELECT v_existente, 0, 0, 0, 0;   -- idempotência
    RETURN;
  END IF;

  SELECT cnpj INTO v_cnpj FROM clientes WHERE id = v_snap.cliente_id;

  -- Os 4 workers levantam exceção ao atingir o cap de páginas ou o timeout;
  -- portanto, resultado presente e não degradado ⇒ paginação completa.
  v_complete := v_snap.status::text = 'completed'
                AND v_snap.result_state IN ('OK', 'EMPTY')
                AND NOT v_snap.degradado;

  INSERT INTO cliente_sancao_coletas (
    cliente_id, source, collection_key, source_snapshot_id, result_state,
    pagination_complete, registros_observados, registros_atribuidos,
    collected_at, error_message
  ) VALUES (
    v_snap.cliente_id, v_source, v_snap.collection_key, p_snapshot_id,
    v_snap.result_state, v_complete, 0, 0, v_snap.collected_at,
    v_snap.error_message
  )
  RETURNING id INTO v_coleta;

  IF NOT v_complete THEN                              -- regra 3
    RETURN QUERY SELECT v_coleta, 0, 0, 0, 0;
    RETURN;
  END IF;

  FOR r IN
    SELECT * FROM _w8_extrair_sancoes(v_source, v_snap.parsed_result, v_cnpj)
  LOOP
    v_obs := greatest(v_obs, r.total_observado);
    v_atr := v_atr + 1;

    INSERT INTO cliente_sancoes (
      cliente_id, source, tipo, identificador, orgao_sancionador, descricao,
      data_inicio, data_fim, primeira_deteccao, ultima_confirmacao,
      ausente_desde, last_seen_run_id, payload
    ) VALUES (
      v_snap.cliente_id, v_source, r.tipo, r.identificador, r.orgao,
      r.descricao, r.data_inicio, r.data_fim, v_snap.collected_at,
      v_snap.collected_at, NULL, v_coleta, r.payload
    )
    ON CONFLICT (cliente_id, source, tipo, identificador) DO UPDATE SET
      orgao_sancionador  = excluded.orgao_sancionador,
      descricao          = excluded.descricao,
      data_inicio        = excluded.data_inicio,
      data_fim           = excluded.data_fim,
      ultima_confirmacao = GREATEST(cliente_sancoes.ultima_confirmacao,
                                    excluded.ultima_confirmacao),
      ausente_desde      = NULL,                       -- regra 4
      last_seen_run_id   = excluded.last_seen_run_id,
      payload            = excluded.payload
    WHERE cliente_sancoes.ultima_confirmacao <= excluded.ultima_confirmacao;

    IF FOUND THEN
      IF (SELECT primeira_deteccao = v_snap.collected_at
            FROM cliente_sancoes
           WHERE cliente_id = v_snap.cliente_id AND source = v_source
             AND tipo = r.tipo AND identificador = r.identificador)
      THEN v_novas := v_novas + 1;
      ELSE v_conf := v_conf + 1;
      END IF;
    END IF;
  END LOOP;

  -- Regras 1 e 2: ausências só após coleta completa, na mesma transação.
  WITH ausentes AS (
    UPDATE cliente_sancoes s
       SET ausente_desde = v_snap.collected_at
     WHERE s.cliente_id = v_snap.cliente_id
       AND s.source = v_source
       AND s.ausente_desde IS NULL
       AND s.ultima_confirmacao < v_snap.collected_at
    RETURNING 1
  )
  SELECT count(*) INTO v_aus FROM ausentes;

  SELECT count(*) INTO v_reap
    FROM cliente_sancoes
   WHERE cliente_id = v_snap.cliente_id AND source = v_source
     AND last_seen_run_id = v_coleta AND primeira_deteccao < v_snap.collected_at
     AND ausente_desde IS NULL;

  UPDATE cliente_sancao_coletas
     SET registros_observados = v_obs, registros_atribuidos = v_atr
   WHERE id = v_coleta;

  RETURN QUERY SELECT v_coleta, v_novas, v_conf, v_aus, v_reap;
END;
$$;

-- -------------------------------------------------------------
-- 6. Situação atual por cliente e fonte (sem leitor ainda; serve à
--    validação e à futura etapa 2).
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW vw_cliente_situacao_sancoes
WITH (security_invoker = true) AS
SELECT c.id AS cliente_id,
       c.cnpj,
       f.source,
       ult.collected_at            AS ultima_verificacao,
       ult.result_state,
       ult.pagination_complete,
       coalesce(s.observadas, 0)   AS sancoes_observadas,
       coalesce(s.vigentes, 0)     AS sancoes_vigentes
  FROM clientes c
 CROSS JOIN (VALUES ('CEIS'), ('CNEP'), ('CEPIM'), ('ACORDOS_LENIENCIA')) f(source)
  LEFT JOIN LATERAL (
    SELECT k.collected_at, k.result_state, k.pagination_complete
      FROM cliente_sancao_coletas k
     WHERE k.cliente_id = c.id AND k.source = f.source
     ORDER BY k.collected_at DESC
     LIMIT 1
  ) ult ON TRUE
  LEFT JOIN LATERAL (
    SELECT count(*) FILTER (WHERE x.ausente_desde IS NULL) AS observadas,
           count(*) FILTER (WHERE x.ausente_desde IS NULL
                              AND (x.data_inicio IS NULL OR x.data_inicio <= CURRENT_DATE)
                              AND (x.data_fim IS NULL OR x.data_fim >= CURRENT_DATE)) AS vigentes
      FROM cliente_sancoes x
     WHERE x.cliente_id = c.id AND x.source = f.source
  ) s ON TRUE;

-- -------------------------------------------------------------
-- 7. Segurança
-- -------------------------------------------------------------
ALTER TABLE cliente_sancao_coletas ENABLE ROW LEVEL SECURITY;
ALTER TABLE cliente_sancoes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all" ON cliente_sancao_coletas;
CREATE POLICY "service_role_all" ON cliente_sancao_coletas
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP POLICY IF EXISTS "service_role_all" ON cliente_sancoes;
CREATE POLICY "service_role_all" ON cliente_sancoes
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

REVOKE ALL ON FUNCTION projetar_sancoes_de_snapshot(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION projetar_sancoes_de_snapshot(UUID) TO service_role;
REVOKE ALL ON FUNCTION _w8_extrair_sancoes(TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION _w8_data_br(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION _w8_digitos(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION _w8_tipo_sancao(TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON vw_cliente_situacao_sancoes FROM PUBLIC, anon, authenticated;
GRANT SELECT ON vw_cliente_situacao_sancoes TO service_role;

COMMIT;

-- -------------------------------------------------------------
-- 8. Backfill (idempotente; em ordem cronológica, para que a detecção de
--    ausência siga a mesma sequência da realidade).
-- -------------------------------------------------------------
SELECT count(*) AS snapshots_projetados
  FROM (
    SELECT projetar_sancoes_de_snapshot(cs.id)
      FROM cliente_snapshots cs
     WHERE cs.component IN ('ceis', 'cnep', 'cepim', 'acordos_leniencia')
     ORDER BY cs.collected_at
  ) x;

NOTIFY pgrst, 'reload schema';
