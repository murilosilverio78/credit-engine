-- Politica v2: rascunho para comparacao em sombra. Nao promove decisao oficial.
INSERT INTO finding_catalog (codigo, versao, escopo, classe_padrao, natureza, tipo_valor, descricao)
VALUES (
  'exposicao_sobre_pl', 1, 'OPERACAO', 'AJUSTE', 'CODIGO', 'NUMERO',
  'Valor enquadrado dividido pelo patrimonio liquido do exercicio corrente'
)
ON CONFLICT (codigo, versao) DO NOTHING;

INSERT INTO policy_versions (versao, status, descricao)
VALUES (2, 'RASCUNHO', 'v2: efeitos genericos, condicoes precedentes e comparacao com v1')
ON CONFLICT (versao) DO NOTHING;

-- A v2 parte dos parametros deterministas da v1 para preservar a paridade do score.
INSERT INTO policy_parameters (policy_version_id, chave, valor, unidade, descricao)
SELECT v2.id, p.chave, p.valor, p.unidade, p.descricao
FROM policy_versions v1
JOIN policy_parameters p ON p.policy_version_id = v1.id
JOIN policy_versions v2 ON v2.versao = 2
WHERE v1.versao = 1
ON CONFLICT (policy_version_id, chave) DO NOTHING;

INSERT INTO policy_parameters (policy_version_id, chave, valor, descricao)
SELECT id, 'limite_fator_minimo', '0.4'::JSONB,
       'Piso para o produto dos multiplicadores de limite'
FROM policy_versions
WHERE versao = 2
ON CONFLICT (policy_version_id, chave) DO NOTHING;

-- As cinco regras v1 permanecem, inclusive os dois ajustes especiais de paridade.
INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
SELECT v2.id, r.codigo, r.classe, r.parametro_alvo, r.direcao, r.magnitude, r.condicao, r.ordem
FROM policy_versions v1
JOIN policy_rules r ON r.policy_version_id = v1.id
JOIN policy_versions v2 ON v2.versao = 2
WHERE v1.versao = 1
ON CONFLICT DO NOTHING;

-- A ordem documenta a precedencia: veto primeiro; CP, alcada, limite, taxa e info coexistem.
INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
SELECT id, seed.codigo, seed.classe, seed.parametro_alvo, NULL, seed.magnitude, seed.condicao, seed.ordem
FROM policy_versions
CROSS JOIN (VALUES
  ('cessao_previa_mesmo_contrato', 'VETO', NULL::TEXT, NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"confianca_minima":"ALTA"}'::JSONB, 10),
  ('cessao_previa_mesmo_contrato', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO","AMBIGUO"],"mensagem":"Declaração de inexistência de cessão do contrato","documento_exigido":"Declaração de inexistência de cessão do contrato"}'::JSONB, 11),
  ('capital_social_alterado_recente', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"mensagem":"Comprovar integralização do capital","documento_exigido":"Comprovação de integralização do capital"}'::JSONB, 12),
  ('documento_financeiro_desatualizado', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"mensagem":"Enviar documentos atualizados","documento_exigido":"Documentos financeiros atualizados"}'::JSONB, 13),
  ('documento_financeiro_ausente', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"comparacao":{"campo":"familias","op":"!=","ref":[]},"mensagem":"Enviar documentos","documento_exigido":"Documentos financeiros"}'::JSONB, 14),
  ('avalista_sem_irpf', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"mensagem":"IRPF completo do avalista (declaração, não só recibo)","documento_exigido":"IRPF completo do avalista"}'::JSONB, 15),
  ('simples_com_cessao_mao_obra', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"mensagem":"Esclarecer regime tributário","documento_exigido":"Esclarecimento do regime tributário"}'::JSONB, 16),
  ('balanco_inconsistente', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"mensagem":"Balanço que fecha, assinado por contador","documento_exigido":"Balanço assinado por contador"}'::JSONB, 17),
  ('exposicao_sobre_pl', 'AJUSTE', 'alcada', 2::NUMERIC,
   '{"estados":["CONFIRMADO"],"confianca_minima":"MEDIA","comparacao":{"campo":"","op":">","ref":1}}'::JSONB, 20),
  ('exposicao_sobre_pl', 'AJUSTE', 'alcada', 3::NUMERIC,
   '{"estados":["CONFIRMADO"],"confianca_minima":"MEDIA","comparacao":{"campo":"","op":">","ref":2}}'::JSONB, 21),
  ('margem_liquida', 'AJUSTE', 'alcada', 2::NUMERIC,
   '{"estados":["CONFIRMADO"],"confianca_minima":"MEDIA","comparacao":{"campo":"","op":"<","ref":0}}'::JSONB, 22),
  ('incoerencia_receita_publica', 'AJUSTE', 'alcada', 2::NUMERIC,
   '{"estados":["CONFLITO"]}'::JSONB, 23),
  ('salto_escala_real', 'AJUSTE', 'alcada', 2::NUMERIC,
   '{"estados":["CONFIRMADO"],"confianca_minima":"MEDIA","comparacao":{"campo":"","op":">","ref":3}}'::JSONB, 24),
  ('patrimonio_liquido_negativo', 'AJUSTE', 'alcada', 3::NUMERIC,
   '{"estados":["CONFIRMADO"]}'::JSONB, 25),
  ('rating', 'AJUSTE', 'alcada', 2::NUMERIC, '{"rating_in":["D"]}'::JSONB, 26),
  ('rating', 'AJUSTE', 'alcada', 3::NUMERIC, '{"rating_in":["E"]}'::JSONB, 27),
  ('patrimonio_liquido_negativo', 'AJUSTE', 'limite_mult', 0.5::NUMERIC,
   '{"estados":["CONFIRMADO"]}'::JSONB, 30),
  ('alavancagem_declarada', 'AJUSTE', 'limite_mult', 0.8::NUMERIC,
   '{"estados":["CONFIRMADO"],"confianca_minima":"MEDIA","comparacao":{"campo":"","op":">","ref":1.5}}'::JSONB, 31),
  ('rating', 'AJUSTE', 'limite_mult', 0.7::NUMERIC, '{"rating_in":["D"]}'::JSONB, 32),
  ('liquidez_corrente', 'AJUSTE', 'taxa_pp', 0.3::NUMERIC,
   '{"estados":["CONFIRMADO"],"confianca_minima":"MEDIA","comparacao":{"campo":"","op":"<","ref":1}}'::JSONB, 40),
  ('aval_cobertura', 'INFORMATIVO', 'info', NULL::NUMERIC, '{"estados":["CONFIRMADO"]}'::JSONB, 50),
  ('aval_patrimonio_nao_verificavel', 'INFORMATIVO', 'info', NULL::NUMERIC, '{"estados":["CONFIRMADO"]}'::JSONB, 51),
  ('dependencia_publica', 'INFORMATIVO', 'info', NULL::NUMERIC, '{"estados":["CONFIRMADO"]}'::JSONB, 52),
  ('divergencia_documental', 'INFORMATIVO', 'info', NULL::NUMERIC, '{"estados":["CONFIRMADO"]}'::JSONB, 53),
  ('margem_liquida', 'INFORMATIVO', 'info', NULL::NUMERIC, '{"estados":["CONFIRMADO"]}'::JSONB, 54),
  ('receita_declarada_12m', 'INFORMATIVO', 'info', NULL::NUMERIC, '{"estados":["CONFIRMADO"]}'::JSONB, 55)
) AS seed(codigo, classe, parametro_alvo, magnitude, condicao, ordem)
WHERE policy_versions.versao = 2;

NOTIFY pgrst, 'reload schema';
