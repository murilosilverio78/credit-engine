-- Migration 046 — status terminais da triagem Broadfactor.
--
-- IMPORTANTE: os dois blocos abaixo devem permanecer separados. PostgreSQL não
-- permite usar valores recém-adicionados a um enum na mesma transação.

-- ============================================================================
-- BLOCO 1 — executar isoladamente, sem BEGIN/COMMIT envolvendo o bloco 2.
-- ============================================================================
ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'reprovada_triagem';
ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'cotacao_encerrada';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'operation_status_changed';

-- ============================================================================
-- BLOCO 2 — backfill e estruturas auxiliares; idempotente.
-- ============================================================================
BEGIN;

ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS pendencia_coleta BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS broadfactor_ingestion_listagens (
  id BIGSERIAL PRIMARY KEY,
  quote_count INTEGER NOT NULL CHECK (quote_count > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_broadfactor_ingestion_listagens_created
  ON broadfactor_ingestion_listagens (created_at DESC);

-- Mantém a RPC de 042 intacta para compatibilidade e expõe uma assinatura nova
-- com filtro de status terminal e a pendência de coleta. A janela padrão não
-- devolve terminais, evitando que reapareçam em DOCUMENTADA/ENCERRADA.
CREATE OR REPLACE FUNCTION listar_funil_operacoes(
  p_estagio TEXT, p_cnpj TEXT, p_busca TEXT, p_rating TEXT, p_relatorio TEXT,
  p_tipo_motivo TEXT, p_limit INTEGER, p_offset INTEGER,
  p_operation_status TEXT
)
RETURNS TABLE (
  cotacao_id VARCHAR, cnpj VARCHAR, nome_fornecedor TEXT, valor_solicitado NUMERIC,
  margem_disponivel NUMERIC, saldo_vincendo NUMERIC, valor_enquadrado NUMERIC,
  tipo VARCHAR, data_expiracao DATE, operation_id UUID, estagio VARCHAR, estagio_max VARCHAR,
  estagio_motivo TEXT, n_documentos INTEGER, tipos_documento TEXT[], estagio_atualizado_em TIMESTAMPTZ,
  created_at TIMESTAMPTZ, razao_social VARCHAR, source VARCHAR, operation_created_at TIMESTAMPTZ,
  rating TEXT, score NUMERIC, taxa_sugerida NUMERIC, relatorio_gerado BOOLEAN,
  pendencias JSONB, score_flags JSONB, operation_status TEXT, pendencia_coleta BOOLEAN,
  total_count BIGINT
)
LANGUAGE SQL STABLE AS $$
  WITH filtradas AS (
    SELECT f.*, o.status::TEXT AS operation_status,
           COALESCE(o.pendencia_coleta, FALSE) AS pendencia_coleta
      -- Busca SEM paginar na função base (042); filtra o status terminal e só
      -- então pagina, para que LIMIT/OFFSET e total_count reflitam o filtro.
      FROM listar_funil_operacoes(p_estagio, p_cnpj, p_busca, p_rating, p_relatorio,
                                  p_tipo_motivo, 2147483647, 0) f
      LEFT JOIN operations o ON o.id = f.operation_id
     WHERE (
       (p_operation_status IS NOT NULL AND o.status::TEXT = p_operation_status)
       OR (
         p_operation_status IS NULL
         AND COALESCE(o.status::TEXT, '') NOT IN ('reprovada_triagem', 'cotacao_encerrada')
       )
     )
  )
  SELECT cotacao_id, cnpj, nome_fornecedor, valor_solicitado, margem_disponivel,
         saldo_vincendo, valor_enquadrado, tipo, data_expiracao, operation_id,
         estagio, estagio_max, estagio_motivo, n_documentos, tipos_documento,
         estagio_atualizado_em, created_at, razao_social, source, operation_created_at,
         rating, score, taxa_sugerida, relatorio_gerado, pendencias, score_flags,
         operation_status, pendencia_coleta, COUNT(*) OVER ()
    FROM filtradas
   ORDER BY estagio_atualizado_em DESC NULLS LAST, cotacao_id
   LIMIT GREATEST(p_limit, 1) OFFSET GREATEST(p_offset, 0);
$$;
REVOKE ALL ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, TEXT)
  TO service_role;

CREATE OR REPLACE FUNCTION resumo_funil_operacoes()
RETURNS TABLE (
  total_fila BIGINT,
  estagios JSONB,
  relatorios_gerados BIGINT
)
LANGUAGE SQL STABLE AS $$
  WITH cotacoes AS (
    SELECT c.cotacao_id, c.operation_id, c.estagio, o.status::TEXT AS operation_status
      FROM cotacoes_broadfactor c
      LEFT JOIN operations o ON o.id = c.operation_id
     WHERE c.ambiente = 'PRODUCAO'
  ), estagios_contados AS (
    SELECT COALESCE(JSONB_OBJECT_AGG(estagio, quantidade), '{}'::JSONB) AS valores
      FROM (
        SELECT CASE
          WHEN operation_status = 'reprovada_triagem' THEN 'REPROVADAS'
          WHEN operation_status = 'cotacao_encerrada' THEN 'COTACOES_ENCERRADAS'
          ELSE estagio
        END AS estagio, COUNT(*)::BIGINT AS quantidade
        FROM cotacoes
       GROUP BY 1
      ) agrupados
  ), relatorios AS (
    SELECT COUNT(DISTINCT c.operation_id)::BIGINT AS quantidade
      FROM cotacoes c
      JOIN component_snapshots cs ON cs.operation_id = c.operation_id
     WHERE cs.component = 'score_engine' AND cs.status = 'completed'
  )
  SELECT total.quantidade, e.valores, r.quantidade
    FROM (SELECT COUNT(*)::BIGINT AS quantidade FROM cotacoes) total
    CROSS JOIN estagios_contados e
    CROSS JOIN relatorios r;
$$;
REVOKE ALL ON FUNCTION resumo_funil_operacoes() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION resumo_funil_operacoes() TO service_role;

WITH encerradas AS (
  UPDATE operations o
     SET status = 'cotacao_encerrada', pendencia_coleta = FALSE
    FROM cotacoes_broadfactor q
   WHERE q.operation_id = o.id
     AND o.status = 'aguardando_relatorio'
     AND q.estagio = 'ENCERRADA'
  RETURNING o.id, q.estagio_motivo
)
INSERT INTO audit_trail (operation_id, action, actor_type, previous_value, new_value, payload)
SELECT id, 'operation_status_changed', 'system',
       jsonb_build_object('status', 'aguardando_relatorio'),
       jsonb_build_object('status', 'cotacao_encerrada'),
       jsonb_build_object(
         'motivo', 'backfill_046',
         'motivos', ARRAY['cotacao_ausente_na_listagem'],
         'motivos_classificados', jsonb_build_object(
           'reprovacao', ARRAY[]::TEXT[],
           'tecnico', ARRAY['cotacao_ausente_na_listagem']
         )
       )
  FROM encerradas;

WITH reprovadas AS (
  UPDATE operations o
     SET status = 'reprovada_triagem', pendencia_coleta = FALSE
    FROM cotacoes_broadfactor q
   WHERE q.operation_id = o.id
     AND o.status = 'aguardando_relatorio'
     AND q.estagio = 'DOCUMENTADA'
     AND EXISTS (
       SELECT 1
       FROM regexp_split_to_table(COALESCE(q.estagio_motivo, ''), '\s*;\s*') AS motivo(codigo)
        WHERE codigo <> ''
          AND codigo <> 'situacao_cadastral_nao_verificada'
          AND codigo NOT LIKE 'indisponibilidade_fonte:%'
     )
  RETURNING o.id, q.estagio_motivo
)
INSERT INTO audit_trail (operation_id, action, actor_type, previous_value, new_value, payload)
SELECT id, 'operation_status_changed', 'system',
       jsonb_build_object('status', 'aguardando_relatorio'),
       jsonb_build_object('status', 'reprovada_triagem'),
       jsonb_build_object(
         'motivo', 'backfill_046',
         'motivos', regexp_split_to_array(estagio_motivo, '\s*;\s*'),
         'motivos_classificados', jsonb_build_object(
           'reprovacao', ARRAY(
             SELECT m FROM regexp_split_to_table(estagio_motivo, '\s*;\s*') AS t(m)
              WHERE m <> '' AND m <> 'situacao_cadastral_nao_verificada'
                AND m NOT LIKE 'indisponibilidade_fonte:%'),
           'tecnico', ARRAY(
             SELECT m FROM regexp_split_to_table(estagio_motivo, '\s*;\s*') AS t(m)
              WHERE m = 'situacao_cadastral_nao_verificada'
                 OR m LIKE 'indisponibilidade_fonte:%')
         )
       )
  FROM reprovadas;

UPDATE operations o
   SET pendencia_coleta = TRUE
  FROM cotacoes_broadfactor q
 WHERE q.operation_id = o.id
   AND o.status = 'aguardando_relatorio'
   AND q.estagio_motivo IS NOT NULL
   AND EXISTS (
     SELECT 1
       FROM regexp_split_to_table(q.estagio_motivo, '\s*;\s*') AS motivo(codigo)
      WHERE codigo = 'situacao_cadastral_nao_verificada'
         OR codigo LIKE 'indisponibilidade_fonte:%'
   )
   AND NOT EXISTS (
     SELECT 1
       FROM regexp_split_to_table(q.estagio_motivo, '\s*;\s*') AS motivo(codigo)
      WHERE codigo <> ''
        AND codigo <> 'situacao_cadastral_nao_verificada'
        AND codigo NOT LIKE 'indisponibilidade_fonte:%'
   );

COMMIT;

NOTIFY pgrst, 'reload schema';
