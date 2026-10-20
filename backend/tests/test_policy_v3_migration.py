"""The v3 policy is delivered as a draft migration, never promoted by source SQL."""

from pathlib import Path


SQL = Path(__file__).parents[2] / "infra/supabase/migrations/063_politica_v3_rascunho.sql"


def test_v3_draft_copies_v2_and_adds_only_the_five_cash_flow_rules():
    sql = SQL.read_text(encoding="utf-8")

    assert "VALUES (3, 'RASCUNHO', 'v3: concorrência de cessão, empenho e cobertura de parcela')" in sql
    assert sql.count("WHERE v2.versao = 2") == 2
    assert "r.magnitude" in sql  # preserves v2's rating-D 0.85 factor
    for code in ("antecipacao_concorrente", "empenho_insuficiente", "cobertura_parcela"):
        assert f"'{code}'" in sql
    assert "regras_v3 <> regras_v2 + 5" in sql
    assert "UPDATE policy_versions" not in sql
    assert "status = 'SOMBRA'" not in sql
    assert "status = 'ATIVA'" not in sql
