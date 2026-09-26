import copy
import os
from types import SimpleNamespace

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import funil_qualificacao_service as service  # noqa: E402


PARAMS = {
    "prazo_minimo_dias": 60,
    "funil_hist_min_meses": 6,
    "funil_orgaos_min": 2,
    "funil_cobertura_min": 2.0,
}


def valid_context():
    operation = {
        "id": "op-1",
        "valor_enquadrado": 100_000,
        "prazo_final_meses": 12,
        "prazo_dias": 360,
    }
    snapshots = {
        "brasil_api": {"situacao_cadastral": "ATIVA"},
        "pessoa_juridica": {
            "possui_sancao": False,
            "sancionado_ceaf": False,
        },
        "ceis": {"possui_sancao": False, "registros": []},
        "cnep": {"possui_sancao": False, "registros": []},
        "cepim": {"possui_sancao": False, "registros": []},
        "acordos_leniencia": {"possui_acordo": False, "acordos": []},
        "contratos_comprasnet": {
            "status_consulta": "ENCONTRADO",
            "prazo_vincendo_meses": 12,
        },
        "recursos_recebidos": {
            "periodo_inicio": "01/2025",
            "periodo_fim": "12/2025",
            "orgaos_pagadores": ["ORGAO 1", "ORGAO 2"],
            "valor_total_recebido": 300_000,
        },
    }
    statuses = {
        component: "completed" for component in service.SANCTION_COMPONENTS
    }
    statuses.update({"brasil_api": "completed", "pessoa_juridica": "completed"})
    return operation, snapshots, statuses


def evaluate(monkeypatch, mutate=None, *, statuses_mutate=None):
    operation, snapshots, statuses = valid_context()
    if mutate:
        mutate(operation, snapshots)
    if statuses_mutate:
        statuses_mutate(statuses)
    monkeypatch.setattr(service, "_load_operation", lambda _operation_id: operation)
    monkeypatch.setattr(
        service,
        "_load_snapshots",
        lambda _operation_id: (snapshots, statuses),
    )
    monkeypatch.setattr(service, "get_eligibility_config", lambda: PARAMS.copy())
    return service.avaliar_qualificacao_funil("op-1")


@pytest.mark.parametrize(
    ("criterion", "mutate", "expected"),
    [
        (
            "situacao",
            lambda _op, snapshots: snapshots["brasil_api"].update(
                situacao_cadastral="INAPTA"
            ),
            "Situacao cadastral diferente de ATIVA",
        ),
        (
            "sancao",
            lambda _op, snapshots: snapshots["ceis"].update(
                possui_sancao=True,
                registros=[{"situacao": "ATIVO"}],
            ),
            "Sancao ativa em CEIS",
        ),
        (
            "contrato_comprasnet",
            lambda _op, snapshots: snapshots["contratos_comprasnet"].update(
                status_consulta="NAO_ENCONTRADO"
            ),
            "contrato_comprasnet_nao_encontrado",
        ),
        (
            "prazo",
            lambda op, snapshots: (
                op.update(prazo_final_meses=1, prazo_dias=30),
                snapshots["contratos_comprasnet"].update(prazo_vincendo_meses=1),
            ),
            "prazo_vincendo_insuficiente:30d",
        ),
        (
            "historico",
            lambda _op, snapshots: snapshots["recursos_recebidos"].update(
                periodo_inicio="10/2025", periodo_fim="12/2025"
            ),
            "historico_recebimentos_insuficiente:3m",
        ),
        (
            "orgaos",
            lambda _op, snapshots: snapshots["recursos_recebidos"].update(
                orgaos_pagadores=["ORGAO 1"]
            ),
            "orgaos_pagadores_insuficientes:1",
        ),
        (
            "cobertura",
            lambda _op, snapshots: snapshots["recursos_recebidos"].update(
                valor_total_recebido=150_000
            ),
            "cobertura_insuficiente:1.50",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_each_qualification_criterion_blocks_in_isolation(
    monkeypatch, criterion, mutate, expected
):
    qualified, reasons = evaluate(monkeypatch, mutate)

    assert qualified is False, criterion
    assert len(reasons) == 1
    assert expected in reasons[0]


def test_all_qualification_criteria_pass_together(monkeypatch):
    qualified, reasons = evaluate(monkeypatch)

    assert qualified is True
    assert reasons == []


def test_unverified_sanction_source_blocks_qualification(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        statuses_mutate=lambda statuses: statuses.update(ceis="failed"),
    )

    assert qualified is False
    assert reasons == ["fonte_sancao_nao_verificada:ceis"]


def test_unverified_registry_status_blocks_qualification(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        lambda _operation, snapshots: snapshots["brasil_api"].clear(),
    )

    assert qualified is False
    assert reasons == ["situacao_cadastral_nao_verificada"]


def test_unverified_ceaf_source_blocks_qualification(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        statuses_mutate=lambda statuses: statuses.update(pessoa_juridica="failed"),
    )

    assert qualified is False
    assert reasons == ["fonte_sancao_nao_verificada:ceaf"]


def test_reevaluation_promotes_previously_rejected_quote(monkeypatch):
    operation, snapshots, statuses = valid_context()
    snapshots["recursos_recebidos"]["valor_total_recebido"] = 100_000
    monkeypatch.setattr(service, "_load_operation", lambda _operation_id: operation)
    monkeypatch.setattr(
        service,
        "_load_snapshots",
        lambda _operation_id: (copy.deepcopy(snapshots), statuses.copy()),
    )
    monkeypatch.setattr(service, "get_eligibility_config", lambda: PARAMS.copy())

    qualified, reasons = service.avaliar_qualificacao_funil("op-1")
    assert qualified is False
    assert reasons == ["cobertura_insuficiente:1.00"]

    snapshots["recursos_recebidos"]["valor_total_recebido"] = 300_000
    saved = {}

    class Query:
        def update(self, data):
            saved.update(data)
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    monkeypatch.setattr(
        service,
        "supabase",
        SimpleNamespace(table=lambda _name: Query()),
    )

    stage, reasons = service.atualizar_estagio_pos_fase2("C-1", "op-1")

    assert stage == "QUALIFICADA"
    assert reasons == []
    assert saved["estagio"] == "QUALIFICADA"
    assert saved["estagio_max"] == "QUALIFICADA"
    assert saved["status_ingestao"] == "AGUARDANDO_RELATORIO"
