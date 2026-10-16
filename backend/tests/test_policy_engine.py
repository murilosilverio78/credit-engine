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
