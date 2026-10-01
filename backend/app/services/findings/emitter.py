"""Best-effort, decision-neutral emission to ``registrar_achados``."""
from __future__ import annotations
from typing import Any
import structlog
from app.services.findings.adapters import ADAPTERS
from app.services.findings.catalog import get_catalog, validate_value
from app.services.findings.hashing import entrada_hash
from app.services.findings.schemas import ExecucaoEnvelope
from app.services.findings import version

logger = structlog.get_logger()
REQUIRED = {
    "cadastro_regularidade": ("brasil_api", "pessoa_juridica", "ceis", "cnep", "cepim", "acordos_leniencia"),
    "sacado_orgao": ("contratos", "recursos_recebidos", "contratos_comprasnet"),
    "documentos": ("contrato_extracao",), "reputacional": ("web_research",), "porte": (),
}
OPTIONAL_INPUTS = {
    "cadastro_regularidade": ("cnd_federal", "cndt_tst", "fgts", "ceaf"),
    "sacado_orgao": (), "documentos": (), "reputacional": (), "porte": (),
}
TERMINAL = {"completed", "failed"}

def _first_row(query):
    result = query.limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None

def _load_operation(database, operation_id: str) -> dict[str, Any] | None:
    return _first_row(database.table("operations").select("ambiente,valor_enquadrado").eq("id", operation_id))

def _load_snapshots(database, operation_id: str):
    result = database.table("component_snapshots").select("component,status,parsed_result").eq("operation_id", operation_id).execute()
    parsed, statuses = {}, {}
    for row in result.data or []:
        component = row.get("component")
        if component:
            statuses[component] = str(row.get("status") or "missing")
            if row.get("status") == "completed" and isinstance(row.get("parsed_result"), dict):
                parsed[component] = row["parsed_result"]
    return parsed, statuses

def _quote_catalog(database, operation_id: str):
    row = _first_row(database.table("cotacoes_broadfactor").select("tipos_documento").eq("operation_id", operation_id))
    if not row:
        return None
    return {"documentos_broadfactor": [{"tipo": item} for item in (row.get("tipos_documento") or []) if item]}

def emit_findings(operation_id: str, especialista: str, *, database=None, overrides: dict[str, Any] | None = None) -> None:
    """Emit one idempotent run without changing a decision.

    Any adapter-rule change must increment ``EMITTER_VERSION``; otherwise the
    RPC deduplicates existing operation inputs and does not emit a new run.
    """
    try:
        db = database
        if db is None:
            from app.core.database import supabase
            db = supabase
        required, adapter = REQUIRED.get(especialista), ADAPTERS.get(especialista)
        if required is None or adapter is None:
            logger.warning("findings.unknown_specialist", operation_id=operation_id, especialista=especialista); return
        operation = _load_operation(db, operation_id)
        if not operation:
            logger.warning("findings.operation_not_found", operation_id=operation_id, especialista=especialista); return
        snapshots, statuses = _load_snapshots(db, operation_id)
        if any(statuses.get(component) not in TERMINAL for component in required):
            logger.debug("findings.inputs_not_terminal", operation_id=operation_id, especialista=especialista); return
        catalog = get_catalog(database=db)
        if catalog is None:
            logger.warning("findings.emission_skipped_catalog_unavailable", operation_id=operation_id, especialista=especialista); return
        quote_catalog = _quote_catalog(db, operation_id)
        if quote_catalog:
            snapshots["catalogo_broadfactor"] = quote_catalog
        if overrides:
            snapshots.update(overrides)
        snapshots["__statuses__"] = statuses
        input_components = set(required) | set(OPTIONAL_INPUTS[especialista]) | set(overrides or {})
        input_rows = [
            (component, statuses.get(component, "missing"), snapshots.get(component))
            for component in sorted(input_components)
        ]
        fingerprint = entrada_hash({"versao_emissor": version.EMITTER_VERSION, "insumos": input_rows, "valor_enquadrado": operation.get("valor_enquadrado"), "tipos_documento": (quote_catalog or {}).get("documentos_broadfactor"), "overrides": overrides or {}})
        candidates = adapter(snapshots, fingerprint=fingerprint, operation=operation)
        achados = []
        for candidate in candidates:
            definition = catalog.get(f"{candidate.codigo}:{candidate.catalogo_versao}")
            if not definition:
                logger.warning("findings.unknown_code_discarded", operation_id=operation_id, codigo=candidate.codigo); continue
            if definition.get("escopo") != candidate.escopo.value or not validate_value(candidate, definition):
                logger.warning("findings.invalid_candidate_discarded", operation_id=operation_id, codigo=candidate.codigo); continue
            achados.append(candidate.model_dump(mode="json"))
        run_status = "PARCIAL" if any(statuses.get(component) == "failed" for component in required) else "COMPLETO"
        envelope = ExecucaoEnvelope(operation_id=operation_id, ambiente=str(operation.get("ambiente") or "PRODUCAO"), especialista=especialista, status=run_status, entrada_hash=fingerprint, versao_emissor=version.EMITTER_VERSION)
        db.rpc("registrar_achados", {"p_run": envelope.model_dump(mode="json"), "p_achados": achados}).execute()
    except Exception as exc:
        logger.warning("findings.emission_failed", operation_id=operation_id, especialista=especialista, error=str(exc))
