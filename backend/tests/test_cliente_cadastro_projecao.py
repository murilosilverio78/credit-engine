from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import cliente_service  # noqa: E402
from app.workers.tasks import brasil_api  # noqa: E402


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
        if self.name == "projetar_cadastro_de_snapshot":
            if self.db.projection_error:
                raise self.db.projection_error
            return SimpleNamespace(
                data=[
                    {
                        "out_revision": 3,
                        "out_campos_alterados": ["razao_social", "porte"],
                        "out_divergencias": self.db.divergences,
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
    def __init__(
        self,
        *,
        projection_error: Exception | None = None,
        divergences: int = 0,
    ):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.projection_error = projection_error
        self.divergences = divergences

    def rpc(self, name: str, params: dict[str, Any]):
        return FakeRpc(self, name, params)


def _snapshot_args(
    *,
    component: str = "brasil_api",
    result_state: str = "OK",
) -> dict[str, Any]:
    return {
        "cliente_id": "cliente-1",
        "component": component,
        "collection_key": "collection-1",
        "status": "completed",
        "result_state": result_state,
        "collected_at": "2026-09-11T12:00:00+00:00",
        "parsed_result": {"cnpj": "12345678000190"},
        "source_operation_id": "operation-1",
    }


def _setup_service(monkeypatch, db: FakeSupabase) -> tuple[cliente_service.ClienteService, FakeLogger]:
    fake_logger = FakeLogger()
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr(cliente_service, "logger", fake_logger)
    monkeypatch.setattr(
        cliente_service.ClienteService,
        "is_cliente_component",
        lambda _self, _component: True,
    )
    return cliente_service.ClienteService(), fake_logger


def test_brasil_api_ok_projects_returned_snapshot(monkeypatch):
    db = FakeSupabase(divergences=1)
    service, fake_logger = _setup_service(monkeypatch, db)

    snapshot_id = service.registrar_snapshot(**_snapshot_args())

    assert snapshot_id == "snapshot-1"
    assert db.calls[-1] == (
        "projetar_cadastro_de_snapshot",
        {"p_snapshot_id": "snapshot-1"},
    )
    materialized = next(
        payload
        for event, payload in fake_logger.info_calls
        if event == "client.cadastro_materializado"
    )
    assert materialized == {
        "cliente_id": "cliente-1",
        "snapshot_id": "snapshot-1",
        "revision": 3,
        "campos_alterados": ["razao_social", "porte"],
        "divergencias": 1,
    }
    assert any(
        event == "client.cadastro_divergencia"
        for event, _payload in fake_logger.warning_calls
    )


@pytest.mark.parametrize(
    ("component", "result_state"),
    [
        ("pessoa_juridica", "OK"),
        ("brasil_api", "EMPTY"),
        ("brasil_api", "ERROR"),
    ],
)
def test_non_eligible_observation_does_not_project(
    monkeypatch,
    component,
    result_state,
):
    db = FakeSupabase()
    service, _logger = _setup_service(monkeypatch, db)

    service.registrar_snapshot(
        **_snapshot_args(component=component, result_state=result_state)
    )

    assert [name for name, _params in db.calls] == ["registrar_cliente_snapshot"]


def test_projection_failure_does_not_escape_snapshot_registration(monkeypatch):
    db = FakeSupabase(projection_error=RuntimeError("projection unavailable"))
    service, fake_logger = _setup_service(monkeypatch, db)

    snapshot_id = service.registrar_snapshot(**_snapshot_args())

    assert snapshot_id == "snapshot-1"
    failed = next(
        payload
        for event, payload in fake_logger.warning_calls
        if event == "client.cadastro_projecao_failed"
    )
    assert failed == {
        "cliente_id": "cliente-1",
        "snapshot_id": "snapshot-1",
        "error": "projection unavailable",
    }


def test_brasil_api_fetch_includes_cnae_and_branch_identifier(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "cnpj": "12345678000190",
                "cnae_fiscal": 6201501,
                "identificador_matriz_filial": 2,
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, _url):
            return FakeResponse()

    monkeypatch.setattr(brasil_api.httpx, "Client", FakeClient)

    result = brasil_api._fetch("12345678000190")

    assert result["cnae_fiscal"] == "6201501"
    assert result["identificador_matriz_filial"] == 2
