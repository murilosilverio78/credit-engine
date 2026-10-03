"""Shadow comparison and append-only persistence for policy v0."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from app.services.policy.engine import avaliar
from app.services.policy.inputs import montar_entrada
from app.services.policy.loader import load_policy

_FIELDS = ("merit", "merit_potencial", "fator_regularidade", "fator_potencial", "penalizacao_balanco", "score", "rating", "rating_potencial", "limite_aprovado_rs", "bloqueios", "ajuste_pd")


def _rows(query) -> list[dict[str, Any]]:
    result = query.execute()
    return list((result.data or []) if result else [])


def _reference(value: Any) -> date:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return date.today()


def _official(database, operation_id: str) -> tuple[dict[str, Any] | None, date]:
    rows = _rows(database.table("component_snapshots").select("parsed_result,created_at").eq("operation_id", operation_id).eq("component", "score_engine").eq("status", "completed").order("created_at", desc=True).limit(1))
    if not rows or not isinstance(rows[0].get("parsed_result"), dict):
        return None, date.today()
    return rows[0]["parsed_result"], _reference(rows[0].get("created_at"))


def _hash(runs: dict[str, dict[str, str]], policy_id: str, ref: date) -> str:
    raw = json.dumps({"runs": runs, "policy_version_id": policy_id, "data_referencia": ref.isoformat()}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def avaliar_sombra(operation_id: str, *, database=None, aplicar: bool = False) -> dict[str, Any]:
    if database is None:
        from app.core.database import supabase
        database = supabase
    policy = load_policy(database=database, status="SOMBRA")
    entrada = montar_entrada(operation_id, database=database)
    official, ref = _official(database, operation_id)
    result = avaliar(entrada, policy["parametros"], policy["regras"], ref)
    divergencias = []
    if official:
        computed = result.as_dict()
        for field in _FIELDS:
            policy_value, official_value = computed.get(field), official.get(field)
            if isinstance(policy_value, (int, float)) and isinstance(official_value, (int, float)):
                different = abs(policy_value - official_value) > 0.05
            else:
                different = policy_value != official_value
            if different:
                divergencias.append({"campo": field, "valor_politica": policy_value, "valor_oficial": official_value, "diferenca": None, "classe": "PARIDADE", "motivo": "valor divergente"})
    if entrada.indisponiveis or official is None:
        classe = "SEM_DADOS"
    elif not divergencias:
        classe = "IGUAL"
    elif result.efeitos_novos:
        classe = "ESPERADA"
    else:
        classe = "INESPERADA"
    payload = {"operation_id": operation_id, "ambiente": entrada.operation.get("ambiente") or "PRODUCAO", "policy_version_id": policy["version"]["id"], "entrada_hash": _hash(entrada.runs_usados, str(policy["version"]["id"]), ref), "data_referencia": ref.isoformat(), "runs_usados": entrada.runs_usados, "resultado": result.as_dict(), "oficial": official or {}, "divergencias": divergencias, "classe_geral": classe, "parametros_divergentes": []}
    if aplicar:
        database.table("policy_shadow_runs").insert(payload).execute()
    return payload
