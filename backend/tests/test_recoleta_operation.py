import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.api.v1.endpoints import operations  # noqa: E402


class _Query:
    def __init__(self, data):
        self.data = data

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def update(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class _Database:
    def table(self, name):
        if name == "component_snapshots":
            return _Query([
                {"component": "contratos_pncp", "status": "failed"},
                {"component": "contratos_comprasnet", "status": "completed"},
                {"component": "pagamentos_portal", "status": "completed"},
                {"component": "recursos_recebidos", "status": "completed"},
                {"component": "brasil_api", "status": "completed"},
            ])
        return _Query([])


def test_recoleta_pendente_agenda_sem_cache_e_reavalia_em_background(monkeypatch):
    from app.services import funil_qualificacao_service
    from app.workers.tasks import contratos_pncp

    monkeypatch.setattr(operations, "supabase", _Database())
    monkeypatch.setattr(
        operations, "_operation_snapshot", lambda _id: {
            "status": "aguardando_relatorio", "pendencia_coleta": True, "cotacao_id": "C-1"
        }
    )
    calls = []
    monkeypatch.setattr(
        contratos_pncp,
        "run_contratos_pncp",
        lambda operation_id, *, use_cache: calls.append((operation_id, use_cache)) or {
            "status": "failed", "snapshot_preservado": True
        },
    )
    monkeypatch.setattr(
        funil_qualificacao_service,
        "atualizar_estagio_pos_fase2",
        lambda *_args: ("QUALIFICADA", []),
    )
    monkeypatch.setattr(operations.audit, "log", lambda *_args, **_kwargs: None)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})
    background = BackgroundTasks()

    result = asyncio.run(
        operations.recoletar_operacao("op-1", background, request, {"id": "user", "role": "analista"})
    )

    assert result["status"] == "accepted"
    assert result["componentes"] == ["contratos_pncp"]
    assert calls == []
    asyncio.run(background())
    assert calls == [("op-1", False)]


def test_recoleta_recusa_operacao_concluida_sem_alterar_status(monkeypatch):
    monkeypatch.setattr(
        operations,
        "_operation_snapshot",
        lambda _id: {"status": "completed", "pendencia_coleta": False},
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})

    with pytest.raises(HTTPException, match="Operação não está pendente de coleta") as caught:
        asyncio.run(
            operations.recoletar_operacao(
                "op-concluida", BackgroundTasks(), request, {"role": "analista"}
            )
        )

    assert caught.value.status_code == 409
