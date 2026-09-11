-- =============================================================
-- Migration 029 — W8 etapa 1b.1: materialização do cadastro de clientes
--
-- Projeta a observação da brasil_api (cliente_snapshots) nas colunas de
-- `clientes`, com as 5 regras mínimas da spec §6.1, histórico de revisões
-- (§6.2) e registro de divergências. Continua sem nenhum leitor.
--
-- Execução MANUAL no Supabase SQL Editor. Idempotente.
-- Backfill ao final (seção 8), também idempotente.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 0. Pré-condição: etapa 1a aplicada.
-- -------------------------------------------------------------
DO $$ BEGIN
  IF to_regclass('public.cliente_snapshots') IS NULL
     OR to_regclass('public.clientes') IS NULL THEN
    RAISE EXCEPTION 'migration 027 (etapa 1a) não aplicada';
  END IF;
END $$;

-- -------------------------------------------------------------
-- 1. clientes_historico (spec §6.2)
--    Ajuste: `registro` guarda o estado RESULTANTE da revisão (não o
--    anterior), para que cada revisão identifique o estado que a fonte
--    produziu. O estado anterior é a revisão imediatamente menor.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clientes_historico (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id         UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  cadastro_revision  BIGINT NOT NULL,
  registro           JSONB NOT NULL,
  campos_alterados   TEXT[] NOT NULL,
  fonte              VARCHAR(30) NOT NULL,
  source_snapshot_id UUID REFERENCES cliente_snapshots(id) ON DELETE SET NULL,
  observed_at        TIMESTAMPTZ NOT NULL,
  alterado_por       UUID,
  alterado_em        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT clientes_historico_revision_unique UNIQUE (cliente_id, cadastro_revision),
  CONSTRAINT clientes_historico_campos_check CHECK (cardinality(campos_alterados) > 0)
);

CREATE INDEX IF NOT EXISTS idx_clientes_historico_cliente
  ON clientes_historico (cliente_id, cadastro_revision DESC);

