import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.api.v1.endpoints import operations  # noqa: E402
from app.services.eligibility_service import EligibilityResult  # noqa: E402


class _Query:
    def __init__(self, data):
        self.data = data

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class _Database:
    def __init__(self, operation=None):
        self.operation = operation or {}

    def table(self, name):
        assert name == "operations"
        return _Query(self.operation)


def _payload():
    return operations.PropostaInput(
        cnpj="12.345.678/0001-90",
        origem_dados="MANUAL",
        valor_solicitado=200_000,
        contrato_saldo=70_000,
    )


def test_manual_quote_with_existing_operation_returns_conflict(monkeypatch):
    monkeypatch.setattr(operations, "is_shutting_down", lambda: False)
    monkeypatch.setattr(
        operations,
        "_cotacao_ativa_por_cnpj",
        lambda _cnpj: {"cotacao_id": "C-ativa"},
    )
    monkeypatch.setattr(operations, "supabase", _Database({"id": "op-existente"}))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(operations.create_operation(_payload(), BackgroundTasks()))

    assert caught.value.status_code == 409
    assert caught.value.detail["operation_id"] == "op-existente"


def test_manual_quote_links_active_quote_and_ignores_typed_balance(monkeypatch):
    from app.services import eligibility_service, operation_service

    monkeypatch.setattr(operations, "is_shutting_down", lambda: False)
    monkeypatch.setattr(
        operations,
        "_cotacao_ativa_por_cnpj",
        lambda _cnpj: {
            "cotacao_id": "C-ativa",
            "saldo_vincendo": 300_000,
            "margem_disponivel": 210_000,
        },
    )
    monkeypatch.setattr(operations, "supabase", _Database())
    monkeypatch.setattr(
        eligibility_service,
        "check_eligibility",
        lambda **kwargs: EligibilityResult(
            elegivel=True,
            valor_enquadrado=200_000,
            saldo_vincendo=300_000,
            prazo_final_meses=12,
        ),
    )
    created = {}

    class FakeService:
        async def create(self, **kwargs):
            created.update(kwargs)
            return {"id": "op-nova"}

    monkeypatch.setattr(operation_service, "OperationService", FakeService)
    monkeypatch.setattr(operations.audit, "log", lambda **kwargs: created.setdefault("audit", kwargs))

    result = asyncio.run(operations.create_operation(_payload(), BackgroundTasks()))

    assert result["operation_id"] == "op-nova"
    assert created["cotacao_id"] == "C-ativa"
    assert created["contrato_saldo"] == 300_000
    assert created["margem_disponivel"] == 210_000
    assert created["audit"]["payload"]["saldo_digitado"] == 70_000
