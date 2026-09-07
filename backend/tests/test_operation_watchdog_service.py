from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.services.operation_watchdog_service import (
    DEFAULT_HEARTBEAT_TIMEOUT_MINUTES,
    get_watchdog_timeout_minutes,
    invalidate_watchdog_config_cache,
    run_operation_watchdog,
)


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class Query:
    def __init__(self, db: "FakeSupabase", table: str):
        self.db = db
        self.table_name = table
        self.action = "select"
        self.payload: dict[str, Any] = {}
        self.filters: list[tuple[str, str, Any]] = []

    def select(self, _columns: str):
        return self

    def update(self, payload: dict[str, Any]):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, field: str, value: Any):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field: str, values: list[Any]):
        self.filters.append(("in", field, values))
        return self

    def is_(self, field: str, value: Any):
        self.filters.append(("is", field, value))
        return self

    def lt(self, field: str, value: Any):
        self.filters.append(("lt", field, value))
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for operator, field, value in self.filters:
            current = row.get(field)
            if operator == "eq" and current != value:
                return False
            if operator == "in" and current not in value:
                return False
            if operator == "is" and value == "null" and current is not None:
                return False
            if operator == "lt" and (current is None or current >= value):
                return False
        return True

    def execute(self):
        if self.action == "update" and self.db.before_update is not None:
            self.db.before_update(self)
        matched = [row for row in self.db.tables[self.table_name] if self._matches(row)]
        if self.action == "update":
            for row in matched:
                row.update(deepcopy(self.payload))
        return Result(deepcopy(matched))


class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        self.tables = deepcopy(tables)
        self.before_update: Callable[[Query], None] | None = None

    def table(self, name: str):
        return Query(self, name)


def _database(
    *,
    heartbeat_at: datetime | None,
    created_at: datetime | None = None,
    status: str = "processing",
) -> FakeSupabase:
    created_at = created_at or NOW - timedelta(hours=1)
    return FakeSupabase(
        {
            "operations": [
                {
                    "id": "op-1",
                    "cotacao_id": "C-1",
                    "status": status,
                    "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
                    "created_at": created_at.isoformat(),
                    "error_message": None,
                }
            ],
            "component_snapshots": [
                {
                    "operation_id": "op-1",
                    "component": "web_research",
                    "status": "running",
                    "error_message": None,
                    "completed_at": None,
                },
                {
                    "operation_id": "op-1",
                    "component": "contratos",
                    "status": "completed",
                    "error_message": None,
                },
            ],
            "cotacoes_broadfactor": [
                {
                    "cotacao_id": "C-1",
                    "operation_id": "op-1",
                    "status_ingestao": "OPERACAO_CRIADA",
                }
            ],
        }
    )


def test_stale_heartbeat_marks_operation_snapshot_and_quote_failed():
    db = _database(heartbeat_at=NOW - timedelta(minutes=16))

    result = run_operation_watchdog(timeout_minutes=15, now=NOW, db=db)

    assert result == {
        "status": "completed",
        "timeout_minutos": 15.0,
        "candidatas": 1,
        "marcadas": 1,
        "corridas_ignoradas": 0,
        "snapshots_marcados": 1,
        "cotacoes_marcadas": 1,
        "erros": 0,
    }
    operation = db.tables["operations"][0]
    assert operation["status"] == "failed"
    assert operation["error_message"] == (
        "watchdog: heartbeat vencido em web_research"
    )
    assert db.tables["component_snapshots"][0]["status"] == "failed"
    assert db.tables["component_snapshots"][1]["status"] == "completed"
    assert db.tables["cotacoes_broadfactor"][0]["status_ingestao"] == "ERRO_ANALISE"


def test_recent_heartbeat_is_not_marked():
    db = _database(heartbeat_at=NOW - timedelta(minutes=14))

    result = run_operation_watchdog(timeout_minutes=15, now=NOW, db=db)

    assert result["candidatas"] == 0
    assert result["marcadas"] == 0
    assert db.tables["operations"][0]["status"] == "processing"
    assert db.tables["component_snapshots"][0]["status"] == "running"


def test_stale_pending_operation_without_heartbeat_uses_created_at():
    db = _database(
        status="pending",
        heartbeat_at=None,
        created_at=NOW - timedelta(minutes=16),
    )
    db.tables["component_snapshots"][0]["status"] = "pending"

    result = run_operation_watchdog(timeout_minutes=15, now=NOW, db=db)

    assert result["candidatas"] == 1
    assert result["marcadas"] == 1
    assert db.tables["operations"][0]["status"] == "failed"
    assert db.tables["operations"][0]["error_message"] == (
        "watchdog: heartbeat vencido em pipeline"
    )
    assert db.tables["component_snapshots"][0]["status"] == "pending"


def test_recent_pending_operation_without_heartbeat_is_not_marked():
    db = _database(
        status="pending",
        heartbeat_at=None,
        created_at=NOW - timedelta(minutes=14),
    )

    result = run_operation_watchdog(timeout_minutes=15, now=NOW, db=db)

    assert result["candidatas"] == 0
    assert result["marcadas"] == 0
    assert db.tables["operations"][0]["status"] == "pending"


def test_completed_operation_between_read_and_write_is_not_overwritten():
    db = _database(heartbeat_at=NOW - timedelta(minutes=16))

    def complete_before_claim(query: Query) -> None:
        if query.table_name == "operations":
            db.tables["operations"][0]["status"] = "completed"
            db.before_update = None

    db.before_update = complete_before_claim

    result = run_operation_watchdog(timeout_minutes=15, now=NOW, db=db)

    assert result["candidatas"] == 1
    assert result["marcadas"] == 0
    assert result["corridas_ignoradas"] == 1
    assert db.tables["operations"][0]["status"] == "completed"
    assert db.tables["component_snapshots"][0]["status"] == "running"
    assert db.tables["cotacoes_broadfactor"][0]["status_ingestao"] == (
        "OPERACAO_CRIADA"
    )


def test_watchdog_is_idempotent():
    db = _database(heartbeat_at=NOW - timedelta(minutes=16))

    first = run_operation_watchdog(timeout_minutes=15, now=NOW, db=db)
    state_after_first = deepcopy(db.tables)
    second = run_operation_watchdog(timeout_minutes=15, now=NOW, db=db)

    assert first["marcadas"] == 1
    assert second["candidatas"] == 0
    assert second["marcadas"] == 0
    assert db.tables == state_after_first


def test_timeout_parameter_falls_back_when_table_is_unavailable():
    class UnavailableDatabase:
        def table(self, _name: str):
            raise RuntimeError("relation does not exist")

    invalidate_watchdog_config_cache()
    try:
        value = get_watchdog_timeout_minutes(
            force_reload=True,
            db=UnavailableDatabase(),
        )
    finally:
        invalidate_watchdog_config_cache()

    assert value == DEFAULT_HEARTBEAT_TIMEOUT_MINUTES == 15.0
