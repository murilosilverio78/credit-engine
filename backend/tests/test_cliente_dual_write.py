from __future__ import annotations

# ruff: noqa: E402

import os
from types import SimpleNamespace
from typing import Any

import pytest

for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import cliente_result_classifier as classifier
from app.services import cliente_service
from app.services.operation_service import OperationService
from app.workers.base import BaseComponentTask


class FakeQuery:
    def __init__(self, db: "FakeSupabase", table: str):
        self.db = db
        self.table = table
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, payload, **_kwargs):
        self.payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        self.db.table_calls.append(self.table)
        if self.table == "component_config":
            return SimpleNamespace(
                data=[
                    {"component": self.db.component, "data_scope": self.db.scope}
                ]
            )
        if self.table == "operations" and self.payload is not None:
            operation = {"id": "op-created", **self.payload}
            return SimpleNamespace(data=[operation])
        if self.table == "operations":
            return SimpleNamespace(
                data={
                    "cliente_id": self.db.cliente_id,
                    "cotacao_id": self.db.cotacao_id,
                }
            )
        return SimpleNamespace(data=[])


class FakeRpc:
    def __init__(self, db: "FakeSupabase", name: str, params: dict[str, Any]):
        self.db = db
        self.name = name
        self.params = params

    def execute(self):
        self.db.rpc_calls.append((self.name, self.params))
        if self.name in self.db.failing_rpcs:
            raise RuntimeError(f"rpc indisponivel: {self.name}")
        if self.name == "vincular_cliente_operacao":
            return SimpleNamespace(
                data=[
                    {
                        "out_cliente_id": self.db.linked_cliente_id,
                        "out_criado": True,
                        "out_vinculado": True,
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
        component: str = "brasil_api",
        scope: str = "CLIENTE",
        cliente_id: str | None = "cliente-1",
        linked_cliente_id: str | None = None,
        cotacao_id: str | None = "cotacao-1",
        failing_rpcs: set[str] | None = None,
    ):
        self.component = component
        self.scope = scope
        self.cliente_id = cliente_id
        self.linked_cliente_id = linked_cliente_id or cliente_id
        self.cotacao_id = cotacao_id
        self.failing_rpcs = failing_rpcs or set()
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []
        self.table_calls: list[str] = []

    def table(self, name: str):
        return FakeQuery(self, name)

    def rpc(self, name: str, params: dict[str, Any]):
        return FakeRpc(self, name, params)


@pytest.fixture(autouse=True)
def reset_component_scope_cache(monkeypatch):
    monkeypatch.setattr(cliente_service, "_COMPONENT_SCOPES", {})
    monkeypatch.setattr(cliente_service, "_COMPONENT_SCOPES_TS", 0.0)


def _patch_component_dependencies(monkeypatch, *, cached=None):
    from app.services.audit_service import AuditService
    from app.services.cache_service import CacheService
    from app.services.snapshot_service import SnapshotService

    monkeypatch.setattr(SnapshotService, "get_cnpj", lambda *_args: "12345678000190")
    monkeypatch.setattr(SnapshotService, "mark_running", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(SnapshotService, "save_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(CacheService, "get", lambda *_args, **_kwargs: cached)
    monkeypatch.setattr(CacheService, "set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(AuditService, "log", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12.345.678/0001-90", "12345678000190"),
        ("ab12.cd34/ef56-90", "AB12CD34EF5690"),
        ("123.456.789-01", None),
        ("", None),
        (None, None),
    ],
)
def test_normalizar_cnpj(value, expected):
    assert cliente_service.normalizar_cnpj(value) == expected


@pytest.mark.parametrize(
    ("component", "result", "expected_state", "expected_source"),
    [
        ("brasil_api", {}, "ERROR", "BRASIL_API"),
        (
            "pessoa_juridica",
            {"erro": "sem_registro"},
            "ERROR",
            "PORTAL_TRANSPARENCIA",
        ),
        (
            "contratos",
            {"total_contratos": 0, "contratos_detalhe": []},
            "EMPTY",
            "PORTAL_TRANSPARENCIA",
        ),
        (
            "recursos_recebidos",
            {
                "total_registros": 0,
                "fonte_primaria": "BROADFACTOR",
                "recursos_detalhe": [],
            },
            "EMPTY",
            "BROADFACTOR",
        ),
        (
            "ceis",
            {"total_registros": 0, "registros": []},
            "EMPTY",
            "PORTAL_TRANSPARENCIA",
        ),
        (
            "cnep",
            {"total_registros": 1, "registros": [{}]},
            "OK",
            "PORTAL_TRANSPARENCIA",
        ),
        (
            "cepim",
            {"total_registros": 0, "registros": []},
            "EMPTY",
            "PORTAL_TRANSPARENCIA",
        ),
        (
            "acordos_leniencia",
            {"total_acordos": 0, "acordos": []},
            "EMPTY",
            "PORTAL_TRANSPARENCIA",
        ),
        (
            "web_research",
            {"flags_reputacao": ["reputacao_parse_falhou"]},
            "ERROR",
            "WEB_RESEARCH",
        ),
        ("componente_futuro", {"valor": 1}, "OK", None),
    ],
)
def test_classificar_componentes(
    component,
    result,
    expected_state,
    expected_source,
):
    classification = classifier.classificar(
        component,
        result,
        cotacao_id="cotacao-1",
    )
    assert classification.result_state == expected_state
    assert classification.fonte == expected_source


def test_classificar_rejeita_resultado_que_nao_e_dict():
    classification = classifier.classificar(
        "brasil_api",
        [],
        cotacao_id=None,
    )
    assert classification.result_state == "ERROR"
    assert classification.error_message == "resultado nao e dict"


def test_classificar_combina_degradacoes():
    classification = classifier.classificar(
        "recursos_recebidos",
        {
            "total_registros": 1,
            "fonte_primaria": "PORTAL_TRANSPARENCIA",
            "recursos_detalhe": [{}],
            "_pagination": {"atingiu_cap": True},
        },
        cotacao_id="cotacao-1",
    )
    assert classification.degradado is True
    assert classification.degradacao_motivo == (
        "fallback_broadfactor;paginacao_truncada"
    )


def test_recursos_portal_manual_nao_e_degradado():
    classification = classifier.classificar(
        "recursos_recebidos",
        {
            "total_registros": 1,
            "fonte_primaria": "PORTAL_TRANSPARENCIA",
            "recursos_detalhe": [{}],
        },
        cotacao_id=None,
    )
    assert classification.degradado is False
    assert classification.degradacao_motivo is None


def test_payload_hash_independe_da_ordem_das_chaves():
    first = {"b": 2, "a": {"d": 4, "c": 3}}
    second = {"a": {"c": 3, "d": 4}, "b": 2}
    assert classifier.calcular_payload_hash(first) == classifier.calcular_payload_hash(
        second
    )


def test_execute_ignora_falha_da_rpc_de_dual_write(monkeypatch):
    db = FakeSupabase(failing_rpcs={"registrar_cliente_snapshot"})
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr("app.core.database.supabase", db)
    _patch_component_dependencies(monkeypatch)

    result = BaseComponentTask().execute(
        "op-1",
        "brasil_api",
        lambda _cnpj: {"cnpj": "12345678000190"},
    )

    assert result == {
        "operation_id": "op-1",
        "component": "brasil_api",
        "status": "completed",
    }
    assert [name for name, _params in db.rpc_calls] == [
        "registrar_cliente_snapshot"
    ]


def test_execute_nao_armazena_resultado_com_erro_no_cache(monkeypatch):
    from app.services.cache_service import CacheService

    db = FakeSupabase(component="pessoa_juridica")
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr("app.core.database.supabase", db)
    _patch_component_dependencies(monkeypatch)
    cache_writes = []
    monkeypatch.setattr(
        CacheService,
        "set",
        lambda *_args, **_kwargs: cache_writes.append((_args, _kwargs)),
    )

    result = BaseComponentTask().execute(
        "op-1",
        "pessoa_juridica",
        lambda _cnpj: {"erro": "sem_registro"},
    )

    assert result["status"] == "completed"
    assert cache_writes == []


def test_execute_relança_excecao_original_e_grava_error(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr("app.core.database.supabase", db)
    _patch_component_dependencies(monkeypatch)
    original = RuntimeError("fonte indisponivel")

    def fail(_cnpj):
        raise original

    with pytest.raises(RuntimeError) as caught:
        BaseComponentTask().execute("op-1", "brasil_api", fail)

    assert caught.value is original
    rpc_call = next(
        params
        for name, params in db.rpc_calls
        if name == "registrar_cliente_snapshot"
    )
    assert rpc_call["p_status"] == "failed"
    assert rpc_call["p_result_state"] == "ERROR"
    assert rpc_call["p_error_message"] == "fonte indisponivel"
    assert rpc_call["p_parsed_result"] is None


def test_execute_cache_hit_nao_grava_cliente_snapshot(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr("app.core.database.supabase", db)
    _patch_component_dependencies(monkeypatch, cached={"cnpj": "12345678000190"})

    result = BaseComponentTask().execute(
        "op-1",
        "brasil_api",
        lambda _cnpj: pytest.fail("handler nao deveria executar"),
    )

    assert result == {
        "operation_id": "op-1",
        "component": "brasil_api",
        "cached": True,
    }
    assert db.rpc_calls == []


def test_execute_escopo_operacao_nao_tenta_rpc(monkeypatch):
    db = FakeSupabase(component="score_engine", scope="OPERACAO")
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr("app.core.database.supabase", db)
    _patch_component_dependencies(monkeypatch)

    result = BaseComponentTask().execute(
        "op-1",
        "score_engine",
        lambda _cnpj: {"score": 70},
        use_cache=False,
    )

    assert result["status"] == "completed"
    assert db.rpc_calls == []
    assert "operations" not in db.table_calls


def test_execute_autocorrige_operacao_sem_cliente(monkeypatch):
    db = FakeSupabase(cliente_id=None, linked_cliente_id="cliente-recuperado")
    monkeypatch.setattr(cliente_service, "supabase", db)
    monkeypatch.setattr("app.core.database.supabase", db)
    _patch_component_dependencies(monkeypatch)

    result = BaseComponentTask().execute(
        "op-antiga",
        "brasil_api",
        lambda _cnpj: {"cnpj": "12345678000190"},
    )

    assert result["status"] == "completed"
    assert [name for name, _params in db.rpc_calls] == [
        "vincular_cliente_operacao",
        "registrar_cliente_snapshot",
    ]
    assert db.rpc_calls[1][1]["p_cliente_id"] == "cliente-recuperado"


@pytest.mark.asyncio
async def test_operation_create_ignora_falha_ao_vincular(monkeypatch):
    from app.services import operation_service

    db = FakeSupabase(failing_rpcs={"vincular_cliente_operacao"})
    monkeypatch.setattr(operation_service, "supabase", db)
    monkeypatch.setattr(cliente_service, "supabase", db)

    async def no_snapshots(_operation_id):
        return None

    service = OperationService()
    monkeypatch.setattr(service, "_init_snapshots", no_snapshots)

    operation = await service.create("12345678000190", "MANUAL")

    assert operation == {
        "id": "op-created",
        "cnpj": "12345678000190",
        "origem_dados": "MANUAL",
        "status": "pending",
        "source": "frontend_mvp",
        "prazo_vincendo_indisponivel": False,
    }
    assert db.rpc_calls[0][0] == "vincular_cliente_operacao"
