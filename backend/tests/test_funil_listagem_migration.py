from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "supabase"
    / "migrations"
    / "038_funil_listagem_agregada.sql"
)


def test_funil_rpcs_are_not_executable_by_public_roles():
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").split())

    assert (
        "REVOKE ALL ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER) "
        "FROM PUBLIC, anon, authenticated;"
    ) in sql
    assert (
        "REVOKE ALL ON FUNCTION resumo_funil_operacoes() "
        "FROM PUBLIC, anon, authenticated;"
    ) in sql
    assert (
        "GRANT EXECUTE ON FUNCTION listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER) "
        "TO service_role;"
    ) in sql
    assert "GRANT EXECUTE ON FUNCTION resumo_funil_operacoes() TO service_role;" in sql


def test_funil_search_uses_literal_position_not_ilike_wildcards():
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").split())

    assert "POSITION( LOWER(p_busca) IN LOWER(COALESCE(o.razao_social, q.nome_fornecedor, '')) ) > 0" in sql
    assert "POSITION( LOWER(REGEXP_REPLACE(p_busca, '\\D', '', 'g')) IN LOWER(q.cnpj) ) > 0" in sql
    assert "ILIKE '%' || p_busca || '%'" not in sql
