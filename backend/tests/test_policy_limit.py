import pytest

from app.services.policy.engine import _limit
from app.services.policy.types import EntradaPolitica, FindingValue


@pytest.mark.parametrize("operation,total,expected,flags", [
    ({"pct_max_contrato": .2, "contrato_saldo": 1000}, 0, 200, []),
    ({"pct_max_contrato": .2}, 0, 0, ["limite_sem_base_contrato"]),
    ({"pct_max_contrato": .2, "margem_disponivel": 700, "contrato_saldo": 1}, 0, 200, []),
    ({"pct_max_contrato": .2, "saldo_vincendo": 1000}, 0, 200, []),
    ({"pct_max_contrato": .2}, 1000, 200, []),
    ({"contrato_saldo": 1000}, 0, 0, ["limite_sem_pct_max_contrato"]),
    ({"pct_max_contrato": .2, "contrato_saldo": 1000, "valor_solicitado": 150}, 0, 150, []),
])
def test_legacy_limit_sources(operation, total, expected, flags):
    findings = {"contratos_valor_total_ativo_rs": FindingValue("contratos_valor_total_ativo_rs", total, "CONFIRMADO", "ALTA")}
    assert _limit(EntradaPolitica(operation, findings), {"pct_margem_sobre_saldo": .7}, {}) == (expected, flags)