-- -------------------------------------------------------------
-- 2. Divergências cadastrais (regra 4). A spec pede o registro, mas não
--    define a tabela.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clientes_divergencias_cadastrais (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id         UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  campo              VARCHAR(40) NOT NULL,
  valor_vigente      JSONB,
  fonte_vigente      VARCHAR(30),
  valor_observado    JSONB NOT NULL,
  fonte_observada    VARCHAR(30) NOT NULL,
  observed_at        TIMESTAMPTZ NOT NULL,
  source_snapshot_id UUID NOT NULL REFERENCES cliente_snapshots(id) ON DELETE CASCADE,
  resolvida_em       TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT clientes_divergencias_unique UNIQUE (cliente_id, campo, source_snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_clientes_divergencias_abertas
  ON clientes_divergencias_cadastrais (cliente_id)
  WHERE resolvida_em IS NULL;

-- -------------------------------------------------------------
-- 3. Proveniência na operação (spec §6.4): revisão cadastral observada.
-- -------------------------------------------------------------
ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS cliente_cadastro_revision BIGINT;

-- -------------------------------------------------------------
-- 4. Conversões seguras: valor inválido vira NULL (regra 1 o ignora),
--    em vez de abortar a materialização inteira.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION _w8_safe_date(p TEXT) RETURNS DATE
LANGUAGE plpgsql IMMUTABLE SET search_path = public AS $$
BEGIN
  IF p IS NULL OR btrim(p) = '' THEN RETURN NULL; END IF;
  RETURN p::date;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION _w8_safe_numeric(p TEXT) RETURNS NUMERIC
LANGUAGE plpgsql IMMUTABLE SET search_path = public AS $$
BEGIN
  IF p IS NULL OR btrim(p) = '' THEN RETURN NULL; END IF;
  RETURN p::numeric;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION _w8_texto(p TEXT, p_max INTEGER DEFAULT NULL)
RETURNS TEXT LANGUAGE sql IMMUTABLE SET search_path = public AS $$
  SELECT CASE
    WHEN p IS NULL OR btrim(p) = '' THEN NULL
    WHEN p_max IS NULL THEN btrim(p)
    ELSE left(btrim(p), p_max)
  END
$$;

-- -------------------------------------------------------------
-- 5. Mapeamento brasil_api → campos de `clientes` (único lugar da regra).
--    `cnae_fiscal` e `identificador_matriz_filial` só existem em snapshots
--    gravados depois do ajuste no worker; antes disso, ficam NULL.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION mapear_cadastro_brasil_api(p JSONB)
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SET search_path = public AS $$
DECLARE
  v_regime TEXT;
  v_uf     TEXT := upper(_w8_texto(p->>'uf'));
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'object' THEN
    RETURN '{}'::jsonb;
  END IF;

  IF lower(coalesce(p->>'opcao_mei', '')) = 'true' THEN
    v_regime := 'MEI';
  ELSIF lower(coalesce(p->>'opcao_simples', '')) = 'true' THEN
    v_regime := 'SIMPLES NACIONAL';
  ELSIF jsonb_typeof(p->'regime_tributario') = 'array' THEN
    SELECT upper(_w8_texto(r->>'forma', 30)) INTO v_regime
      FROM jsonb_array_elements(p->'regime_tributario') r
     WHERE _w8_texto(r->>'forma') IS NOT NULL
     ORDER BY _w8_safe_numeric(r->>'ano') DESC NULLS LAST
     LIMIT 1;
  END IF;

  RETURN jsonb_strip_nulls(jsonb_build_object(
    'razao_social',             _w8_texto(p->>'razao_social'),
    'nome_fantasia',            _w8_texto(p->>'nome_fantasia'),
    'situacao_cadastral',       upper(_w8_texto(p->>'situacao_cadastral', 30)),
    'data_situacao_cadastral',  _w8_safe_date(p->>'data_situacao'),
    'data_abertura',            _w8_safe_date(p->>'data_abertura'),
    'natureza_juridica',        _w8_texto(p->>'natureza_juridica'),
    'regime_tributario',        v_regime,
    'porte',                    upper(_w8_texto(p->>'porte', 30)),
    'unidade',                  CASE _w8_texto(p->>'identificador_matriz_filial')
                                  WHEN '1' THEN 'HEAD_OFFICE'
                                  WHEN '2' THEN 'BRANCH'
                                END,
    'cnae_principal_codigo',    _w8_texto(p->>'cnae_fiscal', 10),
    'cnae_principal_descricao', _w8_texto(p->>'atividade_principal'),
    'municipio',                _w8_texto(p->>'municipio'),
    'uf',                       CASE WHEN v_uf ~ '^[A-Z]{2}$' THEN v_uf END,
    'capital_social',           CASE WHEN _w8_safe_numeric(p->>'capital_social') >= 0
                                     THEN _w8_safe_numeric(p->>'capital_social') END
  ));
END $$;

-- -------------------------------------------------------------
-- 6. Materialização genérica com as 5 regras (spec §6.1):
--    1. valor nulo não sobrescreve;
--    2. observação anterior não sobrescreve observação mais recente;
--    3. campo `locked` não é sobrescrito por coleta automática;
--    4. conflito entre fontes diferentes mantém o vigente e registra;
--    5. não há remoção (ausência de valor nunca apaga).
--    Mesma fonte com observação mais nova e valor diferente = atualização.
--    Linha travada com FOR UPDATE: proveniência atualizada atomicamente.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION materializar_cadastro_cliente(
  p_cliente_id         UUID,
  p_fonte              TEXT,
  p_observed_at        TIMESTAMPTZ,
  p_campos             JSONB,
  p_source_snapshot_id UUID
)
RETURNS TABLE (out_revision BIGINT, out_campos_alterados TEXT[], out_divergencias INTEGER)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  c_campos CONSTANT TEXT[] := ARRAY[
    'razao_social', 'nome_fantasia', 'situacao_cadastral',
    'data_situacao_cadastral', 'data_abertura', 'natureza_juridica',
    'regime_tributario', 'porte', 'unidade', 'cnae_principal_codigo',
    'cnae_principal_descricao', 'municipio', 'uf', 'capital_social'
  ];
  v_row        clientes%ROWTYPE;
  v_new        clientes%ROWTYPE;
  v_cur        JSONB;
  v_prov       JSONB;
  v_campo      TEXT;
  v_obs        JSONB;
  v_obs_norm   JSONB;
  v_atual      JSONB;
  v_fp         JSONB;
  v_fp_obs_at  TIMESTAMPTZ;
  v_changes    JSONB := '{}'::jsonb;
  v_alterados  TEXT[] := ARRAY[]::TEXT[];
  v_div        INTEGER := 0;
  v_revision   BIGINT;
BEGIN
  IF p_fonte IS NULL OR p_observed_at IS NULL OR p_source_snapshot_id IS NULL THEN
    RAISE EXCEPTION 'fonte, observed_at e source_snapshot_id são obrigatórios';
  END IF;

  SELECT * INTO v_row FROM clientes WHERE id = p_cliente_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'cliente % inexistente', p_cliente_id;
  END IF;

  v_cur  := to_jsonb(v_row);
  v_prov := coalesce(v_row.field_provenance, '{}'::jsonb);

  FOREACH v_campo IN ARRAY c_campos LOOP
    v_obs := p_campos -> v_campo;
    CONTINUE WHEN v_obs IS NULL OR jsonb_typeof(v_obs) = 'null';        -- regra 1 e 5

    v_fp := v_prov -> v_campo;
    CONTINUE WHEN coalesce((v_fp->>'locked')::boolean, FALSE);          -- regra 3

    v_fp_obs_at := (v_fp->>'observed_at')::timestamptz;
    CONTINUE WHEN v_fp_obs_at IS NOT NULL AND v_fp_obs_at > p_observed_at;  -- regra 2

    -- normaliza o valor observado pelo tipo real da coluna
    v_obs_norm := to_jsonb(jsonb_populate_record(NULL::clientes,
                                                 jsonb_build_object(v_campo, v_obs))) -> v_campo;
    CONTINUE WHEN v_obs_norm IS NULL OR jsonb_typeof(v_obs_norm) = 'null';
    v_atual := v_cur -> v_campo;

    IF v_atual IS NULL OR jsonb_typeof(v_atual) = 'null' THEN
      v_changes   := v_changes || jsonb_build_object(v_campo, v_obs_norm);
      v_alterados := v_alterados || v_campo;
      v_prov := jsonb_set(v_prov, ARRAY[v_campo],
                  jsonb_build_object('source', p_fonte, 'observed_at', p_observed_at,
                                     'snapshot_id', p_source_snapshot_id));
    ELSIF v_atual = v_obs_norm THEN
      -- mesmo valor: só renova a confirmação quando a fonte é a mesma
      IF v_fp IS NULL OR v_fp->>'source' = p_fonte THEN
        v_prov := jsonb_set(v_prov, ARRAY[v_campo],
                    jsonb_build_object('source', p_fonte, 'observed_at', p_observed_at,
                                       'snapshot_id', p_source_snapshot_id));
      END IF;
    ELSIF v_fp IS NULL OR v_fp->>'source' = p_fonte THEN
      -- mesma fonte, observação mais nova, valor diferente: atualização
      v_changes   := v_changes || jsonb_build_object(v_campo, v_obs_norm);
      v_alterados := v_alterados || v_campo;
      v_prov := jsonb_set(v_prov, ARRAY[v_campo],
                  jsonb_build_object('source', p_fonte, 'observed_at', p_observed_at,
                                     'snapshot_id', p_source_snapshot_id));
    ELSE
      -- regra 4: fontes diferentes, valores diferentes
      INSERT INTO clientes_divergencias_cadastrais (
        cliente_id, campo, valor_vigente, fonte_vigente, valor_observado,
        fonte_observada, observed_at, source_snapshot_id
      ) VALUES (
        p_cliente_id, v_campo, v_atual, v_fp->>'source', v_obs_norm,
        p_fonte, p_observed_at, p_source_snapshot_id
      )
      ON CONFLICT (cliente_id, campo, source_snapshot_id) DO NOTHING;
      IF FOUND THEN v_div := v_div + 1; END IF;
    END IF;
  END LOOP;

  v_revision := v_row.cadastro_revision;

  IF cardinality(v_alterados) > 0 THEN
    v_new := jsonb_populate_record(v_row, v_changes);
    v_revision := v_row.cadastro_revision + 1;

    UPDATE clientes SET
      razao_social             = v_new.razao_social,
      nome_fantasia            = v_new.nome_fantasia,
      situacao_cadastral       = v_new.situacao_cadastral,
      data_situacao_cadastral  = v_new.data_situacao_cadastral,
      data_abertura            = v_new.data_abertura,
      natureza_juridica        = v_new.natureza_juridica,
      regime_tributario        = v_new.regime_tributario,
      porte                    = v_new.porte,
      unidade                  = v_new.unidade,
      cnae_principal_codigo    = v_new.cnae_principal_codigo,
      cnae_principal_descricao = v_new.cnae_principal_descricao,
      municipio                = v_new.municipio,
      uf                       = v_new.uf,
      capital_social           = v_new.capital_social,
      cadastro_revision        = v_revision,
      field_provenance         = v_prov,
      ultima_coleta_cadastral  = GREATEST(coalesce(v_row.ultima_coleta_cadastral, p_observed_at), p_observed_at)
    WHERE id = p_cliente_id;

    INSERT INTO clientes_historico (
      cliente_id, cadastro_revision, registro, campos_alterados, fonte,
      source_snapshot_id, observed_at
    ) VALUES (
      p_cliente_id, v_revision,
      to_jsonb(v_new) - 'field_provenance' - 'created_at' - 'updated_at'
                      - 'cadastro_revision' - 'ultima_coleta_cadastral',
      v_alterados, p_fonte, p_source_snapshot_id, p_observed_at
    );
  ELSIF v_prov IS DISTINCT FROM coalesce(v_row.field_provenance, '{}'::jsonb) THEN
    -- só confirmação: renova proveniência sem nova revisão
    UPDATE clientes SET
      field_provenance        = v_prov,
      ultima_coleta_cadastral = GREATEST(coalesce(v_row.ultima_coleta_cadastral, p_observed_at), p_observed_at)
    WHERE id = p_cliente_id;
  END IF;

  RETURN QUERY SELECT v_revision, v_alterados, v_div;
END;
$$;

-- -------------------------------------------------------------
-- 7. Projeção a partir do log: a aplicação só informa o snapshot.
--    Só brasil_api, completed, OK e não degradado. Idempotente.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION projetar_cadastro_de_snapshot(p_snapshot_id UUID)
RETURNS TABLE (out_revision BIGINT, out_campos_alterados TEXT[], out_divergencias INTEGER)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  v_snap cliente_snapshots%ROWTYPE;
  v_res  RECORD;
BEGIN
  SELECT * INTO v_snap FROM cliente_snapshots WHERE id = p_snapshot_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'cliente_snapshot % inexistente', p_snapshot_id;
  END IF;

  IF v_snap.component::text <> 'brasil_api'
     OR v_snap.status::text <> 'completed'
     OR v_snap.result_state <> 'OK'
     OR v_snap.degradado THEN
    RETURN QUERY SELECT NULL::BIGINT, ARRAY[]::TEXT[], 0;
    RETURN;
  END IF;

  SELECT * INTO v_res FROM materializar_cadastro_cliente(
    v_snap.cliente_id, 'BRASIL_API', v_snap.collected_at,
    mapear_cadastro_brasil_api(v_snap.parsed_result), v_snap.id
  );

  IF v_snap.source_operation_id IS NOT NULL THEN
    UPDATE operations
       SET cliente_cadastro_revision = v_res.out_revision
     WHERE id = v_snap.source_operation_id
       AND cliente_cadastro_revision IS NULL;
  END IF;

  RETURN QUERY SELECT v_res.out_revision, v_res.out_campos_alterados, v_res.out_divergencias;
END;
$$;

-- -------------------------------------------------------------
-- 8. Segurança
-- -------------------------------------------------------------
ALTER TABLE clientes_historico ENABLE ROW LEVEL SECURITY;
ALTER TABLE clientes_divergencias_cadastrais ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all" ON clientes_historico;
CREATE POLICY "service_role_all" ON clientes_historico
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP POLICY IF EXISTS "service_role_all" ON clientes_divergencias_cadastrais;
CREATE POLICY "service_role_all" ON clientes_divergencias_cadastrais
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

REVOKE ALL ON FUNCTION materializar_cadastro_cliente(UUID, TEXT, TIMESTAMPTZ, JSONB, UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION materializar_cadastro_cliente(UUID, TEXT, TIMESTAMPTZ, JSONB, UUID)
  TO service_role;

REVOKE ALL ON FUNCTION projetar_cadastro_de_snapshot(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION projetar_cadastro_de_snapshot(UUID) TO service_role;

REVOKE ALL ON FUNCTION mapear_cadastro_brasil_api(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION _w8_safe_date(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION _w8_safe_numeric(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION _w8_texto(TEXT, INTEGER) FROM PUBLIC, anon, authenticated;

COMMIT;

-- -------------------------------------------------------------
-- 9. Backfill a partir do log (idempotente; a ordem não importa,
--    pois a regra 2 impede que observação antiga sobrescreva a nova).
-- -------------------------------------------------------------
SELECT count(*) AS snapshots_projetados
  FROM (
    SELECT projetar_cadastro_de_snapshot(cs.id)
      FROM cliente_snapshots cs
     WHERE cs.component = 'brasil_api'
       AND cs.status = 'completed'
       AND cs.result_state = 'OK'
       AND NOT cs.degradado
     ORDER BY cs.collected_at
  ) x;

NOTIFY pgrst, 'reload schema';
