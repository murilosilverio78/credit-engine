"""Destructive integration checks for policy migrations 043--045.

They only run against a deliberately disposable PostgreSQL database.  The
fixture recreates ``public`` for every test, so a normal development or
production database must never set both environment variables.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest


PG_TEST_URL = os.getenv("PG_TEST_URL")
DISPOSABLE = os.getenv("PG_TEST_DISPOSABLE") == "1"
pytestmark = pytest.mark.skipif(
    not (PG_TEST_URL and DISPOSABLE),
    reason="requer PG_TEST_URL e PG_TEST_DISPOSABLE=1",
)
MIGRATIONS = [
    Path(__file__).parents[2] / "infra/supabase/migrations" / name
    for name in ("043_achados_fundacao.sql", "044_catalogo_achados_v2.sql", "045_politica_v0.sql")
]


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(PG_TEST_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
            cursor.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            cursor.execute(
                """
                DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role; END IF;
                END $$;
                """
            )
            cursor.execute("CREATE TABLE operations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), ambiente VARCHAR(10) DEFAULT 'PRODUCAO')")
            for migration in MIGRATIONS:
                cursor.execute(migration.read_text(encoding="utf-8"))
        yield connection


def _operation(cursor) -> str:
    operation_id = str(uuid4())
    cursor.execute("INSERT INTO operations (id) VALUES (%s)", (operation_id,))
    return operation_id


def _policy_id(cursor, version: int = 1):
    cursor.execute("SELECT id FROM policy_versions WHERE versao = %s", (version,))
    return cursor.fetchone()[0]


def _shadow_payload(operation_id: str, policy_id, fingerprint: str) -> tuple:
    return (
        operation_id, "PRODUCAO", policy_id, fingerprint, "2026-01-01", "{}",
        "{}", "{}", "[]", "IGUAL", "[]",
    )


def test_migrations_seed_catalog_parameters_rules_and_privileges(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM finding_catalog")
        assert cursor.fetchone()[0] == 31
        cursor.execute("SELECT count(*) FROM policy_parameters WHERE policy_version_id = %s", (_policy_id(cursor),))
        assert cursor.fetchone()[0] == 33
        cursor.execute("SELECT count(*) FROM policy_rules WHERE policy_version_id = %s", (_policy_id(cursor),))
        assert cursor.fetchone()[0] == 5
        cursor.execute(
            "SELECT has_table_privilege('anon', 'policy_shadow_runs', 'SELECT'), "
            "has_table_privilege('authenticated', 'policy_shadow_runs', 'SELECT'), "
            "has_table_privilege('service_role', 'policy_shadow_runs', 'SELECT')"
        )
        assert cursor.fetchone() == (False, False, True)


def test_policy_state_machine_uniqueness_and_draft_immutability(conn):
    with conn.cursor() as cursor:
        first = _policy_id(cursor)
        cursor.execute("UPDATE policy_versions SET status = 'SOMBRA', sombra_desde = now() WHERE id = %s", (first,))
        cursor.execute("INSERT INTO policy_versions (versao) VALUES (2) RETURNING id")
        second = cursor.fetchone()[0]
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_versions SET status = 'SOMBRA', sombra_desde = now() WHERE id = %s", (second,))
        cursor.execute("UPDATE policy_versions SET status = 'ATIVA', ativada_em = now() WHERE id = %s", (first,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_versions SET status = 'ATIVA', ativada_em = now() WHERE id = %s", (second,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_versions SET status = 'RASCUNHO' WHERE id = %s", (first,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_parameters SET valor = '1'::jsonb WHERE id = (SELECT id FROM policy_parameters WHERE policy_version_id = %s LIMIT 1)", (first,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_rules SET ordem = 99 WHERE id = (SELECT id FROM policy_rules WHERE policy_version_id = %s LIMIT 1)", (first,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_rules SET policy_version_id = %s WHERE policy_version_id = %s", (second, first))


def test_shadow_runs_are_unique_and_append_only(conn):
    with conn.cursor() as cursor:
        operation_id, policy_id = _operation(cursor), _policy_id(cursor)
        sql = """
            INSERT INTO policy_shadow_runs
              (operation_id, ambiente, policy_version_id, entrada_hash, data_referencia, runs_usados, resultado, oficial, divergencias, classe_geral, parametros_divergentes)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb)
        """
        cursor.execute(sql, _shadow_payload(operation_id, policy_id, "same"))
        with pytest.raises(Exception):
            cursor.execute(sql, _shadow_payload(operation_id, policy_id, "same"))
        for statement in (
            "UPDATE policy_shadow_runs SET classe_geral = 'IGUAL'",
            "DELETE FROM policy_shadow_runs",
            "TRUNCATE policy_shadow_runs",
        ):
            with pytest.raises(Exception):
                cursor.execute(statement)


def test_policy_storage_is_service_role_only(conn):
    with conn.cursor() as cursor:
        for role in ("anon", "authenticated"):
            cursor.execute(f"SET ROLE {role}")
            with pytest.raises(Exception):
                cursor.execute("SELECT * FROM policy_shadow_runs")
            with pytest.raises(Exception):
                cursor.execute("SELECT * FROM vw_policy_shadow_ultimos")
            cursor.execute("RESET ROLE")
        cursor.execute("SET ROLE service_role")
        cursor.execute("SELECT * FROM policy_shadow_runs")
        cursor.execute("SELECT * FROM vw_policy_shadow_ultimos")
        cursor.execute("RESET ROLE")
