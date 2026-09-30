import asyncio
import os
from datetime import date
from types import SimpleNamespace

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import operation_service  # noqa: E402
from app.services.report_pdf_service import contracts_annex, cover_section  # noqa: E402
from app.services.verified_contracts_service import contratos_verificados  # noqa: E402
from app.workers.tasks import score_engine  # noqa: E402


CNPJ = "14757507000107"
TODAY = date(2026, 9, 30)


def portal(*, include_same=False, same_by_value=False):
    contracts = [
        {
            "numero": "00001/2025",
            "unidade_codigo": "200344",
            "valor_inicial": 500_000,
            "data_inicio": "2025-01-01",
            "data_fim": "2027-01-01",
            "ativo": True,
            "orgao": "IFBA",
        }
    ]
    if include_same:
        contracts.append(
            {
                "numero": "00004/2026",
                "unidade_codigo": "200344",
                "valor_inicial": 1_918_883.40,
                "data_inicio": "2026-04-02" if same_by_value else "2026-01-01",
                "data_fim": "2027-04-02",
                "ativo": True,
                "orgao": "IFBA",
            }
        )
    return {
        "contratos_ativos": 4,
        "total_contratos": 12,
        "contratos_encerrados": 8,
        "valor_total_ativo": 2_087_110.64,
        "orgaos_contratantes": ["IFBA"],
        "contratos_detalhe": contracts,
    }


def comprasnet(**overrides):
    contract = {
        "numero": "00004/2026",
        "uasg": "200344",
        "valor_global": 1_918_883.40,
        "vigencia_inicio": "2026-04-02",
        "vigencia_fim": "2027-04-02",
        "orgao": "IFBA",
        "situacao": "Ativo",
        "fornecedor_cnpj": CNPJ,
        "match_confianca": "CNPJ_CONFERIDO",
    }
    contract.update(overrides.pop("contract", {}))
    return {"status_consulta": "ENCONTRADO", "contrato_comprasnet": contract, **overrides}


def test_verified_contract_is_added_to_portal_floor_and_pdf_uses_same_totals():
    verified = contratos_verificados(portal(), comprasnet(), CNPJ, today=TODAY)

    assert verified["contratos_ativos_verificados"] == 5
    assert verified["total_contratos_verificados"] == 13
    assert verified["valor_total_ativo_verificado"] == pytest.approx(4_005_994.04)
    assert verified["nota"] == "inclui 1 contrato verificado no Comprasnet, não listado no Portal"

    snapshots = {
        "contratos": {"parsed_result": portal()},
        "contratos_comprasnet": {"parsed_result": comprasnet()},
    }
    operation = {"cnpj": CNPJ, "contratos_verificados": verified}
    assert "5" in cover_section(operation, snapshots, {})
    assert "R$ 4.005.994,04" in cover_section(operation, snapshots, {})
    annex = contracts_annex(operation, snapshots)
    assert "Comprasnet" in annex
    assert "13" in annex


@pytest.mark.parametrize(
    "result",
    [
        comprasnet(status_consulta="NAO_VERIFICADO"),
        comprasnet(status_consulta="NAO_ENCONTRADO_CONFIRMADO"),
        comprasnet(contract={"vigencia_fim": "2026-09-29"}),
        comprasnet(contract={"fornecedor_cnpj": "00000000000000"}),
        comprasnet(contract={"match_confianca": "NUMERO_APROXIMADO"}),
    ],
)
def test_only_current_cnpj_confirmed_active_comprasnet_contract_is_added(result):
    verified = contratos_verificados(portal(), result, CNPJ, today=TODAY)

    assert verified["adicionais"] == []
    assert verified["contratos_ativos_verificados"] == 4
    assert verified["total_contratos_verificados"] == 12


def test_portal_duplicate_by_number_and_uasg_is_not_added():
    verified = contratos_verificados(portal(include_same=True), comprasnet(), CNPJ, today=TODAY)

    assert verified["adicionais"] == []


def test_portal_duplicate_by_value_and_start_date_is_not_added():
    portal_result = portal()
    portal_result["contratos_detalhe"].append(
        {
            "numero": "99999/2026",
            "unidade_codigo": "111111",
            "valor_final": 1_918_883.40,
            "data_inicio": "2026-04-02",
            "ativo": True,
        }
    )

    verified = contratos_verificados(portal_result, comprasnet(), CNPJ, today=TODAY)

    assert verified["adicionais"] == []


class Query:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.single = False

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        data = self.database[self.table]
        return SimpleNamespace(data=data[0] if self.single and data else data)


class Database:
    def __init__(self, snapshots):
        self.data = {
            "operations": [{"id": "op-1", "cnpj": CNPJ}],
            "component_snapshots": snapshots,
            "audit_trail": [],
        }

    def table(self, table):
        return Query(self.data, table)


