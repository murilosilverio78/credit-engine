from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "supabase"
    / "migrations"
    / "035_portal_daily_guardrail.sql"
)


def test_portal_guardrail_rpcs_are_not_executable_by_public_roles():
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").split())

    assert (
        "REVOKE ALL ON FUNCTION claim_portal_daily_request(INTEGER) "
        "FROM PUBLIC, anon, authenticated;"
    ) in sql
    assert (
        "REVOKE ALL ON FUNCTION get_portal_daily_usage() "
        "FROM PUBLIC, anon, authenticated;"
    ) in sql
