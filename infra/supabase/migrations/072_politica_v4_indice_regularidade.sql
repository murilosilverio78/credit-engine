-- Política v4 em RASCUNHO: índice único de regularidade das certidões.
-- Não promove nenhuma política a SOMBRA/ATIVA e não altera a v3.

DO $$
DECLARE
  v4_id UUID;
BEGIN
  SELECT id INTO v4_id
    FROM policy_versions
   WHERE versao = 4 AND status = 'RASCUNHO';
  IF v4_id IS NULL THEN
    RAISE EXCEPTION 'Política v4 deve estar em RASCUNHO para receber o índice de regularidade';
  END IF;

  INSERT INTO policy_parameters (policy_version_id, chave, valor, unidade, descricao) VALUES
    (
      v4_id,
      'pesos_indice_regularidade_certidoes',
      '{
        "certidao_cnd_federal_pendente": 0.35,
        "certidao_cndt_pendente": 0.25,
        "certidao_fgts_pendente": 0.25,
        "certidao_cnd_estadual": 0.10,
        "certidao_cnd_municipal": 0.05
      }'::JSONB,
      'proporcao',
      'Pesos do índice de regularidade; certidões ausentes ficam fora do denominador'
    ),
    (
      v4_id,
      'notas_indice_regularidade_certidoes',
      '{
        "negativa": 1.0,
        "positiva_com_efeitos_negativa": 0.6,
        "vencida": 0.3,
        "nao_validada": 0.3,
        "positiva": 0.0
      }'::JSONB,
      'nota',
      'Notas por estado do índice de regularidade'
    )
  ON CONFLICT (policy_version_id, chave) DO UPDATE
    SET valor = EXCLUDED.valor,
        unidade = EXCLUDED.unidade,
        descricao = EXCLUDED.descricao;

  -- A engine identifica a v4 pela presença dos dois parâmetros acima e, por
  -- isso, deixa de aplicar os haircuts/penalidades legados herdados da v3.
  -- A v3 continua lendo seus próprios parâmetros sem qualquer alteração.
  INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
  SELECT v4_id, seed.codigo, 'CONDICAO', 'cp', NULL, NULL, seed.condicao, seed.ordem
  FROM (
    VALUES
      (
        'certidao_cnd_federal_pendente',
        '{"estados":["CONFIRMADO"],"todas":[{"campo":"estado","op":"==","ref":"ausente"}],"mensagem":"CND Federal ausente: apresentar certidão federal vigente na aprovação","documento_exigido":"Certidão Negativa de Débitos Federais vigente"}'::JSONB,
        20
      ),
      (
        'certidao_cndt_pendente',
        '{"estados":["CONFIRMADO"],"todas":[{"campo":"estado","op":"==","ref":"ausente"}],"mensagem":"CNDT ausente: apresentar certidão trabalhista vigente na aprovação","documento_exigido":"Certidão Negativa de Débitos Trabalhistas vigente"}'::JSONB,
        21
      ),
      (
        'certidao_fgts_pendente',
        '{"estados":["CONFIRMADO"],"todas":[{"campo":"estado","op":"==","ref":"ausente"}],"mensagem":"CRF do FGTS ausente: apresentar Certificado de Regularidade do FGTS vigente na aprovação; a CRF vale 30 dias","documento_exigido":"Certificado de Regularidade do FGTS (CRF) vigente, emitido há no máximo 30 dias"}'::JSONB,
        22
      )
  ) AS seed(codigo, condicao, ordem)
  WHERE NOT EXISTS (
    SELECT 1
      FROM policy_rules existing
     WHERE existing.policy_version_id = v4_id
       AND existing.codigo = seed.codigo
       AND existing.classe = 'CONDICAO'
       AND existing.parametro_alvo = 'cp'
       AND existing.condicao -> 'todas' = '[{"campo":"estado","op":"==","ref":"ausente"}]'::JSONB
  );
END $$;

NOTIFY pgrst, 'reload schema';
