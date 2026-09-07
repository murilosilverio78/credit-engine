import os


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers.tasks import score_engine  # noqa: E402


PARAMS = {
    "penalidade_cnd_federal_ausente": 6,
    "penalidade_cndt_ausente": 6,
    "penalidade_fgts_ausente": 6,
    "penalidade_balanco_ausente": 10,
}


def _dimension(name: str, score: float = 88):
    return score_engine._dimension(
        score,
        score_engine.PESOS_MERITO[name],
        [],
        "Dimensao fixa para teste.",
        fonte="test",
    )


def _valid_certificates():
    return {
        component: {"resultado": "negativa", "valida": True}
        for component in score_engine.CERTIDOES_REGULARIDADE
    }


def _consolidate(monkeypatch, snapshots):
    monkeypatch.setattr(
        "app.services.pricing_params_service.get_pricing_config",
        lambda: (PARAMS, {"A": {"pd_mult": 0.6}, "B": {"pd_mult": 1.0}}),
    )
    monkeypatch.setattr(score_engine, "gates_deterministicos", lambda _snapshots: [])
    monkeypatch.setattr(
        score_engine,
        "score_relacionamento",
        lambda _snapshots: _dimension("relacionamento_governamental"),
    )
    monkeypatch.setattr(
        score_engine,
        "score_saude_cadastral",
        lambda _snapshots: _dimension("saude_cadastral"),
    )
    monkeypatch.setattr(
        score_engine,
        "score_reputacao",
        lambda _snapshots: _dimension("reputacao_mercado"),
    )
    monkeypatch.setattr(
        score_engine,
        "_ajuste_pd_volatilidade",
        lambda _snapshots, _rating: ({}, []),
    )
    return score_engine.consolidar_score(
        "31822605000191",
        snapshots,
        porte_dimension=_dimension("porte_operacionalidade"),
    )


def test_missing_balance_removes_ten_points_from_final_score(monkeypatch):
    result = _consolidate(monkeypatch, _valid_certificates())

    assert result["score"] == 78
    assert result["rating"] == "B"
    assert result["rating_potencial"] == "A"
    assert result["penalizacao_balanco"] == 10
    assert result["penalizacao_total"] == 10
    assert result["pendencias_rating"] == [
        {"documento": "PENULTIMO_BALANCO", "penalizacao": 10}
    ]
    assert "balanco_ausente" in result["flags"]
    assert "balanco_ausente" in result["dimensoes"]["porte_operacionalidade"]["flags"]
    assert result["dimensoes"]["porte_operacionalidade"]["score_potencial"] == 88


def test_broadfactor_balance_document_avoids_penalty(monkeypatch):
    snapshots = {
        **_valid_certificates(),
        "contrato_extracao": {
            "documentos_broadfactor": [
                {
                    "tipo": "PENULTIMO_BALANCO",
                    "id": "doc-1",
                    "dono": "EMPRESA",
                }
            ]
        },
    }

    result = _consolidate(monkeypatch, snapshots)

    assert result["score"] == 88
    assert result["rating"] == result["rating_potencial"] == "A"
    assert result["penalizacao_balanco"] == 0
    assert result["penalizacao_total"] == 0
    assert result["pendencias_rating"] == []
    assert "balanco_ausente" not in result["flags"]


def test_uploaded_balance_document_avoids_penalty(monkeypatch):
    snapshots = {
        **_valid_certificates(),
        "documentos_operacao": {
            "documentos": [{"document_type": "balanco"}],
        },
    }

    result = _consolidate(monkeypatch, snapshots)

    assert result["score"] == 88
    assert result["penalizacao_balanco"] == 0


def test_balance_and_certificate_penalties_share_rating_pendencies(monkeypatch):
    result = _consolidate(monkeypatch, {})

    assert result["score_antes_penalizacao_balanco"] == 72.2
    assert result["score"] == 62.2
    assert result["rating"] == "C"
    assert result["rating_potencial"] == "A"
    assert result["penalizacao_total"] == 28
    assert len(result["pendencias_rating"]) == 4
    assert result["pendencias_rating"][-1] == {
        "documento": "PENULTIMO_BALANCO",
        "penalizacao": 10,
    }
