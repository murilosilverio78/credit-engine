-- Catálogo informativo dos pagamentos efetivos do Portal da Transparência.
-- Entregue para aplicação controlada; não aplicar automaticamente.

INSERT INTO finding_catalog (codigo,versao,escopo,classe_padrao,natureza,tipo_valor,descricao) VALUES
  ('prazo_pagamento_dias', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Prazo mediano entre liquidação e ordem bancária'),
  ('ciclo_caixa_dias', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Ciclo mediano entre emissão da fatura e ordem bancária'),
  ('faturas_liquidadas_sem_pagamento', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Faturas liquidadas sem ordem bancária conciliada'),
  ('taxa_conciliacao_pagamentos', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'NUMERO', 'Taxa de conciliação entre faturas liquidadas e ordens bancárias'),
  ('pago_portal_vs_comprasnet', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Comparação entre pagamentos Portal e liquidações Comprasnet')
ON CONFLICT (codigo,versao) DO NOTHING;

NOTIFY pgrst, 'reload schema';
