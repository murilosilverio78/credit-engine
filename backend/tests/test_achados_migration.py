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
