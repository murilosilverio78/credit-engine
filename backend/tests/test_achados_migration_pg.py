"""Optional PostgreSQL integration checks; skipped without PG_TEST_URL."""
import os
from pathlib import Path

import pytest

PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG_TEST_URL, reason="PG_TEST_URL nao configurada")
MIGRATION = Path(__file__).parents[2] / "infra/supabase/migrations/043_achados_fundacao.sql"


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(PG_TEST_URL, autocommit=True) as connection:
        yield connection


def test_migration_applies_seed_privileges_and_append_only(conn):
    with conn.cursor() as cursor:
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        cursor.execute("SELECT count(*) FROM finding_catalog")
        assert cursor.fetchone()[0] == 26
        cursor.execute("SELECT has_function_privilege('anon', 'registrar_achados(jsonb,jsonb)', 'EXECUTE'), has_function_privilege('authenticated', 'registrar_achados(jsonb,jsonb)', 'EXECUTE'), has_function_privilege('service_role', 'registrar_achados(jsonb,jsonb)', 'EXECUTE')")
        assert cursor.fetchone() == (False, False, True)
        for table in ("finding_catalog", "finding_runs", "findings"):
            with pytest.raises(Exception):
                cursor.execute(f"TRUNCATE {table}")


def test_policy_state_machine(conn):
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO policy_versions (versao) VALUES (9001) RETURNING id")
        policy_id = cursor.fetchone()[0]
        cursor.execute("UPDATE policy_versions SET descricao = 'editavel enquanto rascunho' WHERE id = %s", (policy_id,))
        cursor.execute("UPDATE policy_versions SET status = 'ATIVA', ativada_em = now() WHERE id = %s", (policy_id,))
        cursor.execute("UPDATE policy_versions SET status = 'ARQUIVADA' WHERE id = %s", (policy_id,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_versions SET status = 'RASCUNHO' WHERE id = %s", (policy_id,))
