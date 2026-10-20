-- Promoção controlada da política v3 para SOMBRA.
-- Entregue para aplicação controlada; não aplicar automaticamente.
-- Conferência esperada após aplicar:
-- SELECT versao, status, sombra_desde FROM policy_versions ORDER BY versao;
--   1 | ARQUIVADA | <inalterado>
--   2 | ARQUIVADA | <sombra anterior>
--   3 | SOMBRA    | <agora>

DO $$
DECLARE
  v3_id UUID;
  regras_v3 INTEGER;
  parametros_v3 INTEGER;
BEGIN
  SELECT id INTO v3_id
    FROM policy_versions
   WHERE versao = 3 AND status = 'RASCUNHO';
  IF v3_id IS NULL THEN
    RAISE EXCEPTION 'Política v3 deve estar em RASCUNHO antes da promoção';
  END IF;

  SELECT count(*) INTO regras_v3 FROM policy_rules WHERE policy_version_id = v3_id;
  IF regras_v3 <> 36 THEN
    RAISE EXCEPTION 'Política v3 deve ter 36 regras; encontrou %', regras_v3;
  END IF;

  SELECT count(*) INTO parametros_v3 FROM policy_parameters WHERE policy_version_id = v3_id;
  IF parametros_v3 <> 34 THEN
    RAISE EXCEPTION 'Política v3 deve ter 34 parâmetros; encontrou %', parametros_v3;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM policy_rules
     WHERE policy_version_id = v3_id
       AND codigo = 'cobertura_parcela'
       AND classe = 'AJUSTE'
       AND parametro_alvo = 'alcada'
       AND condicao -> 'comparacao' = '{"campo":"indice","op":"<","ref":1.25}'::JSONB
  ) THEN
    RAISE EXCEPTION 'Regra v3 cobertura_parcela não compara o índice esperado';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM policy_rules
     WHERE policy_version_id = v3_id
       AND codigo = 'rating'
       AND parametro_alvo = 'limite_mult'
       AND magnitude = 0.85
  ) THEN
    RAISE EXCEPTION 'Regra v3 rating/limite_mult deve ter magnitude 0,85';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM policy_versions WHERE versao = 2 AND status = 'SOMBRA'
  ) THEN
    RAISE EXCEPTION 'Política v2 deve estar em SOMBRA antes da promoção da v3';
  END IF;
END $$;

UPDATE policy_versions SET status = 'ARQUIVADA'
 WHERE versao = 2 AND status = 'SOMBRA';

UPDATE policy_versions SET status = 'SOMBRA', sombra_desde = now()
 WHERE versao = 3 AND status = 'RASCUNHO';

NOTIFY pgrst, 'reload schema';
