"""Synchronous, idempotent re-emission for operations predating the hooks."""
from __future__ import annotations
from typing import Any

from app.services.findings.emitter import REQUIRED, _load_snapshots, emit_findings, missing_required

SPECIALISTS = ("cadastro_regularidade", "sacado_orgao", "documentos", "reputacional", "porte")


def _first_row(query):
    response = query.limit(1).execute()
    rows = response.data or []
    return rows[0] if rows else None


def reemitir_operacao(operation_id: str, *, aplicar: bool, database=None) -> dict[str, Any]:
    if database is None:
        from app.core.database import supabase
        database = supabase
    snapshots, statuses = _load_snapshots(database, operation_id)
    output: dict[str, str] = {}
    for specialist in SPECIALISTS:
        if specialist == "porte":
            score = snapshots.get("score_engine") if statuses.get("score_engine") == "completed" else None
            if not isinstance(score, dict):
                output[specialist] = "aguardando"
                continue
            if aplicar:
                output[specialist] = emit_findings(operation_id, specialist, database=database, overrides={"score_engine": score}).desfecho
            else:
                output[specialist] = "pronto"
            continue
        faltantes = missing_required(specialist, statuses)
        if faltantes:
            output[specialist] = "aguardando"
        elif aplicar:
            output[specialist] = emit_findings(operation_id, specialist, database=database).desfecho
        else:
            output[specialist] = "pronto"
    return {"operation_id": operation_id, "especialistas": output}


def select_operations(*, operation_ids: list[str] | None = None, ambiente: str = "PRODUCAO", limite: int = 20, database=None) -> list[str]:
    if database is None:
        from app.core.database import supabase
        database = supabase
    if operation_ids is not None:
        return operation_ids[:limite]
    response = database.table("operations").select("id").eq("ambiente", ambiente).order("created_at", desc=True).limit(limite).execute()
    return [str(row["id"]) for row in (response.data or []) if row.get("id")]
