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


def test_capture_documents_endpoint_without_body_uses_documented_quotes(monkeypatch):
    from app.core import database
    from app.integrations.broadfactor.client import DocumentDownloadError
    from app.services import documentos_captura_service

    db = Postgrest(
        {
            "cotacoes_broadfactor": [
                {
                    "cotacao_id": "C-1",
                    "cnpj": "03012610000101",
                    "operation_id": None,
                    "estagio": "DOCUMENTADA",
                },
                {
                    "cotacao_id": "C-2",
                    "cnpj": "03012610000101",
                    "operation_id": None,
                    "estagio": "QUALIFICADA",
                },
                {
                    "cotacao_id": "C-3",
                    "cnpj": "03012610000101",
                    "operation_id": None,
                    "estagio": "ENQUADRADA",
                },
            ],
        }
    )
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    calls = []

    def fake_capture(cotacao_id, cnpj, operation_id=None):
        calls.append((cotacao_id, cnpj, operation_id))
        if cotacao_id == "C-2":
            raise DocumentDownloadError("falha_download_documento", status=403)
        return {
            "capturados": 1,
            "reaproveitados": 0,
            "falhas": 0,
            "indisponiveis": 0,
            "ignorados": 0,
        }

    monkeypatch.setattr(
        documentos_captura_service, "capturar_documentos_cotacao", fake_capture
    )

    response = make_client().post(
        "/api/v1/internal/documentos/capturar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert [call[0] for call in calls] == ["C-1", "C-2"]
    assert response.json()["totais"] == {
        "capturados": 1,
        "reaproveitados": 0,
        "falhas": 1,
        "indisponiveis": 0,
        "ignorados": 0,
    }
    assert response.json()["cotacoes"][1]["erro"] == (
        "DocumentDownloadError:falha_download_documento:403"
    )


def test_capture_documents_endpoint_filters_explicit_quote_ids(monkeypatch):
    from app.core import database
    from app.services import documentos_captura_service

    db = Postgrest(
        {
            "cotacoes_broadfactor": [
                {
                    "cotacao_id": "C-1",
                    "cnpj": "03012610000101",
                    "operation_id": None,
                    "estagio": "DOCUMENTADA",
                },
                {
                    "cotacao_id": "C-2",
                    "cnpj": "03012610000101",
                    "operation_id": None,
                    "estagio": "ENQUADRADA",
                },
                {
                    "cotacao_id": "C-3",
                    "cnpj": "03012610000101",
                    "operation_id": None,
                    "estagio": "ENCERRADA",
                },
            ],
        }
    )
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        documentos_captura_service,
        "capturar_documentos_cotacao",
        lambda *_args: {
            "capturados": 0,
            "reaproveitados": 1,
            "falhas": 0,
            "indisponiveis": 0,
            "ignorados": 0,
        },
    )

    response = make_client().post(
        "/api/v1/internal/documentos/capturar",
        headers={"X-Internal-Token": "configured-token"},
        json={"cotacao_ids": ["C-2", "C-3"]},
    )

    assert response.status_code == 200
    assert [item["cotacao_id"] for item in response.json()["cotacoes"]] == ["C-2"]


def _setup_recoleta(monkeypatch, operation, motivos=None):
    from app.core import database
    from app.services import audit_service, funil_qualificacao_service
    from app.workers.tasks import brasil_api, orchestrator

    db = Postgrest(
        {
            "operations": [operation],
            "component_snapshots": [
                {
                    "operation_id": operation["id"],
                    "component": "brasil_api",
                    "status": "completed",
                    "parsed_result": {"fonte": "CNPJA_OPEN"},
                }
            ],
            "audit_trail": [],
        }
    )
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(audit_service, "supabase", db)
    monkeypatch.setattr(
        brasil_api, "run_brasil_api", lambda *_args, **_kwargs: {"status": "completed"}
    )
    monkeypatch.setattr(
        orchestrator, "refresh_degraded_registry_flag", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        funil_qualificacao_service,
        "atualizar_estagio_pos_fase2",
        lambda *_args: ("QUALIFICADA", motivos or []),
    )
    scheduled = []

    async def no_analysis(*args, **kwargs):
        scheduled.append((args, kwargs))
        return {"status": "pending"}

    monkeypatch.setattr(orchestrator, "start_analysis", no_analysis)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    db.scheduled_analyses = scheduled
    return db


