from unittest.mock import Mock

import pytest

from app.services.eligibility_service import check_eligibility
from app.workers.tasks.score_engine import _limite_aprovado


PARAMS = {
    "ticket_minimo": 10_000,
    "ticket_maximo": 5_000_000,
    "pct_max_contrato": 0.50,
    "prazo_padrao_meses": 12,
    "dias_minimos_expiracao": 5,
    "prazo_minimo_dias": 60,
    "cnpj_idade_minima_meses": 12,
}


def eligibility(**kwargs):
    return check_eligibility(
        cnpj="03012610000101",
        valor_solicitado=kwargs.pop("valor_solicitado", 1_000_000),
        params=PARAMS,
        **kwargs,
    )


def test_broadfactor_margin_derives_gross_remaining_balance():
    result = eligibility(
        valor_solicitado=600_000,
        margem_disponivel=560_000,
    )

    assert result.saldo_vincendo == 800_000
    assert result.valor_enquadrado == 400_000
    assert "saldo_vincendo_derivado" in result.flags

    score_limit, flags = _limite_aprovado(
        {"contratos": {"valor_total_ativo": 18_052_130}},
        {
            "margem_disponivel": 560_000,
            "valor_solicitado": 600_000,
            "valor_enquadrado": 400_000,
            "saldo_vincendo": 800_000,
            "pct_max_contrato": 0.50,
        },
    )
    assert score_limit == 400_000
    assert flags == []


def test_manual_contract_balance_is_used_without_70_percent_cap():
    result = eligibility(
        valor_solicitado=600_000,
        contrato_saldo=800_000,
    )

    assert result.saldo_vincendo == 800_000
    assert result.valor_enquadrado == 400_000
    assert "saldo_vincendo_derivado" not in result.flags

    score_limit, flags = _limite_aprovado(
        {},
        {
            "contrato_saldo": 18_052_130,
            "valor_solicitado": 20_000_000,
            "valor_enquadrado": 9_026_065,
            "saldo_vincendo": 18_052_130,
            "pct_max_contrato": 0.50,
        },
    )
    assert score_limit == 9_026_065
    assert flags == []


def test_broadfactor_margin_wins_when_both_values_are_present(monkeypatch):
    warning = Mock()
    monkeypatch.setattr("app.services.eligibility_service.logger.warning", warning)

    result = eligibility(
        margem_disponivel=500_000,
        contrato_saldo=10_000_000,
    )

    assert result.saldo_vincendo == 714_285.71
    assert result.valor_enquadrado == 357_142.85
    assert "saldo_vincendo_derivado" in result.flags
    assert "margem_disponivel_prevalece_sobre_contrato_saldo" in result.flags
    warning.assert_called_once()


def test_enquadramento_never_exceeds_requested_value():
    result = eligibility(
        valor_solicitado=100_000,
        margem_disponivel=1_000_000,
    )

    assert result.elegivel is True
    assert result.valor_enquadrado == 100_000


@pytest.mark.parametrize(
    ("valor_solicitado", "margem_disponivel", "expected_value", "eligible"),
    [
        (20_000, 10_000, 7_142.85, False),
        (8_000_000, 5_600_000, 4_000_000, True),
        (8_000_000, 8_400_000, 6_000_000, False),
    ],
)
def test_ticket_limits_apply_to_enquadrado_not_requested(
    valor_solicitado,
    margem_disponivel,
    expected_value,
    eligible,
):
    result = eligibility(
        valor_solicitado=valor_solicitado,
        margem_disponivel=margem_disponivel,
    )

    assert result.valor_enquadrado == expected_value
    assert result.elegivel is eligible


def test_prazo_uses_default_and_flags_missing_vincendo():
    result = eligibility(margem_disponivel=2_000_000)

    assert result.prazo_final_meses == 12
    assert "prazo_vincendo_indisponivel" in result.flags


def test_prazo_is_limited_by_remaining_contract_term():
    result = eligibility(
        margem_disponivel=2_000_000,
        prazo_vincendo_meses=7,
    )

    assert result.prazo_final_meses == 7
    assert "prazo_vincendo_indisponivel" not in result.flags


def test_minimum_term_comes_from_parameters():
    result = eligibility(
        margem_disponivel=2_000_000,
        prazo_dias=59,
    )

    assert result.elegivel is False
    assert result.campo == "prazo_dias"


def test_score_limit_respects_persisted_enquadrado():
    score_limit, flags = _limite_aprovado(
        {},
        {
            "margem_disponivel": 12_636_491,
            "valor_solicitado": 20_000_000,
            "valor_enquadrado": 9_026_065,
            "saldo_vincendo": 18_052_130,
            "pct_max_contrato": 0.50,
        },
    )

    assert score_limit == 9_026_065
    assert flags == []


@pytest.mark.parametrize(
    "operation",
    [
        {
            "margem_disponivel": 560_000,
            "valor_solicitado": 600_000,
            "pct_max_contrato": 0.50,
        },
        {
            "contrato_saldo": 800_000,
            "valor_solicitado": 600_000,
            "pct_max_contrato": 0.50,
        },
    ],
)
def test_score_fallback_uses_50_percent_of_gross_contract_balance(operation):
    score_limit, flags = _limite_aprovado({}, operation)

    assert score_limit == 400_000
    assert flags == []
