"""Best-effort, decision-neutral emission to ``registrar_achados``."""

from __future__ import annotations
from typing import Any
import structlog
from app.services.findings.adapters import ADAPTERS
from app.services.findings.catalog import get_catalog, validate_value
from app.services.findings.hashing import entrada_hash
from app.services.findings.schemas import EmissionResult, ExecucaoEnvelope
from app.services.findings import version

logger = structlog.get_logger()
REQUIRED = {
    "cadastro_regularidade": (
        "brasil_api",
        "pessoa_juridica",
        "ceis",
        "cnep",
        "cepim",
        "acordos_leniencia",
    ),
    "sacado_orgao": ("contratos", "recursos_recebidos", "contratos_comprasnet"),
    "documentos": ("contrato_extracao",),
    "reputacional": ("web_research",),
    "porte": (),
    "financeiro": ("documentos_financeiros",),
}
OPTIONAL_INPUTS = {
    "cadastro_regularidade": (
        "cnd_federal",
        "cndt_tst",
        "fgts",
        "ceaf",
        "contrato_extracao",
    ),
    "sacado_orgao": (),
    "documentos": (),
    "reputacional": (),
    "porte": (),
    "financeiro": ("recursos_recebidos", "contratos_pncp", "brasil_api"),
}
TERMINAL = {"completed", "failed"}


def missing_required(especialista: str, statuses: dict[str, str]) -> list[str]:
    return [
        component
        for component in REQUIRED.get(especialista, ())
        if statuses.get(component) not in TERMINAL
    ]


def _first_row(query):
    result = query.limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def _load_operation(database, operation_id: str) -> dict[str, Any] | None:
    return _first_row(
        database.table("operations")
        .select(
            "id,cnpj,ambiente,valor_enquadrado,contrato_id,contrato_dedicacao_exclusiva,uasg,valor_global_contrato,cotacao_id"
        )
        .eq("id", operation_id)
    )


def _load_snapshots(database, operation_id: str):
    result = (
        database.table("component_snapshots")
        .select("component,status,parsed_result")
        .eq("operation_id", operation_id)
        .execute()
    )
    parsed, statuses = {}, {}
    for row in result.data or []:
        component = row.get("component")
        if component:
            statuses[component] = str(row.get("status") or "missing")
            if row.get("status") == "completed" and isinstance(
                row.get("parsed_result"), dict
            ):
                parsed[component] = row["parsed_result"]
    return parsed, statuses


def _quote_catalog(database, operation_id: str):
    row = _first_row(
        database.table("cotacoes_broadfactor")
        .select("tipos_documento,data_cotacao")
        .eq("operation_id", operation_id)
    )
    if not row:
        return None
    return {
        "documentos_broadfactor": [
            {"tipo": item} for item in (row.get("tipos_documento") or []) if item
        ],
        "data_cotacao": row.get("data_cotacao"),
    }


def _financial_parameters(database):
    try:
        return (
            _first_row(database.table("documentos_financeiros_config").select("*"))
            or {}
        )
    except Exception:
        return {}


def _operation_documents(database, operation_id: str):
    """Return the optional operation-document catalog without affecting emission."""
    try:
        result = (
            database.table("documents")
            .select("document_type")
            .eq("operation_id", operation_id)
            .limit(1000)
            .execute()
        )
        return {"documentos": (result.data or []) if result else []}
    except Exception as exc:
        logger.warning(
            "findings.operation_documents_unavailable",
            operation_id=operation_id,
            error=str(exc),
        )
        return {"documentos": []}


