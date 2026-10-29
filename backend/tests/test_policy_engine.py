import inspect
from datetime import date
import pytest

from app.services.policy.engine import avaliar
from app.services.policy.types import EntradaPolitica, FindingValue


def _rating_runs():
    return {
        name: {"run_id": name, "status": "COMPLETO"}
        for name in ("cadastro_regularidade", "sacado_orgao", "porte")
    }


def _params():
    return {
        "nivel_nota": {
            "Excepcional": 95,
            "Forte": 85,
            "Adequado": 70,
            "Atencao": 55,
            "Fraco": 40,
            "Critico": 20,
        },
        "pesos_merito": {
            "relacionamento_governamental": 0.3,
            "porte_operacionalidade": 0.28,
            "saude_cadastral": 0.24,
            "reputacao_mercado": 0.18,
        },
        "subpesos_cadastral": {
            "idade": 0.35,
            "capital": 0.29,
            "porte": 0.24,
            "estabilidade": 0.12,
        },
        "subpesos_relacionamento": {
            "volume": 0.3,
            "concentracao": 0.3,
            "historico": 0.25,
            "maturidade": 0.15,
        },
        "faixas_idade": [
            {"ate": 1, "inclusivo": False, "nota": 25},
            {"ate": 2, "inclusivo": False, "nota": 45},
            {"ate": 5, "inclusivo": False, "nota": 60},
            {"ate": 10, "inclusivo": False, "nota": 75},
            {"ate": 20, "inclusivo": True, "nota": 88},
            {"nota": 95},
        ],
        "faixas_capital": [
            {"ate": 10000, "inclusivo": False, "nota": 30},
            {"ate": 50000, "inclusivo": False, "nota": 50},
            {"ate": 200000, "inclusivo": False, "nota": 65},
            {"ate": 500000, "inclusivo": False, "nota": 78},
            {"ate": 2000000, "inclusivo": True, "nota": 88},
            {"nota": 95},
        ],
        "faixas_porte_cadastral": {
            "EPP": 72,
            "natureza_nao_empresarial": 55,
            "fallback": 58,
        },
        "faixas_estabilidade_qsa": {
            "sem_dados": 75,
            "maior_3": 85,
            "de_1_a_3": 70,
            "menor_1": 55,
        },
        "hhi_min_meses_recebimento": 6,
        "piso_fator_regularidade": 0.75,
        "haircut_certidao_por_estado": {"ausente": 0, "negativa": 0},
        "penalidade_cnd_federal_ausente": 6,
        "penalidade_cndt_ausente": 6,
        "penalidade_fgts_ausente": 6,
        "penalidade_balanco_ausente": 10,
        "faixas_rating": {"A": 85, "B": 70, "C": 55, "D": 40, "E": 0},
        "pd_min_anos_completos_volatilidade": 2,
        "pd_cv_corte_moderado": 0.7,
        "pd_cv_corte_alto": 0.8,
        "pd_mult_historico_insuficiente": 1.08,
        "pd_mult_volatilidade_moderada": 1.08,
        "pd_mult_volatilidade_alta": 1.15,
        "pd_performada": 0.016,
        "pd_mult_por_rating": {"A": 0.6, "B": 1, "C": 2, "D": 5, "E": 10},
        "pct_margem_sobre_saldo": 0.70,
        "score_bloqueio": 20,
    }


def test_engine_is_pure_and_tracks_facts():
    source = inspect.getsource(__import__("app.services.policy.engine", fromlist=["x"]))
    assert "score_engine" not in source and "supabase" not in source
    findings = {
        code: FindingValue(code, value, "CONFIRMADO", "ALTA", "run")
        for code, value in {
            "idade_empresa_anos": 4,
            "capital_social_rs": 300000,
            "porte_cadastral": "EPP",
            "qsa_estabilidade": {"entrada_mais_recente": "2020-01-01"},
            "contratos_ativos_qtd": 3,
            "contratos_total_qtd": 3,
            "orgaos_distintos_qtd": 2,
            "hhi_recebimentos": 3000,
            "meses_com_recebimento": 8,
            "maturidade_max_anos": 4,
            "capacidade_operacional": "Atencao",
            "reputacao_mercado": "Atencao",
            "certidao_cnd_federal_pendente": {"estado": "ausente"},
            "certidao_cndt_pendente": {"estado": "ausente"},
            "certidao_fgts_pendente": {"estado": "ausente"},
            "balanco_ausente": False,
        }.items()
    }
    result = avaliar(
        EntradaPolitica(
            {"valor_enquadrado": 100, "valor_solicitado": 120}, findings, _rating_runs()
        ),
        _params(),
        [],
        date(2026, 1, 1),
    )
    assert result.score >= 0 and result.limite_aprovado_rs == 100
    assert result.trilha["regularidade"]