def test_recoleta_funil_pending_becomes_qualified(monkeypatch):
    operation = {
        "id": "00000000-0000-0000-0000-000000000001",
        "status": "aguardando_relatorio",
        "cotacao_id": "C-1",
        "source": "broadfactor_ingestao",
        "analysis_attempts": 1,
    }
    db = _setup_recoleta(monkeypatch, operation)
    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro",
        headers={"X-Internal-Token": "configured-token"},
    )
    assert response.status_code == 200
    assert db.tables["operations"][0]["status"] == "aguardando_relatorio"
    assert response.json()["fonte"] == "CNPJA_OPEN"


def test_recoleta_funil_with_rejection_becomes_terminal(monkeypatch):
    operation = {
        "id": "00000000-0000-0000-0000-000000000002",
        "status": "aguardando_relatorio",
        "cotacao_id": "C-2",
        "source": "broadfactor_ingestao",
        "analysis_attempts": 1,
    }
    db = _setup_recoleta(monkeypatch, operation, ["cobertura_insuficiente:1.00"])
    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro",
        headers={"X-Internal-Token": "configured-token"},
    )
    assert response.status_code == 200
    assert db.tables["operations"][0]["status"] == "reprovada_triagem"
    assert db.tables["audit_trail"][0]["payload"]["contexto"] == "recoleta_cadastro"


def test_recoleta_admin_failed_only_brasil_restarts_as_pending(monkeypatch):
    operation = {
        "id": "00000000-0000-0000-0000-000000000003",
        "status": "failed",
        "cotacao_id": None,
        "source": "admin_ui",
        "analysis_attempts": 1,
    }
    db = _setup_recoleta(monkeypatch, operation)
    db.tables["component_snapshots"][0]["status"] = "failed"
    db.tables["component_snapshots"].append(
        {
            "operation_id": operation["id"],
            "component": "score_engine",
            "status": "failed",
        }
    )
    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro",
        headers={"X-Internal-Token": "configured-token"},
    )
    assert response.status_code == 200
    assert response.json()["analysis_restarted"] is True
    assert db.tables["operations"][0]["status"] == "pending"
    assert db.tables["operations"][0]["analysis_attempts"] == 2
    assert db.scheduled_analyses == [((operation["id"],), {"recovery": True})]


def test_recoleta_rejects_running_analysis(monkeypatch):
    operation = {
        "id": "00000000-0000-0000-0000-000000000004",
        "status": "processing",
        "cotacao_id": None,
        "source": "admin_ui",
        "analysis_attempts": 1,
    }
    _setup_recoleta(monkeypatch, operation)
    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/recoletar-cadastro",
        headers={"X-Internal-Token": "configured-token"},
    )
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
        "n_a_iniciar": 2,
        "valor_a_iniciar": 100000,
        "faturamento_contratado_12m": 350000,
        "valor_anualizado_vigente": 300000,
        "valor_anualizado_vigente_deprecated": True,
        "n_orgaos": 3,
        "contrato_cedido_match": "EXATO",
        "contrato_cedido": {"unidade_codigo": "200123"},
        "contratos_detalhe": [{"numero_contrato_empenho": "00006"}],
    }
    db = Postgrest(
        {
            "operations": [operation],
            "component_snapshots": [
                {
                    "operation_id": operation["id"],
                    "component": "contratos_pncp",
                    "status": "completed",
                    "parsed_result": parsed,
                }
            ],
            "audit_trail": [],
        }
    )
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
        "n_a_iniciar": 2,
        "valor_a_iniciar": 100000,
        "faturamento_contratado_12m": 350000,
        "valor_anualizado_vigente": 300000,
        "valor_anualizado_vigente_deprecated": True,
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
    assert db.tables["component_snapshots"] == [
        {
            "operation_id": operation["id"],
            "component": "contratos_pncp",
            "status": "pending",
        }
    ]


