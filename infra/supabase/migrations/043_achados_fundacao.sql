-- Fase 1: fundacao append-only de achados, sem efeito na decisao.
-- Conferencia esperada apos aplicar (anon | authenticated | service_role):
-- SELECT has_function_privilege('anon', 'registrar_achados(jsonb,jsonb)', 'EXECUTE'),
--        has_function_privilege('authenticated', 'registrar_achados(jsonb,jsonb)', 'EXECUTE'),
--        has_function_privilege('service_role', 'registrar_achados(jsonb,jsonb)', 'EXECUTE');
-- Resultado esperado: false | false | true.

CREATE TABLE finding_catalog (
  codigo TEXT NOT NULL,
  versao INT NOT NULL,
  escopo TEXT NOT NULL CHECK (escopo IN ('CEDENTE', 'SACADO', 'CONTRATO', 'OPERACAO')),
  classe_padrao TEXT NOT NULL CHECK (classe_padrao IN ('VETO', 'CONDICAO', 'AJUSTE', 'INFORMATIVO')),
  natureza TEXT NOT NULL CHECK (natureza IN ('CODIGO', 'LLM')),
  tipo_valor TEXT NOT NULL CHECK (tipo_valor IN ('BOOLEANO', 'NUMERO', 'DATA', 'ENUM', 'OBJETO')),
  descricao TEXT,
  ativo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (codigo, versao)
);

CREATE TABLE finding_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL REFERENCES operations(id),
  ambiente VARCHAR(10) NOT NULL CHECK (ambiente IN ('TESTE', 'PRODUCAO')),
  especialista TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('COMPLETO', 'PARCIAL', 'FALHA')),
  entrada_hash TEXT NOT NULL,
  versao_schema TEXT NOT NULL,
  versao_emissor TEXT NOT NULL,
  modelo TEXT NULL,
  custo_usd NUMERIC NULL,
  duracao_ms INT NULL,
  erro TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (operation_id, especialista, entrada_hash)
);

CREATE TABLE findings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES finding_runs(id),
  operation_id UUID NOT NULL REFERENCES operations(id),
  escopo TEXT NOT NULL CHECK (escopo IN ('CEDENTE', 'SACADO', 'CONTRATO', 'OPERACAO')),
  entidade TEXT,
  codigo TEXT NOT NULL,
  catalogo_versao INT NOT NULL,
  valor JSONB,
  estado TEXT NOT NULL CHECK (estado IN ('CONFIRMADO', 'NEGATIVO_CONFIRMADO', 'AMBIGUO', 'NAO_VERIFICADO', 'CONFLITO')),
  confianca TEXT NOT NULL CHECK (confianca IN ('ALTA', 'MEDIA', 'BAIXA')),
  evidencia JSONB NOT NULL DEFAULT '[]'::JSONB,
  valido_ate TIMESTAMPTZ NULL,
  pendencia JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (codigo, catalogo_versao) REFERENCES finding_catalog(codigo, versao)
);

CREATE INDEX idx_findings_operation_codigo ON findings (operation_id, codigo);
CREATE INDEX idx_findings_run_id ON findings (run_id);

CREATE TABLE policy_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  versao INT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'RASCUNHO' CHECK (status IN ('RASCUNHO', 'ATIVA', 'ARQUIVADA')),
  descricao TEXT,
  criada_por TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ativada_em TIMESTAMPTZ
);

CREATE UNIQUE INDEX policy_versions_one_active ON policy_versions (status) WHERE status = 'ATIVA';

CREATE TABLE policy_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  policy_version_id UUID NOT NULL REFERENCES policy_versions(id),
  codigo TEXT NOT NULL,
  classe TEXT NOT NULL CHECK (classe IN ('VETO', 'CONDICAO', 'AJUSTE', 'INFORMATIVO')),
  parametro_alvo TEXT NULL,
  direcao TEXT NULL,
  magnitude NUMERIC NULL,
  condicao JSONB NOT NULL DEFAULT '{}'::JSONB,
  ordem INT NOT NULL
);

