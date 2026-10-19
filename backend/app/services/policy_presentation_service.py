"""Read-only projection of the latest shadow-policy decision for reports."""

from __future__ import annotations

from typing import Any


def _rows(query) -> list[dict[str, Any]]:
    result = query.execute()
    return list(getattr(result, "data", None) or [])


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _codes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [code for item in value.values() for code in _codes(item)]
    if isinstance(value, list):
        return [code for item in value for code in _codes(item)]
    return []


def _evidence(value: Any, documents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    public: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw) if isinstance(raw, dict) else {"valor": raw}
        sha256 = item.get("sha256")
        document = documents.get(str(sha256)) if sha256 else None
        if document:
            item.pop("storage_key", None)
            item["documento"] = {
                "documento_id": document.get("id"),
                "nome_arquivo": document.get("nome_arquivo"),
                "familia": document.get("tipo"),
                "data_base": item.get("data_base") or item.get("data") or document.get("capturado_em"),
                "pagina": item.get("pagina"),
                "trecho": item.get("trecho") or item.get("texto"),
            }
        public.append(item)
    return public


def _finding_projection(finding: dict[str, Any] | None, labels: dict[str, str], documents: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not finding:
        return None
    code = str(finding.get("codigo") or "")
    return {
        "codigo": code,
        "rotulo": labels.get(code) or code.replace("_", " ").capitalize(),
        "estado": finding.get("estado"),
        "confianca": finding.get("confianca"),
        "valor": finding.get("valor"),
        "evidencia": _evidence(finding.get("evidencia"), documents),
    }


def latest_policy_view(operation_id: str, *, database=None, operation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a report-safe view; it never changes the official decision."""
    if database is None:
        from app.core.database import supabase
        database = supabase
    operation = operation or (_rows(database.table("operations").select("id,cnpj,rating,limite_aprovado").eq("id", operation_id).limit(1)) or [None])[0]
    if not operation:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    runs = _rows(
        database.table("policy_shadow_runs").select("*").eq("operation_id", operation_id).order("created_at", desc=True)
    )
    if not runs:
        return {"status": "SEM_AVALIACAO"}
    versions = _rows(database.table("policy_versions").select("id,versao,status").in_("id", [row.get("policy_version_id") for row in runs]))
    version_by_id = {str(row.get("id")): row for row in versions}
    # A SOMBRA is authoritative for this informational panel; ATIVA is a fallback.
    candidates = [row for row in runs if version_by_id.get(str(row.get("policy_version_id")), {}).get("status") in {"SOMBRA", "ATIVA"}]
    shadows = [row for row in candidates if version_by_id.get(str(row.get("policy_version_id")), {}).get("status") == "SOMBRA"]
    candidates = shadows or candidates
    candidates.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if not candidates:
        return {"status": "SEM_AVALIACAO"}
    shadow = candidates[0]
    version = _record(version_by_id.get(str(shadow.get("policy_version_id"))))
    result = _record(shadow.get("resultado"))
    used_runs = set(_codes(shadow.get("runs_usados")))
    findings = _rows(database.table("findings").select("codigo,estado,confianca,valor,evidencia,run_id").eq("operation_id", operation_id))
    if used_runs:
        findings = [row for row in findings if str(row.get("run_id")) in used_runs]
    finding_by_code = {str(row.get("codigo")): row for row in findings}
    catalog = _rows(database.table("finding_catalog").select("codigo,descricao").in_("codigo", list(finding_by_code))) if finding_by_code else []
    labels = {str(row.get("codigo")): str(row.get("descricao") or "") for row in catalog}
    hashes = {str(item.get("sha256")) for finding in findings for item in (finding.get("evidencia") or []) if isinstance(item, dict) and item.get("sha256")}
    docs = _rows(database.table("documentos_cliente").select("id,cnpj,nome_arquivo,tipo,sha256,capturado_em").eq("cnpj", operation.get("cnpj"))) if hashes else []
    docs_by_hash = {str(row.get("sha256")): row for row in docs if str(row.get("sha256")) in hashes}

    def enrich(items: Any) -> list[dict[str, Any]]:
        output = []
        for item in items if isinstance(items, list) else []:
            value = dict(item) if isinstance(item, dict) else {"codigo": str(item)}
            value["finding"] = _finding_projection(finding_by_code.get(str(value.get("codigo") or "")), labels, docs_by_hash)
            output.append(value)
        return output

    bloqueios = []
    for code in result.get("bloqueios") or []:
        bloqueios.append({"codigo": code, "finding": _finding_projection(finding_by_code.get(str(code)), labels, docs_by_hash)})
    oficial = _record(shadow.get("oficial"))
    return {
        "status": "OK",
        "politica": {"versao": version.get("versao"), "status": version.get("status"), "data_referencia": shadow.get("data_referencia"), "shadow_logic_version": result.get("shadow_logic_version")},
        "decisao": {"status_decisao": result.get("status_decisao"), "rating": result.get("rating"), "limite": result.get("limite_aprovado_rs"), "alcada_minima": result.get("alcada_minima", 1), "alcada_motivos": enrich(result.get("alcada_motivos")), "faltantes": result.get("faltantes") or [], "bloqueios": bloqueios},
        "oficial": {"rating": oficial.get("rating", operation.get("rating")), "limite": oficial.get("limite_aprovado_rs", oficial.get("limite", operation.get("limite_aprovado")))},
        "condicoes_precedentes": enrich(result.get("condicoes_precedentes")),
        "ajustes": {"limite_multiplicadores": enrich(result.get("limite_multiplicadores")), "taxa_ajuste_pp": result.get("taxa_ajuste_pp", 0.0)},
        "informativos": enrich(result.get("informativos")),
    }
