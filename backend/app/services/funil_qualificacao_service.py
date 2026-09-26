from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.database import supabase
from app.services.eligibility_params_service import get_eligibility_config
from app.workers.tasks.score_engine import gates_deterministicos


SANCTION_COMPONENTS = ("ceis", "cnep", "cepim", "acordos_leniencia")


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def _months_between(start: date | None, end: date | None) -> int:
    if not start or not end:
        return 0
    return max((end.year - start.year) * 12 + end.month - start.month + 1, 0)


def _parse_month(value: Any) -> date | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    month, year = value.split("/", 1)
    try:
        return date(int(year), int(month), 1)
    except ValueError:
        return None


def _load_snapshots(operation_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    result = (
        supabase.table("component_snapshots")
        .select("component,status,parsed_result")
        .eq("operation_id", operation_id)
        .execute()
    )
    snapshots: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    for row in result.data or []:
        component = row.get("component")
        if not component:
            continue
        statuses[component] = row.get("status") or ""
        if row.get("status") == "completed" and row.get("parsed_result"):
            snapshots[component] = row["parsed_result"]
    return snapshots, statuses


def _load_operation(operation_id: str) -> dict[str, Any]:
    result = (
        supabase.table("operations")
        .select("id,valor_enquadrado,prazo_final_meses,prazo_dias")
        .eq("id", operation_id)
        .maybe_single()
        .execute()
    )
    return result.data or {}


def _contract_found(snapshots: dict[str, Any]) -> bool:
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    return comprasnet.get("status_consulta") == "ENCONTRADO"


def _prazo_dias(operation: dict[str, Any], snapshots: dict[str, Any]) -> int:
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    months = _as_int(
        comprasnet.get("prazo_vincendo_meses")
        or operation.get("prazo_final_meses")
    )
    if months > 0:
        return months * 30
    return _as_int(operation.get("prazo_dias"))


def _received_history(snapshots: dict[str, Any]) -> tuple[int, int, float]:
    recursos = snapshots.get("recursos_recebidos") or {}
    orgaos = recursos.get("orgaos_pagadores") or []
    total = _as_float(recursos.get("valor_total_recebido"))
    start = _parse_month(recursos.get("periodo_inicio"))
    end = _parse_month(recursos.get("periodo_fim"))
    if not start or not end:
        detalhes = recursos.get("recursos_detalhe") or []
        meses = sorted(
            str(item.get("mes"))
            for item in detalhes
            if isinstance(item, dict) and item.get("mes")
        )
        if meses:
            first = meses[0]
            last = meses[-1]
            start = _parse_date(f"{first[:4]}-{first[4:6]}-01")
            end = _parse_date(f"{last[:4]}-{last[4:6]}-01")
    return _months_between(start, end), len(set(orgaos)), total


def avaliar_qualificacao_funil(operation_id: str) -> tuple[bool, list[str]]:
    operation = _load_operation(operation_id)
    snapshots, statuses = _load_snapshots(operation_id)
    motivos: list[str] = []

    if not operation:
        return False, ["operacao_nao_encontrada"]

    deterministic = gates_deterministicos(snapshots)
    motivos.extend(deterministic)

    for component in SANCTION_COMPONENTS:
        if statuses.get(component) != "completed":
            motivos.append(f"fonte_sancao_nao_verificada:{component}")

    if not _contract_found(snapshots):
        motivos.append("contrato_comprasnet_nao_encontrado")

    params = get_eligibility_config()
    prazo_minimo = _as_int(params.get("prazo_minimo_dias")) or 60
    prazo = _prazo_dias(operation, snapshots)
    if prazo < prazo_minimo:
        motivos.append(f"prazo_vincendo_insuficiente:{prazo}d")

    hist_min = _as_int(params.get("funil_hist_min_meses")) or 6
    orgaos_min = _as_int(params.get("funil_orgaos_min")) or 2
    cobertura_min = _as_float(params.get("funil_cobertura_min")) or 2.0
    hist_meses, n_orgaos, recebido_total = _received_history(snapshots)
    if hist_meses < hist_min:
        motivos.append(f"historico_recebimentos_insuficiente:{hist_meses}m")
    if n_orgaos < orgaos_min:
        motivos.append(f"orgaos_pagadores_insuficientes:{n_orgaos}")

    valor_enquadrado = _as_float(operation.get("valor_enquadrado"))
    cobertura = recebido_total / valor_enquadrado if valor_enquadrado > 0 else 0.0
    if cobertura < cobertura_min:
        motivos.append(f"cobertura_insuficiente:{cobertura:.2f}")

    return not motivos, motivos


def atualizar_estagio_pos_fase2(cotacao_id: str, operation_id: str) -> tuple[str, list[str]]:
    qualificada, motivos = avaliar_qualificacao_funil(operation_id)
    estagio = "QUALIFICADA" if qualificada else "DOCUMENTADA"
    data = {
        "estagio": estagio,
        "estagio_max": estagio,
        "estagio_motivo": "; ".join(motivos) if motivos else None,
        "estagio_atualizado_em": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "status_ingestao": "AGUARDANDO_RELATORIO" if qualificada else "DOCUMENTADA",
    }
    supabase.table("cotacoes_broadfactor").update(data).eq("cotacao_id", cotacao_id).execute()
    return estagio, motivos
