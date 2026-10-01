"""Destructive PostgreSQL integration tests for the already-applied 043 schema."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest


PG_TEST_URL = os.getenv("PG_TEST_URL")
DISPOSABLE = os.getenv("PG_TEST_DISPOSABLE") == "1"
pytestmark = pytest.mark.skipif(not PG_TEST_URL, reason="PG_TEST_URL nao configurada")
MIGRATION = Path(__file__).parents[2] / "infra/supabase/migrations/043_achados_fundacao.sql"


@pytest.fixture
def conn():
    if not DISPOSABLE:
        pytest.fail("PG_TEST_DISPOSABLE=1 e obrigatorio: o fixture recria o schema public")
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(PG_TEST_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
            cursor.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            cursor.execute(
                """
                DO $$
                BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role; END IF;
                END $$;
                """
            )
            cursor.execute(
                "CREATE TABLE operations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), ambiente VARCHAR(10) DEFAULT 'PRODUCAO')"
            )
            cursor.execute(MIGRATION.read_text(encoding="utf-8"))
            cursor.execute("GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role")
        yield connection


def _operation(cursor) -> str:
    cursor.execute("INSERT INTO operations (id, ambiente) VALUES (%s, 'PRODUCAO')", (str(uuid4()),))
    cursor.execute("SELECT id FROM operations ORDER BY id DESC LIMIT 1")
    return str(cursor.fetchone()[0])


def _run(operation_id: str, fingerprint: str) -> dict:
    return {
        "operation_id": operation_id,
        "ambiente": "PRODUCAO",
        "especialista": "cadastro_regularidade",
        "status": "COMPLETO",
        "entrada_hash": fingerprint,
        "versao_schema": "1",
        "versao_emissor": "1",
    }


def _finding(code: str = "cadastro_inativo") -> dict:
    return {
        "escopo": "CEDENTE",
        "codigo": code,
        "catalogo_versao": 1,
        "valor": False,
        "estado": "NEGATIVO_CONFIRMADO",
        "confianca": "ALTA",
        "evidencia": [],
    }


def _register(cursor, operation_id: str, fingerprint: str, findings: list[dict]):
    cursor.execute(
        "SELECT * FROM registrar_achados(%s::jsonb, %s::jsonb)",
        (_json(_run(operation_id, fingerprint)), _json(findings)),
    )
    return cursor.fetchone()


def _json(value):
    import json

    return json.dumps(value)


def test_migration_seeds_catalog_and_locks_rpc_privileges(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM finding_catalog")
        assert cursor.fetchone()[0] == 26
        cursor.execute(
            "SELECT has_function_privilege('anon', 'registrar_achados(jsonb,jsonb)', 'EXECUTE'), "
            "has_function_privilege('authenticated', 'registrar_achados(jsonb,jsonb)', 'EXECUTE'), "
            "has_function_privilege('service_role', 'registrar_achados(jsonb,jsonb)', 'EXECUTE')"
        )
        assert cursor.fetchone() == (False, False, True)


def test_rpc_is_idempotent_and_atomic_for_unknown_catalog_code(conn):
    with conn.cursor() as cursor:
        operation_id = _operation(cursor)
        first = _register(cursor, operation_id, "same-input", [_finding()])
        second = _register(cursor, operation_id, "same-input", [_finding()])
        assert first[1] is True
        assert second[1] is False
        cursor.execute("SELECT count(*) FROM finding_runs")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM findings")
        assert cursor.fetchone()[0] == 1

        with pytest.raises(Exception):
            _register(cursor, operation_id, "invalid-input", [_finding("fora_do_catalogo")])
        cursor.execute("SELECT count(*) FROM finding_runs")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM findings")
        assert cursor.fetchone()[0] == 1


@pytest.mark.parametrize("table", ("findings", "finding_runs", "finding_catalog"))
@pytest.mark.parametrize("action", ("UPDATE", "DELETE", "TRUNCATE"))
def test_append_only_tables_block_mutation(conn, table, action):
    with conn.cursor() as cursor:
        statement = f"{action} {table}" if action == "TRUNCATE" else f"{action} {table} SET created_at = created_at" if action == "UPDATE" else f"{action} FROM {table}"
        with pytest.raises(Exception):
            cursor.execute(statement)


def test_policy_state_machine_and_rule_moves_are_blocked(conn):
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO policy_versions (versao) VALUES (1) RETURNING id")
        draft_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO policy_versions (versao) VALUES (2) RETURNING id")
        second_draft_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO policy_rules (policy_version_id, codigo, classe, ordem) VALUES (%s, 'cadastro_inativo', 'VETO', 1) RETURNING id", (draft_id,))
        rule_id = cursor.fetchone()[0]
        cursor.execute("UPDATE policy_versions SET status = 'ATIVA', ativada_em = now() WHERE id = %s", (draft_id,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_versions SET status = 'ATIVA', ativada_em = now() WHERE id = %s", (second_draft_id,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_versions SET status = 'RASCUNHO' WHERE id = %s", (draft_id,))
        cursor.execute("UPDATE policy_versions SET status = 'ARQUIVADA' WHERE id = %s", (draft_id,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_versions SET status = 'ATIVA', ativada_em = now() WHERE id = %s", (draft_id,))
        with pytest.raises(Exception):
            cursor.execute("UPDATE policy_rules SET policy_version_id = %s WHERE id = %s", (second_draft_id, rule_id))
        cursor.execute("UPDATE policy_versions SET status = 'ATIVA', ativada_em = now() WHERE id = %s", (second_draft_id,))
        with pytest.raises(Exception):
            cursor.execute("INSERT INTO policy_rules (policy_version_id, codigo, classe, ordem) VALUES (%s, 'cadastro_inativo', 'VETO', 1)", (second_draft_id,))


def test_rpc_execution_is_denied_to_anon_and_allowed_to_service_role(conn):
    with conn.cursor() as cursor:
        operation_id = _operation(cursor)
        cursor.execute("SET ROLE anon")
        with pytest.raises(Exception):
            _register(cursor, operation_id, "anon", [_finding()])
        cursor.execute("RESET ROLE")
        cursor.execute("SET ROLE service_role")
        assert _register(cursor, operation_id, "service", [_finding()])[1] is True
        cursor.execute("RESET ROLE")
