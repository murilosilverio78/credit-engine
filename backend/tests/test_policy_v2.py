"""Pure contracts for generic v2 policy effects."""

from datetime import date

from app.services.policy.engine import _generic_effects, avaliar
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


def test_authority_records_all_triggering_reasons_including_rating_d():
    effects = _generic_effects(
        EntradaPolitica({}, {"exposicao_sobre_pl": _finding("exposicao_sobre_pl", 3.6)}),
        [
            _rule("rating_d", "alcada", 2, {"rating_in": ["D"], "mensagem": "Rating D"}),
            _rule("exposicao_sobre_pl", "alcada", 3, {"comparacao": {"campo": "", "op": ">", "ref": 2}, "mensagem": "Exposição sobre PL"}),
        ],
        {},
        "D",
        _params(),
    )

    assert effects["alcada_minima"] == 3
    assert effects["alcada_motivos"] == [
        {"codigo": "rating_d", "nivel": 2, "mensagem": "Rating D"},
        {"codigo": "exposicao_sobre_pl", "nivel": 3, "mensagem": "Exposição sobre PL"},
    ]


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


def _v3_rules():
    return [
        _rule(
            "antecipacao_concorrente", None, None,
            {"estados": ["CONFIRMADO"], "comparacao": {"campo": "ativa_mesmo_contrato", "op": "==", "ref": True}},
            "VETO",
        ),
        _rule(
            "antecipacao_concorrente", "cp", None,
            {
                "estados": ["CONFIRMADO"],
                "comparacao": {"campo": "encerrada_mesmo_contrato", "op": "==", "ref": True},
                "mensagem": "Declaração de quitação da antecipação anterior no mesmo contrato",
                "documento_exigido": "Declaração de quitação / extrato da conta vinculada",
            },
            "CONDICAO",
        ),
        _rule(
            "empenho_insuficiente", "cp", None,
            {"estados": ["CONFIRMADO"], "mensagem": "Empenho insuficiente para as parcelas: apresentar reforço de empenho"},
            "CONDICAO",
        ),
        _rule(
            "antecipacao_concorrente", "alcada", 2,
            {"estados": ["CONFIRMADO"], "comparacao": {"campo": "ativa_outro_contrato", "op": "==", "ref": True}},
        ),
        _rule(
            "cobertura_parcela", "alcada", 2,
            {"estados": ["CONFIRMADO"], "comparacao": {"campo": "indice", "op": "<", "ref": 1.25}},
        ),
    ]


def _evaluate_v3(findings):
    return avaliar(
        EntradaPolitica({}, findings, _rating_runs()),
        _params() | {"limite_fator_minimo": 0.4},
        _v3_rules(),
        date(2026, 10, 19),
    )


def test_v3_93ca0242_active_4903_on_same_contract_is_veto():
    result = _evaluate_v3({
        "antecipacao_concorrente": _finding(
            "antecipacao_concorrente",
            {"ativa_mesmo_contrato": True, "encerrada_mesmo_contrato": False, "ativa_outro_contrato": False},
        )
    })

    assert result.status_decisao == "VETO"
    assert result.bloqueios == ["antecipacao_concorrente"]


def test_v3_e91e6e07_closed_4223_on_same_contract_requires_settlement_declaration():
    result = _evaluate_v3({
        "antecipacao_concorrente": _finding(
            "antecipacao_concorrente",
            {"ativa_mesmo_contrato": False, "encerrada_mesmo_contrato": True, "ativa_outro_contrato": False},
        )
    })

    assert result.status_decisao != "VETO"
    assert result.condicoes_precedentes == [{
        "codigo": "antecipacao_concorrente",
        "descricao": "Declaração de quitação da antecipação anterior no mesmo contrato",
        "documento_exigido": "Declaração de quitação / extrato da conta vinculada",
    }]


def test_v3_271d9f08_other_contract_4701_and_coverage_apply_authority_only_when_matched():
    other_contract = _evaluate_v3({
        "antecipacao_concorrente": _finding(
            "antecipacao_concorrente",
            {"ativa_mesmo_contrato": False, "encerrada_mesmo_contrato": False, "ativa_outro_contrato": True},
        ),
    })
    low_coverage = _evaluate_v3({"cobertura_parcela": _finding("cobertura_parcela", {"indice": 1.10})})
    no_anticipation = _evaluate_v3({
        "antecipacao_concorrente": _finding(
            "antecipacao_concorrente",
            {"ativa_mesmo_contrato": False, "encerrada_mesmo_contrato": False, "ativa_outro_contrato": False},
        ),
        "cobertura_parcela": _finding("cobertura_parcela", {"indice": 1.30}),
    })

    assert other_contract.alcada_minima == 2
    assert low_coverage.alcada_minima == 2
    assert no_anticipation.alcada_minima == 1
    assert no_anticipation.condicoes_precedentes == []


def test_veto_comparison_is_not_bypassed_by_a_truthy_value_and_v2_effects_keep_parity():
    v2_rules = [_rule("rating", "limite_mult", 0.85, {"rating_in": ["D"]})]
    findings = {
        "antecipacao_concorrente": _finding(
            "antecipacao_concorrente",
            {"ativa_mesmo_contrato": False, "encerrada_mesmo_contrato": False, "ativa_outro_contrato": False},
        )
    }
    v2 = avaliar(EntradaPolitica({}, findings, _rating_runs()), _params() | {"limite_fator_minimo": 0.4}, v2_rules, date(2026, 10, 19))
    v3_without_matches = _evaluate_v3(findings)

    assert v3_without_matches.status_decisao != "VETO"
    assert v2.limite_multiplicadores == [{"codigo": "rating", "fator": 0.85}]
