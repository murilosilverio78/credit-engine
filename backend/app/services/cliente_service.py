"""Vinculo de clientes e dual-write best-effort de observacoes."""

from __future__ import annotations

import re
import time
from typing import Any

import structlog

from app.core.database import supabase


logger = structlog.get_logger()

_COMPONENT_SCOPES: dict[str, str] = {}
_COMPONENT_SCOPES_TS = 0.0
_COMPONENT_SCOPES_SECONDS = 60.0


def normalizar_cnpj(valor: str | None) -> str | None:
    """Normaliza CNPJ numerico ou alfanumerico sem validar digitos verificadores."""
    if not valor:
        return None
    normalized = re.sub(r"[^0-9A-Za-z]", "", valor).upper()
    if re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", normalized):
        return normalized
    return None


def _get_component_scopes() -> dict[str, str]:
    """Le data_scope e o mantem em memoria por 60 segundos."""
    global _COMPONENT_SCOPES, _COMPONENT_SCOPES_TS

    now = time.monotonic()
    if now - _COMPONENT_SCOPES_TS < _COMPONENT_SCOPES_SECONDS:
        return _COMPONENT_SCOPES

    try:
        result = (
            supabase.table("component_config")
            .select("component,data_scope")
            .execute()
        )
        _COMPONENT_SCOPES = {
            str(row["component"]): str(row["data_scope"])
            for row in (result.data or [])
            if row.get("component") and row.get("data_scope")
        }
    except Exception as exc:
        _COMPONENT_SCOPES = {}
        logger.warning("client.component_scopes_failed", error=str(exc))

    _COMPONENT_SCOPES_TS = now
    return _COMPONENT_SCOPES


def _first_row(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else {}
    return data if isinstance(data, dict) else {}


class ClienteService:
    def is_cliente_component(self, component: str) -> bool:
        return _get_component_scopes().get(component) == "CLIENTE"

    def vincular_operacao(self, operation_id: str) -> str | None:
        """Vincula a operacao ao cliente sem propagar falhas ao chamador."""
        try:
            result = supabase.rpc(
                "vincular_cliente_operacao",
                {"p_operation_id": operation_id},
            ).execute()
            row = _first_row(result.data)
            cliente_id = row.get("out_cliente_id")
            if not cliente_id:
                logger.info(
                    "client.link_skipped_non_pj",
                    operation_id=operation_id,
                )
                return None

            if row.get("out_criado"):
                logger.info(
                    "client.created",
                    cliente_id=cliente_id,
                    source_operation_id=operation_id,
                )
            if row.get("out_vinculado"):
                logger.info(
                    "client.linked_to_operation",
                    cliente_id=cliente_id,
                    source_operation_id=operation_id,
                )
            return str(cliente_id)
        except Exception as exc:
            logger.warning(
                "client.dual_write_failed",
                action="link_operation",
                source_operation_id=operation_id,
                error=str(exc),
            )
            return None

    def registrar_snapshot(
        self,
        *,
        cliente_id: str,
        component: str,
        collection_key: str,
        status: str,
        result_state: str,
        collected_at: str,
        parsed_result: Any,
        raw_result: Any = None,
        payload_hash: str | None = None,
        fonte: str | None = None,
        degradado: bool = False,
        degradacao_motivo: str | None = None,
        source_operation_id: str | None = None,
        source_cotacao_id: str | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> str | None:
        """Registra uma observacao de cliente sem propagar falhas."""
        if not self.is_cliente_component(component):
            return None

        if result_state == "ERROR" and not error_message:
            error_message = "erro sem mensagem"
        if error_message is not None:
            error_message = error_message[:1000]

        context = {
            "cliente_id": cliente_id,
            "component": component,
            "collection_key": collection_key,
            "source_operation_id": source_operation_id,
            "result_state": result_state,
            "duration_ms": duration_ms,
        }
        params = {
            "p_cliente_id": cliente_id,
            "p_component": component,
            "p_collection_key": collection_key,
            "p_status": status,
            "p_result_state": result_state,
            "p_collected_at": collected_at,
            "p_parsed_result": parsed_result,
            "p_raw_result": raw_result,
            "p_payload_hash": payload_hash,
            "p_fonte": fonte,
            "p_degradado": degradado,
            "p_degradacao_motivo": degradacao_motivo,
            "p_source_operation_id": source_operation_id,
            "p_source_cotacao_id": source_cotacao_id,
            "p_error_message": error_message,
            "p_duration_ms": duration_ms,
        }

        try:
            result = supabase.rpc("registrar_cliente_snapshot", params).execute()
            row = _first_row(result.data)
            snapshot_id = row.get("out_snapshot_id")
            event = {
                "OK": "client.snapshot_completed",
                "EMPTY": "client.snapshot_empty",
                "ERROR": "client.snapshot_failed",
            }.get(result_state, "client.snapshot_failed")
            logger.info(event, snapshot_id=snapshot_id, **context)
            if row.get("out_promovido"):
                logger.info(
                    "client.snapshot_promoted",
                    snapshot_id=snapshot_id,
                    **context,
                )
            if component == "brasil_api" and result_state == "OK" and snapshot_id:
                try:
                    from app.workers.base import _execute_snapshot_write

                    projection = _execute_snapshot_write(
                        source_operation_id or cliente_id,
                        component,
                        "projetar_cadastro_de_snapshot",
                        lambda: supabase.rpc(
                            "projetar_cadastro_de_snapshot",
                            {"p_snapshot_id": snapshot_id},
                        ).execute(),
                    )
                    projection_row = _first_row(projection.data)
                    campos_alterados = projection_row.get("out_campos_alterados") or []
                    divergencias = int(
                        projection_row.get("out_divergencias") or 0
                    )
                    projection_context = {
                        "cliente_id": cliente_id,
                        "snapshot_id": snapshot_id,
                        "revision": projection_row.get("out_revision"),
                        "campos_alterados": campos_alterados,
                        "divergencias": divergencias,
                    }
                    if campos_alterados:
                        logger.info(
                            "client.cadastro_materializado",
                            **projection_context,
                        )
                    if divergencias > 0:
                        logger.warning(
                            "client.cadastro_divergencia",
                            **projection_context,
                        )
                except Exception as exc:
                    logger.warning(
                        "client.cadastro_projecao_failed",
                        cliente_id=cliente_id,
                        snapshot_id=snapshot_id,
                        error=str(exc),
                    )
            return str(snapshot_id) if snapshot_id else None
        except Exception as exc:
            logger.warning(
                "client.dual_write_failed",
                action="register_snapshot",
                error=str(exc),
                **context,
            )
            return None
