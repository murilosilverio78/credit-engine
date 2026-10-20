-- Politica v3: rascunho para observar cessao concorrente, empenho e cobertura.
-- Entregue para aplicacao controlada; nao promove nenhuma politica a SOMBRA ou ATIVA.

INSERT INTO policy_versions (versao, status, descricao)
VALUES (3, 'RASCUNHO', 'v3: concorrência de cessão, empenho e cobertura de parcela')
ON CONFLICT (versao) DO NOTHING;

-- A v3 herda literalmente os parametros e regras vigentes da v2, inclusive
-- o multiplicador de limite 0,85 para rating D definido na migration 056.
INSERT INTO policy_parameters (policy_version_id, chave, valor, unidade, descricao)
SELECT v3.id, p.chave, p.valor, p.unidade, p.descricao
FROM policy_versions v2
JOIN policy_parameters p ON p.policy_version_id = v2.id
JOIN policy_versions v3 ON v3.versao = 3
WHERE v2.versao = 2
ON CONFLICT (policy_version_id, chave) DO NOTHING;

INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
SELECT v3.id, r.codigo, r.classe, r.parametro_alvo, r.direcao, r.magnitude, r.condicao, r.ordem
FROM policy_versions v2
JOIN policy_rules r ON r.policy_version_id = v2.id
JOIN policy_versions v3 ON v3.versao = 3
WHERE v2.versao = 2
ON CONFLICT DO NOTHING;

-- A precedencia preserva a numeracao da v2: veto antes das condicoes,
-- seguido de alçadas. As regras copiadas da v2 continuam imutaveis.
INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
SELECT v3.id, seed.codigo, seed.classe, seed.parametro_alvo, NULL, seed.magnitude, seed.condicao, seed.ordem
FROM policy_versions v3
CROSS JOIN (VALUES
  ('antecipacao_concorrente', 'VETO', NULL::TEXT, NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"comparacao":{"campo":"ativa_mesmo_contrato","op":"==","ref":true}}'::JSONB, 9),
  ('antecipacao_concorrente', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"comparacao":{"campo":"encerrada_mesmo_contrato","op":"==","ref":true},"mensagem":"Declaração de quitação da antecipação anterior no mesmo contrato","documento_exigido":"Declaração de quitação / extrato da conta vinculada"}'::JSONB, 18),
  ('empenho_insuficiente', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"mensagem":"Empenho insuficiente para as parcelas: apresentar reforço de empenho","documento_exigido":"Nota de empenho / reforço"}'::JSONB, 19),
  ('antecipacao_concorrente', 'AJUSTE', 'alcada', 2::NUMERIC,
   '{"estados":["CONFIRMADO"],"comparacao":{"campo":"ativa_outro_contrato","op":"==","ref":true}}'::JSONB, 28),
  ('cobertura_parcela', 'AJUSTE', 'alcada', 2::NUMERIC,
   '{"estados":["CONFIRMADO"],"comparacao":{"campo":"","op":"<","ref":1.25}}'::JSONB, 29)
) AS seed(codigo, classe, parametro_alvo, magnitude, condicao, ordem)
WHERE v3.versao = 3
ON CONFLICT DO NOTHING;

DO $$
DECLARE
  regras_v2 INTEGER;
  regras_v3 INTEGER;
BEGIN
  SELECT count(*) INTO regras_v2
    FROM policy_rules r JOIN policy_versions p ON p.id = r.policy_version_id
   WHERE p.versao = 2;
  SELECT count(*) INTO regras_v3
    FROM policy_rules r JOIN policy_versions p ON p.id = r.policy_version_id
   WHERE p.versao = 3;
  IF regras_v3 <> regras_v2 + 5 THEN
    RAISE EXCEPTION 'Politica v3 deve conter todas as regras da v2 mais 5 regras novas (v2=%, v3=%)', regras_v2, regras_v3;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