def test_executar_componente_permite_web_research_sem_cache(monkeypatch):
    from app.workers.tasks import web_research

    db, _calls, operation = _setup_component_execution(monkeypatch)
    calls = []
    monkeypatch.setattr(
        web_research,
        "run_web_research",
        lambda operation_id, *, use_cache: calls.append((operation_id, use_cache)),
    )

    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/web_research/executar?use_cache=false",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert calls == [(operation["id"], False)]


def test_executar_pagamentos_portal_is_accepted_in_background(monkeypatch):
    from app.workers.tasks import pagamentos_portal

    _db, _calls, operation = _setup_component_execution(monkeypatch)
    calls = []
    monkeypatch.setattr(
        pagamentos_portal,
        "run_pagamentos_portal",
        lambda operation_id, *, use_cache: calls.append((operation_id, use_cache)),
    )

    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/pagamentos_portal/executar?use_cache=false",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "operation_id": operation["id"],
        "component": "pagamentos_portal",
        "status": "accepted",
    }
    assert calls == [(operation["id"], False)]


def test_executar_componente_retorna_failed_de_fonte_sem_erro_500(monkeypatch):
    db, _calls, operation = _setup_component_execution(monkeypatch)
    from app.workers.tasks import contratos_pncp

    def source_failure(*_args, **_kwargs):
        db.tables["component_snapshots"][0].update(
            {
                "status": "failed",
                "error_message": "PNCP indisponível",
            }
        )
        raise RuntimeError("PNCP indisponível")

    monkeypatch.setattr(contratos_pncp, "run_contratos_pncp", source_failure)
    response = make_client().post(
        f"/api/v1/internal/operations/{operation['id']}/componentes/contratos_pncp/executar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["erro"] == "PNCP indisponível"


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
    assert (
        make_client()
        .post(
            f"/api/v1/internal/operations/{operation['id']}/componentes/score_engine/executar",
            headers=headers,
        )
        .status_code
        == 422
    )

    operation["status"] = "processing"
    assert (
        make_client()
        .post(
            f"/api/v1/internal/operations/{operation['id']}/componentes/contratos_pncp/executar",
            headers=headers,
        )
        .status_code
        == 409
    )


def test_reavaliar_funil_runs_pncp_then_comprasnet_and_promotes_coverage_rejection(
    monkeypatch,
):
    from app.core import database
    from app.services import audit_service, funil_qualificacao_service
    from app.workers.tasks import contratos_comprasnet, contratos_pncp

    operation = {
        "id": "00000000-0000-0000-0000-000000000006",
        "status": "reprovada_triagem",
        "cotacao_id": "C-6",
        "ambiente": "PRODUCAO",
        "valor_enquadrado": 300_000,
        "capacidade_contrato": 350_000,
        "flags_funil": ["salto_escala:2.0"],
    }
    db = Postgrest({"operations": [operation], "audit_trail": []})
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(audit_service, "supabase", db)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        internal, "_operacoes_para_reavaliar_funil", lambda _ids: [operation]
    )
    calls = []
    monkeypatch.setattr(
        contratos_pncp,
        "run_contratos_pncp",
        lambda op, **_kwargs: calls.append(("pncp", op)),
    )
    monkeypatch.setattr(
        contratos_comprasnet,
        "run_contratos_comprasnet",
        lambda op, **_kwargs: calls.append(("comprasnet", op)),
    )
    monkeypatch.setattr(
        funil_qualificacao_service,
        "atualizar_estagio_pos_fase2",
        lambda *_args: ("QUALIFICADA", []),
    )

    response = make_client().post(
        "/api/v1/internal/funil/reavaliar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert calls == [("pncp", operation["id"]), ("comprasnet", operation["id"])]
    assert response.json()["operacoes"][0]["status_novo"] == "aguardando_relatorio"
    assert db.tables["operations"][0]["status"] == "aguardando_relatorio"


def test_reavaliar_funil_keeps_processing_operation_out_of_the_batch(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        internal,
        "_operacoes_para_reavaliar_funil",
        lambda _ids: [{"id": "op", "status": "running"}],
    )

    response = make_client().post(
        "/api/v1/internal/funil/reavaliar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 409


def test_reavaliar_funil_source_failure_is_reported_without_stopping_item(monkeypatch):
    from app.core import database
    from app.services import funil_qualificacao_service
    from app.workers.tasks import contratos_comprasnet, contratos_pncp

    operation = {
        "id": "00000000-0000-0000-0000-000000000007",
        "status": "reprovada_triagem",
        "cotacao_id": "C-7",
        "ambiente": "PRODUCAO",
    }
    db = Postgrest({"operations": [operation]})
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        internal, "_operacoes_para_reavaliar_funil", lambda _ids: [operation]
    )
    monkeypatch.setattr(
        contratos_pncp,
        "run_contratos_pncp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("PNCP fora")),
    )
    monkeypatch.setattr(
        contratos_comprasnet, "run_contratos_comprasnet", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        funil_qualificacao_service,
        "atualizar_estagio_pos_fase2",
        lambda *_args: ("DOCUMENTADA", ["indisponibilidade_fonte:contrato_cedido"]),
    )

    response = make_client().post(
        "/api/v1/internal/funil/reavaliar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert "PNCP fora" in response.json()["operacoes"][0]["erro"]


CAPACIDADE_PARAMS = {
    "cap_fator_liquido_mao_obra": 0.645,
    "cap_fator_liquido_demais": 0.85,
    "cap_cobertura_parcela": 1.25,
    "cap_taxa_referencia_am": 0.035,
    "cap_folga_meses": 1,
    "cap_sob_demanda_janela_meses": 6,
    "cap_sob_demanda_min_faturas": 2,
}

AUDIT_ACTIONS = {
    "operation_created",
    "component_started",
    "component_completed",
    "component_failed",
    "upload_requested",
    "upload_received",
    "decision_generated",
    "override_applied",
    "operation_expired",
    "operation_error",
    "pricing_parameter_updated",
    "pricing_matrix_updated",
    "alcada_config_updated",
    "score_reprocessed",
    "operation_status_changed",
}


def _operation_for_capacity(operation_id, **overrides):
    return {
        "id": operation_id,
        "ambiente": "PRODUCAO",
        "status": "completed",
        "valor_enquadrado": 250_000,
        "valor_enquadrado_pre_capacidade": 250_000,
        "capacidade_contrato": 180_000,
        "capacidade_memoria": {
            "fonte_mensal": "NOMINAL",
            "mensal_bruto": 20_000,
        },
        "flags_funil": ["manual:preservar", "salto_escala:2.0"],
        "valor_global_contrato": 360_000,
        "contrato_vigencia_inicio": "2026-01-01",
        "contrato_vigencia_fim": "2027-12-31",
        "contrato_dedicacao_exclusiva": False,
    } | overrides


def _snapshots_for_capacity(operation_id, modalidade="CONTINUO", faturas=None):
    return [
        {
            "operation_id": operation_id,
            "component": "contratos_pncp",
            "status": "completed",
            "parsed_result": {
                "contrato_cedido_match": "ENCONTRADO",
                "contrato_cedido": {
                    "valor_global": 360_000,
                    "data_inicio_vigencia": "2026-01-01",
                    "data_fim_vigencia": "2027-12-31",
                    "dedicacao_exclusiva": False,
                },
            },
        },
        {
            "operation_id": operation_id,
            "component": "contratos_comprasnet",
            "status": "completed",
            "parsed_result": {
                "status_consulta": "ENCONTRADO",
                "status_atualizacao": "ATUALIZADO",
                "contrato_comprasnet": {"modalidade_execucao": modalidade},
                "faturas": faturas or [],
            },
        },
    ]


def _cotacao_for_capacity(cotacao_id, **overrides):
    return {
        "cotacao_id": cotacao_id,
        "valor_enquadrado": 250_000,
        "estagio": "QUALIFICADA",
        "estagio_max": "QUALIFICADA",
        "estagio_motivo": None,
    } | overrides


def _setup_recalculo_capacidade(monkeypatch, operations, snapshots, cotacoes=None):
    from app.core import database
    from app.services import audit_service, funil_qualificacao_service

    db = Postgrest(
        {
            "operations": operations,
            "component_snapshots": snapshots,
            "cotacoes_broadfactor": cotacoes or [],
            "audit_trail": [],
        }
    )
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(funil_qualificacao_service, "supabase", db)
    monkeypatch.setattr(audit_service, "supabase", db)
    monkeypatch.setattr(
        funil_qualificacao_service,
        "get_eligibility_config",
        lambda: CAPACIDADE_PARAMS,
    )
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    return db


def _record_updates(monkeypatch, db):
    calls = []
    original_table = db.table

    def tracked_table(name):
        query = original_table(name)
        original_update = query.update

        def tracked_update(payload):
            calls.append((name, payload.copy()))
            return original_update(payload)

        query.update = tracked_update
        return query

    monkeypatch.setattr(db, "table", tracked_table)
    return calls


def test_recalcular_capacidade_dry_run_returns_before_after_without_update(monkeypatch):
    operation_id = "00000000-0000-0000-0000-000000000101"
    cotacao_id = "C-101"
    operation = _operation_for_capacity(operation_id, cotacao_id=cotacao_id)
    db = _setup_recalculo_capacidade(
        monkeypatch,
        [operation],
        _snapshots_for_capacity(operation_id),
        [_cotacao_for_capacity(cotacao_id)],
    )
    updates = _record_updates(monkeypatch, db)

    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "configured-token"},
        json={"operation_ids": [operation_id]},
    )

    assert response.status_code == 200
    item = response.json()["operacoes"][0]
    assert updates == []
    assert item["grava"] is False
    assert item["antes"]["capacidade_contrato"] == 180_000
    assert item["depois"]["capacidade_contrato"] is not None
    assert "faturas_na_janela" in item["depois"]
    assert item["cotacao"]["antes"] == {
        "valor_enquadrado": 250_000,
        "estagio": "QUALIFICADA",
    }
    assert db.tables["cotacoes_broadfactor"][0]["estagio"] == "QUALIFICADA"