def emit_findings(
    operation_id: str,
    especialista: str,
    *,
    database=None,
    overrides: dict[str, Any] | None = None,
) -> EmissionResult:
    """Emit one idempotent run without changing a decision.

    Any adapter-rule or hash-composition change must increment
    ``EMITTER_VERSION``; otherwise the RPC deduplicates existing operation
    inputs and does not emit a new run.
    """
    try:
        db = database
        if db is None:
            from app.core.database import supabase

            db = supabase
        required, adapter = REQUIRED.get(especialista), ADAPTERS.get(especialista)
        if required is None or adapter is None:
            logger.warning(
                "findings.unknown_specialist",
                operation_id=operation_id,
                especialista=especialista,
            )
            return EmissionResult("erro")
        operation = _load_operation(db, operation_id)
        if not operation:
            logger.warning(
                "findings.operation_not_found",
                operation_id=operation_id,
                especialista=especialista,
            )
            return EmissionResult("erro")
        snapshots, statuses = _load_snapshots(db, operation_id)
        faltantes = missing_required(especialista, statuses)
        if faltantes:
            logger.debug(
                "findings.inputs_not_terminal",
                operation_id=operation_id,
                especialista=especialista,
            )
            return EmissionResult("aguardando", faltantes=faltantes)
        catalog = get_catalog(database=db)
        if catalog is None:
            logger.warning(
                "findings.emission_skipped_catalog_unavailable",
                operation_id=operation_id,
                especialista=especialista,
            )
            return EmissionResult("sem_catalogo")
        extras = version.HASH_EXTRAS[especialista]
        quote_catalog = (
            _quote_catalog(db, operation_id)
            if "tipos_documento" in extras or "cotacao_data" in extras
            else None
        )
        if quote_catalog:
            snapshots["catalogo_broadfactor"] = quote_catalog
        operation_documents = None
        if "documentos_operacao" in extras:
            operation_documents = _operation_documents(db, operation_id)
            snapshots["documentos_operacao"] = operation_documents
        if overrides and "overrides" in extras:
            snapshots.update(overrides)
        snapshots["__statuses__"] = statuses
        input_components = (
            set(required)
            | set(OPTIONAL_INPUTS[especialista])
            | (set(overrides or {}) if "overrides" in extras else set())
        )
        input_rows = [
            (component, statuses.get(component, "missing"), snapshots.get(component))
            for component in sorted(input_components)
        ]
        hash_extras = {}
        if "valor_enquadrado" in extras:
            hash_extras["valor_enquadrado"] = operation.get("valor_enquadrado")
        if "contrato_operacao" in extras:
            hash_extras["contrato_operacao"] = {
                key: operation.get(key)
                for key in (
                    "contrato_id",
                    "contrato_dedicacao_exclusiva",
                    "uasg",
                    "valor_global_contrato",
                    "cotacao_id",
                )
            }
        if "cotacao_data" in extras:
            operation["data_cotacao"] = (quote_catalog or {}).get("data_cotacao")
            hash_extras["cotacao_data"] = operation["data_cotacao"]
        if "parametros_financeiros" in extras:
            hash_extras["parametros_financeiros"] = _financial_parameters(db)
        if "tipos_documento" in extras:
            hash_extras["tipos_documento"] = (quote_catalog or {}).get(
                "documentos_broadfactor"
            )
        if "documentos_operacao" in extras:
            hash_extras["documentos_operacao"] = (operation_documents or {}).get(
                "documentos"
            )
        if "overrides" in extras:
            hash_extras["overrides"] = overrides or {}
        fingerprint = entrada_hash(
            {
                "versao_emissor": version.EMITTER_VERSION,
                "insumos": input_rows,
                "extras": hash_extras,
            }
        )
        adapter_kwargs = {"fingerprint": fingerprint, "operation": operation}
        if especialista == "financeiro":
            adapter_kwargs["parameters"] = hash_extras.get("parametros_financeiros", {})
        if especialista == "sacado_orgao":
            adapter_kwargs["database"] = db
        candidates = adapter(snapshots, **adapter_kwargs)
        achados = []
        for candidate in candidates:
            definition = catalog.get(f"{candidate.codigo}:{candidate.catalogo_versao}")
            if not definition:
                logger.warning(
                    "findings.unknown_code_discarded",
                    operation_id=operation_id,
                    codigo=candidate.codigo,
                )
                continue
            if definition.get("escopo") != candidate.escopo.value or not validate_value(
                candidate, definition
            ):
                logger.warning(
                    "findings.invalid_candidate_discarded",
                    operation_id=operation_id,
                    codigo=candidate.codigo,
                )
                continue
            achados.append(candidate.model_dump(mode="json"))
        run_status = (
            "PARCIAL"
            if any(statuses.get(component) == "failed" for component in required)
            else "COMPLETO"
        )
        envelope = ExecucaoEnvelope(
            operation_id=operation_id,
            ambiente=str(operation.get("ambiente") or "PRODUCAO"),
            especialista=especialista,
            status=run_status,
            entrada_hash=fingerprint,
            versao_emissor=version.EMITTER_VERSION,
        )
        response = db.rpc(
            "registrar_achados",
            {"p_run": envelope.model_dump(mode="json"), "p_achados": achados},
        ).execute()
        row = ((response.data or [None])[0] if response else None) or {}
        return EmissionResult(
            "emitido" if row.get("inserido", True) else "deduplicado",
            run_id=str(row.get("run_id")) if row.get("run_id") else None,
        )
    except Exception as exc:
        logger.warning(
            "findings.emission_failed",
            operation_id=operation_id,
            especialista=especialista,
            error=str(exc),
        )
        return EmissionResult("erro")
