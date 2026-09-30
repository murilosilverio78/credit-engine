import os
import sys
import types


for key in (
    "ANTHROPIC_API_KEY",
    "PORTAL_TRANSPARENCIA_TOKEN",
    "SECRET_KEY",
    "TWOCAPTCHA_API_KEY",
    "RESEND_API_KEY",
):
    os.environ.setdefault(key, "test")

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")
os.environ.setdefault(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
    "testsignature",
)
os.environ.setdefault("SUPABASE_URL", "http://localhost")


from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.endpoints import admin  # noqa: E402


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(admin.router, prefix="/api/v1/admin")
    return TestClient(app)


def test_admin_operations_respects_limit_and_offset(monkeypatch):
    calls = []

    class FakeOperationService:
        async def list(self, **kwargs):
            calls.append(kwargs)
            offset = kwargs["offset"]
            limit = kwargs["limit"]
            return {
                "items": [
                    {"id": f"op-{index}", "status": kwargs["status"]}
                    for index in range(offset, offset + limit)
                ],
                "total": 12,
                "limit": limit,
                "offset": offset,
            }

    fake_module = types.SimpleNamespace(OperationService=FakeOperationService)
    monkeypatch.setitem(sys.modules, "app.services.operation_service", fake_module)

    client = make_client()
    first_page = client.get("/api/v1/admin/operations?limit=5&offset=0&status=completed")
    second_page = client.get("/api/v1/admin/operations?limit=5&offset=5&status=completed")

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    first_payload = first_page.json()
    second_payload = second_page.json()
    assert first_payload["total"] == second_payload["total"] == 12
    assert first_payload["items"] != second_payload["items"]
    assert [item["id"] for item in first_payload["items"]] == [
        "op-0",
        "op-1",
        "op-2",
        "op-3",
        "op-4",
    ]
    assert [item["id"] for item in second_payload["items"]] == [
        "op-5",
        "op-6",
        "op-7",
        "op-8",
        "op-9",
    ]
    assert calls == [
        {
            "status": "completed", "cnpj": None, "estagio": None,
            "busca": None, "rating": None, "relatorio": None,
            "tipo_motivo": None, "limit": 5, "offset": 0,
        },
        {
            "status": "completed", "cnpj": None, "estagio": None,
            "busca": None, "rating": None, "relatorio": None,
            "tipo_motivo": None, "limit": 5, "offset": 5,
        },
    ]


def test_admin_operations_forwards_funnel_filters(monkeypatch):
    calls = []

    class FakeOperationService:
        async def list(self, **kwargs):
            calls.append(kwargs)
            return {"items": [], "total": 0, "limit": kwargs["limit"], "offset": kwargs["offset"]}

    monkeypatch.setitem(
        sys.modules,
        "app.services.operation_service",
        types.SimpleNamespace(OperationService=FakeOperationService),
    )

    response = make_client().get(
        "/api/v1/admin/operations?estagio=QUALIFICADA&busca=Fornecedor&"
        "rating=A&relatorio=gerado&tipo_motivo=criterio"
    )

    assert response.status_code == 200
    assert calls[0]["busca"] == "Fornecedor"
    assert calls[0]["rating"] == "A"
    assert calls[0]["relatorio"] == "gerado"
    assert calls[0]["tipo_motivo"] == "criterio"


def test_admin_operations_validates_pagination_query():
    client = make_client()

    invalid_limit = client.get("/api/v1/admin/operations?limit=0")
    invalid_offset = client.get("/api/v1/admin/operations?offset=-1")

    assert invalid_limit.status_code == 422
    assert invalid_offset.status_code == 422
