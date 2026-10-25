"""The on-demand capacity parameters are delivered for controlled application."""

from pathlib import Path


SQL = Path(__file__).parents[2] / "infra/supabase/migrations/069_capacidade_sob_demanda.sql"


def test_on_demand_capacity_parameters_are_idempotent_and_manual():
    sql = SQL.read_text(encoding="utf-8")

    assert "cap_sob_demanda_janela_meses" in sql
    assert "cap_sob_demanda_min_faturas" in sql
    assert "'capacidade'" in sql
    assert "ON CONFLICT (key) DO NOTHING" in sql
    assert "UPDATE eligibility_parameters" not in sql
