"""Catalog lookup with the same short-lived cache pattern as parameters."""
from __future__ import annotations

import time
from typing import Any

import structlog

logger = structlog.get_logger()
_CACHE_TTL = 60.0
_cache: dict[str, Any] = {"items": None, "ts": 0.0}


def get_catalog(*, database=None, force_reload: bool = False) -> dict[str, dict[str, Any]] | None:
    from app.core.database import supabase

    now = time.time()
    if not force_reload and _cache["items"] is not None and now - _cache["ts"] <= _CACHE_TTL:
        return dict(_cache["items"])
    db = database or supabase
    try:
        result = db.table("finding_catalog").select("codigo,versao,escopo,tipo_valor,ativo").eq("ativo", True).execute()
        rows = result.data or []
    except Exception as exc:
        logger.warning("findings.catalog_unavailable", error=str(exc))
        return None
    catalog = {f"{row['codigo']}:{row['versao']}": row for row in rows}
    _cache.update({"items": catalog, "ts": now})
    return dict(catalog)


def invalidate_catalog_cache() -> None:
    _cache.update({"items": None, "ts": 0.0})


def validate_value(achado, definition: dict[str, Any]) -> bool:
    value_type = definition.get("tipo_valor")
    value = achado.valor
    if value is None:
        return achado.estado.value == "NAO_VERIFICADO"
    if value_type == "BOOLEANO":
        return isinstance(value, bool)
    if value_type == "NUMERO":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "DATA":
        return isinstance(value, str)
    if value_type == "ENUM":
        return isinstance(value, str)
    if value_type == "OBJETO":
        return isinstance(value, (dict, list))
    return False
