"""Comparative shadow and coverage migrations are delivered, not applied in tests."""

from pathlib import Path


ROOT = Path(__file__).parents[2] / "infra/supabase/migrations"


def test_comparative_shadow_migration_adds_nullable_audit_columns_and_check():
    sql = (ROOT / "064_policy_shadow_comparativo.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS comparativo JSONB NULL" in sql
    assert "ADD COLUMN IF NOT EXISTS classe_comparativo TEXT NULL" in sql
    assert "('IGUAL', 'ESPERADA', 'INESPERADA')" in sql
    assert "UPDATE policy_versions" not in sql


def test_coverage_v2_catalog_and_draft_v3_rule_migration_are_delivered():
    sql = (ROOT / "065_cobertura_parcela_objeto_v3.sql").read_text(encoding="utf-8")

    assert "'cobertura_parcela', 2" in sql
    assert "'OBJETO'" in sql
    assert "p.versao = 3" in sql and "p.status = 'RASCUNHO'" in sql
    assert '"campo":"indice","op":"<","ref":1.25' in sql
    assert "UPDATE policy_versions" not in sql


def test_v3_shadow_promotion_aborts_when_rule_or_parameter_counts_do_not_match():
    sql = (ROOT / "066_politica_v3_sombra.sql").read_text(encoding="utf-8")

    assert "IF regras_v3 <> 36 THEN" in sql  # includes the 35-rule abort path
    assert "IF parametros_v3 <> 34 THEN" in sql
    assert "condicao -> 'comparacao' = '{\"campo\":\"indice\",\"op\":\"<\",\"ref\":1.25}'::JSONB" in sql
    assert "magnitude = 0.85" in sql
    assert "versao = 2 AND status = 'SOMBRA'" in sql
    assert "UPDATE policy_versions SET status = 'ARQUIVADA'" in sql
    assert "status = 'SOMBRA', sombra_desde = now()" in sql


def test_shadow_activation_view_exposes_latest_runs_and_comparative_alerts():
    sql = (ROOT / "067_vw_policy_sombra_ativacao.sql").read_text(encoding="utf-8")

    assert "CREATE OR REPLACE VIEW vw_policy_sombra_ativacao" in sql
    assert "row_number() OVER" in sql
    assert "operation.created_at >= policy.sombra_desde" in sql
    assert "classe_comparativo = 'INESPERADA'" in sql
    assert "regras_novas_disparadas" in sql