def test_balance_penalty_is_limited_by_low_capability_contribution():
    findings = {
        code: FindingValue(code, value, "CONFIRMADO", "ALTA", "run")
        for code, value in {
            "idade_empresa_anos": 4,
            "capital_social_rs": 300000,
            "porte_cadastral": "EPP",
            "qsa_estabilidade": {},
            "contratos_ativos_qtd": 3,
            "contratos_total_qtd": 3,
            "orgaos_distintos_qtd": 2,
            "hhi_recebimentos": 3000,
            "meses_com_recebimento": 8,
            "maturidade_max_anos": 4,
            "capacidade_operacional": "Critico",
            "reputacao_mercado": "Atencao",
            "certidao_cnd_federal_pendente": {"estado": "negativa"},
            "certidao_cndt_pendente": {"estado": "negativa"},
            "certidao_fgts_pendente": {"estado": "negativa"},
            "balanco_ausente": True,
        }.items()
    }
    result = avaliar(
        EntradaPolitica({}, findings, _rating_runs()), _params(), [], date(2026, 1, 1)
    )
    # 20 (Critico) * 0.28: the official engine cannot deduct all 10 points.
    assert result.penalizacao_balanco == 5.6
    assert result.merit_potencial - result.merit == pytest.approx(5.6)
    assert result.dimensoes["porte_operacionalidade"] == 0.0


def test_engine_without_current_runs_returns_insufficient_data():
    result = avaliar(EntradaPolitica({}, {}), _params(), [], date(2026, 1, 1))

    assert result.status_decisao == "DADOS_INSUFICIENTES"
    assert result.faltantes == ["cadastro_regularidade", "porte", "sacado_orgao"]
    assert result.rating is None
    assert result.limite_aprovado_rs is None


def test_confirmed_veto_remains_decisive_with_partial_inputs():
    finding = FindingValue("cadastro_inativo", True, "CONFIRMADO", "ALTA", "run")
    result = avaliar(
        EntradaPolitica({}, {"cadastro_inativo": finding}),
        _params(),
        [{"classe": "VETO", "codigo": "cadastro_inativo"}],
        date(2026, 1, 1),
    )

    assert result.status_decisao == "VETO"
    assert result.bloqueios == ["cadastro_inativo"]
    assert result.rating == "E"


_INDEX_WEIGHTS = {
    "certidao_cnd_federal_pendente": 0.35,
    "certidao_cndt_pendente": 0.25,
    "certidao_fgts_pendente": 0.25,
    "certidao_cnd_estadual": 0.10,
    "certidao_cnd_municipal": 0.05,
}
_INDEX_NOTES = {
    "negativa": 1.0,
    "positiva_com_efeitos_negativa": 0.6,
    "vencida": 0.3,
    "nao_validada": 0.3,
    "positiva": 0.0,
}


def _index_parameters():
    return _params() | {
        "pesos_indice_regularidade_certidoes": _INDEX_WEIGHTS,
        "notas_indice_regularidade_certidoes": _INDEX_NOTES,
    }


def _index_input(states):
    values = {
        "idade_empresa_anos": 4,
        "capital_social_rs": 300000,
        "porte_cadastral": "EPP",
        "qsa_estabilidade": {"entrada_mais_recente": "2020-01-01"},
        "contratos_ativos_qtd": 3,
        "contratos_total_qtd": 3,
        "orgaos_distintos_qtd": 2,
        "hhi_recebimentos": 3000,
        "meses_com_recebimento": 8,
        "maturidade_max_anos": 4,
        "capacidade_operacional": "Atencao",
        "reputacao_mercado": "Atencao",
        "balanco_ausente": False,
    }
    values.update({code: {"estado": state} for code, state in states.items()})
    return EntradaPolitica(
        {"valor_enquadrado": 100, "valor_solicitado": 100},
        {
            code: FindingValue(code, value, "CONFIRMADO", "ALTA", "run")
            for code, value in values.items()
        },
        _rating_runs(),
    )


