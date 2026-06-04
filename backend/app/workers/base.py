"""
BaseComponentTask: classe base para todos os workers de consulta.
Gerencia: snapshot lifecycle, cache, auditoria, erro handling.
"""
import time
from typing import Callable, TypeVar

import httpx
from app.utils.encoding import fix_dict_encoding
import structlog

logger = structlog.get_logger()

T = TypeVar("T")
SNAPSHOT_WRITE_RETRY_DELAYS = (0.2, 0.5)


def _is_transient_connection_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (httpx.RemoteProtocolError, httpx.ConnectError)):
            return True
        if "Server disconnected" in str(current):
            return True
        current = current.__cause__ or current.__context__

    return False


def _execute_snapshot_write(
    operation_id: str,
    component: str,
    action: str,
    write: Callable[[], T],
) -> T:
    for attempt in range(len(SNAPSHOT_WRITE_RETRY_DELAYS) + 1):
        try:
            return write()
        except Exception as exc:
            should_retry = (
                _is_transient_connection_error(exc)
                and attempt < len(SNAPSHOT_WRITE_RETRY_DELAYS)
            )
            if not should_retry:
                raise

            delay = SNAPSHOT_WRITE_RETRY_DELAYS[attempt]
            logger.warning(
                "snapshot.write_retry",
                operation_id=operation_id,
                component=component,
                action=action,
                attempt=attempt + 1,
                delay_seconds=delay,
                error=str(exc),
            )
            time.sleep(delay)

    raise RuntimeError("snapshot write retry loop exhausted")


class BaseComponentTask:
    """
    Herdar desta classe garante que todo componente:
    - Marca snapshot como 'running' ao iniciar
    - Persiste resultado raw + parsed ao completar
    - Marca snapshot como 'failed' em caso de erro
    - Registra duração e custo
    - Verifica cache antes de executar
    - Registra no audit trail
    """
    def execute(self, operation_id: str, component: str, handler: Callable) -> dict:
        from app.services.snapshot_service import SnapshotService
        from app.services.cache_service import CacheService
        from app.services.audit_service import AuditService

        snap_svc = SnapshotService()
        cache_svc = CacheService()
        audit_svc = AuditService()

        # Busca CNPJ da operação
        cnpj = snap_svc.get_cnpj(operation_id)

        logger.info(
            "component.started",
            operation_id=operation_id,
            component=component,
            cnpj=cnpj,
        )

        # Verifica cache
        cached = cache_svc.get(cnpj, component)
        if cached:
            cached = fix_dict_encoding(cached)
            logger.info("component.cache_hit", component=component, cnpj=cnpj)
            _execute_snapshot_write(
                operation_id,
                component,
                "save_result_cache_hit",
                lambda: snap_svc.save_result(
                    operation_id=operation_id,
                    component=component,
                    raw_result=cached,
                    parsed_result=cached,
                    status="completed",
                    duration_ms=0,
                    from_cache=True,
                ),
            )
            return {"operation_id": operation_id, "component": component, "cached": True}

        # Marca como running
        _execute_snapshot_write(
            operation_id,
            component,
            "mark_running",
            lambda: snap_svc.mark_running(operation_id, component),
        )
        audit_svc.log(operation_id, "component_started", payload={"component": component})

        start = time.time()
        try:
            import inspect
            sig = inspect.signature(handler)
            if "operation_id" in sig.parameters:
                result = handler(cnpj, operation_id=operation_id)
            else:
                result = handler(cnpj)
            result = fix_dict_encoding(result)
            duration_ms = int((time.time() - start) * 1000)

            _execute_snapshot_write(
                operation_id,
                component,
                "save_result_completed",
                lambda: snap_svc.save_result(
                    operation_id=operation_id,
                    component=component,
                    raw_result=result,
                    parsed_result=result,
                    status="completed",
                    duration_ms=duration_ms,
                ),
            )

            # Salva no cache
            cache_svc.set(cnpj, component, result)

            audit_svc.log(
                operation_id,
                "component_completed",
                payload={"component": component, "duration_ms": duration_ms},
            )

            logger.info(
                "component.completed",
                operation_id=operation_id,
                component=component,
                duration_ms=duration_ms,
            )

            return {"operation_id": operation_id, "component": component, "status": "completed"}

        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            error_message = str(exc)
            logger.error(
                "component.failed",
                operation_id=operation_id,
                component=component,
                error=error_message,
            )

            _execute_snapshot_write(
                operation_id,
                component,
                "save_result_failed",
                lambda: snap_svc.save_result(
                    operation_id=operation_id,
                    component=component,
                    raw_result=None,
                    parsed_result=None,
                    status="failed",
                    duration_ms=duration_ms,
                    error_message=error_message,
                ),
            )

            audit_svc.log(
                operation_id,
                "component_failed",
                payload={"component": component, "error": error_message},
            )

            # Propaga; o orquestrador trata falha por componente.
            raise
