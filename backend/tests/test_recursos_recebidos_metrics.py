import os
from datetime import date

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.integrations.broadfactor.client import Recebimento  # noqa: E402
from app.workers.tasks import recursos_recebidos  # noqa: E402


TODAY = date(2026, 9, 4)


def receipt(value: float, organization: str, competency: str) -> Recebimento:
    return Recebimento(
        valor=value,
        orgao=organization,
        codigo_orgao=None,
        orgao_superior=None,
        unidade_gestora=None,
        competencia=competency,
    )


def portal_pagination() -> dict:
    return {
        "paginas_lidas": 1,
        "registros": 1,
        "atingiu_cap": False,
        "motivo_fim": "pagina_vazia",
    }


def test_competency_is_sorted_by_year_then_month():
    metrics = recursos_recebidos._derived_metrics(
        [
            receipt(100, "Orgao A", "01/2026"),
            receipt(200, "Orgao A", "12/2025"),
        ],
        TODAY,
    )

    assert recursos_recebidos._month_key("01/2026") > recursos_recebidos._month_key(
        "12/2025"
    )
    assert metrics["primeira_competencia"] == "12/2025"
    assert metrics["ultima_competencia"] == "01/2026"
    assert metrics["faturamento_verificado_12m"] == 300


def test_resource_detail_preserves_legacy_fields_and_month_type():
    item = receipt(125.5, "Orgao A", "11/2025")
    item.acao = "Acao A"

    detail = recursos_recebidos._receipt_detail(item)

    assert detail["mes"] == 202511
    assert isinstance(detail["mes"], int)
    assert detail["competencia"] == "11/2025"
    assert detail["valor"] == 125.5
    assert isinstance(detail["valor"], float)
    assert detail["orgao"] == "Orgao A"
    assert detail["acao"] == "Acao A"


def test_resource_details_sort_correctly_with_legacy_month_format():
    snapshot = recursos_recebidos._build_snapshot(
        [
            receipt(100, "Orgao A", "01/2026"),
            receipt(200, "Orgao A", "12/2025"),
        ],
        "BROADFACTOR",
        None,
        TODAY,
        {},
    )

    assert [item["mes"] for item in snapshot["recursos_detalhe"]] == [
        202512,
        202601,
    ]


def test_single_source_concentration_has_hhi_10000():
    metrics = recursos_recebidos._derived_metrics(
        [
            receipt(100, "Orgao Unico", "01/2025"),
            receipt(300, "Orgao Unico", "02/2025"),
        ],
        TODAY,
    )

    assert metrics["concentracao"] == {
        "hhi": 10000.0,
        "n_orgaos": 1,
        "top_orgao": "Orgao Unico",
        "top_participacao": 1.0,
        "faixa": "CONCENTRADO",
    }


def test_current_partial_year_is_excluded_from_volatility():
    metrics = recursos_recebidos._derived_metrics(
        [
            receipt(100, "Orgao A", "06/2024"),
            receipt(200, "Orgao A", "06/2025"),
            receipt(1, "Orgao A", "01/2026"),
        ],
        TODAY,
    )

    assert metrics["serie_anual"] == {
        "2024": 100.0,
        "2025": 200.0,
        "2026": 1.0,
    }
    assert metrics["volatilidade"] == {
        "cv": 0.3333,
        "maior_queda_anual_pct": 0.0,
    }


@pytest.mark.parametrize(
    ("portal_total", "expected_pct", "expected_status"),
    [
        (95, 5.0, "CONVERGENTE"),
        (89, 11.0, "DIVERGENTE"),
    ],
)
def test_reconciliation_uses_ten_percent_tolerance(
    portal_total,
    expected_pct,
    expected_status,
):
    result = recursos_recebidos._reconcile(
        [
            receipt(100, "Orgao A", "06/2025"),
            receipt(10_000, "Orgao A", "01/2026"),
        ],
        [
            receipt(portal_total, "Orgao A", "06/2025"),
            receipt(1, "Orgao A", "01/2026"),
        ],
        TODAY,
    )

    assert result["janela_inicio"] == "2025-01-01"
    assert result["janela_fim"] == "2025-12-31"
    assert result["divergencia_pct"] == expected_pct
    assert result["status"] == expected_status


def test_broadfactor_failure_falls_back_to_portal(monkeypatch):
    portal = [receipt(150, "Orgao Portal", "06/2025")]
    monkeypatch.setattr(recursos_recebidos, "_get_cotacao_id", lambda _: "C-1")
    monkeypatch.setattr(
        recursos_recebidos,
        "_fetch_broadfactor",
        lambda _: (_ for _ in ()).throw(ConnectionError("Broadfactor offline")),
    )
    monkeypatch.setattr(
        recursos_recebidos,
        "_fetch_portal",
        lambda *args, **kwargs: (portal, portal_pagination()),
    )

    result = recursos_recebidos._fetch("03012610000101", operation_id="op-1", today=TODAY)

    assert result["fonte_primaria"] == "PORTAL_TRANSPARENCIA"
    assert result["reconciliacao"]["status"] == "SEM_DADO_BROADFACTOR"
    assert result["valor_total_recebido"] == 150


def test_portal_failure_does_not_fail_broadfactor_snapshot(monkeypatch):
    broadfactor = [receipt(200, "Orgao Broadfactor", "06/2025")]
    monkeypatch.setattr(recursos_recebidos, "_get_cotacao_id", lambda _: "C-1")
    monkeypatch.setattr(recursos_recebidos, "_fetch_broadfactor", lambda _: broadfactor)
    monkeypatch.setattr(
        recursos_recebidos,
        "_fetch_portal",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("Portal offline")),
    )

    result = recursos_recebidos._fetch("03012610000101", operation_id="op-1", today=TODAY)

    assert result["fonte_primaria"] == "BROADFACTOR"
    assert result["reconciliacao"]["status"] == "SEM_DADO_PORTAL"
    assert result["valor_total_recebido"] == 200


def test_manual_operation_uses_only_portal(monkeypatch):
    portal = [receipt(300, "Orgao Portal", "06/2025")]
    monkeypatch.setattr(recursos_recebidos, "_get_cotacao_id", lambda _: None)
    monkeypatch.setattr(
        recursos_recebidos,
        "_fetch_broadfactor",
        lambda _: pytest.fail("manual operation queried Broadfactor"),
    )
    monkeypatch.setattr(
        recursos_recebidos,
        "_fetch_portal",
        lambda *args, **kwargs: (portal, portal_pagination()),
    )

    result = recursos_recebidos._fetch("03012610000101", operation_id="op-1", today=TODAY)

    assert result["fonte_primaria"] == "PORTAL_TRANSPARENCIA"
    assert result["reconciliacao"] is None


def test_broadfactor_fetch_requires_complete_pagination(monkeypatch):
    captured = {}

    class FakeClient:
        def recebimentos(self, cotacao_id, **kwargs):
            captured["cotacao_id"] = cotacao_id
            captured.update(kwargs)
            return []

    monkeypatch.setattr(recursos_recebidos, "BroadfactorClient", FakeClient)

    recursos_recebidos._fetch_broadfactor("C-885")

    assert captured == {
        "cotacao_id": "C-885",
        "paginas": recursos_recebidos.MAX_PAGES,
        "tamanho": recursos_recebidos.BROADFACTOR_PAGE_SIZE,
        "exigir_completo": True,
    }