def test_operation_detail_keeps_working_and_returns_null_without_comprasnet(monkeypatch):
    database = Database([{"component": "contratos", "parsed_result": portal()}])
    monkeypatch.setattr(operation_service, "supabase", database)

    result = asyncio.run(operation_service.OperationService().get_with_snapshots("op-1"))

    assert result["id"] == "op-1"
    assert result["contratos_verificados"] is None


def test_operation_detail_and_pdf_share_verified_totals(monkeypatch):
    database = Database(
        [
            {"component": "contratos", "parsed_result": portal()},
            {"component": "contratos_comprasnet", "parsed_result": comprasnet()},
        ]
    )
    monkeypatch.setattr(operation_service, "supabase", database)

    result = asyncio.run(operation_service.OperationService().get_with_snapshots("op-1"))

    assert result["contratos_verificados"]["contratos_ativos_verificados"] == 5
    assert result["contratos_verificados"]["valor_total_ativo_verificado"] == pytest.approx(4_005_994.04)


class ScoreQuery:
    def __init__(self, result):
        self.result = result

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return SimpleNamespace(data=self.result)


class ScoreDatabase:
    def __init__(self, result):
        self.result = result

    def table(self, _name):
        return ScoreQuery(self.result)


def relationship_portal() -> dict:
    result = portal()
    result["orgaos_contratantes"] = ["IFBA", "MEC", "FNDE"]
    result["contratos_detalhe"] = [
        {
            "numero": "00001/2025",
            "unidade_codigo": "200344",
            "ativo": True,
            "data_inicio": "2025-01-01",
            "data_fim": "2027-01-01",
            "orgao": "IFBA",
        }
    ]
    return result


def test_score_relationship_counts_verified_contract_in_volume_history_and_factor():
    snapshots = {"contratos": relationship_portal()}
    before = score_engine.score_relacionamento(snapshots)

    score_engine._add_verified_comprasnet_contract_for_score(
        "op-1",
        CNPJ,
        snapshots,
        ScoreDatabase([{"parsed_result": comprasnet()}]),
    )
    after = score_engine.score_relacionamento(snapshots)

    assert before["score"] == 75.1
    assert after["score"] == 81.8
    assert "contrato_comprasnet_incluido_no_relacionamento" in after["flags"]
    assert "1 contrato(s) verificado(s) no Comprasnet incluido(s)" in after["fatores"]
    assert snapshots["contratos"]["contratos_ativos"] == 5
    assert snapshots["contratos"]["total_contratos"] == 13
    assert snapshots["contratos"]["contratos_detalhe"][-1]["data_inicio"] == "2026-04-02"
    assert snapshots["contratos"]["contratos_detalhe"][-1]["data_fim"] == "2027-04-02"


def test_score_without_eligible_addition_is_unchanged_and_does_not_mutate_portal_snapshot():
    portal_snapshot = relationship_portal()
    snapshots = {"contratos": portal_snapshot}
    expected = score_engine.score_relacionamento(snapshots)

    score_engine._add_verified_comprasnet_contract_for_score(
        "op-1",
        CNPJ,
        snapshots,
        ScoreDatabase([{"parsed_result": comprasnet(status_consulta="NAO_VERIFICADO")}]),
    )

    assert score_engine.score_relacionamento(snapshots) == expected
    assert snapshots["contratos"] is portal_snapshot


def test_score_ignores_duplicate_or_unavailable_comprasnet_contract():
    snapshots = {"contratos": portal(include_same=True)}
    expected = score_engine.score_relacionamento(snapshots)

    score_engine._add_verified_comprasnet_contract_for_score(
        "op-1", CNPJ, snapshots, ScoreDatabase([{"parsed_result": comprasnet()}])
    )
    assert score_engine.score_relacionamento(snapshots) == expected

    score_engine._add_verified_comprasnet_contract_for_score(
        "op-1", CNPJ, snapshots, ScoreDatabase(RuntimeError("database offline"))
    )
    assert score_engine.score_relacionamento(snapshots) == expected


def test_recalculation_uses_the_same_in_memory_verified_contract_rule():
    initial = {"contratos": relationship_portal()}
    recalculated = {"contratos": relationship_portal()}
    database = ScoreDatabase([{"parsed_result": comprasnet()}])

    score_engine._add_verified_comprasnet_contract_for_score("op-1", CNPJ, initial, database)
    score_engine._add_verified_comprasnet_contract_for_score("op-1", CNPJ, recalculated, database)

    assert score_engine.score_relacionamento(recalculated) == score_engine.score_relacionamento(initial)
