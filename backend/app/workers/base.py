"""
BaseComponentTask: classe base para todos os workers de consulta.
Gerencia: snapshot lifecycle, cache, auditoria, erro handling.
"""
import time
from celery import Task
from typing import Callable
from app.core.config import settings
import structlog

logger = structlog.get_logger()


class BaseComponentTask(Task):
    """
    Herdar desta classe garante que todo componente:
    - Marca snapshot como 'running' ao iniciar
    - Persiste resultado raw + parsed ao completar
    - Marca snapshot como 'failed' em caso de erro
    - Registra duração e custo
    - Verifica cache antes de executar
    - Registra no audit trail
    """
    abstract = True

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
            logger.info("component.cache_hit", component=component, cnpj=cnpj)
            snap_svc.save_result(
                operation_id=operation_id,
                component=component,
                raw_result=cached,
                parsed_result=cached,
                status="completed",
                duration_ms=0,
                from_cache=True,
            )
            return {"operation_id": operation_id, "component": component, "cached": True}

        # Marca como running
        snap_svc.mark_running(operation_id, component)
        audit_svc.log(operation_id, "component_started", payload={"component": component})

        start = time.time()
        try:
            import inspect
            sig = inspect.signature(handler)
            if "operation_id" in sig.parameters:
                result = handler(cnpj, operation_id=operation_id)
            else:
                result = handler(cnpj)
            duration_ms = int((time.time() - start) * 1000)

            snap_svc.save_result(
                operation_id=operation_id,
                component=component,
                raw_result=result,
                parsed_result=result,
                status="completed",
                duration_ms=duration_ms,
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
            logger.error(
                "component.failed",
                operation_id=operation_id,
                component=component,
                error=str(exc),
            )

            snap_svc.save_result(
                operation_id=operation_id,
                component=component,
                raw_result=None,
                parsed_result=None,
                status="failed",
                duration_ms=duration_ms,
                error_message=str(exc),
            )

            audit_svc.log(
                operation_id,
                "component_failed",
                payload={"component": component, "error": str(exc)},
            )

            raise self.retry(exc=exc)
