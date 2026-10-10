-- Migration 050 — enquadramento pela capacidade do contrato cedido.
-- Arquivo para execução manual; não altera migrations 043–049.

BEGIN;

ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS valor_enquadrado_pre_capacidade NUMERIC(15,2),
  ADD COLUMN IF NOT EXISTS capacidade_contrato NUMERIC(15,2),
  ADD COLUMN IF NOT EXISTS capacidade_memoria JSONB,
  ADD COLUMN IF NOT EXISTS flags_funil JSONB;

INSERT INTO eligibility_parameters (key, value, unit, label, grupo) VALUES
  ('cap_fator_liquido_mao_obra', 0.645, 'decimal', 'Fator líquido — contratos com dedicação exclusiva de mão de obra', 'capacidade'),
  ('cap_fator_liquido_demais', 0.85, 'decimal', 'Fator líquido — demais contratos', 'capacidade'),
  ('cap_cobertura_parcela', 1.25, 'decimal', 'Cobertura mínima recebimento líquido / parcela', 'capacidade'),
  ('cap_taxa_referencia_am', 0.035, 'decimal', 'Taxa de referência a.m. para o cálculo da capacidade', 'capacidade'),
  ('cap_folga_meses', 1, 'meses', 'Meses de folga antes do fim do prazo firme', 'capacidade'),
  ('alerta_salto_escala', 1.5, 'decimal', 'Faturamento contratado 12m / recebido 12m que dispara alerta', 'capacidade')
ON CONFLICT (key) DO NOTHING;

-- O retorno mudou; PostgreSQL exige remover a assinatura de nove parâmetros
-- executada em produção antes de recriá-la. A assinatura de oito parâmetros
-- de 042 é preservada como função-base.
DROP FUNCTION IF EXISTS listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, TEXT);

CREATE FUNCTION listar_funil_operacoes(
  p_estagio TEXT, p_cnpj TEXT, p_busca TEXT, p_rating TEXT, p_relatorio TEXT,
  p_tipo_motivo TEXT, p_limit INTEGER, p_offset INTEGER, p_operation_status TEXT
)
RETURNS TABLE (
  cotacao_id VARCHAR, cnpj VARCHAR, nome_fornecedor TEXT, valor_solicitado NUMERIC,
  margem_disponivel NUMERIC, saldo_vincendo NUMERIC, valor_enquadrado NUMERIC,
  tipo VARCHAR, data_expiracao DATE, operation_id UUID, estagio VARCHAR, estagio_max VARCHAR,
  estagio_motivo TEXT, n_documentos INTEGER, tipos_documento TEXT[], estagio_atualizado_em TIMESTAMPTZ,
  created_at TIMESTAMPTZ, razao_social VARCHAR, source VARCHAR, operation_created_at TIMESTAMPTZ,
  rating TEXT, score NUMERIC, taxa_sugerida NUMERIC, relatorio_gerado BOOLEAN,
  pendencias JSONB, score_flags JSONB, operation_status TEXT, pendencia_coleta BOOLEAN,
  valor_enquadrado_pre_capacidade NUMERIC, capacidade_contrato NUMERIC,
  capacidade_memoria JSONB, flags_funil JSONB, total_count BIGINT
)
LANGUAGE SQL STABLE AS $$
  WITH filtradas AS (
    SELECT f.*, o.status::TEXT AS operation_status,
           COALESCE(o.pendencia_coleta, FALSE) AS pendencia_coleta,
           o.valor_enquadrado_pre_capacidade, o.capacidade_contrato,
           o.capacidade_memoria, COALESCE(o.flags_funil, '[]'::JSONB) AS flags_funil
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
         operation_status, pendencia_coleta, valor_enquadrado_pre_capacidade,
         capacidade_contrato, capacidade_memoria, flags_funil, COUNT(*) OVER ()
    FROM filtradas
   ORDER BY estagio_atualizado_em DESC NULLS LAST, cotacao_id
   LIMIT GREATEST(p_limit, 1) OFFSET GREATEST(p_offset, 0);
$$;

REVOKE ALL ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, TEXT)
  TO service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';
