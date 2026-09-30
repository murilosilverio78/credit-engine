"""Best-effort, decision-neutral emission to ``registrar_achados``."""
from __future__ import annotations

from typing import Any

import structlog

from app.services.findings.adapters import ADAPTERS
from app.services.findings.catalog import get_catalog, validate_value
from app.services.findings.hashing import entrada_hash
from app.services.findings.schemas import ExecucaoEnvelope

logger = structlog.get_logger()


def _load_operation(database, operation_id: str) -> dict[str, Any] | None:
    result = database.table("operations").select("ambiente,valor_enquadrado").eq("id", operation_id).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def _load_snapshots(database, operation_id: str) -> dict[str, Any]:
    result = database.table("component_snapshots").select("component,parsed_result").eq("operation_id", operation_id).eq("status", "completed").execute()
    return {row["component"]: row.get("parsed_result") for row in (result.data or []) if row.get("component") and isinstance(row.get("parsed_result"), dict)}


def emit_findings(operation_id: str, especialista: str, snapshot: dict[str, Any] | None = None, *, database=None) -> None:
    """Emit once through the RPC; all exceptions are intentionally contained."""
    from app.core.database import supabase

    try:
        db = database or supabase
        operation = _load_operation(db, operation_id)
        if not operation:
            logger.warning("findings.operation_not_found", operation_id=operation_id, especialista=especialista)
            return
        snapshots = snapshot if isinstance(snapshot, dict) and any(name in snapshot for name in ADAPTERS) else _load_snapshots(db, operation_id)
        fingerprint = entrada_hash(snapshot if snapshot is not None else snapshots)
        catalog = get_catalog(database=db)
        if catalog is None:
            logger.warning("findings.emission_skipped_catalog_unavailable", operation_id=operation_id, especialista=especialista)
            return
        adapter = ADAPTERS.get(especialista)
        if adapter is None:
            logger.warning("findings.unknown_specialist", operation_id=operation_id, especialista=especialista)
            return
        candidates = adapter(snapshots, fingerprint=fingerprint, operation=operation)
        achados = []
        for candidate in candidates:
            definition = catalog.get(f"{candidate.codigo}:{candidate.catalogo_versao}")
            if not definition:
                logger.warning("findings.unknown_code_discarded", operation_id=operation_id, codigo=candidate.codigo)
                continue
            if definition.get("escopo") != candidate.escopo.value or not validate_value(candidate, definition):
                logger.warning("findings.invalid_candidate_discarded", operation_id=operation_id, codigo=candidate.codigo)
                continue
            achados.append(candidate.model_dump(mode="json"))
        envelope = ExecucaoEnvelope(operation_id=operation_id, ambiente=str(operation.get("ambiente") or "PRODUCAO"), especialista=especialista, status="COMPLETO", entrada_hash=fingerprint)
        db.rpc("registrar_achados", {"p_run": envelope.model_dump(mode="json"), "p_achados": achados}).execute()
    except Exception as exc:
        logger.warning("findings.emission_failed", operation_id=operation_id, especialista=especialista, error=str(exc))
