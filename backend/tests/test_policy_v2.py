"""Pure contracts for generic v2 policy effects."""

from datetime import date

from app.services.policy.engine import avaliar
from app.services.policy.types import EntradaPolitica, FindingValue
from tests.test_policy_engine import _params, _rating_runs


def _finding(code, value, state="CONFIRMADO", confidence="ALTA"):
    return FindingValue(code, value, state, confidence, "run")


def _rule(code, target, magnitude, condition, classe="AJUSTE"):
    return {
        "codigo": code,
        "classe": classe,
        "parametro_alvo": target,
        "magnitude": magnitude,
        "condicao": condition,
    }


def test_generic_v2_composes_effects_and_uses_maximum_authority():
    findings = {
        "exposicao_sobre_pl": _finding("exposicao_sobre_pl", 3.6, confidence="MEDIA"),
        "capital_social_alterado_recente": _finding(
            "capital_social_alterado_recente", {"variacao": 0.2}
        ),
        "alavancagem_declarada": _finding(
            "alavancagem_declarada", 2.0, confidence="MEDIA"
        ),
        "liquidez_corrente": _finding("liquidez_corrente", 0.8, confidence="MEDIA"),
        "aval_cobertura": _finding("aval_cobertura", {"cobertura": 1.1}),
    }
    rules = [
        _rule(
            "capital_social_alterado_recente",
            "cp",
            None,
            {
                "estados": ["CONFIRMADO"],
                "mensagem": "Integralizar",
                "documento_exigido": "Comprovante",
            },
            "CONDICAO",
        ),
        _rule(
            "exposicao_sobre_pl",
            "alcada",
            2,
            {
                "estados": ["CONFIRMADO"],
                "confianca_minima": "MEDIA",
                "comparacao": {"campo": "", "op": ">", "ref": 1},
            },
        ),
        _rule(
            "exposicao_sobre_pl",
            "alcada",
            3,
            {
                "estados": ["CONFIRMADO"],
                "confianca_minima": "MEDIA",
                "comparacao": {"campo": "", "op": ">", "ref": 2},
            },
        ),
        _rule(
            "alavancagem_declarada",
            "limite_mult",
            0.8,
            {
                "estados": ["CONFIRMADO"],
                "confianca_minima": "MEDIA",
                "comparacao": {"campo": "", "op": ">", "ref": 1.5},
            },
        ),
        _rule(
            "liquidez_corrente",
            "taxa_pp",
            0.3,
            {
                "estados": ["CONFIRMADO"],
                "confianca_minima": "MEDIA",
                "comparacao": {"campo": "", "op": "<", "ref": 1},
            },
        ),
        _rule(
            "aval_cobertura", "info", None, {"estados": ["CONFIRMADO"]}, "INFORMATIVO"
        ),
    ]
    params = _params() | {"limite_fator_minimo": 0.4}
    result = avaliar(
        EntradaPolitica(
            {"valor_enquadrado": 1000, "valor_solicitado": 1000},
            findings,
            _rating_runs(),
        ),
        params,
        rules,
        date(2026, 1, 1),
    )

    assert result.alcada_minima == 3
    assert result.condicoes_precedentes == [
        {
            "codigo": "capital_social_alterado_recente",
            "descricao": "Integralizar",
            "documento_exigido": "Comprovante",
        }
    ]
    assert result.limite_multiplicadores == [
        {"codigo": "alavancagem_declarada", "fator": 0.8}
    ]
    assert result.taxa_ajuste_pp == 0.3
    assert result.informativos == [
        {"codigo": "aval_cobertura", "valor": {"cobertura": 1.1}}
    ]
    assert result.limite_aprovado_rs == 800


def test_generic_v2_respects_confidence_absence_and_veto_precedence():
    rules = [
        _rule(
            "cessao_previa_mesmo_contrato",
            None,
            None,
            {"estados": ["CONFIRMADO"], "confianca_minima": "ALTA"},
            "VETO",
        ),
        _rule(
            "cessao_previa_mesmo_contrato",
            "cp",
            None,
            {"estados": ["CONFIRMADO", "AMBIGUO"], "mensagem": "Declaracao"},
            "CONDICAO",
        ),
        _rule("ausente", "alcada", 3, {"estados": ["CONFIRMADO"]}),
        _rule(
            "baixa",
            "alcada",
            3,
            {"estados": ["CONFIRMADO"], "confianca_minima": "MEDIA"},
        ),
    ]
    result = avaliar(
        EntradaPolitica(
            {},
            {
                "cessao_previa_mesmo_contrato": _finding(
                    "cessao_previa_mesmo_contrato", True
                ),
                "baixa": _finding("baixa", 9, confidence="BAIXA"),
            },
        ),
        _params(),
        rules,
        date(2026, 1, 1),
    )

    assert result.bloqueios == ["cessao_previa_mesmo_contrato"]
    assert result.condicoes_precedentes == []
    assert result.alcada_minima == 1


def test_veto_e_dados_insuficientes_preservam_efeitos_documentais():
    effects = [
        _rule(
            "documento_pendente",
            "cp",
            None,
            {"estados": ["CONFIRMADO"], "mensagem": "Enviar documento"},
            "CONDICAO",
        ),
        _rule(
            "exposicao",
            "alcada",
            2,
            {"estados": ["CONFIRMADO"]},
        ),
        _rule(
            "documento_pendente",
            "info",
            None,
            {"estados": ["CONFIRMADO"]},
            "INFORMATIVO",
        ),
    ]
    findings = {
        "documento_pendente": _finding("documento_pendente", {"tipo": "IRPF"}),
        "exposicao": _finding("exposicao", 3),
    }
    insufficient = avaliar(
        EntradaPolitica({}, findings), _params(), effects, date(2026, 1, 1)
    )
    veto = avaliar(
        EntradaPolitica({}, {**findings, "bloqueio": _finding("bloqueio", True)}),
        _params(),
        [
            _rule("bloqueio", None, None, {"estados": ["CONFIRMADO"]}, "VETO"),
            *effects,
        ],
        date(2026, 1, 1),
    )

    for result in (insufficient, veto):
        assert result.condicoes_precedentes[0]["codigo"] == "documento_pendente"
        assert result.alcada_minima == 2
        assert result.informativos == [
            {"codigo": "documento_pendente", "valor": {"tipo": "IRPF"}}
        ]
    assert insufficient.status_decisao == "DADOS_INSUFICIENTES"
    assert insufficient.rating is None and insufficient.limite_aprovado_rs is None
    assert veto.status_decisao == "VETO"
    assert veto.rating == "E" and veto.limite_aprovado_rs == 0
