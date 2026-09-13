-- =============================================================
-- Migration 032 — W8 etapa 1b.3: sinais financeiros por cliente
--
-- Projeta a observação de recursos_recebidos (cliente_snapshots) em
-- medições financeiras versionadas por coleta, com a série anual
-- associada. Continua sem nenhum leitor.
--
-- Execução MANUAL no Supabase SQL Editor. Idempotente.
-- Backfill ao final (seção 8).
--
-- Formato real do worker (recursos_recebidos._build_snapshot):
--   faturamento_verificado_12m, meses_com_recebimento,
--   primeira_competencia / ultima_competencia  -> "MM/AAAA"
--   valor_por_ano (= serie_anual)              -> {"2026": 408068.30}
--   concentracao {hhi, n_orgaos, top_orgao, top_participacao, faixa}
--   volatilidade {cv, anos_completos, maior_queda_anual_pct}
--   reconciliacao {status, divergencia_pct, janela_inicio, janela_fim, ...}
--   fonte_primaria -> BROADFACTOR | PORTAL_TRANSPARENCIA
--
-- Nada é recalculado aqui: a migration só projeta o que o worker mediu.
-- =============================================================

BEGIN;

DO $$ BEGIN
  IF to_regclass('public.cliente_snapshots') IS NULL THEN
    RAISE EXCEPTION 'migration 027 (etapa 1a) não aplicada';
  END IF;
END $$;

