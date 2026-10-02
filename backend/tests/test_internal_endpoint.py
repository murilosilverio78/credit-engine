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


def test_findings_reemit_requires_internal_token_and_validates_limit(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    assert make_client().post("/api/v1/internal/findings/reemitir").status_code == 401
    assert make_client().post("/api/v1/internal/findings/reemitir", headers={"X-Internal-Token": "wrong"}).status_code == 401
    for limit in (0, 101):
        response = make_client().post(
            "/api/v1/internal/findings/reemitir",
            json={"limite": limit}, headers={"X-Internal-Token": "configured-token"},
        )
        assert response.status_code == 422


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


def test_watchdog_endpoint_returns_summary_with_internal_token(monkeypatch):
    expected = {"status": "completed", "candidatas": 1, "marcadas": 1}

    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal, "run_operation_watchdog", lambda: expected)

    response = make_client().post(
        "/api/v1/internal/watchdog/operacoes",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json() == expected


def test_ingestion_is_rejected_during_shutdown(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal, "is_shutting_down", lambda: True)

    response = make_client().post(
        "/api/v1/internal/ingestao/broadfactor",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Serviço em encerramento; tente novamente em instantes"
    )


def test_ip_diagnostic_requires_internal_token(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")

    response = make_client().get("/api/v1/internal/diagnostico/ip")

    assert response.status_code == 401


def test_ip_diagnostic_returns_outbound_ip_and_portal_probe(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 15.0, "verify": True}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if url == internal.IPIFY_URL:
                return FakeResponse(200, {"ip": "203.0.113.10"})
            return FakeResponse(504)

    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal.settings, "PORTAL_TRANSPARENCIA_TOKEN", "portal-key")
    monkeypatch.setattr(internal.settings, "PORTAL_BASE_URL", "https://proxy.example/")
    monkeypatch.setattr(internal.settings, "PORTAL_PROXY_TOKEN", "proxy-secret")
    monkeypatch.setattr(internal.settings, "HTTPX_VERIFY_SSL", True)
    monkeypatch.setattr(internal.httpx, "Client", FakeClient)
    monkeypatch.setattr(internal, "acquire_portal_call", lambda: None)
    monkeypatch.setattr(
        internal,
        "portal_guardrail_snapshot",
        lambda: {
            "minuto": {"consumo": 12, "limite": 150},
            "ciclo": {"consumo": 345, "limite": 2000, "ativo": True},
            "dia": {
                "consumo": 678,
                "limite": 10000,
                "data": "2026-09-28",
                "erro": None,
            },
        },
    )

    response = make_client().get(
        "/api/v1/internal/diagnostico/ip",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ip_saida"] == "203.0.113.10"
    assert data["ipify"]["status_code"] == 200
    assert data["ipify"]["tempo_ms"] >= 0
    assert data["portal_transparencia"] == {
        "status_code": 504,
        "tempo_ms": data["portal_transparencia"]["tempo_ms"],
        "erro": None,
    }
    assert data["portal_transparencia"]["tempo_ms"] >= 0
    assert data["guardrails"]["minuto"] == {"consumo": 12, "limite": 150}
    assert data["guardrails"]["ciclo"]["consumo"] == 345
    assert data["guardrails"]["dia"]["consumo"] == 678
    assert calls == [
        (internal.IPIFY_URL, {}),
        (
            "https://proxy.example/api-de-dados/pessoa-juridica",
            {
                "headers": {
                    "chave-api-dados": "portal-key",
                    "X-Proxy-Token": "proxy-secret",
                },
                "params": {"cnpj": internal.PORTAL_DIAGNOSTIC_CNPJ},
            },
        ),
    ]
