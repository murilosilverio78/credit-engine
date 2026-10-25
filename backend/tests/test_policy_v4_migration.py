"""The v4 policy is delivered as a draft migration, never promoted by source SQL."""

from pathlib import Path


SQL = Path(__file__).parents[2] / "infra/supabase/migrations/068_politica_v4_rascunho.sql"


def test_v4_draft_copies_v3_replaces_only_anticipation_veto_and_adds_bank_parameter():
    sql = SQL.read_text(encoding="utf-8")

    assert "VALUES (4, 'RASCUNHO', 'v4: financiador da antecipacao concorrente')" in sql
    assert sql.count("WHERE v3.versao = 3") == 2
    assert "financiador_banco_codigo" in sql
    assert "'null'::JSONB" in sql
    assert "AND NOT (r.codigo = 'antecipacao_concorrente' AND r.classe = 'VETO')" in sql
    assert '"op":"nao_contem"' in sql
    assert '"op":"contem"' in sql
    assert "regras_v4 <> regras_v3 - 1 + 3" in sql
    assert "UPDATE policy_versions" not in sql
    assert "status = 'SOMBRA'" not in sql
    assert "status = 'ATIVA'" not in sql
