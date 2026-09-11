"""
BaseComponentTask: classe base para todos os workers de consulta.
Gerencia: snapshot lifecycle, cache, auditoria, erro handling.
"""
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar
from uuid import uuid4

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


def _dual_write_cliente(
    *,
    operation_id: str,
    component: str,
    collection_key: str,
    collected_at: datetime,
    status: str,
    result: object,
    duration_ms: int,
    error_message: str | None = None,
) -> None:
    """Espelha uma consulta real no cadastro sem afetar o pipeline."""
    try:
        from app.core.database import supabase
        from app.services.cliente_result_classifier import (
            Classificacao,
            calcular_payload_hash,
            classificar,
        )
        from app.services.cliente_service import ClienteService

        cliente_svc = ClienteService()
        if not cliente_svc.is_cliente_component(component):
            return

        operation_result = _execute_snapshot_write(
            operation_id,
            component,
            "load_operation_for_client_dual_write",
            lambda: supabase.table("operations")
            .select("cliente_id,cotacao_id")
            .eq("id", operation_id)
            .maybe_single()
            .execute(),
        )
        operation = operation_result.data or {}
        cliente_id = operation.get("cliente_id")
        if not cliente_id:
            cliente_id = cliente_svc.vincular_operacao(operation_id)
        if not cliente_id:
            return

        if status == "failed":
            classification = Classificacao(
                result_state="ERROR",
                degradado=False,
                degradacao_motivo=None,
                fonte=None,
                error_message=error_message or "erro sem mensagem",
                fingerprint=None,
            )
            parsed_result = None
        else:
            classification = classificar(
                component,
                result,
                cotacao_id=operation.get("cotacao_id"),
            )
            parsed_result = result

        cliente_svc.registrar_snapshot(
            cliente_id=str(cliente_id),
            component=component,
            collection_key=collection_key,
            status=status,
            result_state=classification.result_state,
            collected_at=collected_at.isoformat(),
            parsed_result=parsed_result,
            raw_result=None,
            payload_hash=calcular_payload_hash(classification.fingerprint),
            fonte=classification.fonte,
            degradado=classification.degradado,
            degradacao_motivo=classification.degradacao_motivo,
            source_operation_id=operation_id,
            source_cotacao_id=operation.get("cotacao_id"),
            error_message=classification.error_message,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        logger.warning(
            "client.dual_write_failed",
            action="dual_write_component",
            operation_id=operation_id,
            component=component,
            collection_key=collection_key,
            error=str(exc),
        )


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
    def execute(
        self,
        operation_id: str,
        component: str,
        handler: Callable,
        use_cache: bool = True,
    ) -> dict:
        from app.services.snapshot_service import SnapshotService
        from app.services.cache_service import CacheService
        from app.services.audit_service import AuditService

        snap_svc = SnapshotService()
        cache_svc = CacheService()
        audit_svc = AuditService()

        try:
            cnpj = snap_svc.get_cnpj(operation_id)
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            logger.error(
                "component.preflight_failed",
                operation_id=operation_id,
                component=component,
                error=error_message,
            )
            try:
                _execute_snapshot_write(
                    operation_id,
                    component,
                    "save_result_preflight_failed",
                    lambda: snap_svc.save_result(
                        operation_id=operation_id,
                        component=component,
                        raw_result=None,
                        parsed_result=None,
                        status="failed",
                        duration_ms=0,
                        error_message=error_message,
                    ),
                )
            except Exception as persist_exc:
                logger.error(
                    "component.preflight_failure_persist_failed",
                    operation_id=operation_id,
                    component=component,
                    original_error=error_message,
                    persist_error=str(persist_exc),
                )
            raise

        logger.info(
            "component.started",
            operation_id=operation_id,
            component=component,
            cnpj=cnpj,
        )

        # Verifica cache
        cached = cache_svc.get(cnpj, component) if use_cache else None
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

        collection_key = str(uuid4())

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
            collected_at = datetime.now(timezone.utc)
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
            if use_cache and not (
                isinstance(result, dict) and "erro" in result
            ):
                cache_svc.set(cnpj, component, result)

            _dual_write_cliente(
                operation_id=operation_id,
                component=component,
                collection_key=collection_key,
                collected_at=collected_at,
                status="completed",
                result=result,
                duration_ms=duration_ms,
            )

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
            collected_at = datetime.now(timezone.utc)
            duration_ms = int((time.time() - start) * 1000)
            error_message = str(exc)
            logger.error(
                "component.failed",
                operation_id=operation_id,
                component=component,
                error=error_message,
            )

            _dual_write_cliente(
                operation_id=operation_id,
                component=component,
                collection_key=collection_key,
                collected_at=collected_at,
                status="failed",
                result=None,
                duration_ms=duration_ms,
                error_message=error_message,
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
