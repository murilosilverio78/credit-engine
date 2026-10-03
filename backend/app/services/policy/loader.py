"""Carregamento curto de parametros da politica; o avaliador nunca faz I/O."""
from __future__ import annotations

import time
from typing import Any

_CACHE_TTL = 15.0
_cache: dict[tuple[str | None, str], tuple[float, dict[str, Any]]] = {}


def _rows(query) -> list[dict[str, Any]]:
    result = query.execute()
    return list((result.data or []) if result else [])


def load_policy(*, database=None, policy_version_id: str | None = None, status: str = "SOMBRA") -> dict[str, Any]:
    """Load one policy by id or status; absence is an explicit error."""
    key = (policy_version_id, status)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL:
        return cached[1]
    if database is None:
        from app.core.database import supabase
        database = supabase
    query = database.table("policy_versions").select("id,versao,status,descricao")
    query = query.eq("id", policy_version_id) if policy_version_id else query.eq("status", status)
    rows = _rows(query.limit(1))
    if not rows:
        raise LookupError("politica nao encontrada")
    version = rows[0]
    params = _rows(database.table("policy_parameters").select("chave,valor").eq("policy_version_id", version["id"]))
    rules = _rows(database.table("policy_rules").select("*").eq("policy_version_id", version["id"]).order("ordem"))
    value = {"version": version, "parametros": {row["chave"]: row["valor"] for row in params}, "regras": rules}
    _cache[key] = (time.monotonic(), value)
    return value


def clear_policy_cache() -> None:
    _cache.clear()