CREATE OR REPLACE FUNCTION raise_append_only()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'Tabela % e append-only', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER trg_finding_catalog_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON finding_catalog
  FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER trg_finding_runs_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON finding_runs
  FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER trg_findings_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON findings
  FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();

CREATE OR REPLACE FUNCTION policy_rules_only_draft()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  v_status TEXT;
  v_policy_version_id UUID;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_policy_version_id := OLD.policy_version_id;
  ELSE
    v_policy_version_id := NEW.policy_version_id;
  END IF;
  SELECT status INTO v_status FROM policy_versions WHERE id = v_policy_version_id;
  IF v_status IS DISTINCT FROM 'RASCUNHO' THEN
    RAISE EXCEPTION 'Regras so podem ser alteradas enquanto a politica esta em RASCUNHO';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_policy_rules_only_draft
  BEFORE INSERT OR UPDATE OR DELETE ON policy_rules
  FOR EACH ROW EXECUTE FUNCTION policy_rules_only_draft();

CREATE OR REPLACE FUNCTION policy_versions_no_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'policy_versions nao permite DELETE';
END;
$$;

CREATE TRIGGER trg_policy_versions_no_delete
  BEFORE DELETE ON policy_versions
  FOR EACH ROW EXECUTE FUNCTION policy_versions_no_delete();

ALTER TABLE finding_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE finding_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_rules ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all_finding_catalog" ON finding_catalog FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_role_all_finding_runs" ON finding_runs FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_role_all_findings" ON findings FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_role_all_policy_versions" ON policy_versions FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_role_all_policy_rules" ON policy_rules FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE OR REPLACE FUNCTION registrar_achados(p_run JSONB, p_achados JSONB)
RETURNS TABLE(run_id UUID, inserido BOOLEAN)
LANGUAGE plpgsql
AS $$
DECLARE
  v_run_id UUID;
BEGIN
  INSERT INTO finding_runs (
    operation_id, ambiente, especialista, status, entrada_hash,
    versao_schema, versao_emissor, modelo, custo_usd, duracao_ms, erro
  ) VALUES (
    (p_run->>'operation_id')::UUID, p_run->>'ambiente', p_run->>'especialista',
    p_run->>'status', p_run->>'entrada_hash', p_run->>'versao_schema',
    p_run->>'versao_emissor', p_run->>'modelo',
    NULLIF(p_run->>'custo_usd', '')::NUMERIC,
    NULLIF(p_run->>'duracao_ms', '')::INT, p_run->>'erro'
  ) ON CONFLICT (operation_id, especialista, entrada_hash) DO NOTHING
  RETURNING id INTO v_run_id;

  IF v_run_id IS NULL THEN
    SELECT id INTO v_run_id FROM finding_runs
    WHERE operation_id = (p_run->>'operation_id')::UUID
      AND especialista = p_run->>'especialista'
      AND entrada_hash = p_run->>'entrada_hash;
    RETURN QUERY SELECT v_run_id, FALSE;
    RETURN;
  END IF;

  INSERT INTO findings (
    id, run_id, operation_id, escopo, entidade, codigo, catalogo_versao,
    valor, estado, confianca, evidencia, valido_ate, pendencia
  )
  SELECT
    COALESCE(NULLIF(item->>'id', '')::UUID, gen_random_uuid()),
    v_run_id, (p_run->>'operation_id')::UUID, item->>'escopo', item->>'entidade',
    item->>'codigo', (item->>'catalogo_versao')::INT,
    COALESCE(item->'valor', 'null'::JSONB), item->>'estado', item->>'confianca',
    COALESCE(item->'evidencia', '[]'::JSONB),
    NULLIF(item->>'valido_ate', '')::TIMESTAMPTZ, item->'pendencia'
  FROM JSONB_ARRAY_ELEMENTS(COALESCE(p_achados, '[]'::JSONB)) AS item;

  RETURN QUERY SELECT v_run_id, TRUE;
