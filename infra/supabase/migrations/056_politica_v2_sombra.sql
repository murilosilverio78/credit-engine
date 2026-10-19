-- Politica v2: fator de limite do rating D 0,7 -> 0,85 (decisao Murilo 18/10/2026)
-- e promocao da v2 a SOMBRA. A v1 (replica da decisao oficial) vai para ARQUIVADA;
-- continua carregavel por versao para comparacao. A partir daqui a v2 fica imutavel.
-- Conferencia esperada apos aplicar:
-- SELECT versao, status, sombra_desde FROM policy_versions ORDER BY versao;
--   1 | ARQUIVADA | <inalterado>
--   2 | SOMBRA    | <agora>
-- SELECT magnitude FROM policy_rules WHERE id = '3a4813d9-bd51-4daa-a2a1-cdc4704485e9';  -- 0.85


UPDATE policy_rules
   SET magnitude = 0.85
 WHERE id = '3a4813d9-bd51-4daa-a2a1-cdc4704485e9'
   AND policy_version_id = (SELECT id FROM policy_versions WHERE versao = 2 AND status = 'RASCUNHO')
   AND codigo = 'rating'
   AND parametro_alvo = 'limite_mult'
   AND magnitude = 0.7;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM policy_rules
     WHERE id = '3a4813d9-bd51-4daa-a2a1-cdc4704485e9' AND magnitude = 0.85
  ) THEN
    RAISE EXCEPTION 'Regra de limite do rating D nao encontrada ou ja alterada';
  END IF;
END $$;

-- Indice unico permite uma unica SOMBRA: arquivar a v1 antes de promover a v2.
UPDATE policy_versions SET status = 'ARQUIVADA' WHERE versao = 1 AND status = 'SOMBRA';
UPDATE policy_versions SET status = 'SOMBRA', sombra_desde = now()
 WHERE versao = 2 AND status = 'RASCUNHO';