@pytest.mark.parametrize(
    ("states", "expected_index", "expected_factor"),
    [
        ({"certidao_cnd_federal_pendente": "negativa"}, 1.0, 1.0),
        (
            {
                "certidao_cnd_federal_pendente": "positiva_com_efeitos_negativa",
                "certidao_cndt_pendente": "negativa",
                "certidao_fgts_pendente": "vencida",
            },
            (0.35 * 0.6 + 0.25 * 1.0 + 0.25 * 0.3) / 0.85,
            0.91,
        ),
        (
            {
                "certidao_cnd_federal_pendente": "negativa",
                "certidao_cndt_pendente": "positiva_com_efeitos_negativa",
                "certidao_fgts_pendente": "vencida",
                "certidao_cnd_estadual": "positiva",
                "certidao_cnd_municipal": "nao_validada",
            },
            0.35 * 1.0 + 0.25 * 0.6 + 0.25 * 0.3 + 0.10 * 0.0 + 0.05 * 0.3,
            0.90,
        ),
        ({"certidao_cnd_federal_pendente": "vencida"}, 0.3, 0.82),
        ({"certidao_cnd_federal_pendente": "positiva"}, 0.0, 0.75),
    ],
)
def test_parameterized_certificate_index_uses_only_assessed_certificates(
    states, expected_index, expected_factor,
):
    result = avaliar(_index_input(states), _index_parameters(), [], date(2026, 1, 1))

    assert result.indice_regularidade == pytest.approx(expected_index)
    assert result.fator_regularidade == pytest.approx(expected_factor)
    assert result.fator_regularidade_v4 == pytest.approx(expected_factor)
    assert {item["codigo"] for item in result.certidoes_avaliadas} == set(states)


def test_certificate_index_is_neutral_when_no_certificate_was_assessed():
    result = avaliar(_index_input({}), _index_parameters(), [], date(2026, 1, 1))

    assert result.indice_regularidade is None
    assert result.fator_regularidade == 1.0
    assert len(result.certidoes_ausentes) == 5


def test_certificate_absence_is_outside_index_denominator():
    result = avaliar(
        _index_input({
            "certidao_cnd_federal_pendente": "positiva",
            "certidao_cndt_pendente": "ausente",
            "certidao_fgts_pendente": "ausente",
        }),
        _index_parameters(), [], date(2026, 1, 1),
    )

    assert result.indice_regularidade == 0.0
    assert result.fator_regularidade == 0.75
    assert {item["codigo"] for item in result.certidoes_ausentes} == {
        "certidao_cndt_pendente", "certidao_fgts_pendente", "certidao_cnd_estadual", "certidao_cnd_municipal",
    }


def test_v3_regularidade_penalties_remain_unchanged_without_v4_parameters():
    result = avaliar(_index_input({}), _params(), [], date(2026, 1, 1))

    assert result.fator_regularidade == 0.82
    assert result.fator_regularidade_v4 is None
    assert result.indice_regularidade is None


def test_v4_absent_core_certificates_become_precedent_conditions():
    rules = [
        {
            "codigo": code,
            "classe": "CONDICAO",
            "parametro_alvo": "cp",
            "condicao": {
                "estados": ["CONFIRMADO"],
                "todas": [{"campo": "estado", "op": "==", "ref": "ausente"}],
                "mensagem": message,
            },
        }
        for code, message in (
            ("certidao_cnd_federal_pendente", "CND Federal ausente"),
            ("certidao_cndt_pendente", "CNDT ausente"),
            ("certidao_fgts_pendente", "CRF do FGTS vale 30 dias e deve estar vigente na aprovação"),
        )
    ]
    result = avaliar(
        _index_input({
            "certidao_cnd_federal_pendente": "ausente",
            "certidao_cndt_pendente": "ausente",
            "certidao_fgts_pendente": "ausente",
        }),
        _index_parameters(), rules, date(2026, 1, 1),
    )

    assert [item["codigo"] for item in result.condicoes_precedentes] == [
        "certidao_cnd_federal_pendente", "certidao_cndt_pendente", "certidao_fgts_pendente",
    ]
    assert "30 dias" in result.condicoes_precedentes[-1]["descricao"]
