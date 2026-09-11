from __future__ import annotations

import os

import pytest
from fastapi import HTTPException, Request


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.api.v1.endpoints import operations  # noqa: E402


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/operations/op-1/approve",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


def _setup_approval(
    monkeypatch,
    *,
    requires_review: bool | None,
    operation_status: str = "completed",
    patch_review_context: bool = True,
):
    operation = {
        "id": "op-1",
        "status": operation_status,
        "rating": "B",
        "score": 75,
        "valor_solicitado": 100_000,
    }
    approval_calls = []
    audit_calls = []
    status_updates = []

    monkeypatch.setattr(operations, "_operation_snapshot", lambda _id: operation)
    monkeypatch.setattr(
        operations,
        "_get_alcada_config",
        lambda _role: {
            "max_valor": 1_000_000,
            "max_rating": "D",
            "pode_aprovar_escalada": True,
        },
    )
    if patch_review_context:
        monkeypatch.setattr(
            operations,
            "_score_manual_review_context",
            lambda _id: (
                requires_review,
                ["ceis"] if requires_review else [],
            ),
        )

    def insert_approval(*args, **kwargs):
        approval_calls.append((args, kwargs))
        return {"id": "approval-1"}

    monkeypatch.setattr(operations, "_insert_approval", insert_approval)
    monkeypatch.setattr(
        operations,
        "_update_operation_status",
        lambda *args, **kwargs: status_updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        operations.audit,
        "log",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    return approval_calls, audit_calls, status_updates


@pytest.mark.asyncio
async def test_approve_requires_justification_for_unverified_sanctions(monkeypatch):
    approval_calls, audit_calls, status_updates = _setup_approval(
        monkeypatch,
        requires_review=True,
    )

    with pytest.raises(HTTPException) as caught:
        await operations.approve_operation(
            "op-1",
            operations.ApprovalInput(),
            _request(),
            {"id": "user-1", "role": "diretor"},
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == (
        "Sanções não verificadas: justificativa obrigatória para aprovar"
    )
    assert approval_calls == []
    assert audit_calls == []
    assert status_updates == []


@pytest.mark.asyncio
async def test_approve_with_justification_audits_unverified_sanctions(monkeypatch):
    approval_calls, audit_calls, status_updates = _setup_approval(
        monkeypatch,
        requires_review=True,
    )

    result = await operations.approve_operation(
        "op-1",
        operations.ApprovalInput(justificativa="  Risco revisado manualmente  "),
        _request(),
        {"id": "user-1", "role": "diretor"},
    )

    assert result == {"ok": True, "approval_id": "approval-1"}
    assert approval_calls[0][0][3] == "Risco revisado manualmente"
    assert audit_calls[0]["payload"] == {
        "approval_id": "approval-1",
        "sancao_nao_verificada": True,
        "fontes_sancao_nao_verificadas": ["ceis"],
    }
    assert len(status_updates) == 1


@pytest.mark.asyncio
async def test_approve_with_running_score_returns_conflict_not_server_error(
    monkeypatch,
):
    class EmptyScoreQuery:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return None

    class EmptyScoreSupabase:
        def table(self, _name):
            return EmptyScoreQuery()

    approval_calls, audit_calls, status_updates = _setup_approval(
        monkeypatch,
        requires_review=None,
        patch_review_context=False,
    )
    monkeypatch.setattr(operations, "supabase", EmptyScoreSupabase())

    with pytest.raises(HTTPException) as caught:
        await operations.approve_operation(
            "op-1",
            operations.ApprovalInput(),
            _request(),
            {"id": "user-1", "role": "diretor"},
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == (
        "Score em reprocessamento ou indisponível; aguarde para aprovar"
    )
    assert approval_calls == []
    assert audit_calls == []
    assert status_updates == []


@pytest.mark.asyncio
async def test_approved_escalation_requires_unverified_sanction_justification(
    monkeypatch,
):
    approval_calls, audit_calls, status_updates = _setup_approval(
        monkeypatch,
        requires_review=True,
        operation_status="escalated",
    )

    with pytest.raises(HTTPException) as caught:
        await operations.resolve_escalation(
            "op-1",
            operations.ResolveEscalationInput(
                approval_id="original-approval",
                action="escalation_approved",
            ),
            _request(),
            {"id": "user-1", "role": "diretor"},
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == (
        "Sanções não verificadas: justificativa obrigatória para aprovar"
    )
    assert approval_calls == []
    assert audit_calls == []
    assert status_updates == []


@pytest.mark.asyncio
async def test_approved_escalation_audits_unverified_sanctions(monkeypatch):
    approval_calls, audit_calls, status_updates = _setup_approval(
        monkeypatch,
        requires_review=True,
        operation_status="escalated",
    )

    result = await operations.resolve_escalation(
        "op-1",
        operations.ResolveEscalationInput(
            approval_id="original-approval",
            action="escalation_approved",
            justificativa="Risco de sanções revisado manualmente",
        ),
        _request(),
        {"id": "user-1", "role": "diretor"},
    )

    assert result == {"ok": True, "approval_id": "approval-1"}
    assert approval_calls[0][0][3] == "Risco de sanções revisado manualmente"
    assert audit_calls[0]["payload"] == {
        "approval_id": "approval-1",
        "resolved_approval_id": "original-approval",
        "sancao_nao_verificada": True,
        "fontes_sancao_nao_verificadas": ["ceis"],
    }
    assert len(status_updates) == 1


@pytest.mark.asyncio
async def test_rejected_escalation_does_not_read_score_context(monkeypatch):
    approval_calls, audit_calls, status_updates = _setup_approval(
        monkeypatch,
        requires_review=True,
        operation_status="escalated",
    )
    monkeypatch.setattr(
        operations,
        "_score_manual_review_context",
        lambda _id: pytest.fail("rejeicao nao deve consultar o score"),
    )

    result = await operations.resolve_escalation(
        "op-1",
        operations.ResolveEscalationInput(
            approval_id="original-approval",
            action="escalation_rejected",
            justificativa="Risco incompatível com a política",
        ),
        _request(),
        {"id": "user-1", "role": "diretor"},
    )

    assert result["ok"] is True
    assert approval_calls[0][0][3] == "Risco incompatível com a política"
    assert "sancao_nao_verificada" not in audit_calls[0]["payload"]
    assert len(status_updates) == 1


@pytest.mark.asyncio
async def test_approve_without_flag_keeps_optional_justification(monkeypatch):
    approval_calls, audit_calls, status_updates = _setup_approval(
        monkeypatch,
        requires_review=False,
    )

    result = await operations.approve_operation(
        "op-1",
        operations.ApprovalInput(),
        _request(),
        {"id": "user-1", "role": "diretor"},
    )

    assert result["ok"] is True
    assert approval_calls[0][0][3] is None
    assert audit_calls[0]["payload"]["sancao_nao_verificada"] is False
    assert len(status_updates) == 1
