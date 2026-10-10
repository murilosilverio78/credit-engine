from datetime import date

import pytest

from app.services.capacidade_contrato_service import calcular_capacidade_contrato


PARAMS = {
    "cap_fator_liquido_mao_obra": 0.645,
    "cap_fator_liquido_demais": 0.85,
    "cap_cobertura_parcela": 1.25,
    "cap_taxa_referencia_am": 0.035,
    "cap_folga_meses": 1,
}


def test_ps_ufpr_capacity_reference_case():
    result = calcular_capacidade_contrato(
        1_963_873.90, "2026-07-20", "2027-07-20", True, PARAMS, date(2026, 10, 9)
    )

    assert result["memoria"]["n"] == 9
    assert result["memoria"]["mensal_liquido"] == pytest.approx(105_640, rel=0.01)
    assert result["capacidade"] == pytest.approx(640_000, rel=0.01)


def test_facilita_capacity_reference_case_uses_next_contract_anniversary():
    result = calcular_capacidade_contrato(
        280_200, "2026-08-18", "2028-08-18", True, PARAMS, date(2026, 10, 9)
    )

    assert result["memoria"]["horizonte_firme"] == "2027-08-18"
    assert 48_000 <= result["capacidade"] <= 51_000
