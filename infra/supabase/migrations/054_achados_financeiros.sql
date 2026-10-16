-- Achados financeiros deterministas; nao altera score, funil ou politica.
ALTER TABLE documentos_financeiros_config
  ADD COLUMN IF NOT EXISTS desconto_especie NUMERIC NOT NULL DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS desconto_quotas_cedente NUMERIC NOT NULL DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS desconto_veiculos NUMERIC NOT NULL DEFAULT 0.6,
  ADD COLUMN IF NOT EXISTS desconto_imoveis NUMERIC NOT NULL DEFAULT 0.5,
  ADD COLUMN IF NOT EXISTS desconto_aplicacoes NUMERIC NOT NULL DEFAULT 0.2,
  ADD COLUMN IF NOT EXISTS desconto_outros NUMERIC NOT NULL DEFAULT 0.5,
  ADD COLUMN IF NOT EXISTS capital_alteracao_recente_meses INTEGER NOT NULL DEFAULT 6,
  ADD COLUMN IF NOT EXISTS simples_atividades_excecao TEXT[] NOT NULL DEFAULT ARRAY['construcao','obras','vigilancia','limpeza','conservacao'];

INSERT INTO finding_catalog (codigo,versao,escopo,classe_padrao,natureza,tipo_valor,descricao) VALUES
('cessao_previa_mesmo_contrato',1,'CONTRATO','VETO','CODIGO','OBJETO','Cessao previa declarada para o contrato da operacao'),
('cessao_recebiveis_outros_contratos',1,'CEDENTE','AJUSTE','CODIGO','OBJETO','Cessoes de recebiveis para outros contratos'),
('patrimonio_liquido_negativo',1,'CEDENTE','CONDICAO','CODIGO','NUMERO','Patrimonio liquido negativo'),
('balanco_inconsistente',1,'CEDENTE','CONDICAO','CODIGO','BOOLEANO','Balanco nao fecha'),
('documento_financeiro_desatualizado',1,'CEDENTE','CONDICAO','CODIGO','OBJETO','Documento financeiro desatualizado'),
('documento_financeiro_ausente',1,'CEDENTE','CONDICAO','CODIGO','OBJETO','Familia financeira nao extraida'),
('liquidez_corrente',1,'CEDENTE','AJUSTE','CODIGO','NUMERO','Liquidez corrente'),
('alavancagem_declarada',1,'CEDENTE','AJUSTE','CODIGO','NUMERO','Alavancagem declarada'),
('servico_divida_sobre_faturamento',1,'CEDENTE','AJUSTE','CODIGO','NUMERO','Servico da divida sobre faturamento'),
('margem_liquida',1,'CEDENTE','INFORMATIVO','CODIGO','NUMERO','Margem liquida'),
('receita_declarada_12m',1,'CEDENTE','INFORMATIVO','CODIGO','NUMERO','Receita declarada em doze meses'),
('divergencia_documental',1,'CEDENTE','AJUSTE','CODIGO','OBJETO','Divergencias entre documentos'),
('capital_social_alterado_recente',1,'CEDENTE','AJUSTE','CODIGO','OBJETO','Capital social alterado recentemente'),
('incoerencia_receita_publica',1,'CEDENTE','AJUSTE','CODIGO','OBJETO','Receita declarada menor que recebimentos publicos'),
('dependencia_publica',1,'CEDENTE','INFORMATIVO','CODIGO','NUMERO','Dependencia de recebimentos publicos'),
('salto_escala_real',1,'CEDENTE','AJUSTE','CODIGO','NUMERO','Salto de escala por contratos publicos'),
('simples_com_cessao_mao_obra',1,'CEDENTE','CONDICAO','CODIGO','BOOLEANO','Simples Nacional com cessao de mao de obra'),
('aval_cobertura',1,'OPERACAO','AJUSTE','CODIGO','NUMERO','Cobertura de aval'),
('aval_patrimonio_nao_verificavel',1,'OPERACAO','INFORMATIVO','CODIGO','OBJETO','Patrimonio de aval nao verificavel'),
('avalista_sem_irpf',1,'OPERACAO','CONDICAO','CODIGO','OBJETO','Administrador sem IRPF extraido')
ON CONFLICT (codigo,versao) DO NOTHING;
NOTIFY pgrst, 'reload schema';
