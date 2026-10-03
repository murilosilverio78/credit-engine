-- Fase 2a: politica deterministica v0, inicialmente apenas em modo SOMBRA.
-- Conferencia esperada (anon | authenticated | service_role):
-- SELECT has_table_privilege('anon', 'policy_shadow_runs', 'SELECT'),
--        has_table_privilege('authenticated', 'policy_shadow_runs', 'SELECT'),
--        has_table_privilege('service_role', 'policy_shadow_runs', 'SELECT');
-- Resultado esperado: false | false | true.

ALTER TABLE policy_versions
  DROP CONSTRAINT IF EXISTS policy_versions_status_check;
ALTER TABLE policy_versions
  ADD CONSTRAINT policy_versions_status_check
  CHECK (status IN ('RASCUNHO', 'SOMBRA', 'ATIVA', 'ARQUIVADA'));
ALTER TABLE policy_versions
  ADD COLUMN IF NOT EXISTS sombra_desde TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS policy_versions_one_shadow
  ON policy_versions (status) WHERE status = 'SOMBRA';

CREATE OR REPLACE FUNCTION policy_versions_only_valid_transitions()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.versao IS DISTINCT FROM OLD.versao THEN
    RAISE EXCEPTION 'id e versao da politica sao imutaveis';
  END IF;
  IF OLD.status = 'RASCUNHO' AND NEW.status = 'RASCUNHO' THEN
    RETURN NEW;
  END IF;
  IF OLD.status = 'RASCUNHO' AND NEW.status = 'SOMBRA' THEN
    IF NEW.sombra_desde IS NULL OR NEW.ativada_em IS DISTINCT FROM OLD.ativada_em
       OR NEW.descricao IS DISTINCT FROM OLD.descricao
       OR NEW.criada_por IS DISTINCT FROM OLD.criada_por
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'RASCUNHO para SOMBRA so permite preencher sombra_desde';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.status = 'SOMBRA' AND NEW.status = 'ATIVA' THEN
    IF NEW.ativada_em IS NULL OR NEW.sombra_desde IS DISTINCT FROM OLD.sombra_desde
       OR NEW.descricao IS DISTINCT FROM OLD.descricao
       OR NEW.criada_por IS DISTINCT FROM OLD.criada_por
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'SOMBRA para ATIVA so permite preencher ativada_em';
    END IF;
    RETURN NEW;
  END IF;
  IF (OLD.status, NEW.status) IN (('RASCUNHO', 'ARQUIVADA'), ('SOMBRA', 'ARQUIVADA'), ('ATIVA', 'ARQUIVADA')) THEN
    IF NEW.sombra_desde IS DISTINCT FROM OLD.sombra_desde
       OR NEW.ativada_em IS DISTINCT FROM OLD.ativada_em
       OR NEW.descricao IS DISTINCT FROM OLD.descricao
       OR NEW.criada_por IS DISTINCT FROM OLD.criada_por
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'Transicao para ARQUIVADA so permite mudar status';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Transicao de politica invalida: % para %', OLD.status, NEW.status;
END;
$$;

CREATE TABLE policy_parameters (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  policy_version_id UUID NOT NULL REFERENCES policy_versions(id),
  chave TEXT NOT NULL,
  valor JSONB NOT NULL,
  unidade TEXT NULL,
  descricao TEXT NULL,
  UNIQUE (policy_version_id, chave)
);

CREATE OR REPLACE FUNCTION policy_parameters_only_draft()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  v_status TEXT;
  v_policy_version_id UUID;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.policy_version_id IS DISTINCT FROM NEW.policy_version_id THEN
    SELECT status INTO v_status FROM policy_versions WHERE id = OLD.policy_version_id;
    IF v_status IS DISTINCT FROM 'RASCUNHO' THEN
      RAISE EXCEPTION 'Parametros nao podem sair de politica nao rascunho';
    END IF;
  END IF;
  v_policy_version_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.policy_version_id ELSE NEW.policy_version_id END;
  SELECT status INTO v_status FROM policy_versions WHERE id = v_policy_version_id;
  IF v_status IS DISTINCT FROM 'RASCUNHO' THEN
    RAISE EXCEPTION 'Parametros so podem ser alterados enquanto a politica esta em RASCUNHO';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_policy_parameters_only_draft
  BEFORE INSERT OR UPDATE OR DELETE ON policy_parameters
  FOR EACH ROW EXECUTE FUNCTION policy_parameters_only_draft();

