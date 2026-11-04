from __future__ import annotations

from datetime import date
import os
import sys
from types import ModuleType


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.endpoints import internal  # noqa: E402
from app.services import audit_service, tst_cndt_service, upload_service  # noqa: E402
from tests.fakes.postgrest import Postgrest  # noqa: E402


OPERATION_ID = "00000000-0000-0000-0000-000000000101"
CNPJ = "04251091000199"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(internal.router, prefix="/api/v1/internal")
    return TestClient(app)


class Storage:
    def __init__(self):
        self.uploads: list[tuple[str, bytes, str]] = []

    def upload_document_at_key(self, key, content, mime):
        self.uploads.append((key, content, mime))
        return True


class Proxy:
    def __init__(self, result, pdf=b"%PDF-cndt"):
        self.result = result
        self.pdf = pdf
        self.calls = 0

    def issue(self, _cnpj):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result, self.pdf


def _certificate_result():
    return {
        "status": "obtida",
        "fonte": "tst",
        "valida": True,
        "resultado": "negativa",
        "cnpj_certidao": CNPJ,
        "data_emissao": "01/01/2030",
        "data_validade": "31/12/2030",
        "numero_certidao": "123/2030",
        "filename": "cndt.pdf",
    }


def _setup(monkeypatch, *, proxy: Proxy, cache: bool = False, pending_task=False):
    from app.core import database

    tables = {
        "operations": [{"id": OPERATION_ID, "cnpj": CNPJ}],
        "component_snapshots": [],
        "certidoes_cndt_tst_lineage": [],
        "certidoes_cndt_tst_cache": [],
        "upload_tasks": [],
        "audit_trail": [],
    }
    if cache:
        tables["certidoes_cndt_tst_cache"].append(
            {
                "cnpj": CNPJ,
                "sha256": "a" * 64,
                "storage_key": "clientes/x/documentos/cache.pdf",
                "filename": "cndt.pdf",
                "resultado": "negativa",
                "data_emissao": "2030-01-01",
                "data_validade": "2030-12-31",
                "numero_certidao": "123/2030",
                "valida": True,
            }
        )
    if pending_task:
        tables["upload_tasks"].append(
            {
                "id": "task-1",
                "operation_id": OPERATION_ID,
                "document_type": "cndt_tst",
                "status": "pending",
                "file_content": "unused",
            }
        )
    db = Postgrest(tables)
    storage = Storage()
    timeouts: list[float | None] = []

    def proxy_factory(*, timeout_seconds=None):
        timeouts.append(timeout_seconds)
        return proxy

    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(upload_service, "supabase", db)
    monkeypatch.setattr(audit_service, "supabase", db)
    storage_module = ModuleType("app.services.storage_service")
    storage_module.StorageService = lambda: storage
    monkeypatch.setitem(sys.modules, "app.services.storage_service", storage_module)
    monkeypatch.setattr(tst_cndt_service, "TstCndtProxyClient", proxy_factory)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    return db, storage, timeouts


def test_manual_cndt_success_uses_requested_timeout_and_satisfies_task(monkeypatch):
    proxy = Proxy(_certificate_result())
    db, storage, timeouts = _setup(
        monkeypatch,
        proxy=proxy,
        pending_task=True,
    )

    response = _client().post(
        f"/api/v1/internal/operations/{OPERATION_ID}/certidoes/cndt/coletar",
        json={"timeout_seconds": 120},
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "operation_id": OPERATION_ID,
        "obtida": True,
        "fonte": "tst",
        "erro": None,
        "proxima_acao": "Reprocessar score",
    }
    assert timeouts == [120]
    assert storage.uploads
    assert db.tables["upload_tasks"][0]["status"] == "completed"
    assert db.tables["upload_tasks"][0]["file_content"] is None
    audit = db.tables["audit_trail"][0]
    assert audit["action"] == "component_completed"
    assert audit["payload"]["component"] == "cndt_tst"
    assert CNPJ not in str(response.json())


