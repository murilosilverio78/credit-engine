from datetime import date

from app.services.policy.engine import avaliar
from app.services.policy.types import EntradaPolitica, FindingValue
from tests.test_policy_engine import _params


def _finding(code, value, state="CONFIRMADO", confidence="ALTA"):
    return FindingValue(code, value, state, confidence, "run")


def test_golden_99e4_parity_inputs():
    findings = {
        "idade_empresa_anos": _finding("idade_empresa_anos", 4.67),
        "capital_social_rs": _finding("capital_social_rs", 370000),
        "porte_cadastral": _finding("porte_cadastral", "EPP"),
        "natureza_juridica_empresarial": _finding("natureza_juridica_empresarial", True),
        "qsa_estabilidade": _finding("qsa_estabilidade", {"entrada_mais_recente": "2024-01-01"}),
        "contratos_ativos_qtd": _finding("contratos_ativos_qtd", 3),
        "contratos_total_qtd": _finding("contratos_total_qtd", 3),
        "orgaos_distintos_qtd": _finding("orgaos_distintos_qtd", 2),
        "hhi_recebimentos": _finding("hhi_recebimentos", 5472.4),
        "meses_com_recebimento": _finding("meses_com_recebimento", 8),
        "maturidade_max_anos": _finding("maturidade_max_anos", 4),
        "capacidade_operacional": _finding("capacidade_operacional", "Atencao"),
        "reputacao_mercado": _finding("reputacao_mercado", "Atencao"),
        "certidao_cnd_federal_pendente": _finding("certidao_cnd_federal_pendente", {"estado": "ausente"}),
        "certidao_cndt_pendente": _finding("certidao_cndt_pendente", {"estado": "ausente"}),
        "certidao_fgts_pendente": _finding("certidao_fgts_pendente", {"estado": "ausente"}),
        "balanco_ausente": _finding("balanco_ausente", False),
        "volatilidade_cv": _finding("volatilidade_cv", None),
        "receita_serie_anual": _finding("receita_serie_anual", {"anos_informados": 0, "serie_qtd_chaves": 0, "anos_na_serie": []}),
    }
    result = avaliar(EntradaPolitica({"valor_enquadrado": 114401.4, "valor_solicitado": 120000, "pct_max_contrato": .2}, findings), _params(), [], date(2026, 1, 1))
    assert result.dimensoes["relacionamento_governamental"] == 71.6
    assert result.dimensoes["saude_cadastral"] == 69.3
    assert (result.merit, result.fator_regularidade, result.score, result.rating, result.rating_potencial) == (63.4, .82, 52.0, "D", "C")
    assert result.limite_aprovado_rs == 114401.4
    assert result.ajuste_pd["faixa_volatilidade"] == "HISTORICO_INSUFICIENTE"
    assert result.ajuste_pd["multiplicador_volatilidade"] == 1.08
    assert (result.ajuste_pd["pd_base"], result.ajuste_pd["pd_ajustada"]) == (.08, .0864)


def test_veto_forces_score_rating_limit_and_null_pd():
    result = avaliar(EntradaPolitica({}, {"cadastro_inativo": _finding("cadastro_inativo", True)}), _params(), [{"classe": "VETO", "codigo": "cadastro_inativo"}], date(2026, 1, 1))
    assert (result.score, result.rating, result.limite_aprovado_rs, result.ajuste_pd) == (20, "E", 0, None)
