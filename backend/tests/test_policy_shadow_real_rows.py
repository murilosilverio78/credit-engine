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
    input_snapshots = [
        {**item, "operation_id": operation_id}
        for item in row.get("input_snapshots", [])
    ]
    tables = {
        **_policy_tables(),
        "operations": [operation],
        "finding_runs": runs,
        "findings": row["findings"],
        "component_snapshots": (
            ([{**snapshot, "operation_id": operation_id}] if snapshot else [])
            + input_snapshots
        ),
        "policy_shadow_runs": [],
    }
    loader.clear_policy_cache()
    return Postgrest(tables)


def _v5_row(record: dict) -> dict:
    """Materialize the compact, redacted capture into the production tables."""
    operation = {"id": record["id"], "ambiente": "PRODUCAO", **record["operation"]}
    facts = record["facts"]
    runs = [{"id": f"{record['id']}:{specialist}", "especialista": specialist, "status": "COMPLETO"}
            for specialist in ("cadastro_regularidade", "sacado_orgao", "reputacional", "porte")]
    run_id = {run["especialista"]: run["id"] for run in runs}

    def finding(specialist: str, codigo: str, valor, estado="CONFIRMADO", confianca="ALTA", *, evidence=False):
        item = {"run_id": run_id[specialist], "codigo": codigo, "valor": valor,
                "estado": estado, "confianca": confianca, "evidencia": []}
        if evidence:
            item["evidencia"] = [{"data_referencia": "2026-10-04", "caminho": "data_abertura"}]
        return item

    certificates = {"fator": 0.0, "estado": "ausente", "validade": None, "status_componente": "waiting_upload"}
    cadastro = [
        finding("cadastro_regularidade", "acordo_leniencia_ativo", False, "NEGATIVO_CONFIRMADO"),
        finding("cadastro_regularidade", "atividade_restrita", False),
        finding("cadastro_regularidade", "balanco_ausente", True),
        finding("cadastro_regularidade", "cadastro_inativo", False, "NEGATIVO_CONFIRMADO"),
        finding("cadastro_regularidade", "capital_social_rs", facts["capital"]),
        *(finding("cadastro_regularidade", code, certificates) for code in (
            "certidao_cnd_federal_pendente", "certidao_cndt_pendente", "certidao_fgts_pendente")),
        finding("cadastro_regularidade", "idade_empresa_anos", facts["idade"], evidence=True),
        finding("cadastro_regularidade", "natureza_juridica_empresarial", True),
        finding("cadastro_regularidade", "porte_cadastral", facts.get("porte_cadastral", "DEMAIS")),
        finding("cadastro_regularidade", "qsa_estabilidade", facts["qsa"]),
        finding("cadastro_regularidade", "sancao_ativa", [], "NEGATIVO_CONFIRMADO"),
    ]
    confianca_reputacao = facts.get("reputacao_confianca", "MEDIA")
    sacado = [
        finding("sacado_orgao", "anos_completos_receita", facts["anos"]),
        finding("sacado_orgao", "cobertura_exposicao", facts["cobertura"]),
        finding("sacado_orgao", "contratos_ativos_qtd", facts["ativos"]),
        finding("sacado_orgao", "contratos_comprasnet_incluidos_qtd", facts.get("comprasnet", 0)),
        finding("sacado_orgao", "contratos_total_qtd", facts["total"]),
        finding("sacado_orgao", "contratos_valor_total_ativo_rs", facts["valor_ativo"]),
        finding("sacado_orgao", "glosa_historica", facts["glosa"]),
        finding("sacado_orgao", "hhi_recebimentos", facts["hhi"]),
        finding("sacado_orgao", "maturidade_max_anos", 4.999315537303217),
        finding("sacado_orgao", "meses_com_recebimento", facts["meses"]),
        finding("sacado_orgao", "orgaos_distintos_qtd", facts["orgaos"]),
        finding("sacado_orgao", "receita_serie_anual", {"anos_na_serie": facts["serie"], "anos_informados": facts["anos"], "serie_qtd_chaves": len(facts["serie"])}),
        finding("sacado_orgao", "volatilidade_cv", facts["cv"], "CONFIRMADO" if facts["cv"] is not None else "NAO_VERIFICADO", "ALTA" if facts["cv"] is not None else "BAIXA"),
    ]
    findings = cadastro + sacado + [
        finding("porte", "capacidade_operacional", facts["porte_operacional"], confianca="MEDIA"),
        finding("reputacional", "alertas_reputacionais", {}, confianca=confianca_reputacao),
        finding("reputacional", "reputacao_mercado", facts["reputacao"], confianca=confianca_reputacao),
    ]
    return {
        "operation": operation,
        "finding_runs": runs,
        "findings": findings,
        "score_snapshot": {"component": "score_engine", "status": "completed", "completed_at": record["score_completed_at"], "created_at": record["score_completed_at"], "parsed_result": {"bloqueios": [], **record["official"]}},
        "input_snapshots": [{**snapshot, "status": "completed"} for snapshot in record["componentes_concluidos"]],
    }


