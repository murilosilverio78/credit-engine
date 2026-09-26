import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.api.v1.endpoints import operations  # noqa: E402


class Query:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.filters = []
        self.single = False

    def select(self, *_args):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def maybe_single(self):
        self.single = True
        return self

    def execute(self):
        rows = self.database[self.table]
        matches = [
            row.copy()
            for row in rows
            if all(row.get(field) == value for field, value in self.filters)
        ]
        if self.single and not matches:
            return None
        return SimpleNamespace(data=(matches[0] if self.single else matches))


class Supabase:
    def __init__(
        self,
        stage="QUALIFICADA",
        score_completed=False,
        include_quote=True,
    ):
        self.tables = {
            "component_snapshots": (
                [{
                    "operation_id": "op-1",
                    "component": "score_engine",
                    "status": "completed",
                }]
                if score_completed
                else []
            ),
            "cotacoes_broadfactor": (
                [{
                    "cotacao_id": "C-1",
                    "operation_id": "op-1",
                    "estagio": stage,
                }]
                if include_quote
                else []
            ),
        }

    def table(self, name):
        return Query(self.tables, name)


def request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


def call_endpoint(background, user=None):
    return asyncio.run(
        operations.generate_operation_report(
            "op-1",
            background,
            request(),
            user or {"id": "analyst-1", "role": "analista"},
        )
    )


def test_qualified_quote_without_score_schedules_report_and_audits(monkeypatch):
    audit_entries = []
    monkeypatch.setattr(operations, "supabase", Supabase())
    monkeypatch.setattr(
        operations,
        "_operation_snapshot",
        lambda _operation_id: {"id": "op-1", "status": "aguardando_relatorio", "rating": None},
    )
    monkeypatch.setattr(
        operations,
        "audit",
        SimpleNamespace(log=lambda **kwargs: audit_entries.append(kwargs)),
    )
    background = BackgroundTasks()

    result = call_endpoint(background)

    assert result["status"] == "accepted"
    assert len(background.tasks) == 1
    assert background.tasks[0].func.__name__ == "start_report_analysis"
    assert background.tasks[0].args == ("op-1",)
    assert audit_entries[0]["action"] == "relatorio_solicitado"
    assert audit_entries[0]["payload"] == {"cotacao_id": "C-1"}


def test_unqualified_quote_cannot_generate_report(monkeypatch):
    monkeypatch.setattr(operations, "supabase", Supabase(stage="DOCUMENTADA"))
    monkeypatch.setattr(
        operations,
        "_operation_snapshot",
        lambda _operation_id: {"id": "op-1", "status": "aguardando_relatorio", "rating": None},
    )

    with pytest.raises(HTTPException) as exc:
        call_endpoint(BackgroundTasks())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "QUOTE_NOT_QUALIFIED"
    assert exc.value.detail["estagio"] == "DOCUMENTADA"


def test_operation_without_linked_quote_returns_not_qualified(monkeypatch):
    monkeypatch.setattr(
        operations,
        "supabase",
        Supabase(include_quote=False),
    )
    monkeypatch.setattr(
        operations,
        "_operation_snapshot",
        lambda _operation_id: {
            "id": "op-1",
            "status": "aguardando_relatorio",
            "rating": None,
        },
    )

    with pytest.raises(HTTPException) as exc:
        call_endpoint(BackgroundTasks())

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "QUOTE_NOT_QUALIFIED",
        "message": "Relatorio so pode ser gerado para cotacao qualificada",
        "estagio": None,
    }


@pytest.mark.parametrize(
    "operation,score_completed",
    [
        ({"id": "op-1", "status": "completed", "rating": "A"}, False),
        ({"id": "op-1", "status": "aguardando_relatorio", "rating": None}, True),
    ],
)
def test_generated_report_cannot_be_requested_again(
    monkeypatch, operation, score_completed
):
    monkeypatch.setattr(
        operations,
        "supabase",
        Supabase(score_completed=score_completed),
    )
    monkeypatch.setattr(operations, "_operation_snapshot", lambda _id: operation)

    with pytest.raises(HTTPException) as exc:
        call_endpoint(BackgroundTasks())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "REPORT_ALREADY_GENERATED"
