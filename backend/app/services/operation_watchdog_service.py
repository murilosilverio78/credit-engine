"""Detect and close credit analyses abandoned with a stale heartbeat."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.workers.base import _execute_snapshot_write


logger = structlog.get_logger()
PARAMETER_KEY = "watchdog_heartbeat_timeout_minutos"
DEFAULT_HEARTBEAT_TIMEOUT_MINUTES = 15.0
WATCHED_OPERATION_STATUSES = ("pending", "processing")
_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, float | None] = {"value": None, "ts": 0.0}


def _execute_db(action: str, request):
    return _execute_snapshot_write("watchdog", "operation_watchdog", action, request)


def _load_timeout_minutes(db: Any) -> float | None:
    try:
        result = _execute_db(
            "load_timeout_parameter",
            lambda: db.table("eligibility_parameters")
            .select("value")
            .eq("key", PARAMETER_KEY)
            .maybe_single()
            .execute(),
        )
    except Exception as exc:
        logger.warning("operation_watchdog.parameter_fallback", error=str(exc))
        return None

    row = result.data or {}
    try:
        value = float(row.get("value"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def get_watchdog_timeout_minutes(
    *,
    force_reload: bool = False,
    db: Any | None = None,
) -> float:
    if db is None:
        from app.core.database import supabase

        db = supabase

    now = time.time()
    if (
        force_reload
        or _cache["value"] is None
        or now - float(_cache["ts"] or 0.0) > _CACHE_TTL_SECONDS
    ):
        loaded = _load_timeout_minutes(db)
        value = loaded or _cache["value"] or DEFAULT_HEARTBEAT_TIMEOUT_MINUTES
        _cache.update({"value": float(value), "ts": now})
    return float(_cache["value"] or DEFAULT_HEARTBEAT_TIMEOUT_MINUTES)


def invalidate_watchdog_config_cache() -> None:
    _cache.update({"value": None, "ts": 0.0})


def _running_components(db: Any, operation_id: str) -> list[str]:
    result = _execute_db(
        "load_running_components",
        lambda: db.table("component_snapshots")
        .select("component")
        .eq("operation_id", operation_id)
        .eq("status", "running")
        .execute(),
    )
    return sorted(
        str(row["component"])
        for row in (result.data or [])
        if row.get("component")
    )


def _stage_label(components: list[str]) -> str:
    if not components:
        return "pipeline"
    if len(components) == 1:
        return components[0]
    return "componentes " + ",".join(components)


def _load_stale_operations(db: Any, cutoff_iso: str) -> list[dict[str, Any]]:
    stale_heartbeat = _execute_db(
        "load_stale_operations_with_heartbeat",
        lambda: db.table("operations")
        .select("id,cotacao_id,status,heartbeat_at,created_at")
        .in_("status", list(WATCHED_OPERATION_STATUSES))
        .lt("heartbeat_at", cutoff_iso)
        .execute(),
    )
    missing_heartbeat = _execute_db(
        "load_stale_operations_without_heartbeat",
        lambda: db.table("operations")
        .select("id,cotacao_id,status,heartbeat_at,created_at")
        .in_("status", list(WATCHED_OPERATION_STATUSES))
        .is_("heartbeat_at", "null")
        .lt("created_at", cutoff_iso)
        .execute(),
    )
    candidates = {
        str(row["id"]): row
        for row in [
            *(stale_heartbeat.data or []),
            *(missing_heartbeat.data or []),
        ]
        if row.get("id")
    }
    return list(candidates.values())


def _claim_stale_operation(
    db: Any,
    candidate: dict[str, Any],
    cutoff_iso: str,
    message: str,
):
    operation_id = str(candidate["id"])

    def request():
        query = (
            db.table("operations")
            .update({"status": "failed", "error_message": message})
            .eq("id", operation_id)
            .in_("status", list(WATCHED_OPERATION_STATUSES))
        )
        if candidate.get("heartbeat_at") is None:
            query = query.is_("heartbeat_at", "null").lt("created_at", cutoff_iso)
        else:
            query = query.lt("heartbeat_at", cutoff_iso)
        return query.execute()

    return _execute_db("mark_operation_failed", request)


def run_operation_watchdog(
    *,
    timeout_minutes: float | None = None,
    now: datetime | None = None,
    db: Any | None = None,
) -> dict[str, Any]:
    """Mark stale pending or processing operations without starting a retry."""
    if db is None:
        from app.core.database import supabase

        db = supabase

    if timeout_minutes is None:
        timeout_minutes = get_watchdog_timeout_minutes(db=db)
    timeout_minutes = float(timeout_minutes)
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be greater than zero")

    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    cutoff = reference_time - timedelta(minutes=timeout_minutes)
    cutoff_iso = cutoff.isoformat()

    summary: dict[str, Any] = {
        "status": "completed",
        "timeout_minutos": timeout_minutes,
        "candidatas": 0,
        "marcadas": 0,
        "corridas_ignoradas": 0,
        "snapshots_marcados": 0,
        "cotacoes_marcadas": 0,
        "erros": 0,
    }

    try:
        candidates = _load_stale_operations(db, cutoff_iso)
    except Exception as exc:
        logger.error("operation_watchdog.load_failed", error=str(exc))
        return {**summary, "status": "failed", "erros": 1, "error": str(exc)}

    summary["candidatas"] = len(candidates)

    for candidate in candidates:
        operation_id = str(candidate.get("id") or "")
        if not operation_id:
            summary["erros"] += 1
            continue

        try:
            components = _running_components(db, operation_id)
            message = f"watchdog: heartbeat vencido em {_stage_label(components)}"
            claimed = _claim_stale_operation(db, candidate, cutoff_iso, message)
            if not claimed.data:
                summary["corridas_ignoradas"] += 1
                logger.info(
                    "operation_watchdog.race_skipped",
                    operation_id=operation_id,
                )
                continue

            snapshots = _execute_db(
                "mark_running_snapshots_failed",
                lambda: db.table("component_snapshots")
                .update(
                    {
                        "status": "failed",
                        "error_message": message,
                        "completed_at": reference_time.isoformat(),
                    }
                )
                .eq("operation_id", operation_id)
                .eq("status", "running")
                .execute(),
            )
            quote = _execute_db(
                "mark_quote_analysis_error",
                lambda: db.table("cotacoes_broadfactor")
                .update({"status_ingestao": "ERRO_ANALISE"})
                .eq("operation_id", operation_id)
                .execute(),
            )

            summary["marcadas"] += 1
            summary["snapshots_marcados"] += len(snapshots.data or [])
            summary["cotacoes_marcadas"] += len(quote.data or [])
            logger.warning(
                "operation_watchdog.operation_failed",
                operation_id=operation_id,
                components=components,
                heartbeat_at=candidate.get("heartbeat_at"),
                timeout_minutes=timeout_minutes,
            )
        except Exception as exc:
            summary["erros"] += 1
            logger.error(
                "operation_watchdog.operation_update_failed",
                operation_id=operation_id,
                error=str(exc),
            )

    if summary["erros"]:
        summary["status"] = "partial_failure"
    logger.info("operation_watchdog.completed", **summary)
    return summary


__all__ = [
    "DEFAULT_HEARTBEAT_TIMEOUT_MINUTES",
    "get_watchdog_timeout_minutes",
    "invalidate_watchdog_config_cache",
    "run_operation_watchdog",
]
