import os

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers.tasks import score_engine  # noqa: E402


def contracts(org_count: int = 5) -> dict:
    return {
        "contratos_ativos": 2,
        "total_contratos": 4,
        "orgaos_contratantes": [f"Orgao {index}" for index in range(org_count)],
        "contratos_detalhe": [],
    }


def pricing_config() -> tuple[dict, dict]:
    return (
        {
            "pd_performada": 0.016,
            "pd_cv_corte_moderado": 0.70,
            "pd_cv_corte_alto": 0.80,
            "pd_mult_volatilidade_moderada": 1.08,
            "pd_mult_volatilidade_alta": 1.15,
        },
        {
            "A": {"pd_mult": 0.6},
            "B": {"pd_mult": 1.0},
            "C": {"pd_mult": 2.0},
            "D": {"pd_mult": 5.0},
        },
    )


def porte_dimension() -> dict:
    return score_engine._dimension(
        70,
        score_engine.PESOS_MERITO["porte_operacionalidade"],
        [],
        "Porte adequado para teste.",
        fonte="test",
    )


@pytest.mark.parametrize(
    ("hhi", "expected_concentration_score"),
    [
        (2499, 90),
        (2500, 70),
        (6000, 70),
        (6001, 45),
    ],
)
def test_relationship_uses_hhi_boundaries(hhi, expected_concentration_score):
    result = score_engine.score_relacionamento(
        {
            "contratos": contracts(),
            "recursos_recebidos": {"concentracao": {"hhi": hhi}},
        }
    )
    expected = round(
        0.30 * 68
        + 0.30 * expected_concentration_score
        + 0.25 * 68
        + 0.15 * 55,
        1,
    )

    assert result["score"] == expected
    assert "diversificacao_hhi_utilizada" in result["flags"]


def test_relationship_falls_back_to_organization_count_without_hhi():
    result = score_engine.score_relacionamento({"contratos": contracts(org_count=5)})

    assert result["score"] == 72.7
    assert "diversificacao_fallback_contagem_orgaos" in result["flags"]


def test_verified_revenue_wins_and_flags_large_declared_divergence():
    context = score_engine._faturamento_context(
        {
            "brasil_api": {"faturamento_anual": 1_000_000},
            "recursos_recebidos": {"faturamento_verificado_12m": 2_000_000},
        }
    )

    assert context["valor"] == 2_000_000
    assert context["fonte"] == "VERIFICADO"
    assert context["divergencia_pct"] == 100.0
    assert "faturamento_verificado_utilizado" in context["flags"]
    assert "faturamento_declarado_divergente" in context["flags"]


def test_declared_revenue_is_audited_fallback_for_legacy_snapshot():
    context = score_engine._faturamento_context(
        {"pessoa_juridica": {"faturamentoAnual": 800_000}}
    )

    assert context["valor"] == 800_000
    assert context["fonte"] == "DECLARADO"
    assert context["flags"] == ["faturamento_declarado_utilizado_fallback"]


@pytest.mark.parametrize(
    ("cv", "expected_multiplier", "expected_band"),
    [
        (0.70, 1.0, "BAIXA"),
        (0.71, 1.08, "MODERADA"),
        (0.80, 1.08, "MODERADA"),
        (0.81, 1.15, "ALTA"),
    ],
)
def test_pd_adjustment_uses_database_multipliers(
    monkeypatch,
    cv,
    expected_multiplier,
    expected_band,
):
    monkeypatch.setattr(
        "app.services.pricing_params_service.get_pricing_config",
        pricing_config,
    )

    adjustment, flags = score_engine._ajuste_pd_volatilidade(
        {"recursos_recebidos": {"volatilidade": {"cv": cv}}},
        "B",
    )

    assert adjustment["multiplicador_volatilidade"] == expected_multiplier
    assert adjustment["faixa_volatilidade"] == expected_band
    assert adjustment["pd_base"] == 0.016
    assert adjustment["pd_ajustada"] == pytest.approx(0.016 * expected_multiplier)
    assert flags


def test_missing_pd_parameter_degrades_to_no_adjustment(monkeypatch):
    monkeypatch.setattr(
        "app.services.pricing_params_service.get_pricing_config",
        lambda: ({"pd_performada": 0.016}, {"B": {"pd_mult": 1.0}}),
    )

    adjustment, flags = score_engine._ajuste_pd_volatilidade(
        {"recursos_recebidos": {"volatilidade": {"cv": 0.8}}},
        "B",
    )

    assert adjustment["multiplicador_volatilidade"] == 1.0
    assert "pd_volatilidade_parametro_indisponivel_sem_ajuste" in flags


@pytest.mark.parametrize(
    ("valor", "revenue", "expected", "expected_band"),
    [
        (500_000, 1_000_000, 0.5, "CONFORTAVEL"),
        (750_000, 1_000_000, 0.75, "MODERADA"),
        (1_500_000, 1_000_000, 1.5, "ACIMA_RECEITA_ANUAL"),
    ],
)
def test_coverage_bands(valor, revenue, expected, expected_band):
    coverage, band, flags = score_engine._cobertura_exposicao(
        {"valor_enquadrado": valor},
        {"faturamento_verificado_12m": revenue},
    )

    assert coverage == expected
    assert band == expected_band
    assert flags == ["cobertura_exposicao_calculada"]


def test_short_receipt_history_only_adds_disbursement_flag():
    flags = score_engine._flags_historico_recebimentos(
        {"recursos_recebidos": {"meses_com_recebimento": 11}}
    )

    assert flags == ["desembolso_pos_ateste_obrigatorio"]


def test_consolidated_result_exposes_credit_signals_without_changing_rating(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.pricing_params_service.get_pricing_config",
        pricing_config,
    )
    base_snapshots = {
        "brasil_api": {
            "situacao_cadastral": "ATIVA",
            "data_abertura": "2010-01-01",
            "capital_social": 1_000_000,
            "porte": "EPP",
        },
        "pessoa_juridica": {"possui_sancao": False},
        "contratos": contracts(),
        "recursos_recebidos": {
            "faturamento_verificado_12m": 2_000_000,
            "meses_com_recebimento": 11,
            "concentracao": {"hhi": 4322},
            "volatilidade": {"cv": 0.76},
        },
    }
    operation = {
        "valor_solicitado": 500_000,
        "valor_enquadrado": 500_000,
        "pct_max_contrato": 0.5,
    }

    short_history = score_engine.consolidar_score(
        "03012610000101",
        base_snapshots,
        operation,
        porte_dimension=porte_dimension(),
    )
    base_snapshots["recursos_recebidos"]["meses_com_recebimento"] = 12
    mature_history = score_engine.consolidar_score(
        "03012610000101",
        base_snapshots,
        operation,
        porte_dimension=porte_dimension(),
    )

    assert short_history["faturamento"]["fonte"] == "VERIFICADO"
    assert short_history["cobertura"] == 0.25
    assert short_history["cobertura_faixa"] == "CONFORTAVEL"
    assert short_history["ajuste_pd"]["multiplicador_volatilidade"] == 1.08
    assert "desembolso_pos_ateste_obrigatorio" in short_history["flags"]
    assert "diversificacao_hhi_utilizada" in short_history["flags"]
    assert short_history["score"] == mature_history["score"]
    assert short_history["rating"] == mature_history["rating"]
