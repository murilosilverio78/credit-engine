"""Detect and close credit analyses abandoned with a stale heartbeat."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.services.analysis_runtime import enqueue_analysis
from app.services.funil_qualificacao_service import (
    atualizar_estagio_pos_fase2,
    decidir_status_pos_fase2,
)
from app.workers.base import _execute_snapshot_write


logger = structlog.get_logger()
PARAMETER_KEY = "watchdog_heartbeat_timeout_minutos"
DEFAULT_HEARTBEAT_TIMEOUT_MINUTES = 15.0
DEFAULT_MAX_RETOMADAS = 1
DEFAULT_COMPONENT_ORPHAN_MINUTES = 10.0
WATCHED_OPERATION_STATUSES = ("pending", "processing")
RECOLLECTION_OPERATION_STATUSES = ("aguardando_relatorio", "reprovada_triagem")
ORPHAN_ERROR_MESSAGE = "recoleta interrompida (container reiniciado)"
_BASE_OPERATION_FIELDS = "id,cotacao_id,status,heartbeat_at,created_at"
_RESUME_OPERATION_FIELDS = f"{_BASE_OPERATION_FIELDS},retomadas_count"
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


def _is_missing_resume_column_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "retomadas_count" in message and any(
        marker in message
        for marker in (
            "does not exist",
            "could not find",
            "pgrst204",
            "42703",
        )
    )


def _query_stale_operations(
    db: Any,
    cutoff_iso: str,
    fields: str,
) -> list[dict[str, Any]]:
    stale_heartbeat = _execute_db(
        "load_stale_operations_with_heartbeat",
        lambda: db.table("operations")
        .select(fields)
        .in_("status", list(WATCHED_OPERATION_STATUSES))
        .lt("heartbeat_at", cutoff_iso)
        .execute(),
    )
    missing_heartbeat = _execute_db(
        "load_stale_operations_without_heartbeat",
        lambda: db.table("operations")
        .select(fields)
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


def _load_stale_operations(
    db: Any,
    cutoff_iso: str,
) -> tuple[list[dict[str, Any]], bool]:
    try:
        return (
            _query_stale_operations(db, cutoff_iso, _RESUME_OPERATION_FIELDS),
            True,
        )
    except Exception as exc:
        if not _is_missing_resume_column_error(exc):
            raise
        logger.warning(
            "operation_watchdog.resume_column_unavailable",
            error=str(exc),
        )
        return _query_stale_operations(db, cutoff_iso, _BASE_OPERATION_FIELDS), False


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


def _claim_stale_operation_for_resume(
    db: Any,
    candidate: dict[str, Any],
    cutoff_iso: str,
    reference_iso: str,
    current_attempt: int,
):
    operation_id = str(candidate["id"])

    def request():
        query = (
            db.table("operations")
            .update(
                {
                    "status": "pending",
                    "heartbeat_at": reference_iso,
                    "error_message": None,
                    "completed_at": None,
                    "retomadas_count": current_attempt + 1,
                }
            )
            .eq("id", operation_id)
            .in_("status", list(WATCHED_OPERATION_STATUSES))
            .eq("retomadas_count", current_attempt)
        )
        if candidate.get("heartbeat_at") is None:
            query = query.is_("heartbeat_at", "null").lt("created_at", cutoff_iso)
        else:
            query = query.lt("heartbeat_at", cutoff_iso)
        return query.execute()

    return _execute_db("claim_operation_for_resume", request)


def _record_audit(
    db: Any,
    operation_id: str,
    action: str,
    payload: dict[str, Any],
    *,
    previous_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {
        "operation_id": operation_id,
        "action": action,
        "actor_type": "system",
        "payload": payload,
    }
    if previous_value is not None:
        data["previous_value"] = previous_value
    if new_value is not None:
        data["new_value"] = new_value
    _execute_db(
        "record_orphan_component_audit",
        lambda: db.table("audit_trail").insert(data).execute(),
    )


def _reevaluate_recollection_operation(
    db: Any,
    operation_id: str,
    operation: dict[str, Any],
) -> None:
    if (
        not operation.get("cotacao_id")
        or operation.get("status") not in RECOLLECTION_OPERATION_STATUSES
        or operation.get("pendencia_coleta") is not True
    ):
        return

    _stage, reasons = atualizar_estagio_pos_fase2(
        str(operation["cotacao_id"]), operation_id
    )
    outcome = decidir_status_pos_fase2(reasons)
    current_result = _execute_db(
        "reload_operation_after_orphan_recovery",
        lambda: db.table("operations")
        .select("id,status,pendencia_coleta")
        .eq("id", operation_id)
        .execute(),
    )
    current = (current_result.data or [None])[0] or {}
    previous_status = current.get("status")
    if (
        previous_status not in RECOLLECTION_OPERATION_STATUSES
        or current.get("pendencia_coleta") is not True
    ):
        return

    next_status = outcome["status"]
    updated = _execute_db(
        "update_operation_after_orphan_recovery",
        lambda: db.table("operations")
        .update(
            {
                "status": next_status,
                "pendencia_coleta": outcome["pendencia_coleta"],
            }
        )
        .eq("id", operation_id)
        .eq("status", previous_status)
        .eq("pendencia_coleta", True)
        .execute(),
    )
    if updated.data and next_status != previous_status:
        _record_audit(
            db,
            operation_id,
            "operation_status_changed",
            {
                "contexto": "watchdog_orfao",
                "motivos_classificados": outcome["motivos_classificados"],
            },
            previous_value={"status": previous_status},
            new_value={"status": next_status},
        )


def _recover_orphan_components(
    db: Any,
    *,
    reference_time: datetime,
    orphan_minutes: float,
    summary: dict[str, Any],
) -> None:
    cutoff_iso = (reference_time - timedelta(minutes=orphan_minutes)).isoformat()
    snapshots_result = _execute_db(
        "load_orphan_components",
        lambda: db.table("component_snapshots")
        .select("operation_id,component,status,started_at,completed_at,parsed_result")
        .eq("status", "running")
        .lt("started_at", cutoff_iso)
        .execute(),
    )
    snapshots = snapshots_result.data or []
    operation_ids = sorted(
        {str(row.get("operation_id")) for row in snapshots if row.get("operation_id")}
    )
    if not operation_ids:
        return

    operations_result = _execute_db(
        "load_orphan_component_operations",
        lambda: db.table("operations")
        .select("id,cotacao_id,status,pendencia_coleta")
        .in_("id", operation_ids)
        .execute(),
    )
    operations = {
        str(row["id"]): row
        for row in (operations_result.data or [])
        if row.get("id")
    }
    eligible = [
        snapshot
        for snapshot in snapshots
        if str(snapshot.get("operation_id")) in operations
        and operations[str(snapshot.get("operation_id"))].get("status")
        not in WATCHED_OPERATION_STATUSES
    ]
    summary["componentes_orfaos"] += len(eligible)
    affected_operations: set[str] = set()

    for snapshot in eligible:
        operation_id = str(snapshot["operation_id"])
        restored = (
            snapshot.get("parsed_result") is not None
            and snapshot.get("completed_at") is not None
        )
        update = {
            "status": "completed" if restored else "failed",
            "error_message": ORPHAN_ERROR_MESSAGE,
        }
        if not restored:
            update["completed_at"] = reference_time.isoformat()
        claimed = _execute_db(
            "recover_orphan_component",
            lambda snapshot=snapshot, update=update: db.table("component_snapshots")
            .update(update)
            .eq("operation_id", snapshot["operation_id"])
            .eq("component", snapshot["component"])
            .eq("status", "running")
            .eq("started_at", snapshot["started_at"])
            .execute(),
        )
        if not claimed.data:
            summary["corridas_ignoradas"] += 1
            continue

        counter = "restaurados" if restored else "marcados_failed"
        summary[counter] += 1
        affected_operations.add(operation_id)
        _record_audit(
            db,
            operation_id,
            "component_refresh_failed",
            {
                "component": snapshot["component"],
                "motivo": "orfao_watchdog",
                "restaurado": restored,
            },
        )

    for operation_id in affected_operations:
        _reevaluate_recollection_operation(db, operation_id, operations[operation_id])


def run_operation_watchdog(
    *,
    timeout_minutes: float | None = None,
    max_retomadas: int | None = None,
    orphan_minutes: float | None = None,
    now: datetime | None = None,
    db: Any | None = None,
) -> dict[str, Any]:
    """Resume stale operations once, then close them after the retry limit."""
    if db is None:
        from app.core.database import supabase

        db = supabase

    if timeout_minutes is None:
        timeout_minutes = get_watchdog_timeout_minutes(db=db)
    timeout_minutes = float(timeout_minutes)
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be greater than zero")
    if max_retomadas is None:
        try:
            from app.core.config import settings

            max_retomadas = settings.WATCHDOG_MAX_RETOMADAS
        except Exception as exc:
            logger.warning(
                "operation_watchdog.max_retomadas_fallback",
                error=str(exc),
            )
            max_retomadas = DEFAULT_MAX_RETOMADAS
    max_retomadas = max(int(max_retomadas), 0)

    if orphan_minutes is None:
        try:
            from app.core.config import settings

            orphan_minutes = settings.COMPONENTE_ORFAO_MINUTOS
        except Exception as exc:
            logger.warning(
                "operation_watchdog.orphan_timeout_fallback",
                error=str(exc),
            )
            orphan_minutes = DEFAULT_COMPONENT_ORPHAN_MINUTES
    orphan_minutes = float(orphan_minutes)
    if orphan_minutes <= 0:
        raise ValueError("orphan_minutes must be greater than zero")

    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    cutoff = reference_time - timedelta(minutes=timeout_minutes)
    cutoff_iso = cutoff.isoformat()
    reference_iso = reference_time.isoformat()

    summary: dict[str, Any] = {
        "status": "completed",
        "timeout_minutos": timeout_minutes,
        "candidatas": 0,
        "marcadas": 0,
        "corridas_ignoradas": 0,
        "snapshots_marcados": 0,
        "cotacoes_marcadas": 0,
        "erros": 0,
        "retomadas": 0,
        "retomadas_esgotadas": 0,
        "componentes_orfaos": 0,
        "restaurados": 0,
        "marcados_failed": 0,
    }

    try:
        _recover_orphan_components(
            db,
            reference_time=reference_time,
            orphan_minutes=orphan_minutes,
            summary=summary,
        )
    except Exception as exc:
        summary["erros"] += 1
        logger.error("operation_watchdog.orphan_recovery_failed", error=str(exc))

    try:
        candidates, resume_column_available = _load_stale_operations(
            db,
            cutoff_iso,
        )
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
            current_resume = int(candidate.get("retomadas_count") or 0)
            should_resume = (
                resume_column_available and current_resume < max_retomadas
            )

            if should_resume:
                claimed = _claim_stale_operation_for_resume(
                    db,
                    candidate,
                    cutoff_iso,
                    reference_iso,
                    current_resume,
                )
                if not claimed.data:
                    summary["corridas_ignoradas"] += 1
                    logger.info(
                        "operation_watchdog.race_skipped",
                        operation_id=operation_id,
                    )
                    continue

                try:
                    snapshots = _execute_db(
                        "reset_running_snapshots_for_resume",
                        lambda: db.table("component_snapshots")
                        .update(
                            {
                                "status": "pending",
                                "error_message": None,
                                "started_at": None,
                                "completed_at": None,
                            }
                        )
                        .eq("operation_id", operation_id)
                        .eq("status", "running")
                        .execute(),
                    )
                    if not enqueue_analysis(operation_id):
                        raise RuntimeError(
                            "event loop indisponivel para reenfileirar analise"
                        )
                except Exception as resume_exc:
                    failure_message = f"{message}; retomada falhou: {resume_exc}"
                    failed = _execute_db(
                        "mark_failed_after_resume_error",
                        lambda: db.table("operations")
                        .update(
                            {
                                "status": "failed",
                                "error_message": failure_message,
                            }
                        )
                        .eq("id", operation_id)
                        .eq("status", "pending")
                        .eq("heartbeat_at", reference_iso)
                        .eq("retomadas_count", current_resume + 1)
                        .execute(),
                    )
                    failed_snapshots = _execute_db(
                        "mark_snapshots_failed_after_resume_error",
                        lambda: db.table("component_snapshots")
                        .update(
                            {
                                "status": "failed",
                                "error_message": failure_message,
                                "completed_at": reference_iso,
                            }
                        )
                        .eq("operation_id", operation_id)
                        .in_("status", ["pending", "running"])
                        .execute(),
                    )
                    quote = _execute_db(
                        "mark_quote_failed_after_resume_error",
                        lambda: db.table("cotacoes_broadfactor")
                        .update({"status_ingestao": "ERRO_ANALISE"})
                        .eq("operation_id", operation_id)
                        .execute(),
                    )
                    summary["erros"] += 1
                    summary["marcadas"] += len(failed.data or [])
                    summary["snapshots_marcados"] += len(
                        failed_snapshots.data or []
                    )
                    summary["cotacoes_marcadas"] += len(quote.data or [])
                    logger.error(
                        "operation_watchdog.retomada_failed",
                        operation_id=operation_id,
                        error=str(resume_exc),
                    )
                    continue

                attempt = current_resume + 1
                summary["retomadas"] += 1
                logger.warning(
                    "operation_watchdog.retomada",
                    operation_id=operation_id,
                    componentes=components,
                    tentativa=attempt,
                    snapshots_resetados=len(snapshots.data or []),
                )
                continue

            claimed = _claim_stale_operation(db, candidate, cutoff_iso, message)
            if not claimed.data:
                summary["corridas_ignoradas"] += 1
                logger.info(
                    "operation_watchdog.race_skipped",
                    operation_id=operation_id,
                )
                continue

            if resume_column_available and current_resume >= max_retomadas:
                summary["retomadas_esgotadas"] += 1
                logger.warning(
                    "operation_watchdog.retomada_esgotada",
                    operation_id=operation_id,
                    tentativa=current_resume,
                    max_retomadas=max_retomadas,
                )

            snapshots = _execute_db(
                "mark_running_snapshots_failed",
                lambda: db.table("component_snapshots")
                .update(
                    {
                        "status": "failed",
                        "error_message": message,
                        "completed_at": reference_iso,
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
