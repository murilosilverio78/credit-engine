import os


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.endpoints import internal  # noqa: E402
from tests.fakes.postgrest import Postgrest  # noqa: E402


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(internal.router, prefix="/api/v1/internal")
    return TestClient(app)


def _setup_recoleta(monkeypatch, operation, motivos=None):
    from app.core import database
    from app.services import audit_service, funil_qualificacao_service
    from app.workers.tasks import brasil_api, orchestrator

    db = Postgrest({
        "operations": [operation],
        "component_snapshots": [{
            "operation_id": operation["id"], "component": "brasil_api", "status": "completed",
            "parsed_result": {"fonte": "CNPJA_OPEN"},
        }],
        "audit_trail": [],
    })
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(audit_service, "supabase", db)
    monkeypatch.setattr(brasil_api, "run_brasil_api", lambda *_args, **_kwargs: {"status": "completed"})
    monkeypatch.setattr(orchestrator, "refresh_degraded_registry_flag", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(funil_qualificacao_service, "atualizar_estagio_pos_fase2", lambda *_args: ("QUALIFICADA", motivos or []))
    scheduled = []
    async def no_analysis(*args, **kwargs):
        scheduled.append((args, kwargs))
        return {"status": "pending"}
    monkeypatch.setattr(orchestrator, "start_analysis", no_analysis)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    db.scheduled_analyses = scheduled
    return db


def test_recoleta_funil_pending_becomes_qualified(monkeypatch):
    operation = {"id": "00000000-0000-0000-0000-000000000001", "status": "aguardando_relatorio", "cotacao_id": "C-1", "source": "broadfactor_ingestao", "analysis_attempts": 1}
    db = _setup_recoleta(monkeypatch, operation)
    response = make_client().post(f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro", headers={"X-Internal-Token": "configured-token"})
    assert response.status_code == 200
    assert db.tables["operations"][0]["status"] == "aguardando_relatorio"
    assert response.json()["fonte"] == "CNPJA_OPEN"


def test_recoleta_funil_with_rejection_becomes_terminal(monkeypatch):
    operation = {"id": "00000000-0000-0000-0000-000000000002", "status": "aguardando_relatorio", "cotacao_id": "C-2", "source": "broadfactor_ingestao", "analysis_attempts": 1}
    db = _setup_recoleta(monkeypatch, operation, ["cobertura_insuficiente:1.00"])
    response = make_client().post(f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro", headers={"X-Internal-Token": "configured-token"})
    assert response.status_code == 200
    assert db.tables["operations"][0]["status"] == "reprovada_triagem"
    assert db.tables["audit_trail"][0]["payload"]["contexto"] == "recoleta_cadastro"


def test_recoleta_admin_failed_only_brasil_restarts_as_pending(monkeypatch):
    operation = {"id": "00000000-0000-0000-0000-000000000003", "status": "failed", "cotacao_id": None, "source": "admin_ui", "analysis_attempts": 1}
    db = _setup_recoleta(monkeypatch, operation)
    db.tables["component_snapshots"][0]["status"] = "failed"
    db.tables["component_snapshots"].append({
        "operation_id": operation["id"], "component": "score_engine", "status": "failed",
    })
    response = make_client().post(f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro", headers={"X-Internal-Token": "configured-token"})
    assert response.status_code == 200
    assert response.json()["analysis_restarted"] is True
    assert db.tables["operations"][0]["status"] == "pending"
    assert db.tables["operations"][0]["analysis_attempts"] == 2
    assert db.scheduled_analyses == [((operation["id"],), {"recovery": True})]


def test_recoleta_rejects_running_analysis(monkeypatch):
    operation = {"id": "00000000-0000-0000-0000-000000000004", "status": "processing", "cotacao_id": None, "source": "admin_ui", "analysis_attempts": 1}
    _setup_recoleta(monkeypatch, operation)
    response = make_client().post(f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro", headers={"X-Internal-Token": "configured-token"})
    assert response.status_code == 409


def _setup_component_execution(monkeypatch, operation=None):
    from app.core import database
    from app.workers.tasks import contratos_pncp

    operation = operation or {
        "id": "00000000-0000-0000-0000-000000000005",
        "status": "aguardando_relatorio",
        "cotacao_id": "C-5",
    }
    parsed = {
        "n_contratos": 11,
        "n_vigentes": 4,
        "valor_anualizado_vigente": 300000,
        "n_orgaos": 3,
        "contrato_cedido_match": "EXATO",
        "contrato_cedido": {"unidade_codigo": "200123"},
        "contratos_detalhe": [{"numero_contrato_empenho": "00006"}],
    }
    db = Postgrest({
        "operations": [operation],
        "component_snapshots": [{
            "operation_id": operation["id"],
            "component": "contratos_pncp",
            "status": "completed",
            "parsed_result": parsed,
        }],
        "audit_trail": [],
    })
    calls = []
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        contratos_pncp,
        "run_contratos_pncp",
        lambda operation_id, *, use_cache: calls.append((operation_id, use_cache)),
    )
    return db, calls, operation


def test_executar_componente_uses_cache_flag_and_returns_pncp_summary(monkeypatch):
    _db, calls, operation = _setup_component_execution(monkeypatch)

    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/contratos_pncp/executar?use_cache=false",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert calls == [(operation["id"], False)]
    assert response.json()["parsed_result"] == {
        "n_contratos": 11,
        "n_vigentes": 4,
        "valor_anualizado_vigente": 300000,
        "n_orgaos": 3,
        "contrato_cedido_match": "EXATO",
        "contrato_cedido": {"unidade_codigo": "200123"},
    }


def test_executar_componente_cria_snapshot_ausente(monkeypatch):
    db, calls, operation = _setup_component_execution(monkeypatch)
    db.tables["component_snapshots"] = []

    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/contratos_pncp/executar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert calls == [(operation["id"], True)]
    assert db.tables["component_snapshots"] == [{
        "operation_id": operation["id"],
        "component": "contratos_pncp",
        "status": "pending",
    }]


def test_executar_componente_reavalia_funil_aberto(monkeypatch):
    from app.services import funil_qualificacao_service

    db, _calls, operation = _setup_component_execution(monkeypatch)
    monkeypatch.setattr(
        funil_qualificacao_service,
        "atualizar_estagio_pos_fase2",
        lambda *_args: ("QUALIFICADA", []),
    )

    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/contratos_pncp/executar?reavaliar_funil=true",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["reavaliacao_funil"]["status"] == "aguardando_relatorio"
    assert db.tables["operations"][0]["pendencia_coleta"] is False


def test_executar_componente_rejeita_indisponivel_e_analise_em_andamento(monkeypatch):
    _db, _calls, operation = _setup_component_execution(monkeypatch)
    headers = {"X-Internal-Token": "configured-token"}
    assert make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/score_engine/executar",
        headers=headers,
    ).status_code == 422

    operation["status"] = "processing"
    assert make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/contratos_pncp/executar",
        headers=headers,
    ).status_code == 409


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


def test_policy_shadow_requires_token_and_validates_body(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    assert make_client().post("/api/v1/internal/politica/sombra").status_code == 401
    for body in ({"limite": 0}, {"limite": 101}, {"ambiente": "INVALIDO"}):
        assert make_client().post("/api/v1/internal/politica/sombra", json=body, headers={"X-Internal-Token": "configured-token"}).status_code == 422


def test_policy_shadow_rejects_invalid_token_and_empty_server_token(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    response = make_client().post("/api/v1/internal/politica/sombra", json={}, headers={"X-Internal-Token": "wrong-token"})
    assert response.status_code == 401

    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "")
    response = make_client().post("/api/v1/internal/politica/sombra", json={}, headers={"X-Internal-Token": "configured-token"})
    assert response.status_code == 503


def test_policy_shadow_dry_run_does_not_persist_and_apply_is_idempotent(monkeypatch):
    persisted: set[str] = set()
    calls: list[bool] = []

    def fake_shadow(operation_id, *, aplicar, **_kwargs):
        calls.append(aplicar)
        if aplicar:
            persisted.add(operation_id)
        return {"classe_geral": "IGUAL", "divergencias": []}

    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    from app.core import database
    monkeypatch.setattr(database, "supabase", Postgrest({"operations": [{"id": "op-1", "ambiente": "PRODUCAO"}]}))
    monkeypatch.setattr(internal, "load_policy", lambda **_kwargs: {"version": {"id": "shadow"}})
    monkeypatch.setattr(internal, "avaliar_sombra", fake_shadow)

    client = make_client()
    dry = client.post("/api/v1/internal/politica/sombra", json={"aplicar": False}, headers={"X-Internal-Token": "configured-token"})
    assert dry.status_code == 200 and persisted == set() and calls == [False]

    first = client.post("/api/v1/internal/politica/sombra", json={"aplicar": True}, headers={"X-Internal-Token": "configured-token"})
    second = client.post("/api/v1/internal/politica/sombra", json={"aplicar": True}, headers={"X-Internal-Token": "configured-token"})
    assert first.status_code == second.status_code == 200
    assert persisted == {"op-1"}
    assert "configured-token" not in first.text and "configured-token" not in second.text


def test_policy_shadow_reports_remaining_after_budget(monkeypatch):
    clock = iter((0.0, 0.0, 46.0))
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal, "load_policy", lambda **_kwargs: {"version": {"id": "shadow"}})
    monkeypatch.setattr(internal, "select_operations", lambda **_kwargs: ["op-1", "op-2"])
    monkeypatch.setattr(internal, "avaliar_sombra", lambda *_args, **_kwargs: {"classe_geral": "IGUAL", "divergencias": []})
    monkeypatch.setattr(internal, "_monotonic", lambda: next(clock))

    response = make_client().post("/api/v1/internal/politica/sombra", json={}, headers={"X-Internal-Token": "configured-token"})

    assert response.status_code == 200
    assert response.json()["processadas"] == 1
    assert response.json()["restantes"] == ["op-2"]


def test_policy_shadow_returns_409_without_shadow_version(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal, "load_policy", lambda **_kwargs: (_ for _ in ()).throw(LookupError()))
    response = make_client().post("/api/v1/internal/politica/sombra", json={}, headers={"X-Internal-Token": "configured-token"})
    assert response.status_code == 409


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