def test_recalcular_capacidade_apply_updates_only_capacity_fields(monkeypatch):
    operation_id = "00000000-0000-0000-0000-000000000102"
    cotacao_id = "C-102"
    operation = _operation_for_capacity(operation_id, cotacao_id=cotacao_id)
    db = _setup_recalculo_capacidade(
        monkeypatch,
        [operation],
        _snapshots_for_capacity(operation_id),
        [_cotacao_for_capacity(cotacao_id)],
    )
    updates = _record_updates(monkeypatch, db)

    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "configured-token"},
        json={"operation_ids": [operation_id], "aplicar": True},
    )

    assert response.status_code == 200
    assert response.json()["operacoes"][0]["grava"] is True
    operation_updates = [payload for table, payload in updates if table == "operations"]
    assert len(operation_updates) == 1
    assert set(operation_updates[0]) <= {
        "capacidade_contrato",
        "capacidade_memoria",
        "valor_enquadrado",
        "valor_enquadrado_pre_capacidade",
        "flags_funil",
    }
    assert "status" not in operation_updates[0]
    assert db.tables["operations"][0]["status"] == "completed"
    assert db.tables["audit_trail"][0]["action"] in AUDIT_ACTIONS
    assert db.tables["audit_trail"][0]["action"] == "operation_status_changed"
    assert db.tables["audit_trail"][0]["payload"]["contexto"] == (
        "recalculo_capacidade_sob_demanda"
    )
    assert db.tables["audit_trail"][0]["payload"]["memoria"] == db.tables[
        "operations"
    ][0]["capacidade_memoria"]
    quote = db.tables["cotacoes_broadfactor"][0]
    assert quote["valor_enquadrado"] == response.json()["operacoes"][0]["cotacao"][
        "depois"
    ]["valor_enquadrado"]
    assert quote["estagio"] == response.json()["operacoes"][0]["cotacao"]["depois"][
        "estagio"
    ]
    assert quote["estagio"] == "DOCUMENTADA"


