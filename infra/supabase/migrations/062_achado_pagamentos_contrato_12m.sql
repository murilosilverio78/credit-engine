-- Achado informativo de pagamentos de contratos sem faturamento no Comprasnet.
-- Entregue para aplicação controlada; não aplicar automaticamente.

INSERT INTO finding_catalog (codigo,versao,escopo,classe_padrao,natureza,tipo_valor,descricao) VALUES
  ('pagamentos_contrato_12m', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Ordens bancárias válidas do contrato nos últimos 12 meses')
ON CONFLICT (codigo,versao) DO NOTHING;

NOTIFY pgrst, 'reload schema';
