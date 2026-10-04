"""End-to-end regression coverage for captured production shadow rows."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path

from app.services.policy import loader, shadow
from app.services.findings.version import EMITTER_VERSION
from tests.fakes.postgrest import Postgrest


_ROOT = Path(__file__).parents[2]
_FIXTURES = Path(__file__).parent / "fixtures" / "real_rows"
_POLICY_ID = "92c234b7-5c70-41bf-88e4-5620bd1b59b0"


def _policy_tables() -> dict[str, list[dict]]:
    sql = (_ROOT / "infra" / "supabase" / "migrations" / "045_politica_v0.sql").read_text(encoding="utf-8")
    parameters = [
        {"chave": key, "valor": json.loads(value), "policy_version_id": _POLICY_ID}
        for key, value in re.findall(r"\('([a-z_0-9]+)',\s*'(.*?)'::JSONB\)", sql)
    ]
    rules = [
        {"id": "1", "policy_version_id": _POLICY_ID, "codigo": "cadastro_inativo", "classe": "VETO", "condicao": {"estado": "CONFIRMADO", "confianca": "ALTA"}, "ordem": 1},
        {"id": "2", "policy_version_id": _POLICY_ID, "codigo": "sancao_ativa", "classe": "VETO", "condicao": {"estado": "CONFIRMADO", "confianca": "ALTA"}, "ordem": 2},
        {"id": "3", "policy_version_id": _POLICY_ID, "codigo": "acordo_leniencia_ativo", "classe": "VETO", "condicao": {"estado": "CONFIRMADO", "confianca": "ALTA"}, "ordem": 3},
        {"id": "4", "policy_version_id": _POLICY_ID, "codigo": "glosa_historica", "classe": "AJUSTE", "parametro_alvo": "pd_multiplicador", "direcao": "adverso", "magnitude": 1.15, "condicao": {"taxa_glosa_min": 0.02, "faturado_total_min": 500000}, "ordem": 4},
        {"id": "5", "policy_version_id": _POLICY_ID, "codigo": "conta_vinculada_regime", "classe": "AJUSTE", "parametro_alvo": "lgd_multiplicador", "direcao": "favoravel", "magnitude": 0.95, "condicao": {"valor": "CONTA_DEPOSITO_VINCULADA", "confianca_minima": "ALTA"}, "ordem": 5},
    ]
    return {
        "policy_versions": [{"id": _POLICY_ID, "versao": 1, "status": "SOMBRA", "descricao": "v0"}],
        "policy_parameters": parameters,
        "policy_rules": rules,
    }


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _database(row: dict) -> Postgrest:
    row = deepcopy(row)
    operation = row["operation"]
    operation_id = operation["id"]
    runs = [{**run, "operation_id": operation_id, "versao_emissor": EMITTER_VERSION} for run in row["finding_runs"]]
    snapshot = row.get("score_snapshot")
    tables = {
        **_policy_tables(),
        "operations": [operation],
        "finding_runs": runs,
        "findings": row["findings"],
        "component_snapshots": [{**snapshot, "operation_id": operation_id}] if snapshot else [],
        "policy_shadow_runs": [],
    }
    loader.clear_policy_cache()
    return Postgrest(tables)


def test_real_row_99e4_is_equal_without_documentos_run():
    db = _database(_load("shadow_99e4d490.json"))

    result = shadow.avaliar_sombra("99e4d490-8dd2-4669-b5e5-b78323b57074", database=db)

    assert result["classe_geral"] == "IGUAL"
    assert result["divergencias"] == []


def test_real_row_veto_is_equal_with_persisted_null_fields():
    db = _database(_load("shadow_33295ef8_veto.json"))

    result = shadow.avaliar_sombra("33295ef8-2196-4fc6-a52e-e8f8169e3e4e", database=db)

    assert result["classe_geral"] == "IGUAL"
    assert result["divergencias"] == []


def test_missing_documentos_and_optional_runs_without_score_is_sem_dados():
    row = _load("shadow_99e4d490.json")
    row["finding_runs"] = [
        run for run in row["finding_runs"]
        if run["especialista"] not in {"documentos", "reputacional", "porte"}
    ]
    remaining_ids = {run["id"] for run in row["finding_runs"]}
    row["findings"] = [finding for finding in row["findings"] if finding["run_id"] in remaining_ids]
    row.pop("score_snapshot")
    db = _database(row)

    result = shadow.avaliar_sombra("99e4d490-8dd2-4669-b5e5-b78323b57074", database=db)

    assert result["classe_geral"] == "SEM_DADOS"


def test_official_score_without_reputacional_run_is_sem_dados():
    row = _load("shadow_99e4d490.json")
    missing_run = next(run["id"] for run in row["finding_runs"] if run["especialista"] == "reputacional")
    row["finding_runs"] = [run for run in row["finding_runs"] if run["id"] != missing_run]
    row["findings"] = [finding for finding in row["findings"] if finding["run_id"] != missing_run]
    db = _database(row)

    result = shadow.avaliar_sombra("99e4d490-8dd2-4669-b5e5-b78323b57074", database=db)

    assert result["classe_geral"] == "SEM_DADOS"
