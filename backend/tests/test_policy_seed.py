"""The policy seed is checked from its SQL source, never a test-side copy."""
from pathlib import Path


SQL = Path(__file__).parents[2] / "infra/supabase/migrations/045_politica_v0.sql"


def test_seed_matches_score_constants_and_risk_parameters():
    from app.services.eligibility_service import PCT_MARGEM_SOBRE_SALDO
    from app.workers.tasks import score_engine

    sql = SQL.read_text(encoding="utf-8")
    for level, score in score_engine.NIVEL_NOTA.items():
        assert f'"{level}":{score}' in sql
    for dimension, weight in score_engine.PESOS_MERITO.items():
        assert f'"{dimension}":{weight:.2f}' in sql
    for key, weight in score_engine.SUBPESOS_CADASTRAL.items():
        assert f'"{key}":{weight:.2f}' in sql
    for value in (6, 6, 6, 10, 6, 2, 0.7, 0.8, 1.08, 1.15, 0.016):
        assert str(value) in sql
    assert f"'pct_margem_sobre_saldo', '{PCT_MARGEM_SOBRE_SALDO:.2f}'::JSONB" in sql


def test_seed_declares_explicit_boundaries_for_official_inclusive_bands():
    sql = SQL.read_text(encoding="utf-8")
    assert '"ate":20,"inclusivo":true,"nota":88' in sql
    assert '"ate":2000000,"inclusivo":true,"nota":88' in sql
