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
    rows = _rows(database.table("component_snapshots").select("parsed_result,completed_at,created_at").eq("operation_id", operation_id).eq("component", "score_engine").eq("status", "completed").order("completed_at", desc=True).limit(1))
    if not rows or not isinstance(rows[0].get("parsed_result"), dict):
        return None, date.today()
    official = dict(rows[0]["parsed_result"])
    # Snapshots reais do score persistem esse fator dentro de regularidade;
    # versões anteriores também podem tê-lo no nível superior.
    if "fator_potencial" not in official and isinstance(official.get("regularidade"), dict):
        official["fator_potencial"] = official["regularidade"].get("fator_potencial")
    return official, _reference(rows[0].get("completed_at") or rows[0].get("created_at"))


def _set_pct_max_contrato(operation: dict[str, Any], official: dict[str, Any] | None) -> None:
    """Use the exact historical cap, falling back to the score's config source."""
    if isinstance(official, dict) and official.get("limite_sugerido_pct_contrato") is not None:
        operation["pct_max_contrato"] = official["limite_sugerido_pct_contrato"]
        operation["pct_max_contrato_origem"] = "oficial"
        return
    try:
        from app.services.eligibility_params_service import get_eligibility_config

        operation["pct_max_contrato"] = get_eligibility_config()["pct_max_contrato"]
        operation["pct_max_contrato_origem"] = "eligibility"
    except Exception:
        # Keep it absent: _limit emits limite_sem_pct_max_contrato just as the
        # deterministic score does when the cap is unavailable.
        operation.pop("pct_max_contrato", None)
        operation["pct_max_contrato_origem"] = "indisponivel"


def _block_category(value: Any) -> str:
    text = str(value)
    if text.startswith("Situacao cadastral") or text == "cadastro_inativo":
        return "cadastro_inativo"
    if text.startswith("Sancao ativa") or text == "sancao_ativa":
        return "sancao_ativa"
    if text.startswith("Acordo de leniencia") or text == "acordo_leniencia_ativo":
        return "acordo_leniencia_ativo"
    return text


def _effects_explain(divergencias: list[dict[str, Any]], effects: list[dict[str, Any]]) -> bool:
    """Only explicitly declared parity fields can justify a divergence.

    Current glosa and conta-vinculada effects target pricing/LGD, neither of
    which changes the score-parity fields persisted by this shadow runner.
    """
    fields = {str(item["campo"]) for item in divergencias}
    explained = {
        str(field)
        for effect in effects
        for field in (effect.get("campos_paridade") or [])
    }
    return bool(fields) and fields <= explained


def _hash(runs: dict[str, dict[str, str]], operation: dict[str, Any], policy_id: str, ref: date) -> str:
    context = {key: operation.get(key) for key in ("valor_enquadrado", "valor_solicitado", "pct_max_contrato", "margem_disponivel", "contrato_saldo", "saldo_vincendo")}
    raw = json.dumps({"runs": runs, "contexto": context, "policy_version_id": policy_id, "data_referencia": ref.isoformat()}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def avaliar_sombra(operation_id: str, *, database=None, aplicar: bool = False) -> dict[str, Any]:
    if database is None:
        from app.core.database import supabase
        database = supabase
    policy = load_policy(database=database, status="SOMBRA")
    entrada = montar_entrada(operation_id, database=database)
    official, ref = _official(database, operation_id)
    _set_pct_max_contrato(entrada.operation, official)
    result = avaliar(entrada, policy["parametros"], policy["regras"], ref)
    divergencias = []
    if official:
        computed = result.as_dict()
        for field in _FIELDS:
            policy_value, official_value = computed.get(field), official.get(field)
            if field == "bloqueios":
                policy_value = sorted({_block_category(item) for item in (policy_value or [])})
                official_value = sorted({_block_category(item) for item in (official_value or [])})
                different = policy_value != official_value
            elif isinstance(policy_value, (int, float)) and isinstance(official_value, (int, float)):
                different = abs(policy_value - official_value) > 0.05
            else:
                different = policy_value != official_value
            if different:
                divergencias.append({"campo": field, "valor_politica": policy_value, "valor_oficial": official_value, "diferenca": None, "classe": "PARIDADE", "motivo": "valor divergente"})
    partial_run = any(str(run.get("status") or "").upper() == "PARCIAL" for run in entrada.runs_usados.values())
    if entrada.indisponiveis or partial_run or official is None:
        classe = "SEM_DADOS"
    elif not divergencias:
        classe = "IGUAL"
    elif _effects_explain(divergencias, result.efeitos_novos):
        classe = "ESPERADA"
    else:
        classe = "INESPERADA"
    payload = {"operation_id": operation_id, "ambiente": entrada.operation.get("ambiente") or "PRODUCAO", "policy_version_id": policy["version"]["id"], "entrada_hash": _hash(entrada.runs_usados, entrada.operation, str(policy["version"]["id"]), ref), "data_referencia": ref.isoformat(), "runs_usados": entrada.runs_usados, "resultado": result.as_dict(), "oficial": official or {}, "divergencias": divergencias, "classe_geral": classe, "parametros_divergentes": []}
    if aplicar:
        database.table("policy_shadow_runs").insert(payload).execute()
    return payload
