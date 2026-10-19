-- Achado informativo de antecipações/cessões já ativas no Portal da Transparência.
-- Entregue para aplicação controlada; não aplicar automaticamente.

INSERT INTO finding_catalog (codigo,versao,escopo,classe_padrao,natureza,tipo_valor,descricao) VALUES
  ('antecipacao_concorrente', 1, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Antecipações ou cessões concorrentes identificadas nas ordens bancárias')
ON CONFLICT (codigo,versao) DO NOTHING;

NOTIFY pgrst, 'reload schema';
