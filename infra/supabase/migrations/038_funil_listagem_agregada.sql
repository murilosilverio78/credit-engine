-- Paginação, filtros e totais do funil executados no PostgreSQL.
-- Evita o limite padrão de 1000 linhas do PostgREST nos cartões do funil.

CREATE OR REPLACE FUNCTION listar_funil_operacoes(
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
  cotacao_id VARCHAR,
  cnpj VARCHAR,
  nome_fornecedor TEXT,
  valor_solicitado NUMERIC,
  margem_disponivel NUMERIC,
  saldo_vincendo NUMERIC,
  valor_enquadrado NUMERIC,
  tipo VARCHAR,
  data_expiracao DATE,
  operation_id UUID,
  estagio VARCHAR,
  estagio_max VARCHAR,
  estagio_motivo TEXT,
  n_documentos INTEGER,
  tipos_documento TEXT[],
  estagio_atualizado_em TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  razao_social VARCHAR,
  source VARCHAR,
  operation_created_at TIMESTAMPTZ,
  rating TEXT,
  score NUMERIC,
  taxa_sugerida NUMERIC,
  relatorio_gerado BOOLEAN,
  total_count BIGINT
)
LANGUAGE SQL
STABLE
AS $$
  WITH filtradas AS (
    SELECT
      q.*,
      o.razao_social,
      o.source,
      o.created_at AS operation_created_at,
      o.rating::TEXT AS operation_rating,
      o.score AS operation_score,
      o.taxa_sugerida AS operation_taxa_sugerida,
      EXISTS (
        SELECT 1
        FROM component_snapshots cs
        WHERE cs.operation_id = q.operation_id
          AND cs.component = 'score_engine'
          AND cs.status = 'completed'
      ) AS score_engine_completed
    FROM cotacoes_broadfactor q
    LEFT JOIN operations o ON o.id = q.operation_id
    WHERE q.ambiente = 'PRODUCAO'
      AND q.estagio = p_estagio
      AND (p_cnpj IS NULL OR q.cnpj = p_cnpj)
      AND (
        p_busca IS NULL
        OR BTRIM(p_busca) = ''
        OR POSITION(
          LOWER(p_busca) IN LOWER(COALESCE(o.razao_social, q.nome_fornecedor, ''))
        ) > 0
        OR (
          p_busca !~ '[[:alpha:]]'
          AND LENGTH(REGEXP_REPLACE(p_busca, '\D', '', 'g')) >= 3
          AND POSITION(
            LOWER(REGEXP_REPLACE(p_busca, '\D', '', 'g')) IN LOWER(q.cnpj)
          ) > 0
        )
      )
  ), com_filtros AS (
    SELECT *
    FROM filtradas f
    WHERE (
        p_rating IS NULL
        OR (f.score_engine_completed AND f.operation_rating = p_rating)
      )
      AND (
        p_relatorio IS NULL
        OR (p_relatorio = 'gerado' AND f.score_engine_completed)
        OR (p_relatorio = 'pendente' AND NOT f.score_engine_completed)
      )
      AND (
        p_tipo_motivo IS NULL
        OR (
          p_tipo_motivo = 'indisponibilidade'
          AND f.estagio_motivo LIKE '%indisponibilidade_fonte:%'
        )
        OR (
          p_tipo_motivo = 'criterio'
          AND EXISTS (
            SELECT 1
            FROM REGEXP_SPLIT_TO_TABLE(COALESCE(f.estagio_motivo, ''), '\s*;\s*') AS motivo(codigo)
            WHERE codigo <> ''
              AND codigo NOT LIKE 'indisponibilidade_fonte:%'
          )
        )
      )
  )
  SELECT
    cotacao_id, cnpj, nome_fornecedor, valor_solicitado, margem_disponivel,
    saldo_vincendo, valor_enquadrado, tipo, data_expiracao, operation_id,
    estagio, estagio_max, estagio_motivo, n_documentos, tipos_documento,
    estagio_atualizado_em, created_at, razao_social, source,
    operation_created_at, operation_rating, operation_score,
    operation_taxa_sugerida, score_engine_completed, COUNT(*) OVER ()
  FROM com_filtros
  ORDER BY estagio_atualizado_em DESC NULLS LAST, cotacao_id
  LIMIT GREATEST(p_limit, 1)
  OFFSET GREATEST(p_offset, 0);