CREATE TABLE policy_shadow_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES operations(id),
  ambiente VARCHAR(10) NOT NULL CHECK (ambiente IN ('TESTE', 'PRODUCAO')),
  policy_version_id UUID NOT NULL REFERENCES policy_versions(id),
  entrada_hash TEXT NOT NULL,
  data_referencia DATE NOT NULL,
  runs_usados JSONB NOT NULL,
  resultado JSONB NOT NULL,
  oficial JSONB NOT NULL,
  divergencias JSONB NOT NULL DEFAULT '[]'::JSONB,
  classe_geral TEXT NOT NULL CHECK (classe_geral IN ('IGUAL', 'ESPERADA', 'SEM_DADOS', 'INESPERADA')),
  parametros_divergentes JSONB NOT NULL DEFAULT '[]'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (operation_id, policy_version_id, entrada_hash)
);
CREATE INDEX idx_policy_shadow_runs_operation_created
  ON policy_shadow_runs (operation_id, created_at DESC);
CREATE TRIGGER trg_policy_shadow_runs_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON policy_shadow_runs
  FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();

ALTER TABLE policy_parameters ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_shadow_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_policy_parameters" ON policy_parameters FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_role_all_policy_shadow_runs" ON policy_shadow_runs FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE OR REPLACE VIEW vw_policy_shadow_ultimos
WITH (security_invoker = true)
AS
SELECT DISTINCT ON (operation_id) *
FROM policy_shadow_runs
ORDER BY operation_id, created_at DESC;

REVOKE ALL ON policy_parameters, policy_shadow_runs FROM PUBLIC, anon, authenticated;
REVOKE ALL ON vw_policy_shadow_ultimos FROM PUBLIC, anon, authenticated;
GRANT SELECT ON policy_parameters, policy_shadow_runs, vw_policy_shadow_ultimos TO service_role;

INSERT INTO policy_versions (versao, status, descricao)
VALUES (1, 'RASCUNHO', 'v0: paridade com score_engine')
ON CONFLICT (versao) DO NOTHING;

