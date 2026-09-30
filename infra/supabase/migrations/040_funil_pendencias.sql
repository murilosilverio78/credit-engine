-- A função 038 já foi aplicada. A mudança de RETURNS TABLE exige recriação.
DROP FUNCTION IF EXISTS listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER);

CREATE FUNCTION listar_funil_operacoes(
  p_estagio TEXT,
  p_cnpj TEXT DEFAULT NULL,
  p_busca TEXT DEFAULT NULL,
  p_rating TEXT DEFAULT NULL,
  p_relatorio TEXT DEFAULT NULL,
  p_tipo_motivo TEXT DEFAULT NULL,
  p_limit INTEGER DEFAULT 20,
  p_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
  cotacao_id VARCHAR, cnpj VARCHAR, nome_fornecedor TEXT,
  valor_solicitado NUMERIC, margem_disponivel NUMERIC, saldo_vincendo NUMERIC,
  valor_enquadrado NUMERIC, tipo VARCHAR, data_expiracao DATE, operation_id UUID,
  estagio VARCHAR, estagio_max VARCHAR, estagio_motivo TEXT, n_documentos INTEGER,
  tipos_documento TEXT[], estagio_atualizado_em TIMESTAMPTZ, created_at TIMESTAMPTZ,
  razao_social VARCHAR, source VARCHAR, operation_created_at TIMESTAMPTZ,
  rating TEXT, score NUMERIC, taxa_sugerida NUMERIC, relatorio_gerado BOOLEAN,
  pendencias JSONB, score_flags JSONB, total_count BIGINT
)
LANGUAGE SQL
STABLE
AS $$
  WITH filtradas AS (
    SELECT
      q.*, o.razao_social, o.source, o.created_at AS operation_created_at,
      o.rating::TEXT AS operation_rating, o.score AS operation_score,
      o.taxa_sugerida AS operation_taxa_sugerida,
      score_snapshot.parsed_result AS score_engine_result,
      score_snapshot.operation_id IS NOT NULL AS score_engine_completed
    FROM cotacoes_broadfactor q
    LEFT JOIN operations o ON o.id = q.operation_id
    LEFT JOIN LATERAL (
      SELECT cs.operation_id, cs.parsed_result
      FROM component_snapshots cs
      WHERE cs.operation_id = q.operation_id
        AND cs.component = 'score_engine'
        AND cs.status = 'completed'
      LIMIT 1
    ) score_snapshot ON TRUE
    WHERE q.ambiente = 'PRODUCAO'
      AND q.estagio = p_estagio
      AND (p_cnpj IS NULL OR q.cnpj = p_cnpj)
      AND (
        p_busca IS NULL OR BTRIM(p_busca) = ''
        OR POSITION(LOWER(p_busca) IN LOWER(COALESCE(o.razao_social, q.nome_fornecedor, ''))) > 0
        OR (
          p_busca !~ '[[:alpha:]]'
          AND LENGTH(REGEXP_REPLACE(p_busca, '\D', '', 'g')) >= 3
          AND POSITION(LOWER(REGEXP_REPLACE(p_busca, '\D', '', 'g')) IN LOWER(q.cnpj)) > 0
        )
      )
  ), com_filtros AS (
    SELECT * FROM filtradas f
    WHERE (p_rating IS NULL OR (f.score_engine_completed AND f.operation_rating = p_rating))
      AND (p_relatorio IS NULL OR (p_relatorio = 'gerado' AND f.score_engine_completed) OR (p_relatorio = 'pendente' AND NOT f.score_engine_completed))
      AND (
        p_tipo_motivo IS NULL
        OR (p_tipo_motivo = 'indisponibilidade' AND f.estagio_motivo LIKE '%indisponibilidade_fonte:%')
        OR (p_tipo_motivo = 'criterio' AND EXISTS (
          SELECT 1
          FROM REGEXP_SPLIT_TO_TABLE(COALESCE(f.estagio_motivo, ''), '\s*;\s*') AS motivo(codigo)
          WHERE codigo <> '' AND codigo NOT LIKE 'indisponibilidade_fonte:%'
        ))
      )
  )
  SELECT
    cotacao_id, cnpj, nome_fornecedor, valor_solicitado, margem_disponivel,
    saldo_vincendo, valor_enquadrado, tipo, data_expiracao, operation_id,
    estagio, estagio_max, estagio_motivo, n_documentos, tipos_documento,
    estagio_atualizado_em, created_at, razao_social, source,
    operation_created_at, operation_rating, operation_score,
    operation_taxa_sugerida, score_engine_completed,
    (
      SELECT COALESCE(JSONB_AGG(pendencia), '[]'::JSONB)
      FROM (
        SELECT JSONB_BUILD_OBJECT('codigo', 'cnd_federal', 'rotulo', 'CND federal pendente') AS pendencia
        WHERE EXISTS (SELECT 1 FROM component_snapshots cs WHERE cs.operation_id = com_filtros.operation_id AND cs.component = 'cnd_federal' AND cs.status = 'waiting_upload')
        UNION ALL
        SELECT JSONB_BUILD_OBJECT('codigo', 'cndt_tst', 'rotulo', 'CNDT pendente')
        WHERE EXISTS (SELECT 1 FROM component_snapshots cs WHERE cs.operation_id = com_filtros.operation_id AND cs.component = 'cndt_tst' AND cs.status = 'waiting_upload')
        UNION ALL
        SELECT JSONB_BUILD_OBJECT('codigo', 'fgts', 'rotulo', 'FGTS pendente')
        WHERE EXISTS (SELECT 1 FROM component_snapshots cs WHERE cs.operation_id = com_filtros.operation_id AND cs.component = 'fgts' AND cs.status = 'waiting_upload')
        UNION ALL
        SELECT JSONB_BUILD_OBJECT('codigo', 'balanco_ausente', 'rotulo', 'Balanço ausente')
        WHERE NOT (COALESCE(com_filtros.tipos_documento, '{}'::TEXT[]) && ARRAY['PENULTIMO_BALANCO', 'ULTIMO_BALANCO'])
        UNION ALL
        SELECT JSONB_BUILD_OBJECT('codigo', 'contrato_nao_verificado', 'rotulo', 'Contrato não verificado')
        WHERE EXISTS (SELECT 1 FROM component_snapshots cs WHERE cs.operation_id = com_filtros.operation_id AND cs.component = 'contratos_comprasnet' AND cs.parsed_result->>'status_consulta' = 'NAO_VERIFICADO')
      ) itens
    ),
    COALESCE(score_engine_result->'flags', '[]'::JSONB),
    COUNT(*) OVER ()
  FROM com_filtros
  ORDER BY estagio_atualizado_em DESC NULLS LAST, cotacao_id
  LIMIT GREATEST(p_limit, 1)
  OFFSET GREATEST(p_offset, 0);
$$;

REVOKE ALL ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER)
  TO service_role;

NOTIFY pgrst, 'reload schema';
