"""Small PostgREST 0.16.11-compatible fake for isolated service tests.

In particular, ``maybe_single().execute()`` returns ``None`` when the query
has no rows.  A normal ``limit(1).execute()`` instead returns a response whose
``data`` is an empty list.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class Query:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = list(rows or [])
        self.filters: list[tuple[str, Any]] = []
        self._maybe_single = False
        self._limit: int | None = None

    def select(self, *_args: Any, **_kwargs: Any) -> "Query":
        return self

    def eq(self, key: str, value: Any) -> "Query":
        self.filters.append((key, value))
        return self

    def maybe_single(self) -> "Query":
        self._maybe_single = True
        return self

    def limit(self, value: int) -> "Query":
        self._limit = value
        return self

    def execute(self):
        rows = [
            row.copy()
            for row in self.rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
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
        return Query(self.tables.get(name, []))

    def rpc(self, name: str, params: dict[str, Any] | None = None) -> Rpc:
        self.rpc_calls.append((name, params))
        return Rpc(self.rpc_data)