INSERT INTO policy_parameters (policy_version_id, chave, valor)
SELECT id, seed.chave, seed.valor
FROM policy_versions
CROSS JOIN (VALUES
  ('nivel_nota', '{"Excepcional":95,"Forte":85,"Adequado":70,"Atencao":55,"Fraco":40,"Critico":20}'::JSONB),
  ('pesos_merito', '{"relacionamento_governamental":0.30,"porte_operacionalidade":0.28,"saude_cadastral":0.24,"reputacao_mercado":0.18}'::JSONB),
  ('subpesos_cadastral', '{"idade":0.35,"capital":0.29,"porte":0.24,"estabilidade":0.12}'::JSONB),
  ('subpesos_relacionamento', '{"volume":0.30,"concentracao":0.30,"historico":0.25,"maturidade":0.15}'::JSONB),
  ('faixas_idade', '[{"ate":1,"inclusivo":false,"nota":25},{"ate":2,"inclusivo":false,"nota":45},{"ate":5,"inclusivo":false,"nota":60},{"ate":10,"inclusivo":false,"nota":75},{"ate":20,"inclusivo":true,"nota":88},{"nota":95}]'::JSONB),
  ('faixas_capital', '[{"ate":10000,"inclusivo":false,"nota":30},{"ate":50000,"inclusivo":false,"nota":50},{"ate":200000,"inclusivo":false,"nota":65},{"ate":500000,"inclusivo":false,"nota":78},{"ate":2000000,"inclusivo":true,"nota":88},{"nota":95}]'::JSONB),
  ('faixas_porte_cadastral', '{"MEI":35,"EPP":72,"MEDIO":85,"GRANDE":95,"MICRO":58,"natureza_nao_empresarial":55,"capital_ate_500000":58,"capital_ate_2000000":85,"capital_acima_2000000":95,"fallback":58}'::JSONB),
  ('faixas_estabilidade_qsa', '{"sem_dados":75,"maior_3":85,"de_1_a_3":70,"menor_1":55}'::JSONB),
  ('faixas_volume_contratos_ativos', '{"0":30,"1":50,"2_4":68,"5_9":82,"10_mais":92}'::JSONB),
  ('faixas_hhi', '{"menos_2500":90,"ate_6000":70,"acima_6000":45}'::JSONB),
  ('faixas_concentracao_fallback_orgaos', '{"1":45,"2":62,"3_4":78,"5_mais":90}'::JSONB),
  ('faixas_historico_contratos', '{"ate_2":50,"ate_5":68,"ate_12":82,"mais_12":92}'::JSONB),
  ('faixas_maturidade', '{"menos_1":55,"menos_3":72,"3_mais":88}'::JSONB),
  ('faixas_rating', '{"A":85,"B":70,"C":55,"D":40,"E":0}'::JSONB),
  ('faixas_nivel_label', '[{"min":90,"nivel":"Excepcional"},{"min":80,"nivel":"Forte"},{"min":67,"nivel":"Adequado"},{"min":52,"nivel":"Atencao"},{"min":38,"nivel":"Fraco"},{"nivel":"Critico"}]'::JSONB),
  ('score_bloqueio', '20'::JSONB), ('score_cap_dado_material_ausente', '55'::JSONB),
  ('piso_fator_regularidade', '0.75'::JSONB),
  ('haircut_certidao_por_estado', '{"vencida":0.05,"nao_validada":0.05,"positiva_com_efeitos_negativa":0.04,"positiva":0.10,"negativa":0,"indisponivel":0}'::JSONB),
  ('penalidade_cnd_federal_ausente', '6'::JSONB), ('penalidade_cndt_ausente', '6'::JSONB), ('penalidade_fgts_ausente', '6'::JSONB), ('penalidade_balanco_ausente', '10'::JSONB),
  ('hhi_min_meses_recebimento', '6'::JSONB), ('pd_min_anos_completos_volatilidade', '2'::JSONB),
  ('pd_cv_corte_moderado', '0.7'::JSONB), ('pd_cv_corte_alto', '0.8'::JSONB),
  ('pd_mult_historico_insuficiente', '1.08'::JSONB), ('pd_mult_volatilidade_moderada', '1.08'::JSONB), ('pd_mult_volatilidade_alta', '1.15'::JSONB),
  ('pd_performada', '0.016'::JSONB), ('pd_mult_por_rating', '{"A":0.6,"B":1.0,"C":2.0,"D":5.0,"E":10.0}'::JSONB)
) AS seed(chave, valor)
WHERE policy_versions.versao = 1
ON CONFLICT (policy_version_id, chave) DO NOTHING;

INSERT INTO policy_rules (policy_version_id, codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
SELECT id, seed.codigo, seed.classe, seed.parametro_alvo, seed.direcao, seed.magnitude, seed.condicao, seed.ordem
FROM policy_versions
CROSS JOIN (VALUES
  ('cadastro_inativo', 'VETO', NULL::TEXT, NULL::TEXT, NULL::NUMERIC, '{"estado":"CONFIRMADO","confianca":"ALTA"}'::JSONB, 1),
  ('sancao_ativa', 'VETO', NULL::TEXT, NULL::TEXT, NULL::NUMERIC, '{"estado":"CONFIRMADO","confianca":"ALTA"}'::JSONB, 2),
  ('acordo_leniencia_ativo', 'VETO', NULL::TEXT, NULL::TEXT, NULL::NUMERIC, '{"estado":"CONFIRMADO","confianca":"ALTA"}'::JSONB, 3),
  ('glosa_historica', 'AJUSTE', 'pd_multiplicador', 'adverso', 1.15::NUMERIC, '{"taxa_glosa_min":0.02,"faturado_total_min":500000}'::JSONB, 4),
  ('conta_vinculada_regime', 'AJUSTE', 'lgd_multiplicador', 'favoravel', 0.95::NUMERIC, '{"valor":"CONTA_DEPOSITO_VINCULADA","confianca_minima":"ALTA"}'::JSONB, 5)
) AS seed(codigo, classe, parametro_alvo, direcao, magnitude, condicao, ordem)
WHERE policy_versions.versao = 1;

NOTIFY pgrst, 'reload schema';
