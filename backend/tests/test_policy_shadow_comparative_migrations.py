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
