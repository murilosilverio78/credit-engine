"""The v4 regularity-index migrations are delivered for controlled application."""

from pathlib import Path


ROOT = Path(__file__).parents[2] / "infra/supabase"


def test_state_and_municipal_certificate_catalog_is_informational():
    sql = (ROOT / "migrations/071_achados_certidoes_estadual_municipal.sql").read_text(
        encoding="utf-8"
    )

    assert "'certidao_cnd_estadual'" in sql
    assert "'certidao_cnd_municipal'" in sql
    assert "'INFORMATIVO'" in sql
    assert "'OBJETO'" in sql


def test_v4_regularidade_migration_is_draft_only_and_parameterized():
    sql = (ROOT / "migrations/072_politica_v4_indice_regularidade.sql").read_text(
        encoding="utf-8"
    )

    assert "versao = 4 AND status = 'RASCUNHO'" in sql
    assert "pesos_indice_regularidade_certidoes" in sql
    assert '"certidao_cnd_federal_pendente": 0.35' in sql
    assert '"certidao_cnd_municipal": 0.05' in sql
    assert "notas_indice_regularidade_certidoes" in sql
    assert '"positiva_com_efeitos_negativa": 0.6' in sql
    assert "CRF vale 30 dias" in sql
    assert "UPDATE policy_versions SET status" not in sql
