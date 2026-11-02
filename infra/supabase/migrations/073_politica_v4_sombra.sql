-- Promoção da política v4 (índice de regularidade de certidões) para SOMBRA.
-- A v3 vai para ARQUIVADA (índice único permite uma única SOMBRA) e continua
-- carregável por versão para comparação. A partir daqui a v4 fica imutável.
-- financiador_banco_codigo permanece nulo por decisão de 25/10/2026: o veto de
-- antecipação concorrente da v4 equivale ao da v3.
-- Conferência esperada após aplicar:
-- SELECT versao, status, sombra_desde FROM policy_versions ORDER BY versao;
--   3 | ARQUIVADA | <sombra anterior>
--   4 | SOMBRA    | <agora>

DO $$
DECLARE
  v4_id UUID;
  regras_v4 INTEGER;
  parametros_v4 INTEGER;
BEGIN
  SELECT id INTO v4_id FROM policy_versions WHERE versao = 4 AND status = 'RASCUNHO';
  IF v4_id IS NULL THEN
    RAISE EXCEPTION 'Política v4 deve estar em RASCUNHO antes da promoção';
  END IF;

  SELECT count(*) INTO regras_v4 FROM policy_rules WHERE policy_version_id = v4_id;
  IF regras_v4 <> 41 THEN
    RAISE EXCEPTION 'Política v4 deve ter 41 regras; encontrou %', regras_v4;
  END IF;

  SELECT count(*) INTO parametros_v4 FROM policy_parameters WHERE policy_version_id = v4_id;
  IF parametros_v4 <> 37 THEN
    RAISE EXCEPTION 'Política v4 deve ter 37 parâmetros; encontrou %', parametros_v4;
  END IF;

  IF (SELECT count(*) FROM policy_parameters
       WHERE policy_version_id = v4_id
         AND chave IN ('pesos_indice_regularidade_certidoes', 'notas_indice_regularidade_certidoes')) <> 2 THEN
    RAISE EXCEPTION 'Política v4 sem os parâmetros do índice de regularidade';
  END IF;

  IF (SELECT count(*) FROM policy_rules
       WHERE policy_version_id = v4_id AND classe = 'CONDICAO' AND parametro_alvo = 'cp'
         AND codigo IN ('certidao_cnd_federal_pendente', 'certidao_cndt_pendente', 'certidao_fgts_pendente')) <> 3 THEN
    RAISE EXCEPTION 'Política v4 sem as três condições precedentes de certidões';
  END IF;

  -- Índice único permite uma única SOMBRA: arquivar a v3 antes de promover a v4.
  UPDATE policy_versions SET status = 'ARQUIVADA' WHERE versao = 3 AND status = 'SOMBRA';
  UPDATE policy_versions SET status = 'SOMBRA', sombra_desde = now()
   WHERE id = v4_id AND status = 'RASCUNHO';
END $$;

NOTIFY pgrst, 'reload schema';
