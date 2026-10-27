from datetime import date

import pytest

from app.services.capacidade_contrato_service import (
    calcular_capacidade_contrato,
    explicar_capacidade,
)


PARAMS = {
    "cap_fator_liquido_mao_obra": 0.645,
    "cap_fator_liquido_demais": 0.85,
    "cap_cobertura_parcela": 1.25,
    "cap_taxa_referencia_am": 0.035,
    "cap_folga_meses": 1,
    "cap_sob_demanda_janela_meses": 6,
    "cap_sob_demanda_min_faturas": 2,
}


def test_ps_ufpr_capacity_reference_case():
    result = calcular_capacidade_contrato(
        1_963_873.90, "2026-07-20", "2027-07-20", True, PARAMS, date(2026, 10, 9)
    )

    assert result["memoria"]["n"] == 9
    assert result["memoria"]["mensal_liquido"] == pytest.approx(105_640, rel=0.01)
    assert result["capacidade"] == pytest.approx(640_000, rel=0.01)


def test_facilita_capacity_reference_case_uses_next_contract_anniversary():
    result = calcular_capacidade_contrato(
        280_200, "2026-08-18", "2028-08-18", True, PARAMS, date(2026, 10, 9)
    )

    assert result["memoria"]["horizonte_firme"] == "2027-08-18"
    assert 48_000 <= result["capacidade"] <= 51_000


def test_future_contract_uses_first_anniversary_and_start_as_capacity_anchor():
    result = calcular_capacidade_contrato(
        4_354_716.60, "2026-11-03", "2027-11-03", False, PARAMS, date(2026, 10, 27)
    )

    assert result["memoria"]["horizonte_firme"] == "2027-11-03"
    assert result["memoria"]["inicio_futuro"] is True
    assert result["memoria"]["ancora"] == "2026-11-03"
    assert result["memoria"]["n"] == 12
    assert result["capacidade"] > 0
    assert result["capacidade"] > result["memoria"]["parcela_max"] * 8


def test_future_contract_with_short_term_ends_at_contract_end():
    result = calcular_capacidade_contrato(
        1_000_000, "2026-11-03", "2027-06-15", False, PARAMS, date(2026, 10, 27)
    )

    assert result["memoria"]["horizonte_firme"] == "2027-06-15"
    assert result["memoria"]["ancora"] == "2026-11-03"
    assert result["memoria"]["n"] == 7


def _demand_execution(invoices, *, verified=True):
    return {
        "modalidade": "SOB_DEMANDA",
        "faturas": invoices,
        "leitura_faturas_verificada": verified,
    }


def _invoice(month, value="10.000,00", *, liquid=None, glosa="0,00", cancelled=False):
    return {
        "emissao": f"2026-{month:02d}-10",
        "valor": value,
        "valorliquido": liquid,
        "glosa": glosa,
        "situacao": "Cancelada" if cancelled else "Emitida",
    }


def _demand_capacity(invoices, *, params=None, verified=True, start="2026-01-01", reference=date(2026, 10, 15)):
    return calcular_capacidade_contrato(
        360_000, start, "2027-01-01", False, params or PARAMS, reference,
        execucao=_demand_execution(invoices, verified=verified),
    )


def test_demand_capacity_uses_real_execution_when_minimum_evidence_is_met():
    result = _demand_capacity([_invoice(month) for month in range(5, 11)])

    assert result["memoria"]["mensal_bruto"] == 10_000
    assert result["memoria"]["fonte_mensal"] == "EXECUCAO_REAL"
    assert result["memoria"]["mensal_nominal"] == 30_000
    assert result["memoria"]["mensal_execucao"] == 10_000
    assert result["memoria"]["faturas_na_janela"] == 6
    assert result["memoria"]["janela_meses"] == 6


def test_demand_capacity_uses_complete_portal_execution_when_comprasnet_has_no_minimum():
    result = calcular_capacidade_contrato(
        360_000, "2026-01-01", "2027-01-01", False, PARAMS, date(2026, 10, 15),
        execucao={
            "modalidade": "SOB_DEMANDA", "faturas": [], "leitura_faturas_verificada": True,
            "leitura_portal_verificada": True,
            "faturas_portal": [
                {"emissao_aproximada": "2026-08-25", "valor": 12_000, "valorliquido": 10_000},
                {"emissao_aproximada": "2026-09-21", "valor": 24_000, "valorliquido": 20_000},
            ],
        },
    )

    assert result["memoria"]["fonte_mensal"] == "EXECUCAO_REAL"
    assert result["memoria"]["fonte_execucao"] == "PORTAL_OB"
    assert result["memoria"]["mensal_execucao"] > 0
    assert result["memoria"]["mensal_bruto"] <= result["memoria"]["mensal_nominal"]