def test_manual_cndt_uses_cache_without_proxy_or_storage(monkeypatch):
    proxy = Proxy(_certificate_result())
    _db, storage, timeouts = _setup(monkeypatch, proxy=proxy, cache=True)
    monkeypatch.setattr(tst_cndt_service, "date", type("FixedDate", (date,), {
        "today": classmethod(lambda cls: cls(2030, 1, 2))
    }))

    response = _client().post(
        f"/api/v1/internal/operations/{OPERATION_ID}/certidoes/cndt/coletar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["obtida"] is True
    assert response.json()["fonte"] == "cache"
    assert proxy.calls == 0
    assert storage.uploads == []
    assert timeouts == [90]


def test_manual_cndt_failure_is_200_without_sensitive_data(monkeypatch):
    proxy = Proxy(
        {"status": "falha", "erro": "BLOQUEIO_ANTIBOT", "fonte": "tst"},
        None,
    )
    db, _storage, _timeouts = _setup(monkeypatch, proxy=proxy)

    response = _client().post(
        f"/api/v1/internal/operations/{OPERATION_ID}/certidoes/cndt/coletar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["obtida"] is False
    assert response.json()["erro"] == "BLOQUEIO_ANTIBOT"
    assert db.tables["audit_trail"] == []
    assert CNPJ not in str(response.json())


def test_manual_cndt_proxy_exception_is_failure_not_500(monkeypatch):
    proxy = Proxy(RuntimeError("proxy secret must not leak"), None)
    _setup(monkeypatch, proxy=proxy)

    response = _client().post(
        f"/api/v1/internal/operations/{OPERATION_ID}/certidoes/cndt/coletar",
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["obtida"] is False
    assert response.json()["erro"] == "PORTAL_INDISPONIVEL"
    assert "secret" not in str(response.json())


def test_active_cndt_dry_run_filters_valid_closed_and_honors_limit(monkeypatch):
    from app.core import database

    ids = [f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 5)]
    db = Postgrest(
        {
            "cotacoes_broadfactor": [
                {"operation_id": ids[0], "ambiente": "PRODUCAO", "estagio": "DOCUMENTADA"},
                {"operation_id": ids[1], "ambiente": "PRODUCAO", "estagio": "QUALIFICADA"},
                {"operation_id": ids[2], "ambiente": "PRODUCAO", "estagio": "DOCUMENTADA"},
                {"operation_id": ids[3], "ambiente": "PRODUCAO", "estagio": "ENCERRADA"},
            ],
            "component_snapshots": [
                {
                    "operation_id": ids[2],
                    "component": "cndt_tst",
                    "status": "completed",
                    "parsed_result": {
                        "valida": True,
                        "data_validade": "2030-12-31",
                    },
                }
            ],
        }
    )
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(internal, "date", type("FixedDate", (date,), {
        "today": classmethod(lambda cls: cls(2030, 1, 2))
    }))

    response = _client().post(
        "/api/v1/internal/certidoes/cndt/coletar-ativas",
        json={"limite": 1, "dry_run": True},
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["processadas"] == 0
    assert [row["operation_id"] for row in response.json()["operacoes"]] == [ids[0]]
    assert response.json()["restantes"] == [ids[1]]


def test_active_cndt_collection_is_sequential_and_honors_limit(monkeypatch):
    from app.core import database

    ids = [f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 4)]
    calls: list[str] = []
    monkeypatch.setattr(database, "supabase", Postgrest({}))
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")
    monkeypatch.setattr(
        internal,
        "_select_active_cndt_operations",
        lambda _database, _operation_ids: ids,
    )

    def collect(operation_id, **_kwargs):
        calls.append(operation_id)
        return {
            "operation_id": operation_id,
            "obtida": True,
            "fonte": "tst",
            "erro": None,
        }

    monkeypatch.setattr(internal, "_collect_cndt_internal", collect)

    response = _client().post(
        "/api/v1/internal/certidoes/cndt/coletar-ativas",
        json={"limite": 2, "dry_run": False},
        headers={"X-Internal-Token": "configured-token"},
    )

    assert response.status_code == 200
    assert response.json()["processadas"] == 2
    assert calls == ids[:2]
    assert response.json()["restantes"] == ids[2:]


def test_cndt_internal_route_requires_token(monkeypatch):
    monkeypatch.setattr(internal.settings, "INTERNAL_JOB_TOKEN", "configured-token")

    response = _client().post(
        f"/api/v1/internal/operations/{OPERATION_ID}/certidoes/cndt/coletar"
    )

    assert response.status_code == 401
