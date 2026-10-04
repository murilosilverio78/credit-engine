"""Small PostgREST 0.16.11-compatible fake with production-schema checks.

For tables captured from production, every literal column used by a query is
validated before execution. Tables outside the fixture intentionally stay
open, so focused tests may model temporary views without inventing schema.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


_SCHEMA_PATH = Path(__file__).parents[1] / "fixtures" / "schema_producao.json"
SCHEMA: dict[str, set[str]] = {
    table: set(columns)
    for table, columns in json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")).items()
}


class APIError(Exception):
    """The subset of a PostgREST error used by schema-contract tests."""

    def __init__(self, table: str, column: str):
        self.code = "42703"
        self.message = f"column {table}.{column} does not exist"
        super().__init__(self.message)


def _column_name(value: str) -> str | None:
    value = value.strip()
    if not value or value == "*" or "(" in value:
        return None
    value = value.split(":", 1)[0].split("::", 1)[0].strip()
    return value or None


class Query:
    def __init__(self, table: str, rows: list[dict[str, Any]] | None = None):
        self.table = table
        self.rows = rows if rows is not None else []
        self.filters: list[tuple[str, str, Any]] = []
        self._maybe_single = False
        self._limit: int | None = None

    def _validate(self, value: Any) -> None:
        if self.table not in SCHEMA or not isinstance(value, str):
            return
        column = _column_name(value)
        if column is not None and column not in SCHEMA[self.table]:
            raise APIError(self.table, column)

    def _validate_payload(self, payload: Any) -> None:
        if self.table not in SCHEMA:
            return
        values = payload if isinstance(payload, list) else [payload]
        for value in values:
            if isinstance(value, dict):
                for key in value:
                    self._validate(str(key))

    def select(self, *args: Any, **_kwargs: Any) -> "Query":
        for arg in args:
            if isinstance(arg, str):
                for column in arg.split(","):
                    self._validate(column)
        return self

    def _filter(self, operator: str, key: str, value: Any) -> "Query":
        self._validate(key)
        self.filters.append((operator, key, value))
        return self

    def eq(self, key: str, value: Any) -> "Query":
        return self._filter("eq", key, value)

    def neq(self, key: str, value: Any) -> "Query":
        return self._filter("neq", key, value)

    def in_(self, key: str, value: Any) -> "Query":
        return self._filter("in", key, value)

    def gt(self, key: str, value: Any) -> "Query":
        return self._filter("gt", key, value)

    def lt(self, key: str, value: Any) -> "Query":
        return self._filter("lt", key, value)

    def maybe_single(self) -> "Query":
        self._maybe_single = True
        return self

    def limit(self, value: int) -> "Query":
        self._limit = value
        return self

    def order(self, key: str, *_args: Any, **_kwargs: Any) -> "Query":
        self._validate(key)
        return self

    def insert(self, payload: Any) -> "Query":
        self._validate_payload(payload)
        values = payload if isinstance(payload, list) else [payload]
        self.rows.extend(value.copy() for value in values if isinstance(value, dict))
        return self

    def upsert(self, payload: Any, **_kwargs: Any) -> "Query":
        return self.insert(payload)

    def update(self, payload: dict[str, Any]) -> "Query":
        self._validate_payload(payload)
        for row in self._filtered_rows():
            row.update(payload)
        return self

    def _filtered_rows(self) -> list[dict[str, Any]]:
        def matches(row: dict[str, Any]) -> bool:
            for operator, key, value in self.filters:
                actual = row.get(key)
                if operator == "eq" and actual != value:
                    return False
                if operator == "neq" and actual == value:
                    return False
                if operator == "in" and actual not in value:
                    return False
                if operator == "gt" and not (actual is not None and actual > value):
                    return False
                if operator == "lt" and not (actual is not None and actual < value):
                    return False
            return True
        return [row for row in self.rows if matches(row)]

    def execute(self):
        rows = [row.copy() for row in self._filtered_rows()]
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._maybe_single:
            return SimpleNamespace(data=rows[0]) if rows else None
        return SimpleNamespace(data=rows)


class Rpc:
    def __init__(self, data: Any = None, error: Exception | None = None):
        self.data = data if data is not None else []
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


class Postgrest:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None, rpc_data: Any = None):
        self.tables = tables or {}
        self.rpc_data = rpc_data if rpc_data is not None else []
        self.rpc_calls: list[tuple[str, dict[str, Any] | None]] = []

    def table(self, name: str) -> Query:
        return Query(name, self.tables.setdefault(name, []))

    def rpc(self, name: str, params: dict[str, Any] | None = None) -> Rpc:
        self.rpc_calls.append((name, params))
        return Rpc(self.rpc_data)
