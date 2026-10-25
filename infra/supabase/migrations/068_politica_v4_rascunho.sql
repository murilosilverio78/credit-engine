-- Politica v4: distingue o financiador na antecipacao concorrente.
-- Entregue para aplicacao controlada; nao promove nenhuma politica a SOMBRA ou ATIVA.

INSERT INTO policy_versions (versao, status, descricao)
VALUES (4, 'RASCUNHO', 'v4: financiador da antecipacao concorrente')
ON CONFLICT (versao) DO NOTHING;

-- A v4 herda todos os parametros da v3 e adiciona o banco do financiador representado.
INSERT INTO policy_parameters (policy_version_id, chave, valor, unidade, descricao)
SELECT v4.id, p.chave, p.valor, p.unidade, p.descricao
FROM policy_versions v3
JOIN policy_parameters p ON p.policy_version_id = v3.id
JOIN policy_versions v4 ON v4.versao = 4
WHERE v3.versao = 3
ON CONFLICT (policy_version_id, chave) DO NOTHING;

INSERT INTO policy_parameters (policy_version_id, chave, valor, unidade, descricao)
SELECT id, 'financiador_banco_codigo', 'null'::JSONB, NULL,
       'Código COMPE do banco/financiador que representamos (preencher antes de promover a v4)'
FROM policy_versions
WHERE versao = 4
ON CONFLICT (policy_version_id, chave) DO NOTHING;

-- Copia a v3, exceto o VETO que passa a depender do financiador identificado.
INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
SELECT v4.id, r.codigo, r.classe, r.parametro_alvo, r.direcao, r.magnitude, r.condicao, r.ordem
FROM policy_versions v3
JOIN policy_rules r ON r.policy_version_id = v3.id
JOIN policy_versions v4 ON v4.versao = 4
WHERE v3.versao = 3
  AND NOT (r.codigo = 'antecipacao_concorrente' AND r.classe = 'VETO')
ON CONFLICT DO NOTHING;

-- Mantem as ordens v3: VETO (9), CP de antecipacao (18) e alcada (28).
INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
SELECT v4.id, seed.codigo, seed.classe, seed.parametro_alvo, NULL, seed.magnitude, seed.condicao, seed.ordem
FROM policy_versions v4
CROSS JOIN (VALUES
  ('antecipacao_concorrente', 'VETO', NULL::TEXT, NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"todas":[{"campo":"ativa_mesmo_contrato","op":"==","ref":true},{"campo":"bancos_ativos_mesmo_contrato","op":"nao_contem","ref_parametro":"financiador_banco_codigo"}]}'::JSONB, 9),
  ('antecipacao_concorrente', 'CONDICAO', 'cp', NULL::NUMERIC,
   '{"estados":["CONFIRMADO"],"todas":[{"campo":"ativa_mesmo_contrato","op":"==","ref":true},{"campo":"bancos_ativos_mesmo_contrato","op":"contem","ref_parametro":"financiador_banco_codigo"}],"mensagem":"Antecipação ativa no mesmo contrato com o nosso financiador: apresentar saldo devedor atualizado da operação anterior; a nova operação deve manter o mesmo financiador e a mesma conta domicílio","documento_exigido":"Declaração de saldo devedor atualizado da operação anterior"}'::JSONB, 18),
  ('antecipacao_concorrente', 'AJUSTE', 'alcada', 2::NUMERIC,
   '{"estados":["CONFIRMADO"],"todas":[{"campo":"ativa_mesmo_contrato","op":"==","ref":true},{"campo":"bancos_ativos_mesmo_contrato","op":"contem","ref_parametro":"financiador_banco_codigo"}]}'::JSONB, 28)
) AS seed(codigo, classe, parametro_alvo, magnitude, condicao, ordem)
WHERE v4.versao = 4
ON CONFLICT DO NOTHING;

DO $$
DECLARE
  regras_v3 INTEGER;
  regras_v4 INTEGER;
BEGIN
  SELECT count(*) INTO regras_v3
    FROM policy_rules r JOIN policy_versions p ON p.id = r.policy_version_id
   WHERE p.versao = 3;
  SELECT count(*) INTO regras_v4
    FROM policy_rules r JOIN policy_versions p ON p.id = r.policy_version_id
   WHERE p.versao = 4;
  IF regras_v4 <> regras_v3 - 1 + 3 THEN
    RAISE EXCEPTION 'Politica v4 deve conter regras v3 - 1 + 3 (v3=%, v4=%)', regras_v3, regras_v4;
  END IF;
  IF NOT EXISTS (
    SELECT 1
      FROM policy_parameters p JOIN policy_versions v ON v.id = p.policy_version_id
     WHERE v.versao = 4
       AND p.chave = 'financiador_banco_codigo'
       AND p.valor = 'null'::JSONB
  ) THEN
    RAISE EXCEPTION 'Politica v4 deve conter o parametro financiador_banco_codigo nulo';
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
