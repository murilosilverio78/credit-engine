"""Shadow comparison and append-only persistence for policy v0."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from app.services.policy.engine import avaliar
from app.services.policy.inputs import montar_entrada
from app.services.policy.loader import load_policy

_FIELDS = ("merit", "merit_potencial", "fator_regularidade", "fator_potencial", "penalizacao_balanco", "score", "rating", "rating_potencial", "limite_aprovado_rs", "bloqueios", "ajuste_pd")
_PARITY_SPECIALISTS = frozenset({"cadastro_regularidade", "sacado_orgao"})
_AJUSTE_PD_FIELDS = ("faixa_volatilidade", "multiplicador_volatilidade", "pd_base", "pd_ajustada")
_AJUSTE_PD_CURRENT_FIELDS = frozenset({"min_anos_completos", "anos_completos"})
_INPUT_COMPONENTS = frozenset({
    "brasil_api", "pessoa_juridica", "ceis", "cnep", "cepim",
    "acordos_leniencia", "cnd_federal", "cndt_tst", "fgts", "ceaf",
    "contrato_extracao", "contratos", "recursos_recebidos",
    "contratos_comprasnet", "web_research",
})


def _rows(query) -> list[dict[str, Any]]:
    result = query.execute()
    return list((result.data or []) if result else [])


def _reference(value: Any) -> date:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return date.today()


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _input_components_after_score(database, operation_id: str) -> list[str]:
    """Return completed score inputs newer than the persisted official score."""
    try:
        score_rows = _rows(database.table("component_snapshots").select("completed_at,created_at").eq("operation_id", operation_id).eq("component", "score_engine").eq("status", "completed"))
        if not score_rows:
            return []
        score_completed_at = max(
            (
                timestamp
                for row in score_rows
                if (timestamp := _timestamp(row.get("completed_at") or row.get("created_at"))) is not None
            ),
            default=None,
        )
        if score_completed_at is None:
            return []
        rows = _rows(database.table("component_snapshots").select("component,completed_at").eq("operation_id", operation_id).eq("status", "completed"))
    except Exception:
        return []
    return sorted({
        str(row["component"])
        for row in rows
        if row.get("component") in _INPUT_COMPONENTS
        and (_timestamp(row.get("completed_at")) or score_completed_at) > score_completed_at
    })


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


def _non_comparable(field: str, policy_value: Any, official_value: Any, reason: str) -> dict[str, Any]:
    return {
        "campo": field,
        "valor_politica": policy_value,
        "valor_oficial": official_value,
        "diferenca": None,
        "classe": "NAO_COMPARAVEL",
        "motivo": reason,
    }


def _ajuste_pd_values(policy_value: Any, official_value: Any) -> tuple[Any, Any] | None:
    """Project the current PD contract, or flag old persisted snapshots."""
    if not isinstance(official_value, dict):
        return policy_value, official_value
    if not _AJUSTE_PD_CURRENT_FIELDS <= set(official_value):
        return None
    policy_pd = policy_value if isinstance(policy_value, dict) else {}
    return (
        {key: policy_pd.get(key) for key in _AJUSTE_PD_FIELDS},
        {key: official_value.get(key) for key in _AJUSTE_PD_FIELDS},
    )


def _missing_parity_specialists(runs_usados: dict[str, dict[str, str]], official: dict[str, Any] | None) -> set[str]:
    """Return only the runs needed to compare the persisted score.

    ``documentos`` feeds a policy effect, but not score parity.  Porte and
    reputacao are only comparable when an official score exists.
    """
    required = _PARITY_SPECIALISTS | ({"reputacional", "porte"} if official is not None else set())
    return required - set(runs_usados)


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
    insumos_posteriores = _input_components_after_score(database, operation_id) if official is not None else []
    if official is not None:
        official = {**official, "_insumos_posteriores": insumos_posteriores}
    _set_pct_max_contrato(entrada.operation, official)
    result = avaliar(entrada, policy["parametros"], policy["regras"], ref)
    divergencias = []
    if official:
        computed = result.as_dict()
        for field in _FIELDS:
            policy_value = computed.get(field)
            if field not in official:
                divergencias.append(_non_comparable(field, policy_value, None, "campo ausente no snapshot oficial"))
                continue
            official_value = official[field]
            if field == "ajuste_pd":
                adjusted = _ajuste_pd_values(policy_value, official_value)
                if adjusted is None:
                    divergencias.append(_non_comparable(field, policy_value, official_value, "formato antigo de ajuste_pd sem anos completos"))
                    continue
                policy_value, official_value = adjusted
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
    parity_divergencias = [item for item in divergencias if item["classe"] == "PARIDADE"]
    if parity_divergencias and insumos_posteriores:
        for divergence in parity_divergencias:
            divergence.update({
                "classe": "INSUMO_POSTERIOR",
                "motivo": "insumos refeitos apos o score oficial",
                "componentes_posteriores": insumos_posteriores,
            })
    if _missing_parity_specialists(entrada.runs_usados, official) or partial_run or official is None or (parity_divergencias and insumos_posteriores):
        classe = "SEM_DADOS"
    elif not parity_divergencias:
        classe = "IGUAL"
    elif _effects_explain(parity_divergencias, result.efeitos_novos):
        classe = "ESPERADA"
    else:
        classe = "INESPERADA"
    payload = {"operation_id": operation_id, "ambiente": entrada.operation.get("ambiente") or "PRODUCAO", "policy_version_id": policy["version"]["id"], "entrada_hash": _hash(entrada.runs_usados, entrada.operation, str(policy["version"]["id"]), ref), "data_referencia": ref.isoformat(), "runs_usados": entrada.runs_usados, "resultado": result.as_dict(), "oficial": official or {}, "divergencias": divergencias, "classe_geral": classe, "parametros_divergentes": []}
    if aplicar:
        database.table("policy_shadow_runs").insert(payload).execute()
    return payload