def _v5_rows() -> list[dict]:
    return [_v5_row(record) for record in _load("shadow_v5_6_operations.json")["rows"]]


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


def test_real_row_1d0d5e00_is_equal():
    row = _load("shadow_1d0d5e00.json")
    result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))

    assert result["classe_geral"] == "IGUAL"
    assert result["divergencias"] == []


def test_real_row_caf47df0_uses_potential_porte_before_balance_penalty():
    row = _load("shadow_caf47df0.json")
    result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))

    assert result["classe_geral"] == "IGUAL"
    assert result["divergencias"] == []
    assert result["resultado"]["merit_potencial"] == 67.7
    assert result["resultado"]["penalizacao_balanco"] == 10.0


def test_real_row_3ce51357_marks_old_pd_as_non_comparable():
    row = _load("shadow_3ce51357.json")
    result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))

    assert result["classe_geral"] == "IGUAL"
    assert result["divergencias"] == [
        {
            "campo": "ajuste_pd",
            "valor_politica": result["resultado"]["ajuste_pd"],
            "valor_oficial": row["score_snapshot"]["parsed_result"]["ajuste_pd"],
            "diferenca": None,
            "classe": "NAO_COMPARAVEL",
            "motivo": "formato antigo de ajuste_pd sem anos completos",
        }
    ]


def test_legacy_99e4_snapshot_absent_fields_are_non_comparable():
    row = _load("shadow_99e4d490.json")
    official = row["score_snapshot"]["parsed_result"]
    for field in ("merit_potencial", "rating_potencial", "penalizacao_balanco", "ajuste_pd", "regularidade"):
        official.pop(field)
    result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))

    assert result["classe_geral"] == "IGUAL"
    assert {item["campo"] for item in result["divergencias"]} == {
        "merit_potencial", "rating_potencial", "penalizacao_balanco", "ajuste_pd", "fator_potencial",
    }
    assert {item["classe"] for item in result["divergencias"]} == {"NAO_COMPARAVEL"}


def test_v5_real_rows_that_still_match_are_equal():
    for row in _v5_rows():
        if row["operation"]["id"].startswith("3f563558"):
            continue
        result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))
        assert result["classe_geral"] == "IGUAL", row["operation"]["id"]
        assert result["divergencias"] == [], row["operation"]["id"]


def test_v5_real_row_with_refreshed_inputs_is_sem_dados():
    row = next(row for row in _v5_rows() if row["operation"]["id"].startswith("3f563558"))

    result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))

    expected = [snapshot["component"] for snapshot in row["input_snapshots"]]
    assert result["classe_geral"] == "SEM_DADOS"
    assert result["oficial"]["_insumos_posteriores"] == sorted(expected)
    assert result["divergencias"]
    assert {item["classe"] for item in result["divergencias"]} == {"INSUMO_POSTERIOR"}
    assert {tuple(item["componentes_posteriores"]) for item in result["divergencias"]} == {tuple(sorted(expected))}


def test_v5_refreshed_inputs_are_unexpected_when_they_predate_score():
    row = next(row for row in _v5_rows() if row["operation"]["id"].startswith("3f563558"))
    for snapshot in row["input_snapshots"]:
        snapshot["completed_at"] = "2026-09-22T03:38:00+00:00"

    result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))

    assert result["classe_geral"] == "INESPERADA"
    assert result["oficial"]["_insumos_posteriores"] == []
    assert {item["classe"] for item in result["divergencias"]} == {"PARIDADE"}


def test_v5_later_inputs_without_parity_difference_remain_equal():
    row = next(row for row in _v5_rows() if row["operation"]["id"].startswith("08f91143"))
    for snapshot in row["input_snapshots"]:
        snapshot["completed_at"] = "2026-10-01T03:38:00+00:00"

    result = shadow.avaliar_sombra(row["operation"]["id"], database=_database(row))

    assert result["classe_geral"] == "IGUAL"
    assert result["divergencias"] == []
    assert result["oficial"]["_insumos_posteriores"] == ["brasil_api", "contratos", "recursos_recebidos"]