def test_recalcular_capacidade_demand_without_invoices_uses_sem_execucao(monkeypatch):
    operation_id = "00000000-0000-0000-0000-000000000103"
    operation = _operation_for_capacity(operation_id)
    _setup_recalculo_capacidade(
        monkeypatch,
        [operation],
        _snapshots_for_capacity(operation_id, modalidade="SOB_DEMANDA"),
    )

    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "configured-token"},
        json={"operation_ids": [operation_id]},
    )

    item = response.json()["operacoes"][0]
    assert response.status_code == 200
    assert item["depois"]["capacidade_contrato"] == 0
    assert item["depois"]["fonte_mensal"] == "SEM_EXECUCAO"


def test_recalcular_capacidade_continuous_preserves_the_same_values(monkeypatch):
    from app.services import funil_qualificacao_service

    operation_id = "00000000-0000-0000-0000-000000000104"
    cotacao_id = "C-104"
    operation = _operation_for_capacity(
        operation_id,
        cotacao_id=cotacao_id,
        valor_enquadrado_pre_capacidade=1_000_000,
    )
    snapshots = _snapshots_for_capacity(operation_id)
    db = _setup_recalculo_capacidade(
        monkeypatch, [operation], snapshots, [_cotacao_for_capacity(cotacao_id)]
    )
    updates = _record_updates(monkeypatch, db)
    by_component = {row["component"]: row["parsed_result"] for row in snapshots}
    statuses = {row["component"]: row["status"] for row in snapshots}
    capacidade = funil_qualificacao_service._capacidade_do_contrato(
        operation, by_component, statuses, CAPACIDADE_PARAMS
    )
    operation.update(
        {
            "valor_enquadrado": capacidade["valor_enquadrado"],
            "capacidade_contrato": capacidade["capacidade"],
            "capacidade_memoria": capacidade["memoria"],
        }
    )

    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "configured-token"},
        json={"operation_ids": [operation_id], "aplicar": True},
    )

    item = response.json()["operacoes"][0]
    assert response.status_code == 200
    for key in item["antes"]:
        assert item["depois"][key] == item["antes"][key]
    assert item["cotacao"]["depois"] == item["cotacao"]["antes"]
    assert not [table for table, _payload in updates if table == "cotacoes_broadfactor"]


