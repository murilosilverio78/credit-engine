from datetime import date

import pytest

from app.services.policy.engine import _pd
from app.services.policy.types import EntradaPolitica, FindingValue


PARAMS = {
    "pd_min_anos_completos_volatilidade": 2, "pd_cv_corte_moderado": .7,
    "pd_cv_corte_alto": .8, "pd_mult_historico_insuficiente": 1.08,
    "pd_mult_volatilidade_moderada": 1.08, "pd_mult_volatilidade_alta": 1.15,
    "pd_performada": .016, "pd_mult_por_rating": {"D": 5},
}


@pytest.mark.parametrize("cv,informed,series,expected", [
    (.2, 2, [2024, 2025], "BAIXA"),
    (None, 2, [2024, 2025], "INDISPONIVEL"),
    (0, None, [2025], "HISTORICO_INSUFICIENTE"),
    (None, None, [2025], "INDISPONIVEL"),
    (None, 1, [], "HISTORICO_INSUFICIENTE"),
    (None, None, [], "INDISPONIVEL"),
])
def test_pd_series_production_patterns(cv, informed, series, expected):
    findings = {
        "volatilidade_cv": FindingValue("volatilidade_cv", cv, "CONFIRMADO", "ALTA", "run"),
        "receita_serie_anual": FindingValue("receita_serie_anual", {"anos_informados": informed, "serie_qtd_chaves": len(series), "anos_na_serie": series}, "CONFIRMADO", "ALTA", "run"),
    }
    result = _pd(EntradaPolitica({}, findings), PARAMS, "D", {}, date(2026, 1, 1))
    assert result["faixa_volatilidade"] == expected
    assert result["pd_base"] == .08


@pytest.mark.parametrize("years,expected", [(1, "HISTORICO_INSUFICIENTE"), (2, "INDISPONIVEL")])
def test_pd_new_snapshot_minimum_boundary(years, expected):
    findings = {
        "volatilidade_cv": FindingValue("volatilidade_cv", None, "CONFIRMADO", "ALTA"),
        "receita_serie_anual": FindingValue("receita_serie_anual", {"anos_informados": years, "serie_qtd_chaves": 1, "anos_na_serie": [2025]}, "CONFIRMADO", "ALTA"),
    }
    assert _pd(EntradaPolitica({}, findings), PARAMS, "D", {}, date(2026, 1, 1))["faixa_volatilidade"] == expected


def test_pd_ignores_current_reference_year_when_inferring_history():
    findings = {
        "volatilidade_cv": FindingValue("volatilidade_cv", 0, "CONFIRMADO", "ALTA"),
        "receita_serie_anual": FindingValue("receita_serie_anual", {"anos_informados": None, "serie_qtd_chaves": 2, "anos_na_serie": [2025, 2026]}, "CONFIRMADO", "ALTA"),
    }
    result = _pd(EntradaPolitica({}, findings), PARAMS, "D", {}, date(2026, 1, 1))
    assert result["anos_completos"] == 1
    assert result["faixa_volatilidade"] == "HISTORICO_INSUFICIENTE"
