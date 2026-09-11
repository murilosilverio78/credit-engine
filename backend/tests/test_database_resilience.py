from __future__ import annotations

import os
from types import SimpleNamespace

import httpx
import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.core import database  # noqa: E402
from app.services import cache_service, snapshot_service  # noqa: E402
from app.services.audit_service import AuditService  # noqa: E402
from app.services.cache_service import CacheService  # noqa: E402
from app.services.snapshot_service import SnapshotService  # noqa: E402
from app.workers import base  # noqa: E402


class ReadQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gt(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        self.database.attempts += 1
        if self.database.attempts == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return SimpleNamespace(data=self.database.data[self.table])


class ReadSupabase:
    def __init__(self, data):
        self.data = data
        self.attempts = 0

    def table(self, name):
        return ReadQuery(self, name)


def test_postgrest_global_session_uses_http1():
    session = database.supabase.postgrest.session
    assert session._transport._pool._http2 is False


def test_get_cnpj_retries_remote_protocol_error(monkeypatch):
    fake = ReadSupabase({"operations": {"cnpj": "31822605000191"}})
    monkeypatch.setattr(snapshot_service, "supabase", fake)
    monkeypatch.setattr(base.time, "sleep", lambda _delay: None)

    assert SnapshotService().get_cnpj("op-1") == "31822605000191"
    assert fake.attempts == 2


def test_cache_get_retries_remote_protocol_error(monkeypatch):
    fake = ReadSupabase({"cnpj_cache": {"result": {"cached": True}}})
    monkeypatch.setattr(cache_service, "supabase", fake)
    monkeypatch.setattr(cache_service, "_ttl_hours", lambda _component: 1)
    monkeypatch.setattr(base.time, "sleep", lambda _delay: None)

    assert CacheService().get("31822605000191", "contratos") == {"cached": True}
    assert fake.attempts == 2


def test_cache_ttl_overrides_retry_remote_protocol_error(monkeypatch):
    fake = ReadSupabase({
        "component_config": [
            {"component": "contratos", "cache_ttl_hours": 8},
        ]
    })
    monkeypatch.setattr(cache_service, "supabase", fake)
    monkeypatch.setattr(cache_service, "_TTL_OVERRIDES", {})
    monkeypatch.setattr(cache_service, "_TTL_OVERRIDES_TS", 0.0)
    monkeypatch.setattr(base.time, "sleep", lambda _delay: None)

    assert cache_service._get_ttl_overrides() == {"contratos": 8}
    assert fake.attempts == 2


def test_execute_marks_snapshot_failed_when_get_cnpj_fails(monkeypatch):
    original = httpx.RemoteProtocolError("Server disconnected")
    saved = []

    def fail_get_cnpj(*_args, **_kwargs):
        raise original

    def save_result(*_args, **kwargs):
        saved.append(kwargs)

    monkeypatch.setattr(SnapshotService, "get_cnpj", fail_get_cnpj)
    monkeypatch.setattr(SnapshotService, "save_result", save_result)
    monkeypatch.setattr(
        SnapshotService,
        "mark_running",
        lambda *_args, **_kwargs: pytest.fail("mark_running nao deveria executar"),
    )
    monkeypatch.setattr(AuditService, "log", lambda *_args, **_kwargs: None)

    with pytest.raises(httpx.RemoteProtocolError) as caught:
        base.BaseComponentTask().execute(
            "op-1",
            "contratos_comprasnet",
            lambda _cnpj: {},
        )

    assert caught.value is original
    assert saved == [
        {
            "operation_id": "op-1",
            "component": "contratos_comprasnet",
            "raw_result": None,
            "parsed_result": None,
            "status": "failed",
            "duration_ms": 0,
            "error_message": "Server disconnected",
        }
    ]
