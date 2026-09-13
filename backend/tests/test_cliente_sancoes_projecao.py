from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import cliente_service  # noqa: E402
from app.workers.tasks import acordos_leniencia  # noqa: E402


SANCTION_COMPONENTS = ("ceis", "cnep", "cepim", "acordos_leniencia")


class FakeLogger:
    def __init__(self):
        self.info_calls: list[tuple[str, dict[str, Any]]] = []
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs):
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs):
        self.warning_calls.append((event, kwargs))


class FakeRpc:
    def __init__(self, db: "FakeSupabase", name: str, params: dict[str, Any]):
        self.db = db
        self.name = name
        self.params = params

    def execute(self):
        self.db.calls.append((self.name, self.params))
        if self.name == "projetar_sancoes_de_snapshot":
            if self.db.projection_error:
                raise self.db.projection_error
            return SimpleNamespace(
                data=[
                    {
                        "out_coleta_id": "collection-1",
                        "out_novas": 1,
                        "out_confirmadas": 2,
                        "out_ausentes": 3,
                        "out_reaparecidas": 4,
                    }
                ]
            )
        if self.name == "projetar_cadastro_de_snapshot":
            return SimpleNamespace(
                data=[
                    {
                        "out_revision": 1,
                        "out_campos_alterados": [],
                        "out_divergencias": 0,
                    }
                ]
            )
        return SimpleNamespace(
            data=[
                {
                    "out_snapshot_id": "snapshot-1",
                    "out_inserido": True,
                    "out_promovido": True,
                }
            ]
        )


class FakeSupabase:
    def __init__(self, projection_error: Exception | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.projection_error = projection_error

    def rpc(self, name: str, params: dict[str, Any]):
        return FakeRpc(self, name, params)


def _snapshot_args(
    *,
    component: str = "ceis",
    result_state: str = "OK",
) -> dict[str, Any]:
    return {
        "cliente_id": "cliente-1",
        "component": component,
        "collection_key": "collection-1",
        "status": "completed" if result_state != "ERROR" else "failed",
        "result_state": result_state,
        "collected_at": "2026-09-11T12:00:00+00:00",
        "parsed_result": {"registros": []},
        "source_operation_id": "operation-1",
        "error_message": "fonte indisponivel" if result_state == "ERROR" else None,
    }


def _setup_service(monkeypatch, db: FakeSupabase):
    fake_logger = FakeLogger()
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr(cliente_service, "logger", fake_logger)
    monkeypatch.setattr(
        cliente_service.ClienteService,
        "is_cliente_component",
        lambda _self, _component: True,
    )
    return cliente_service.ClienteService(), fake_logger


@pytest.mark.parametrize("component", SANCTION_COMPONENTS)
def test_each_sanction_source_projects_returned_snapshot(monkeypatch, component):
    db = FakeSupabase()
    service, fake_logger = _setup_service(monkeypatch, db)

    snapshot_id = service.registrar_snapshot(**_snapshot_args(component=component))

    assert snapshot_id == "snapshot-1"
    assert db.calls[-1] == (
        "projetar_sancoes_de_snapshot",
        {"p_snapshot_id": "snapshot-1"},
    )
    projected = next(
        payload
        for event, payload in fake_logger.info_calls
        if event == "client.sancoes_projetadas"
    )
    assert projected == {
        "cliente_id": "cliente-1",
        "snapshot_id": "snapshot-1",
        "source": component,
        "novas": 1,
        "confirmadas": 2,
        "ausentes": 3,
        "reaparecidas": 4,
    }
    assert any(
        event == "client.sancao_detectada"
        for event, _payload in fake_logger.warning_calls
    )
    assert any(
        event == "client.sancao_ausente"
        for event, _payload in fake_logger.info_calls
    )


@pytest.mark.parametrize("result_state", ("EMPTY", "ERROR"))
def test_empty_and_error_observations_are_projected(monkeypatch, result_state):
    db = FakeSupabase()
    service, _logger = _setup_service(monkeypatch, db)

    service.registrar_snapshot(**_snapshot_args(result_state=result_state))

    assert db.calls[-1] == (
        "projetar_sancoes_de_snapshot",
        {"p_snapshot_id": "snapshot-1"},
    )


def test_brasil_api_does_not_call_sanctions_projection(monkeypatch):
    db = FakeSupabase()
    service, _logger = _setup_service(monkeypatch, db)

    service.registrar_snapshot(**_snapshot_args(component="brasil_api"))

    assert "projetar_sancoes_de_snapshot" not in {
        name for name, _params in db.calls
    }


def test_sanctions_projection_failure_does_not_escape(monkeypatch):
    db = FakeSupabase(projection_error=RuntimeError("projection unavailable"))
    service, fake_logger = _setup_service(monkeypatch, db)

    snapshot_id = service.registrar_snapshot(**_snapshot_args())

    assert snapshot_id == "snapshot-1"
    failed = next(
        payload
        for event, payload in fake_logger.warning_calls
        if event == "client.sancoes_projecao_failed"
    )
    assert failed == {
        "cliente_id": "cliente-1",
        "snapshot_id": "snapshot-1",
        "source": "ceis",
        "error": "projection unavailable",
    }


def test_acordos_fetch_preserves_fields_and_adds_source_metadata(monkeypatch):
    source_agreement = {
        "id": 123,
        "situacao": "Em cumprimento",
        "dataInicioAcordo": "01/02/2025",
        "dataFimAcordo": "01/02/2030",
        "orgaoResponsavel": "CGU",
        "objeto": "Cooperacao e ressarcimento",
        "quantidade": 2,
        "sancoes": [
            {"cnpj": "12345678000190", "razaoSocial": "Empresa A"},
            {"cnpj": "98765432000110", "razaoSocial": "Empresa B"},
        ],
    }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_fetch(_client, _url, *, headers, params):
        assert headers == {"chave-api-dados": "token"}
        return [source_agreement] if params["pagina"] == 1 else []

    monkeypatch.setattr(acordos_leniencia.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        acordos_leniencia,
        "fetch_json_with_retry",
        fake_fetch,
    )

    result = acordos_leniencia._fetch("12.345.678/0001-90", token="token")

    assert result["possui_acordo"] is True
    assert result["total_acordos"] == 1
    assert result["acordos"] == [
        {
            "id": 123,
            "situacao": "Em cumprimento",
            "data_inicio": "01/02/2025",
            "data_fim": "01/02/2030",
            "orgao": "CGU",
            "objeto": "Cooperacao e ressarcimento",
            "quantidade_empresas": 2,
            "empresas": [
                {"cnpj": "12345678000190", "razao_social": "Empresa A"},
                {"cnpj": "98765432000110", "razao_social": "Empresa B"},
            ],
        }
    ]
