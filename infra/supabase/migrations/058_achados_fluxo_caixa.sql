-- Catálogo para o especialista informativo fluxo_caixa.
-- Entregue para aplicação controlada; não aplicar automaticamente.

INSERT INTO finding_catalog (codigo,versao,escopo,classe_padrao,natureza,tipo_valor,descricao) VALUES
  ('mensalidade_faturada_media', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Mensalidade faturada média do contrato cedido'),
  ('meses_sem_fatura_12m', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Meses sem fatura no histórico do contrato'),
  ('glosa_pct_12m', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Percentual de glosa nas faturas'),
  ('prazo_ateste_dias', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Prazo mediano entre emissão e ateste'),
  ('prazo_liquidacao_dias', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Prazo mediano entre emissão e liquidação'),
  ('saldo_empenho_ano', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Saldo de empenho no exercício'),
  ('empenho_insuficiente', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Empenho insuficiente para as competências restantes'),
  ('cobertura_parcela', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Cobertura da parcela estimada pelo fluxo do contrato'),
  ('execucao_vs_contratado', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Execução faturada versus valor contratado')
ON CONFLICT (codigo,versao) DO NOTHING;

NOTIFY pgrst, 'reload schema';
