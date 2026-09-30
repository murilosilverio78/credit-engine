from pathlib import Path
import re
import subprocess


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "supabase"
    / "migrations"
    / "038_funil_listagem_agregada.sql"
)
PENDING_MIGRATION = MIGRATION.with_name("040_funil_pendencias.sql")


def test_applied_migration_038_matches_origin_main_exactly():
    original = subprocess.run(
        ["git", "show", "origin/main:infra/supabase/migrations/038_funil_listagem_agregada.sql"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout

    assert MIGRATION.read_text(encoding="utf-8") == original


def test_pending_funil_migration_recreates_and_secures_new_signature():
    sql = " ".join(PENDING_MIGRATION.read_text(encoding="utf-8").split())
    signature = "listar_funil_operacoes(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER)"

    drop = f"DROP FUNCTION IF EXISTS {signature};"
    create = "CREATE FUNCTION listar_funil_operacoes("
    revoke = f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated;"
    grant = f"GRANT EXECUTE ON FUNCTION {signature} TO service_role;"

    assert drop in sql
    assert create in sql
    assert sql.index(drop) < sql.index(create) < sql.index(revoke) < sql.index(grant)


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


def _cnpj_search_term(busca: str) -> str | None:
    """Espelha a guarda SQL antes de aplicar POSITION no CNPJ."""
    digits = re.sub(r"\D", "", busca)
    if re.search(r"[A-Za-z]", busca) or len(digits) < 3:
        return None
    return digits


def test_cnpj_search_only_accepts_numeric_terms_with_at_least_three_digits():
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").split())

    assert _cnpj_search_term("empresa 1") is None
    assert _cnpj_search_term("12.345") == "12345"
    assert _cnpj_search_term("12345678") == "12345678"
    assert _cnpj_search_term("12.345.678/0001-90") == "12345678000190"
    assert sql.count("p_busca !~ '[[:alpha:]]'") == 2
    assert sql.count("LENGTH(REGEXP_REPLACE(p_busca, '\\D', '', 'g')) >= 3") == 2
