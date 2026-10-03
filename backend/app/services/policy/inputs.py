"""Build policy input from append-only findings, never component snapshots."""
from __future__ import annotations

from typing import Any

from app.services.findings.emitter import REQUIRED
from app.services.findings.version import EMITTER_VERSION
from app.services.policy.types import EntradaPolitica, FindingValue


def _rows(query) -> list[dict[str, Any]]:
    result = query.execute()
    return list((result.data or []) if result else [])


def montar_entrada(operation_id: str, *, database=None) -> EntradaPolitica:
    if database is None:
        from app.core.database import supabase
        database = supabase
    op_rows = _rows(database.table("operations").select("id,valor_enquadrado,valor_solicitado,pct_max_contrato,margem_disponivel,contrato_saldo,saldo_vincendo,ambiente").eq("id", operation_id).limit(1))
    if not op_rows:
        raise LookupError("operacao nao encontrada")
    run_rows = _rows(database.table("finding_runs").select("id,especialista,versao_emissor,created_at").eq("operation_id", operation_id).eq("versao_emissor", EMITTER_VERSION).order("created_at", desc=True))
    latest: dict[str, dict[str, Any]] = {}
    for row in sorted(run_rows, key=lambda item: str(item.get("created_at") or ""), reverse=True):
        latest.setdefault(str(row.get("especialista")), row)
    runs_usados = {name: {"run_id": str(row["id"]), "versao_emissor": EMITTER_VERSION} for name, row in latest.items() if row.get("id")}
    indisponiveis = set(REQUIRED) - set(latest)
    findings: dict[str, FindingValue] = {}
    for row in latest.values():
        run_id = row.get("id")
        if not run_id:
            continue
        for finding in _rows(database.table("findings").select("codigo,valor,estado,confianca,evidencia").eq("run_id", run_id)):
            code = finding.get("codigo")
            if code:
                findings[str(code)] = FindingValue(str(code), finding.get("valor"), str(finding.get("estado")), str(finding.get("confianca")), str(run_id), list(finding.get("evidencia") or []))
    return EntradaPolitica(op_rows[0], findings, runs_usados, indisponiveis)