def test_demand_capacity_never_exceeds_nominal_monthly_value():
    result = _demand_capacity([
        *[_invoice(month, "40.000,00") for month in range(5, 11)],
        _invoice(10, "100.000,00", cancelled=True),
    ])

    assert result["memoria"]["mensal_bruto"] == 30_000
    assert result["memoria"]["fonte_mensal"] == "EXECUCAO_REAL"


def test_demand_capacity_without_confirmed_invoices_is_zero():
    result = _demand_capacity([])

    assert result["capacidade"] == 0
    assert result["memoria"]["mensal_bruto"] == 0
    assert result["memoria"]["fonte_mensal"] == "SEM_EXECUCAO"


def test_demand_capacity_with_insufficient_invoice_evidence_is_zero():
    result = _demand_capacity([_invoice(10)])

    assert result["capacidade"] == 0
    assert result["memoria"]["fonte_mensal"] == "SEM_EXECUCAO"
    assert result["memoria"]["faturas_na_janela"] == 1


def test_demand_capacity_uses_nominal_when_invoice_read_is_not_verified():
    result = _demand_capacity([], verified=False)

    assert result["memoria"]["mensal_bruto"] == 30_000
    assert result["memoria"]["mensal_execucao"] is None
    assert result["memoria"]["fonte_mensal"] == "NOMINAL_EXECUCAO_NAO_VERIFICADA"


def test_demand_minimum_invoices_zero_disables_execution_rule():
    result = _demand_capacity([], params=PARAMS | {"cap_sob_demanda_min_faturas": 0})

    assert result["memoria"]["mensal_bruto"] == 30_000
    assert result["memoria"]["fonte_mensal"] == "NOMINAL"


def test_demand_contract_uses_elapsed_life_when_younger_than_execution_window():
    result = _demand_capacity(
        [_invoice(9), _invoice(10)], start="2026-08-15",
    )

    assert result["memoria"]["janela_meses"] == 2


def test_explicar_capacidade_describes_confirmed_absence_of_execution():
    explanation = explicar_capacidade({
        "fonte_mensal": "SEM_EXECUCAO",
        "faturas_na_janela": 0,
        "janela_meses": 6,
        "mensal_nominal": 30_000,
    }, PARAMS)

    assert explanation == {
        "codigo": "SEM_EXECUCAO",
        "titulo": "Contrato sob demanda sem execução comprovada",
        "detalhe": (
            "0 fatura(s) nos últimos 6 mês(es) (mínimo exigido: 2). "
            "O valor global é um teto de compras, não uma receita garantida; "
            "por isso a capacidade considerada é R$ 0,00. Valor global ÷ meses "
            "seria R$ 30.000,00/mês."
        ),
        "nivel": "ALERTA",
    }


def test_explicar_capacidade_describes_real_or_unverified_execution():
    real = explicar_capacidade({
        "fonte_mensal": "EXECUCAO_REAL",
        "mensal_execucao": 10_000,
        "mensal_nominal": 30_000,
        "janela_meses": 6,
    })
    unverified = explicar_capacidade({"fonte_mensal": "NOMINAL_EXECUCAO_NAO_VERIFICADA"})

    assert real == {
        "codigo": "EXECUCAO_REAL",
        "titulo": "Capacidade limitada pela execução real do contrato",
        "detalhe": (
            "Média mensal executada de R$ 10.000,00 nos últimos 6 mês(es) versus "
            "nominal de R$ 30.000,00; foi usada a menor das duas (R$ 10.000,00/mês)."
        ),
        "nivel": "INFO",
    }
    assert unverified == {
        "codigo": "NOMINAL_EXECUCAO_NAO_VERIFICADA",
        "titulo": "Execução do contrato não verificada",
        "detalhe": (
            "A leitura das faturas no Comprasnet não foi confirmada; usado valor "
            "global ÷ meses. Reverifique antes de decidir."
        ),
        "nivel": "ALERTA",
    }


def test_explicar_capacidade_identifies_contract_that_has_not_started():
    explanation = explicar_capacidade({
        "fonte_mensal": "NOMINAL",
        "inicio_futuro": True,
    })

    assert explanation == {
        "codigo": "INICIO_FUTURO",
        "titulo": "Contrato ainda não iniciado",
        "detalhe": "O contrato ainda não iniciou; o horizonte firme considerado é o primeiro aniversário.",
        "nivel": "INFO",
    }


@pytest.mark.parametrize("memoria", [None, {}, {"fonte_mensal": "NOMINAL"}, {"fonte_mensal": "SEM_EXECUCAO"}])
def test_explicar_capacidade_returns_none_for_legacy_or_nominal_memory(memoria):
    explanation = explicar_capacidade(memoria)

    if memoria == {"fonte_mensal": "SEM_EXECUCAO"}:
        assert explanation is not None
        assert "—" in explanation["detalhe"]
    else:
        assert explanation is None
