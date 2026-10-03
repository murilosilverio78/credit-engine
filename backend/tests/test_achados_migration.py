from pathlib import Path


MIGRATION = Path(__file__).parents[2] / "infra/supabase/migrations/043_achados_fundacao.sql"


def test_achados_migration_has_atomic_rpc_and_service_role_only_grant():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "UNIQUE (operation_id, especialista, entrada_hash)" in sql
    assert "ON CONFLICT (operation_id, especialista, entrada_hash) DO NOTHING" in sql
    assert "REVOKE EXECUTE ON FUNCTION registrar_achados(JSONB, JSONB) FROM PUBLIC, anon, authenticated" in sql
    assert "GRANT EXECUTE ON FUNCTION registrar_achados(JSONB, JSONB) TO service_role" in sql
    assert "false | false | true" in sql


def test_achados_migration_uses_triggers_for_append_only_and_policy_drafts():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE OR TRUNCATE ON finding_catalog" in sql
    assert "BEFORE UPDATE OR DELETE OR TRUNCATE ON finding_runs" in sql
    assert "BEFORE UPDATE OR DELETE OR TRUNCATE ON findings" in sql
    assert "policy_rules_only_draft" in sql
    assert "policy_versions_no_delete" in sql


def test_catalog_v2_migration_is_append_only_and_reloads_postgrest_schema():
    sql = (MIGRATION.parent / "044_catalogo_achados_v2.sql").read_text(encoding="utf-8")
    for code in (
        "natureza_juridica_empresarial",
        "atividade_restrita",
        "contratos_comprasnet_incluidos_qtd",
        "receita_serie_anual",
    ):
        assert code in sql
    assert "INSERT INTO finding_catalog" in sql
    assert "NOTIFY pgrst, 'reload schema';" in sql


def test_policy_v0_migration_adds_shadow_state_and_service_role_only_storage():
    sql = (MIGRATION.parent / "045_politica_v0.sql").read_text(encoding="utf-8")
    assert "'SOMBRA'" in sql
    assert "policy_versions_one_shadow" in sql
    assert "policy_shadow_runs" in sql
    assert "BEFORE UPDATE OR DELETE OR TRUNCATE ON policy_shadow_runs" in sql
    assert "WITH (security_invoker = true)" in sql
    assert "REVOKE ALL ON policy_parameters, policy_shadow_runs FROM PUBLIC, anon, authenticated" in sql
    assert "GRANT SELECT ON policy_parameters, policy_shadow_runs, vw_policy_shadow_ultimos TO service_role" in sql
