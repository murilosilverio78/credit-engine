import asyncio
import os
from types import SimpleNamespace

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


def test_recoleta_reexecuta_falha_sem_cache_e_reavalia_funil(monkeypatch):
    from app.services import funil_qualificacao_service
    from app.workers.tasks import contratos_pncp

    monkeypatch.setattr(operations, "supabase", _Database())
    monkeypatch.setattr(
        operations, "_operation_snapshot", lambda _id: {"status": "completed", "cotacao_id": "C-1"}
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
    monkeypatch.setattr(
        funil_qualificacao_service,
        "decidir_status_pos_fase2",
        lambda _reasons: {"status": "aguardando_relatorio", "pendencia_coleta": False, "motivos_classificados": {}},
    )
    monkeypatch.setattr(operations.audit, "log", lambda **_kwargs: None)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})

    result = asyncio.run(
        operations.recoletar_operacao("op-1", request, {"id": "user", "role": "analista"})
    )

    assert calls == [("op-1", False)]
    assert result["componentes"][0]["snapshot_preservado"] is True
    assert result["reavaliacao_funil"]["status"] == "aguardando_relatorio"
