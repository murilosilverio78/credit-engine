import time

import structlog

from app.workers.base import _execute_snapshot_write


_CACHE_TTL = 60
_cache = {"params": None, "ts": 0.0}
logger = structlog.get_logger()

DEFAULT_PARAMS = {
    "ocr_max_pages": 15.0,
    "timeout_total_seconds": 300.0,
    "llm_max_chars": 60_000.0,
}


def _load_from_db() -> dict[str, float] | None:
    from app.core.database import supabase

    try:
        result = _execute_snapshot_write(
            "configuration",
            "contrato_extracao",
            "load_contract_extraction_parameters",
            lambda: supabase.table("contract_extraction_parameters")
            .select("key,value")
            .execute(),
        )
    except Exception as exc:
        logger.warning(
            "contract_extraction_params.fallback",
            error=str(exc),
        )
        return None

    params = dict(DEFAULT_PARAMS)
    for row in result.data or []:
        key = row.get("key")
        if key in DEFAULT_PARAMS:
            params[key] = float(row["value"])
    return params


def get_contract_extraction_config(force_reload: bool = False) -> dict[str, float]:
    now = time.time()
    if (
        force_reload
        or _cache["params"] is None
        or (now - _cache["ts"]) > _CACHE_TTL
    ):
        loaded = _load_from_db()
        params = loaded or _cache["params"] or dict(DEFAULT_PARAMS)
        _cache.update({"params": params, "ts": now})
    return dict(_cache["params"])


def invalidate_cache() -> None:
    _cache.update({"params": None, "ts": 0.0})
