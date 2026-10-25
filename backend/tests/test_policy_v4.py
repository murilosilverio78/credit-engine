from datetime import date

import pytest

from app.services.policy.engine import _compare, avaliar
from app.services.policy.types import EntradaPolitica, FindingValue
from tests.test_policy_engine import _params, _rating_runs


OUR_FINANCIER_MESSAGE = (
    "Antecipação ativa no mesmo contrato com o nosso financiador: apresentar saldo "
    "devedor atualizado da operação anterior; a nova operação deve manter o mesmo "
    "financiador e a mesma conta domicílio"
)


def _finding(value):
    return FindingValue("antecipacao_concorrente", value, "CONFIRMADO", "ALTA", "run")


def _rule(classe, target, magnitude, condition):
    return {
        "codigo": "antecipacao_concorrente",
        "classe": classe,
        "parametro_alvo": target,
        "magnitude": magnitude,
        "condicao": condition,
    }


def _same_contract_condition(op):
    return {
        "estados": ["CONFIRMADO"],
        "todas": [
            {"campo": "ativa_mesmo_contrato", "op": "==", "ref": True},
            {
                "campo": "bancos_ativos_mesmo_contrato",
                "op": op,
                "ref_parametro": "financiador_banco_codigo",
            },
        ],
    }


def _v4_rules():
    return [
        _rule("VETO", None, None, _same_contract_condition("nao_contem")),
        _rule(
            "CONDICAO", "cp", None,
            _same_contract_condition("contem") | {
                "mensagem": OUR_FINANCIER_MESSAGE,
                "documento_exigido": "Declaração de saldo devedor atualizado da operação anterior",
            },
        ),
        _rule("AJUSTE", "alcada", 2, _same_contract_condition("contem")),
        _rule(
            "CONDICAO", "cp", None,
            {
                "estados": ["CONFIRMADO"],
                "comparacao": {
                    "campo": "encerrada_mesmo_contrato", "op": "==", "ref": True,
                },
                "mensagem": "Declaração de quitação da antecipação anterior no mesmo contrato",
                "documento_exigido": "Declaração de quitação / extrato da conta vinculada",
            },
        ),
        _rule(
            "AJUSTE", "alcada", 2,
            {
                "estados": ["CONFIRMADO"],
                "comparacao": {"campo": "ativa_outro_contrato", "op": "==", "ref": True},
            },
        ),
    ]


def _evaluate(value, bank_parameter="274", *, include_parameter=True):
    parameters = _params() | {"limite_fator_minimo": 0.4}
    if include_parameter:
        parameters["financiador_banco_codigo"] = bank_parameter
    return avaliar(
        EntradaPolitica({}, {"antecipacao_concorrente": _finding(value)}, _rating_runs()),
        parameters,
        _v4_rules(),
        date(2026, 10, 19),
    )


def test_compare_contains_and_not_contains_normalize_string_values():
    assert _compare([" 274 ", 999], "contem", "274")
    assert not _compare(["274"], "nao_contem", "274")
    assert _compare(["274"], "nao_contem", "999")
    assert not _compare([], "contem", None)
    assert _compare([], "nao_contem", None)


@pytest.mark.parametrize(
    ("bank_parameter", "include_parameter"),
    [(None, False), (None, True), ([], True)],
)
def test_v4_missing_null_or_empty_financier_parameter_keeps_safe_veto(
    bank_parameter, include_parameter,
):
    result = _evaluate(
        {
            "ativa_mesmo_contrato": True,
            "encerrada_mesmo_contrato": False,
            "ativa_outro_contrato": False,
            "bancos_ativos_mesmo_contrato": ["274"],
        },
        bank_parameter,
        include_parameter=include_parameter,
    )

    assert result.status_decisao == "VETO"
    assert result.bloqueios == ["antecipacao_concorrente"]


def test_v4_our_financier_on_same_contract_requires_balance_declaration_and_authority():
    result = _evaluate({
        "ativa_mesmo_contrato": True,
        "encerrada_mesmo_contrato": False,
        "ativa_outro_contrato": False,
        "bancos_ativos_mesmo_contrato": ["274"],
    })

    assert result.status_decisao != "VETO"
    assert result.condicoes_precedentes == [{
        "codigo": "antecipacao_concorrente",
        "descricao": OUR_FINANCIER_MESSAGE,
        "documento_exigido": "Declaração de saldo devedor atualizado da operação anterior",
    }]
    assert result.alcada_minima == 2


def test_v4_other_financier_on_same_contract_is_veto():
    result = _evaluate({
        "ativa_mesmo_contrato": True,
        "encerrada_mesmo_contrato": False,
        "ativa_outro_contrato": False,
        "bancos_ativos_mesmo_contrato": ["274"],
    }, "999")

    assert result.status_decisao == "VETO"


def test_v4_same_contract_without_identified_bank_is_veto():
    result = _evaluate({
        "ativa_mesmo_contrato": True,
        "encerrada_mesmo_contrato": False,
        "ativa_outro_contrato": False,
        "bancos_ativos_mesmo_contrato": [],
    })

    assert result.status_decisao == "VETO"


def test_v4_other_contract_keeps_v3_authority_without_veto():
    result = _evaluate({
        "ativa_mesmo_contrato": False,
        "encerrada_mesmo_contrato": False,
        "ativa_outro_contrato": True,
        "bancos_ativos_mesmo_contrato": [],
    })

    assert result.status_decisao != "VETO"
    assert result.alcada_minima == 2


def test_v4_closed_same_contract_keeps_v3_settlement_condition():
    result = _evaluate({
        "ativa_mesmo_contrato": False,
        "encerrada_mesmo_contrato": True,
        "ativa_outro_contrato": False,
        "bancos_ativos_mesmo_contrato": [],
    })

    assert result.status_decisao != "VETO"
    assert result.condicoes_precedentes == [{
        "codigo": "antecipacao_concorrente",
        "descricao": "Declaração de quitação da antecipação anterior no mesmo contrato",
        "documento_exigido": "Declaração de quitação / extrato da conta vinculada",
    }]
