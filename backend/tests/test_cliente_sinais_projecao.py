from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import cliente_service  # noqa: E402


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
        if self.name == "projetar_sinais_financeiros_de_snapshot":
            if self.db.projection_error:
                raise self.db.projection_error
            return SimpleNamespace(
                data=[
                    {
                        "out_sinal_id": "signal-1",
                        "out_inserido": True,
                        "out_anos": 4,
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
    component: str = "recursos_recebidos",
    result_state: str = "OK",
) -> dict[str, Any]:
    return {
        "cliente_id": "cliente-1",
        "component": component,
        "collection_key": "collection-1",
        "status": "completed" if result_state != "ERROR" else "failed",
        "result_state": result_state,
        "collected_at": "2026-09-11T12:00:00+00:00",
        "parsed_result": {
            "faturamento_verificado_12m": 1_000_000,
            "valor_por_ano": {"2025": 900_000},
        },
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


def test_recursos_recebidos_ok_projects_financial_signal(monkeypatch):
    db = FakeSupabase()
    service, fake_logger = _setup_service(monkeypatch, db)

    snapshot_id = service.registrar_snapshot(**_snapshot_args())

    assert snapshot_id == "snapshot-1"
    assert db.calls == [
        ("registrar_cliente_snapshot", ANY),
        (
            "projetar_sinais_financeiros_de_snapshot",
            {"p_snapshot_id": "snapshot-1"},
        ),
    ]
    projected = next(
        payload
        for event, payload in fake_logger.info_calls
        if event == "client.sinais_financeiros_projetados"
    )
    assert projected == {
        "cliente_id": "cliente-1",
        "snapshot_id": "snapshot-1",
        "sinal_id": "signal-1",
        "anos": 4,
    }


@pytest.mark.parametrize("result_state", ("EMPTY", "ERROR"))
def test_empty_and_error_do_not_project_financial_signal(
    monkeypatch,
    result_state,
):
    db = FakeSupabase()
    service, _logger = _setup_service(monkeypatch, db)

    service.registrar_snapshot(**_snapshot_args(result_state=result_state))

    assert [name for name, _params in db.calls] == [
        "registrar_cliente_snapshot"
    ]


def test_other_component_does_not_project_financial_signal(monkeypatch):
    db = FakeSupabase()
    service, _logger = _setup_service(monkeypatch, db)

    service.registrar_snapshot(**_snapshot_args(component="pessoa_juridica"))

    assert [name for name, _params in db.calls] == [
        "registrar_cliente_snapshot"
    ]


def test_financial_projection_failure_does_not_escape(monkeypatch):
    db = FakeSupabase(projection_error=RuntimeError("projection unavailable"))
    service, fake_logger = _setup_service(monkeypatch, db)

    snapshot_id = service.registrar_snapshot(**_snapshot_args())

    assert snapshot_id == "snapshot-1"
    failed = next(
        payload
        for event, payload in fake_logger.warning_calls
        if event == "client.sinais_financeiros_failed"
    )
    assert failed == {
        "cliente_id": "cliente-1",
        "snapshot_id": "snapshot-1",
        "error": "projection unavailable",
    }


def test_recursos_recebidos_does_not_trigger_other_projections(monkeypatch):
    db = FakeSupabase()
    service, _logger = _setup_service(monkeypatch, db)

    service.registrar_snapshot(**_snapshot_args())

    rpc_names = [name for name, _params in db.calls]
    assert "projetar_cadastro_de_snapshot" not in rpc_names
    assert "projetar_sancoes_de_snapshot" not in rpc_names
    assert rpc_names == [
        "registrar_cliente_snapshot",
        "projetar_sinais_financeiros_de_snapshot",
    ]