END;
$$;

REVOKE EXECUTE ON FUNCTION registrar_achados(JSONB, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION registrar_achados(JSONB, JSONB) TO service_role;

INSERT INTO finding_catalog (codigo, versao, escopo, classe_padrao, natureza, tipo_valor, descricao) VALUES
  ('cadastro_inativo', 1, 'CEDENTE', 'VETO', 'CODIGO', 'BOOLEANO', 'Situacao cadastral nao ativa'),
  ('sancao_ativa', 1, 'CEDENTE', 'VETO', 'CODIGO', 'OBJETO', 'Sancao ativa em base restritiva'),
  ('acordo_leniencia_ativo', 1, 'CEDENTE', 'VETO', 'CODIGO', 'BOOLEANO', 'Acordo de leniencia ativo'),
  ('certidao_cnd_federal_pendente', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'OBJETO', 'Certidao federal pendente'),
  ('certidao_cndt_pendente', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'OBJETO', 'Certidao trabalhista pendente'),
  ('certidao_fgts_pendente', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'OBJETO', 'Certidao FGTS pendente'),
  ('balanco_ausente', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'BOOLEANO', 'Balanco ou DRE ausente'),
  ('balanco_catalogado_broadfactor', 1, 'CEDENTE', 'INFORMATIVO', 'CODIGO', 'OBJETO', 'Documento contabil catalogado'),
  ('idade_empresa_anos', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'NUMERO', 'Idade empresarial'),
  ('capital_social_rs', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'NUMERO', 'Capital social'),
  ('porte_cadastral', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'ENUM', 'Porte cadastral'),
  ('qsa_estabilidade', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'OBJETO', 'Estabilidade do QSA'),
  ('contratos_ativos_qtd', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'NUMERO', 'Quantidade de contratos ativos'),
  ('contratos_total_qtd', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'NUMERO', 'Quantidade total de contratos'),
  ('orgaos_distintos_qtd', 1, 'SACADO', 'AJUSTE', 'CODIGO', 'NUMERO', 'Quantidade de orgaos distintos'),
  ('hhi_recebimentos', 1, 'SACADO', 'AJUSTE', 'CODIGO', 'NUMERO', 'Concentracao de recebimentos'),
  ('meses_com_recebimento', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'NUMERO', 'Meses com recebimento'),
  ('maturidade_max_anos', 1, 'CONTRATO', 'AJUSTE', 'CODIGO', 'NUMERO', 'Maior maturidade contratual'),
  ('volatilidade_cv', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'NUMERO', 'Coeficiente de variacao'),
  ('anos_completos_receita', 1, 'CEDENTE', 'AJUSTE', 'CODIGO', 'NUMERO', 'Anos completos de receita'),
  ('cobertura_exposicao', 1, 'OPERACAO', 'AJUSTE', 'CODIGO', 'NUMERO', 'Cobertura da exposicao'),
  ('glosa_historica', 1, 'CONTRATO', 'AJUSTE', 'CODIGO', 'OBJETO', 'Historico de glosas'),
  ('conta_vinculada_regime', 1, 'CONTRATO', 'AJUSTE', 'CODIGO', 'ENUM', 'Regime de conta vinculada'),
  ('capacidade_operacional', 1, 'CEDENTE', 'AJUSTE', 'LLM', 'ENUM', 'Capacidade operacional'),
  ('reputacao_mercado', 1, 'CEDENTE', 'AJUSTE', 'LLM', 'ENUM', 'Reputacao de mercado'),
  ('alertas_reputacionais', 1, 'CEDENTE', 'INFORMATIVO', 'LLM', 'OBJETO', 'Alertas reputacionais');

NOTIFY pgrst, 'reload schema';