-- -------------------------------------------------------------
-- 1. cliente_sinais_financeiros (spec §6.5)
--    UNIQUE (source_snapshot_id): uma medição por coleta, sem upsert
--    global — o vigente é a coleta utilizável mais recente.
--    top_participacao é gravado como fração (o worker entrega fração).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cliente_sinais_financeiros (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id                  UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  source_snapshot_id          UUID NOT NULL
                              REFERENCES cliente_snapshots(id) ON DELETE CASCADE,
  coletado_em                 TIMESTAMPTZ NOT NULL,
  competencia_ate             DATE,

  janela_12m_inicio           DATE,
  janela_12m_fim              DATE,
  faturamento_verificado_12m  NUMERIC(15,2),
  meses_com_recebimento       INTEGER,
  primeira_competencia        DATE,
  ultima_competencia          DATE,
  total_registros             INTEGER,
  valor_total_recebido        NUMERIC(15,2),

  hhi                         NUMERIC(8,2),
  hhi_faixa                   VARCHAR(20),
  n_orgaos                    INTEGER,
  top_orgao                   TEXT,
  top_participacao            NUMERIC(7,6),

  cv_volatilidade             NUMERIC(8,6),
  anos_completos              INTEGER,
  maior_queda_anual_pct       NUMERIC(8,4),

  fonte_primaria              VARCHAR(30),
  reconciliacao_status        VARCHAR(30),
  reconciliacao_divergencia   NUMERIC(8,4),
  source_operation_id         UUID REFERENCES operations(id) ON DELETE SET NULL,
  source_cotacao_id           VARCHAR(100),

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT cliente_sinais_hhi_check
    CHECK (hhi IS NULL OR hhi BETWEEN 0 AND 10000),
  CONSTRAINT cliente_sinais_top_part_check
    CHECK (top_participacao IS NULL OR top_participacao BETWEEN 0 AND 1),
  CONSTRAINT cliente_sinais_meses_check
    CHECK (meses_com_recebimento IS NULL OR meses_com_recebimento >= 0),
  CONSTRAINT cliente_sinais_anos_check
    CHECK (anos_completos IS NULL OR anos_completos >= 0),
  CONSTRAINT cliente_sinais_competencias_check
    CHECK (ultima_competencia IS NULL OR primeira_competencia IS NULL
           OR ultima_competencia >= primeira_competencia),
  CONSTRAINT cliente_sinais_snapshot_unique UNIQUE (source_snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_sinais_cliente_recente
  ON cliente_sinais_financeiros (cliente_id, coletado_em DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_sinais_hhi
  ON cliente_sinais_financeiros (hhi);

-- -------------------------------------------------------------
-- 2. cliente_faturamento_anual (spec §6.6)
--    Versionada pelo sinal: sem upsert global por cliente e ano.
--    `parcial` marca o ano corrente (ainda em curso na coleta).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cliente_faturamento_anual (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sinal_id          UUID NOT NULL
                    REFERENCES cliente_sinais_financeiros(id) ON DELETE CASCADE,
  ano               INTEGER NOT NULL,
  valor_total       NUMERIC(15,2) NOT NULL,
  n_pagamentos      INTEGER,
  parcial           BOOLEAN NOT NULL DEFAULT FALSE,

  CONSTRAINT cliente_faturamento_ano_check CHECK (ano BETWEEN 2000 AND 2200),
  CONSTRAINT cliente_faturamento_valor_check CHECK (valor_total >= 0),
  CONSTRAINT cliente_faturamento_pagamentos_check
    CHECK (n_pagamentos IS NULL OR n_pagamentos >= 0),
  CONSTRAINT cliente_faturamento_unique UNIQUE (sinal_id, ano)
);

CREATE INDEX IF NOT EXISTS idx_faturamento_anual_sinal
  ON cliente_faturamento_anual (sinal_id, ano);

-- -------------------------------------------------------------
-- 3. Conversão de competência "MM/AAAA" -> primeiro dia do mês.
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION _w8_competencia(p TEXT) RETURNS DATE
LANGUAGE plpgsql IMMUTABLE SET search_path = public AS $$
BEGIN
  IF p IS NULL OR btrim(p) !~ '^\d{2}/\d{4}$' THEN RETURN NULL; END IF;
  RETURN to_date('01/' || btrim(p), 'DD/MM/YYYY');
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;

-- -------------------------------------------------------------
-- 4. Projeção de uma observação financeira.
--    Só recursos_recebidos, completed, OK e não degradado.
--    Idempotente pelo UNIQUE (source_snapshot_id).
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION projetar_sinais_financeiros_de_snapshot(p_snapshot_id UUID)
RETURNS TABLE (out_sinal_id UUID, out_inserido BOOLEAN, out_anos INTEGER)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  v_snap     cliente_snapshots%ROWTYPE;
  p          JSONB;
  v_conc     JSONB;
  v_vol      JSONB;
  v_rec      JSONB;
  v_sinal    UUID;
  v_ano_atual INTEGER;
  v_anos     INTEGER := 0;
BEGIN
  SELECT * INTO v_snap FROM cliente_snapshots WHERE id = p_snapshot_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'cliente_snapshot % inexistente', p_snapshot_id;
  END IF;

  IF v_snap.component::text <> 'recursos_recebidos'
     OR v_snap.status::text <> 'completed'
     OR v_snap.result_state <> 'OK'
     OR v_snap.degradado THEN
    RETURN QUERY SELECT NULL::UUID, FALSE, 0;
    RETURN;
  END IF;

  SELECT id INTO v_sinal
    FROM cliente_sinais_financeiros
   WHERE source_snapshot_id = p_snapshot_id;
  IF v_sinal IS NOT NULL THEN
    RETURN QUERY SELECT v_sinal, FALSE,
      (SELECT count(*)::int FROM cliente_faturamento_anual WHERE sinal_id = v_sinal);
    RETURN;
  END IF;

  p := coalesce(v_snap.parsed_result, '{}'::jsonb);
  IF jsonb_typeof(p) <> 'object' THEN
    RETURN QUERY SELECT NULL::UUID, FALSE, 0;
    RETURN;
  END IF;

  v_conc := CASE WHEN jsonb_typeof(p->'concentracao') = 'object'
                 THEN p->'concentracao' ELSE '{}'::jsonb END;
  v_vol  := CASE WHEN jsonb_typeof(p->'volatilidade') = 'object'
                 THEN p->'volatilidade' ELSE '{}'::jsonb END;
  v_rec  := CASE WHEN jsonb_typeof(p->'reconciliacao') = 'object'
                 THEN p->'reconciliacao' ELSE '{}'::jsonb END;

  INSERT INTO cliente_sinais_financeiros (
    cliente_id, source_snapshot_id, coletado_em, competencia_ate,
    janela_12m_inicio, janela_12m_fim, faturamento_verificado_12m,
    meses_com_recebimento, primeira_competencia, ultima_competencia,
    total_registros, valor_total_recebido,
    hhi, hhi_faixa, n_orgaos, top_orgao, top_participacao,
    cv_volatilidade, anos_completos, maior_queda_anual_pct,
    fonte_primaria, reconciliacao_status, reconciliacao_divergencia,
    source_operation_id, source_cotacao_id
  ) VALUES (
    v_snap.cliente_id,
    p_snapshot_id,
    v_snap.collected_at,
    _w8_competencia(p->>'periodo_fim'),
    -- janela de 12 meses ancorada na coleta (mesma regra do worker)
    (date_trunc('month', v_snap.collected_at) - interval '11 months')::date,
    (date_trunc('month', v_snap.collected_at) + interval '1 month - 1 day')::date,
    (p->>'faturamento_verificado_12m')::numeric,
    (p->>'meses_com_recebimento')::int,
    _w8_competencia(p->>'primeira_competencia'),
    _w8_competencia(p->>'ultima_competencia'),
    (p->>'total_registros')::int,
    (p->>'valor_total_recebido')::numeric,
    (v_conc->>'hhi')::numeric,
    left(v_conc->>'faixa', 20),
    (v_conc->>'n_orgaos')::int,
    v_conc->>'top_orgao',
    (v_conc->>'top_participacao')::numeric,
    (v_vol->>'cv')::numeric,
    (v_vol->>'anos_completos')::int,
    (v_vol->>'maior_queda_anual_pct')::numeric,
    left(p->>'fonte_primaria', 30),
    left(v_rec->>'status', 30),
    (v_rec->>'divergencia_pct')::numeric,
    v_snap.source_operation_id,
    v_snap.source_cotacao_id
  )
  RETURNING id INTO v_sinal;

  v_ano_atual := extract(year FROM v_snap.collected_at)::int;

  IF jsonb_typeof(p->'valor_por_ano') = 'object' THEN
    INSERT INTO cliente_faturamento_anual (sinal_id, ano, valor_total, parcial)
    SELECT v_sinal,
           k::int,
           GREATEST(v::numeric, 0),
           k::int >= v_ano_atual
      FROM jsonb_each_text(p->'valor_por_ano') AS s(k, v)
     WHERE k ~ '^\d{4}$'
       AND k::int BETWEEN 2000 AND 2200
    ON CONFLICT (sinal_id, ano) DO NOTHING;
    GET DIAGNOSTICS v_anos = ROW_COUNT;
  END IF;

  RETURN QUERY SELECT v_sinal, TRUE, v_anos;
END;
$$;

-- -------------------------------------------------------------
-- 5. Sinal vigente por cliente (coleta utilizável mais recente;
--    empate por id DESC, conforme §6.5).
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW vw_cliente_sinal_financeiro_vigente
WITH (security_invoker = true) AS
SELECT DISTINCT ON (s.cliente_id)
       s.cliente_id,
       c.cnpj,
       s.id AS sinal_id,
       s.coletado_em,
       s.faturamento_verificado_12m,
       s.meses_com_recebimento,
       s.primeira_competencia,
       s.ultima_competencia,
       s.hhi,
       s.hhi_faixa,
       s.n_orgaos,
       s.top_orgao,
       s.top_participacao,
       s.cv_volatilidade,
       s.anos_completos,
       s.maior_queda_anual_pct,
       s.fonte_primaria,
       s.reconciliacao_status,
       s.reconciliacao_divergencia
  FROM cliente_sinais_financeiros s
  JOIN clientes c ON c.id = s.cliente_id
 ORDER BY s.cliente_id, s.coletado_em DESC, s.id DESC;

-- Série anual do sinal vigente.
CREATE OR REPLACE VIEW vw_cliente_faturamento_anual_vigente
WITH (security_invoker = true) AS
SELECT v.cliente_id, v.cnpj, f.ano, f.valor_total, f.n_pagamentos, f.parcial
  FROM vw_cliente_sinal_financeiro_vigente v
  JOIN cliente_faturamento_anual f ON f.sinal_id = v.sinal_id;

-- -------------------------------------------------------------
-- 6. Segurança
-- -------------------------------------------------------------
ALTER TABLE cliente_sinais_financeiros ENABLE ROW LEVEL SECURITY;
ALTER TABLE cliente_faturamento_anual ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all" ON cliente_sinais_financeiros;
CREATE POLICY "service_role_all" ON cliente_sinais_financeiros
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP POLICY IF EXISTS "service_role_all" ON cliente_faturamento_anual;
CREATE POLICY "service_role_all" ON cliente_faturamento_anual
  FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

REVOKE ALL ON FUNCTION projetar_sinais_financeiros_de_snapshot(UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION projetar_sinais_financeiros_de_snapshot(UUID) TO service_role;
REVOKE ALL ON FUNCTION _w8_competencia(TEXT) FROM PUBLIC, anon, authenticated;

REVOKE ALL ON vw_cliente_sinal_financeiro_vigente FROM PUBLIC, anon, authenticated;
REVOKE ALL ON vw_cliente_faturamento_anual_vigente FROM PUBLIC, anon, authenticated;
GRANT SELECT ON vw_cliente_sinal_financeiro_vigente TO service_role;
GRANT SELECT ON vw_cliente_faturamento_anual_vigente TO service_role;

COMMIT;

-- -------------------------------------------------------------
-- 7. Backfill (idempotente, em ordem cronológica).
-- -------------------------------------------------------------
SELECT count(*) AS snapshots_projetados
  FROM (
    SELECT projetar_sinais_financeiros_de_snapshot(cs.id)
      FROM cliente_snapshots cs
     WHERE cs.component = 'recursos_recebidos'
       AND cs.status = 'completed'
       AND cs.result_state = 'OK'
       AND NOT cs.degradado
     ORDER BY cs.collected_at
  ) x;

NOTIFY pgrst, 'reload schema';
