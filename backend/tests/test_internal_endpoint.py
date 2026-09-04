import os


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.endpoints import internal  # noqa: E402


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(internal.router, prefix="/api/v1/internal")
    return TestClient(app)


def test_missing_internal_token_returns_401(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")

    response = make_client().post(
        "/api/v1/internal/ingestao/broadfactor?dry_run=true"
    )

    assert response.status_code == 401


def test_wrong_internal_token_returns_401(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")

    response = make_client().post(
        "/api/v1/internal/ingestao/broadfactor?dry_run=true",
        headers={"X-Internal-Token": "wrong-token"},
    )

    assert response.status_code == 401


def test_correct_internal_token_returns_dry_run_summary(monkeypatch):
    calls = []
    expected = {
        "status": "dry_run",
        "total": 4,
        "aprovadas": 3,
        "descartadas": 1,
        "descartadas_por_motivo": {"abaixo_ticket_minimo": 1},
        "criadas": 0,
        "duplicadas": 0,
        "falhas": 0,
    }

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal, "run_broadfactor_ingestao", fake_run)

    response = make_client().post(
        "/api/v1/internal/ingestao/broadfactor?dry_run=true&limit=7",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json() == expected
    assert calls == [{"dry_run": True, "limit": 7}]


def test_empty_configured_token_returns_503(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "")

    response = make_client().post(
        "/api/v1/internal/ingestao/broadfactor?dry_run=true",
        headers={"X-Internal-Token": "any-token"},
    )

    assert response.status_code == 503
    assert "INTERNAL_JOB_TOKEN" in response.json()["detail"]


def test_live_ingestion_returns_202_and_uses_background_task(monkeypatch):
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal, "run_broadfactor_ingestao", fake_run)

    response = make_client().post(
        "/api/v1/internal/ingestao/broadfactor?limit=3",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "dry_run": False,
        "limit": 3,
    }
    assert calls == [{"dry_run": False, "limit": 3}]