def test_recalcular_capacidade_missing_quote_returns_warning_without_breaking(monkeypatch):
    operation_id = "00000000-0000-0000-0000-000000000109"
    operation = _operation_for_capacity(operation_id, cotacao_id="C-ausente")
    _setup_recalculo_capacidade(
        monkeypatch, [operation], _snapshots_for_capacity(operation_id)
    )

    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "configured-token"},
        json={"operation_ids": [operation_id], "aplicar": True},
    )

    item = response.json()["operacoes"][0]
    assert response.status_code == 200
    assert item["grava"] is True
    assert item["cotacao"]["aviso"] == "cotacao_nao_encontrada"


def test_recalcular_capacidade_ignores_non_production_operation(monkeypatch):
    operation_id = "00000000-0000-0000-0000-000000000105"
    operation = _operation_for_capacity(operation_id, ambiente="TESTE")
    db = _setup_recalculo_capacidade(monkeypatch, [operation], [])
    updates = _record_updates(monkeypatch, db)

    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "configured-token"},
        json={"operation_ids": [operation_id]},
    )

    item = response.json()["operacoes"][0]
    assert response.status_code == 200
    assert item["grava"] is False
    assert item["motivo_ignorada"] == "ambiente_nao_producao"
    assert updates == []


def test_recalcular_capacidade_rejects_empty_or_too_many_operations(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    headers = {"X-Internal-Token": "configured-token"}
    operation_id = "00000000-0000-0000-0000-000000000106"

    empty = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers=headers,
        json={"operation_ids": []},
    )
    too_many = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers=headers,
        json={"operation_ids": [operation_id] * 21},
    )

    assert empty.status_code == 422
    assert too_many.status_code == 422


def test_recalcular_capacidade_never_runs_collectors(monkeypatch):
    from app.workers.tasks import contratos_comprasnet, contratos_pncp

    operation_id = "00000000-0000-0000-0000-000000000107"
    operation = _operation_for_capacity(operation_id)
    _setup_recalculo_capacidade(
        monkeypatch, [operation], _snapshots_for_capacity(operation_id)
    )

    def collector_called(*_args, **_kwargs):
        raise AssertionError("collector must not run")

    monkeypatch.setattr(contratos_pncp, "run_contratos_pncp", collector_called)
    monkeypatch.setattr(contratos_comprasnet, "run_contratos_comprasnet", collector_called)
    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "configured-token"},
        json={"operation_ids": [operation_id]},
    )

    assert response.status_code == 200
    assert "erro" not in response.json()["operacoes"][0]


