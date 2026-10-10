import copy
import os
from types import SimpleNamespace

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("PORTAL_TRANSPARENCIA_TOKEN", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost/test",
)
os.environ.setdefault(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
    "testsignature",
)
os.environ.setdefault("SUPABASE_URL", "http://localhost")

from app.services import funil_qualificacao_service as service  # noqa: E402


PARAMS = {
    "ticket_minimo": 100_000,
    "prazo_minimo_dias": 60,
    "funil_hist_min_meses": 6,
    "funil_orgaos_min": 2,
    "funil_cobertura_min": 2.0,
    "cap_fator_liquido_mao_obra": 0.645,
    "cap_fator_liquido_demais": 0.85,
    "cap_cobertura_parcela": 1.25,
    "cap_taxa_referencia_am": 0.035,
    "cap_folga_meses": 1,
    "alerta_salto_escala": 1.5,
}


def valid_context():
    operation = {
        "id": "op-1",
        "valor_enquadrado": 100_000,
        "prazo_final_meses": 12,
        "prazo_dias": 360,
        "valor_global_contrato": 2_000_000,
        "contrato_vigencia_inicio": "2026-07-20",
        "contrato_vigencia_fim": "2027-07-20",
        "contrato_dedicacao_exclusiva": True,
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
        "contratos_pncp": {
            "contrato_cedido_match": "EXATO",
            "contrato_cedido": {
                "valor_global": 2_000_000,
                "data_inicio_vigencia": "2026-07-20",
                "data_fim_vigencia": "2027-07-20",
                "dedicacao_exclusiva": True,
            },
            "faturamento_contratado_12m": 500_000,
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
    statuses.update({
        "brasil_api": "completed",
        "pessoa_juridica": "completed",
        "contratos_comprasnet": "completed",
        "contratos_pncp": "completed",
    })
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
                status_consulta="NAO_ENCONTRADO_CONFIRMADO",
                busca_exaustiva=True,
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


def test_four_completed_sanction_snapshots_without_sanction_qualify(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        lambda _operation, snapshots: snapshots.pop("pessoa_juridica"),
        statuses_mutate=lambda statuses: statuses.pop("pessoa_juridica"),
    )

    assert qualified is True
    assert reasons == []


def test_failed_sanction_source_marks_unavailability(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        lambda _operation, snapshots: snapshots.pop("ceis"),
        statuses_mutate=lambda statuses: statuses.update(ceis="failed"),
    )

    assert qualified is False
    assert reasons == ["indisponibilidade_fonte:ceis"]


def test_failed_comprasnet_marks_unavailability_not_rejection(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        lambda _operation, snapshots: snapshots.pop("contratos_comprasnet"),
        statuses_mutate=lambda statuses: statuses.update(
            contratos_comprasnet="failed"
        ),
    )

    assert qualified is False
    assert reasons == ["indisponibilidade_fonte:contratos_comprasnet"]


@pytest.mark.parametrize(
    "result",
    [
        {"status_consulta": "NAO_VERIFICADO", "busca_exaustiva": False},
        {
            "status_consulta": "NAO_ENCONTRADO",
            "motivo": "contrato_sem_match_cnpj",
        },
        {"status_consulta": "NAO_ENCONTRADO", "motivo": "uasg_indisponivel"},
    ],
)
def test_unverified_comprasnet_marks_unavailability(monkeypatch, result):
    qualified, reasons = evaluate(
        monkeypatch,
        lambda _operation, snapshots: snapshots.update(
            contratos_comprasnet=result
        ),
    )

    assert qualified is False
    assert reasons == ["indisponibilidade_fonte:contratos_comprasnet"]


def test_source_unavailability_keeps_quote_documented(monkeypatch):
    operation, snapshots, statuses = valid_context()
    snapshots.pop("ceis")
    statuses["ceis"] = "failed"
    saved = {}

    class Query:
        def update(self, data):
            saved.update(data)
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    monkeypatch.setattr(service, "_load_operation", lambda _operation_id: operation)
    monkeypatch.setattr(
        service,
        "_load_snapshots",
        lambda _operation_id: (snapshots, statuses),
    )
    monkeypatch.setattr(service, "get_eligibility_config", lambda: PARAMS.copy())
    monkeypatch.setattr(
        service,
        "supabase",
        SimpleNamespace(table=lambda _name: Query()),
    )

    stage, reasons = service.atualizar_estagio_pos_fase2("C-1", "op-1")

    assert stage == "DOCUMENTADA"
    assert reasons == ["indisponibilidade_fonte:ceis"]
    assert saved["estagio"] == "DOCUMENTADA"
    assert saved["status_ingestao"] == "DOCUMENTADA"
    assert saved["estagio_motivo"] == "indisponibilidade_fonte:ceis"


def test_unverified_registry_status_blocks_qualification(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        lambda _operation, snapshots: snapshots["brasil_api"].clear(),
    )

    assert qualified is False
    assert reasons == ["situacao_cadastral_nao_verificada"]


@pytest.mark.parametrize(
    ("motivo", "tecnico"),
    [
        ("situacao_cadastral_nao_verificada", True),
        ("indisponibilidade_fonte:ceis", True),
        ("Sancao ativa em CEIS", False),
        ("prazo_vincendo_insuficiente:30d", False),
        ("historico_recebimentos_insuficiente:2m", False),
        ("orgaos_pagadores_insuficientes:1", False),
        ("capacidade_insuficiente:99000", False),
        ("contrato_comprasnet_nao_encontrado", False),
    ],
)
def test_classificar_motivos_covers_persisted_reason_codes(motivo, tecnico):
    classificados = service.classificar_motivos([motivo])
    assert classificados["tecnico" if tecnico else "reprovacao"] == [motivo]
    assert classificados["reprovacao" if tecnico else "tecnico"] == []


def test_classificar_motivos_mixed_rejection_and_collection_failure():
    assert service.classificar_motivos([
        "situacao_cadastral_nao_verificada",
        "indisponibilidade_fonte:ceis",
        "capacidade_insuficiente:99000",
    ]) == {
        "tecnico": ["situacao_cadastral_nao_verificada", "indisponibilidade_fonte:ceis"],
        "reprovacao": ["capacidade_insuficiente:99000"],
    }


@pytest.mark.parametrize(
    ("motivos", "status", "pendencia_coleta"),
    [
        ([], "aguardando_relatorio", False),
        (["capacidade_insuficiente:99000"], "reprovada_triagem", False),
        (["situacao_cadastral_nao_verificada"], "aguardando_relatorio", True),
    ],
)
def test_decidir_status_pos_fase2(motivos, status, pendencia_coleta):
    outcome = service.decidir_status_pos_fase2(motivos)
    assert outcome["status"] == status
    assert outcome["pendencia_coleta"] is pendencia_coleta


def test_real_sanction_blocks_qualification(monkeypatch):
    def add_sanction(_operation, snapshots):
        snapshots["ceis"].update(
            possui_sancao=True,
            registros=[{"situacao": "ATIVO"}],
        )

    qualified, reasons = evaluate(monkeypatch, add_sanction)

    assert qualified is False
    assert reasons == ["Sancao ativa em CEIS"]


def test_receipt_volume_no_longer_rejects_a_qualified_quote(monkeypatch):
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
    assert qualified is True
    assert reasons == []
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


def test_missing_ceded_contract_is_technical_collection_pending(monkeypatch):
    qualified, reasons = evaluate(
        monkeypatch,
        lambda _operation, snapshots: snapshots["contratos_pncp"].update(
            contrato_cedido_match="NAO_ENCONTRADO", contrato_cedido=None
        ),
    )

    assert qualified is False
    assert reasons == ["indisponibilidade_fonte:contrato_cedido"]
    assert service.decidir_status_pos_fase2(reasons)["pendencia_coleta"] is True


def test_capacity_uses_original_framed_value_and_rounds_down_idempotently(monkeypatch):
    operation, snapshots, statuses = valid_context()
    operation["valor_enquadrado"] = 650_999
    monkeypatch.setattr(service, "get_eligibility_config", lambda: PARAMS.copy())

    first = service._capacidade_do_contrato(operation, snapshots, statuses, PARAMS)
    assert first is not None
    assert first["valor_enquadrado"] % 1_000 == 0
    assert first["reduziu"] is True

    second_operation = {
        **operation,
        "valor_enquadrado": first["valor_enquadrado"],
        "valor_enquadrado_pre_capacidade": first["base"],
    }
    second = service._capacidade_do_contrato(second_operation, snapshots, statuses, PARAMS)
    assert second is not None
    assert second["valor_enquadrado"] == first["valor_enquadrado"]


def test_scale_jump_flags_ratio_and_missing_receipt_history():
    snapshots = {
        "contratos_pncp": {"faturamento_contratado_12m": 300},
        "recursos_recebidos": {"faturamento_verificado_12m": 100},
    }
    assert service._flags_salto_escala(snapshots, PARAMS) == ["salto_escala:3.0"]

    snapshots["recursos_recebidos"]["faturamento_verificado_12m"] = 0
    assert service._flags_salto_escala(snapshots, PARAMS) == ["salto_escala:sem_historico"]
