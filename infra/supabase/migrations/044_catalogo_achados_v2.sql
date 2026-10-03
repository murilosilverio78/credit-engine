-- Fase 2a: fatos adicionais para a politica em modo sombra.
-- O catalogo e append-only; esta migration nao altera codigos existentes.
INSERT INTO finding_catalog
  (codigo, versao, escopo, classe_padrao, natureza, tipo_valor, descricao)
VALUES
  ('natureza_juridica_empresarial', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'BOOLEANO',
   'Natureza juridica empresarial'),
  ('atividade_restrita', 1, 'CEDENTE', 'INFORMATIVO', 'CODIGO', 'BOOLEANO',
   'Atividade restrita ou incompativel'),
  ('contratos_comprasnet_incluidos_qtd', 1, 'CONTRATO', 'AJUSTE', 'CODIGO', 'NUMERO',
   'Quantidade de contratos verificados no Comprasnet incluidos no relacionamento'),
  ('receita_serie_anual', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'OBJETO',
   'Fatos da serie anual de recebimentos para o ajuste de PD'),
  ('contratos_valor_total_ativo_rs', 1, 'CONTRATO', 'AJUSTE', 'CODIGO', 'NUMERO',
   'Valor total ativo de contratos usado no limite legado');

NOTIFY pgrst, 'reload schema';