$$;

CREATE OR REPLACE FUNCTION resumo_funil_operacoes()
RETURNS TABLE (
  total_fila BIGINT,
  estagios JSONB,
  relatorios_gerados BIGINT
)
LANGUAGE SQL
STABLE
AS $$
  WITH cotacoes AS (
    SELECT cotacao_id, operation_id, estagio
    FROM cotacoes_broadfactor
    WHERE ambiente = 'PRODUCAO'
  ), estagios_contados AS (
    SELECT COALESCE(JSONB_OBJECT_AGG(estagio, quantidade), '{}'::JSONB) AS valores
    FROM (
      SELECT estagio, COUNT(*)::BIGINT AS quantidade
      FROM cotacoes
      GROUP BY estagio
    ) agrupados
  ), relatorios AS (
    SELECT COUNT(DISTINCT c.operation_id)::BIGINT AS quantidade
    FROM cotacoes c
    JOIN component_snapshots cs ON cs.operation_id = c.operation_id
    WHERE cs.component = 'score_engine'
      AND cs.status = 'completed'
  )
  SELECT total.quantidade, e.valores, r.quantidade
  FROM (SELECT COUNT(*)::BIGINT AS quantidade FROM cotacoes) total
  CROSS JOIN estagios_contados e
  CROSS JOIN relatorios r;
$$;

CREATE OR REPLACE FUNCTION listar_analises_manuais(
  p_incluir_testes BOOLEAN DEFAULT FALSE,
  p_cnpj TEXT DEFAULT NULL,
  p_busca TEXT DEFAULT NULL,
  p_limit INTEGER DEFAULT 20,
  p_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
  id UUID,
  cnpj VARCHAR,
  razao_social VARCHAR,
  status TEXT,
  rating TEXT,
  score NUMERIC,
  taxa_sugerida NUMERIC,
  valor_solicitado NUMERIC,
  created_at TIMESTAMPTZ,
  source VARCHAR,
  ambiente VARCHAR,
  total_count BIGINT
)
LANGUAGE SQL
STABLE
AS $$
  SELECT
    o.id, o.cnpj, o.razao_social, o.status::TEXT, o.rating::TEXT, o.score,
    o.taxa_sugerida, o.valor_solicitado, o.created_at, o.source, o.ambiente,
    COUNT(*) OVER ()
  FROM operations o
  WHERE o.source = 'admin_ui'
    AND (p_incluir_testes OR o.ambiente = 'PRODUCAO')
    AND (p_cnpj IS NULL OR o.cnpj = p_cnpj)
    AND (
      p_busca IS NULL
      OR BTRIM(p_busca) = ''
      OR POSITION(LOWER(p_busca) IN LOWER(COALESCE(o.razao_social, ''))) > 0
      OR (
        p_busca !~ '[[:alpha:]]'
        AND LENGTH(REGEXP_REPLACE(p_busca, '\D', '', 'g')) >= 3
        AND POSITION(
          LOWER(REGEXP_REPLACE(p_busca, '\D', '', 'g')) IN LOWER(o.cnpj)
        ) > 0
      )
    )
  ORDER BY o.created_at DESC
  LIMIT GREATEST(p_limit, 1)
  OFFSET GREATEST(p_offset, 0);
$$;

REVOKE ALL ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION resumo_funil_operacoes()
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION listar_analises_manuais(BOOLEAN, TEXT, TEXT, INTEGER, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER)
  TO service_role;
GRANT EXECUTE ON FUNCTION resumo_funil_operacoes() TO service_role;
GRANT EXECUTE ON FUNCTION listar_analises_manuais(BOOLEAN, TEXT, TEXT, INTEGER, INTEGER)
  TO service_role;

NOTIFY pgrst, 'reload schema';