def test_recalcular_capacidade_rejects_invalid_internal_token(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    operation_id = "00000000-0000-0000-0000-000000000108"

    response = make_client().post(
        "/api/v1/internal/funil/recalcular-capacidade",
        headers={"X-Internal-Token": "wrong-token"},
        json={"operation_ids": [operation_id]},
    )

    assert response.status_code == 401


def test_missing_internal_token_returns_401(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")

    response = make_client().post("/api/v1/internal/ingestao/broadfactor?dry_run=true")

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
    assert (
        make_client()
        .post(
            "/api/v1/internal/findings/reemitir", headers={"X-Internal-Token": "wrong"}
        )
        .status_code
        == 401
    )
    for limit in (0, 101):
        response = make_client().post(
            "/api/v1/internal/findings/reemitir",
            json={"limite": limit},
            headers={"X-Internal-Token": "configured-token"},
        )
        assert response.status_code == 422


def test_policy_shadow_requires_token_and_validates_body(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    assert make_client().post("/api/v1/internal/politica/sombra").status_code == 401
    for body in ({"limite": 0}, {"limite": 101}, {"ambiente": "INVALIDO"}):
        assert (
            make_client()
            .post(
                "/api/v1/internal/politica/sombra",
                json=body,
                headers={"X-Internal-Token": "configured-token"},
            )
            .status_code
            == 422
        )


def test_policy_shadow_rejects_invalid_token_and_empty_server_token(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    response = make_client().post(
        "/api/v1/internal/politica/sombra",
        json={},
        headers={"X-Internal-Token": "wrong-token"},
    )
    assert response.status_code == 401

    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "")
    response = make_client().post(
        "/api/v1/internal/politica/sombra",
        json={},
        headers={"X-Internal-Token": "configured-token"},
    )
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

    monkeypatch.setattr(
        database,
        "supabase",
        Postgrest({"operations": [{"id": "op-1", "ambiente": "PRODUCAO"}]}),
    )
    monkeypatch.setattr(
        internal, "load_policy", lambda **_kwargs: {"version": {"id": "shadow"}}
    )
    monkeypatch.setattr(internal, "avaliar_sombra", fake_shadow)

    client = make_client()
    dry = client.post(
        "/api/v1/internal/politica/sombra",
        json={"aplicar": False},
        headers={"X-Internal-Token": "configured-token"},
    )
    assert dry.status_code == 200 and persisted == set() and calls == [False]

    first = client.post(
        "/api/v1/internal/politica/sombra",
        json={"aplicar": True},
        headers={"X-Internal-Token": "configured-token"},
    )
    second = client.post(
        "/api/v1/internal/politica/sombra",
        json={"aplicar": True},
        headers={"X-Internal-Token": "configured-token"},
    )
    assert first.status_code == second.status_code == 200
    assert persisted == {"op-1"}
    assert (
        "configured-token" not in first.text and "configured-token" not in second.text
    )


def test_policy_shadow_reports_remaining_after_budget(monkeypatch):
    clock = iter((0.0, 0.0, 46.0))
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        internal, "load_policy", lambda **_kwargs: {"version": {"id": "shadow"}}
    )
    monkeypatch.setattr(
        internal, "select_operations", lambda **_kwargs: ["op-1", "op-2"]
    )
    monkeypatch.setattr(
        internal,
        "avaliar_sombra",
        lambda *_args, **_kwargs: {"classe_geral": "IGUAL", "divergencias": []},
    )
    monkeypatch.setattr(internal, "_monotonic", lambda: next(clock))

    response = make_client().post(
        "/api/v1/internal/politica/sombra",
        json={},
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["processadas"] == 1
    assert response.json()["restantes"] == ["op-2"]


def test_policy_shadow_returns_409_without_shadow_version(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        internal, "load_policy", lambda **_kwargs: (_ for _ in ()).throw(LookupError())
    )
    response = make_client().post(
        "/api/v1/internal/politica/sombra",
        json={},
        headers={"X-Internal-Token": "configured-token"},
    )
    assert response.status_code == 409


def test_explicit_operation_ids_are_not_truncated_by_limit(monkeypatch):
    ids = [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
    ]
    reemitted = []
    automatic_shadow = []
    shadowed = []
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    from app.core import database

    monkeypatch.setattr(database, "supabase", Postgrest())
    monkeypatch.setattr(
        internal,
        "reemitir_operacao",
        lambda operation_id, **_kwargs: reemitted.append(operation_id)
        or {"operation_id": operation_id, "especialistas": {}},
    )
    monkeypatch.setattr(
        internal,
        "executar_sombra_automatica",
        lambda operation_id, **_kwargs: automatic_shadow.append(operation_id),
    )

    findings_response = make_client().post(
        "/api/v1/internal/findings/reemitir",
        json={"operation_ids": ids, "limite": 1},
        headers={"X-Internal-Token": "configured-token"},
    )

    assert findings_response.status_code == 200
    assert reemitted == ids
    assert automatic_shadow == ids

    monkeypatch.setattr(
        internal,
        "load_policy",
        lambda **_kwargs: {"version": {"id": "shadow", "versao": 1, "status": "SOMBRA"}},
    )
    monkeypatch.setattr(
        internal,
        "avaliar_sombra",
        lambda operation_id, **_kwargs: shadowed.append(operation_id)
        or {"classe_geral": "IGUAL", "divergencias": []},
    )

    policy_response = make_client().post(
        "/api/v1/internal/politica/sombra",
        json={"operation_ids": ids, "limite": 1},
        headers={"X-Internal-Token": "configured-token"},
    )

    assert policy_response.status_code == 200
    assert shadowed == ids


def test_policy_shadow_compares_requested_draft_without_official_decision(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    from app.core import database

    monkeypatch.setattr(
        database,
        "supabase",
        Postgrest(
            {
                "operations": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "ambiente": "PRODUCAO",
                    }
                ]
            }
        ),
    )

    def fake_policy(*, versao=None, **_kwargs):
        return {
            "version": {
                "id": f"p-{versao or 1}",
                "versao": versao or 1,
                "status": "RASCUNHO" if versao == 2 else "SOMBRA",
            }
        }

    monkeypatch.setattr(internal, "load_policy", fake_policy)
    monkeypatch.setattr(
        internal,
        "comparar_sombra",
        lambda operation_id, **kwargs: {
            "operation_id": operation_id,
            "versao": {
                "rating": "C",
                "limite": 80,
                "vetos": [],
                "condicoes_precedentes": [],
                "alcada_minima": 2,
                "taxa_ajuste_pp": 0.3,
            },
            "comparar_com": {
                "rating": "C",
                "limite": 100,
                "vetos": [],
                "condicoes_precedentes": [],
                "alcada_minima": 1,
                "taxa_ajuste_pp": 0,
            },
            "diff": {"limite": {"versao": 80, "comparar_com": 100}},
            "shadow": {"classe_geral": "IGUAL"},
        },
    )

    response = make_client().post(
        "/api/v1/internal/politica/sombra",
        json={
            "versao": 2,
            "comparar_com": 1,
            "operation_ids": ["00000000-0000-0000-0000-000000000001"],
            "aplicar": True,
        },
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["operacoes"][0]["diff"]["limite"] == {
        "versao": 80,
        "comparar_com": 100,
    }


def test_policy_shadow_uses_nearest_archived_version_when_comparison_is_omitted(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    from app.core import database

    monkeypatch.setattr(
        database,
        "supabase",
        Postgrest({"operations": [{"id": "00000000-0000-0000-0000-000000000001", "ambiente": "PRODUCAO"}]}),
    )
    monkeypatch.setattr(
        internal,
        "load_policy",
        lambda **_kwargs: {"version": {"id": "p3", "versao": 3, "status": "SOMBRA"}},
    )
    monkeypatch.setattr(internal, "versao_arquivada_anterior", lambda *_args, **_kwargs: 2)
    calls = []
    monkeypatch.setattr(
        internal,
        "comparar_sombra",
        lambda operation_id, **kwargs: calls.append((operation_id, kwargs)) or {
            "operation_id": operation_id, "versao": {}, "comparar_com": {}, "diff": {},
            "shadow": {"classe_geral": "IGUAL"},
        },
    )

    response = make_client().post(
        "/api/v1/internal/politica/sombra",
        json={"operation_ids": ["00000000-0000-0000-0000-000000000001"]},
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert calls[0][1]["comparar_com"] == 2


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
