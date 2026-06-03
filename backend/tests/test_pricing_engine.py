from unittest.mock import patch

import pytest

from app.services.pricing_engine import compute_taxa


PARAMS = {
    "cdi_am": 0.0114,
    "pct_subordinado": 0.20,
    "pct_mezzanino": 0.20,
    "spread_mez_aa": 0.10,
    "spread_senior_aa": 0.05,
    "margem_alvo_sub_am": 0.025,
    "taxa_adm_aa": 0.01,
    "bancarizacao": 0.035,
    "serpro_ate_300k": 0.0042,
    "serpro_300k_1m": 0.0034,
    "serpro_1m_10m": 0.0027,
    "serpro_acima_10m": 0.0022,
    "custo_orig_analise": 0.005,
    "custo_plataforma_perf": 0.005,
    "pd_performada": 0.016,
    "lgd_estimada": 0.70,
}

MATRIX = {
    "A": {
        "pd_mult": 0.60,
        "lgd_mult": 0.70,
        "bond_cobertura": 0.50,
        "bond_premio_aa": 0.010,
        "recusa": False,
    },
    "B": {
        "pd_mult": 1.00,
        "lgd_mult": 0.80,
        "bond_cobertura": 0.70,
        "bond_premio_aa": 0.020,
        "recusa": False,
    },
    "C": {
        "pd_mult": 2.00,
        "lgd_mult": 0.95,
        "bond_cobertura": 0.85,
        "bond_premio_aa": 0.035,
        "recusa": False,
    },
    "D": {
        "pd_mult": 5.00,
        "lgd_mult": 1.10,
        "bond_cobertura": 1.00,
        "bond_premio_aa": 0.050,
        "recusa": False,
    },
    "E": {
        "pd_mult": 10.00,
        "lgd_mult": 1.30,
        "bond_cobertura": 1.00,
        "bond_premio_aa": None,
        "recusa": True,
    },
}


@pytest.fixture(autouse=True)
def pricing_config():
    with patch(
        "app.services.pricing_engine._get_pricing_config",
        return_value=(PARAMS, MATRIX),
    ):
        yield


def test_compute_taxa_solves_monthly_cash_flow():
    result = compute_taxa("B", 1_000_000, 6)

    assert 0 < result["taxa_sugerida_am"] < 0.30
    assert len(result["fluxo_caixa"]) == 7
    assert abs(result["detalhes"]["residual_fluxo_rs"]) < 1
    assert result["detalhes"]["metodologia"] == "fluxo_caixa_bissecao"


def test_compute_taxa_increases_with_riskier_rating():
    rates = [compute_taxa(rating, 1_000_000, 6)["taxa_sugerida_am"] for rating in "ABCD"]

    assert rates == sorted(rates)


def test_compute_taxa_rejects_rating_e():
    with pytest.raises(ValueError, match="recusado"):
        compute_taxa("E", 1_000_000, 6)


def test_compute_taxa_matches_reference_spreadsheet_cases():
    cases = [
        ("B", 50_000, 4, 3.98),
        ("A", 50_000, 6, 3.27),
        ("D", 50_000, 12, 2.97),
        ("B", 2_000_000, 6, 3.29),
    ]

    for rating, valor, prazo, esperado_pct in cases:
        taxa = compute_taxa(rating, valor, prazo)["taxa_sugerida_am"] * 100
        assert (
            abs(taxa - esperado_pct) <= 0.02
        ), f"{rating}/{valor}/{prazo}: {taxa:.2f}% != {esperado_pct}%"
