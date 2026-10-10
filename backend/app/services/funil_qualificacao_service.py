from __future__ import annotations

from datetime import date, datetime, timezone
from math import floor
from typing import Any

from app.core.database import supabase
from app.services.eligibility_params_service import get_eligibility_config
from app.workers.tasks.score_engine import gates_deterministicos


SANCTION_COMPONENTS = ("ceis", "cnep", "cepim", "acordos_leniencia")
UNAVAILABLE_SOURCE_PREFIX = "indisponibilidade_fonte:"
TECHNICAL_REASONS = frozenset({
    "situacao_cadastral_nao_verificada",
    "indisponibilidade_fonte:contrato_cedido",
})


def classificar_motivos(motivos: list[str]) -> dict[str, list[str]]:
    """Separa pendências de coleta de reprovações de elegibilidade.

    This boundary is intentionally based on stable persisted reason codes, not
    on their presentation labels.  It is shared by the funnel state machine
    and the API serializer so a technical collection failure can never acquire
    a different colour/meaning in the UI.
    """
    tecnico = [
        motivo
        for motivo in motivos
        if motivo in TECHNICAL_REASONS
        or motivo.startswith(UNAVAILABLE_SOURCE_PREFIX)
    ]
    tecnico_set = set(tecnico)
    return {
        "reprovacao": [motivo for motivo in motivos if motivo not in tecnico_set],
        "tecnico": tecnico,
    }


def decidir_status_pos_fase2(motivos: list[str]) -> dict[str, Any]:
    """Translate phase-two reasons into the operation state-machine outcome."""
    classificados = classificar_motivos(motivos)
    pendencia_coleta = bool(classificados["tecnico"]) and not classificados["reprovacao"]
    return {
        "status": "reprovada_triagem" if classificados["reprovacao"] else "aguardando_relatorio",
        "pendencia_coleta": pendencia_coleta,
        "motivos_classificados": classificados,
    }


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
        .select(
            "id,valor_enquadrado,valor_enquadrado_pre_capacidade,"
            "capacidade_contrato,capacidade_memoria,flags_funil,"
            "valor_global_contrato,contrato_vigencia_inicio,"
            "contrato_vigencia_fim,contrato_dedicacao_exclusiva,"
            "prazo_final_meses,prazo_dias"
        )
        .eq("id", operation_id)
        .maybe_single()
        .execute()
    )
    return result.data or {}


def _contract_found(snapshots: dict[str, Any]) -> bool:
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    return comprasnet.get("status_consulta") == "ENCONTRADO"


def _contract_verification_unavailable(snapshots: dict[str, Any]) -> bool:
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    status = comprasnet.get("status_consulta")
    if status == "NAO_VERIFICADO":
        return True
    return (
        status == "NAO_ENCONTRADO"
        and comprasnet.get("motivo")
        in {"uasg_indisponivel", "contrato_sem_match_cnpj"}
        and comprasnet.get("busca_exaustiva") is not True
    )


def _prazo_dias(operation: dict[str, Any], snapshots: dict[str, Any]) -> int:
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    months = _as_int(
        comprasnet.get("prazo_vincendo_meses")
        or operation.get("prazo_final_meses")
    )
    if months > 0:
        return months * 30
    return _as_int(operation.get("prazo_dias"))


def _received_history(snapshots: dict[str, Any]) -> tuple[int, int]:
    recursos = snapshots.get("recursos_recebidos") or {}
    orgaos = recursos.get("orgaos_pagadores") or []
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
    return _months_between(start, end), len(set(orgaos))


def _contrato_cedido(
    operation: dict[str, Any], snapshots: dict[str, Any], statuses: dict[str, str]
) -> dict[str, Any] | None:
    if statuses.get("contratos_pncp") != "completed":
        return None
    pncp = snapshots.get("contratos_pncp")
    if not isinstance(pncp, dict) or pncp.get("contrato_cedido_match") == "NAO_ENCONTRADO":
        return None
    cedido = pncp.get("contrato_cedido")
    if not isinstance(cedido, dict):
        return None
    contract = {
        "valor_global": cedido.get("valor_global") or operation.get("valor_global_contrato"),
        "vigencia_inicio": cedido.get("data_inicio_vigencia") or operation.get("contrato_vigencia_inicio"),
        "vigencia_fim": cedido.get("data_fim_vigencia") or operation.get("contrato_vigencia_fim"),
        "dedicacao_exclusiva": (
            cedido.get("dedicacao_exclusiva")
            if cedido.get("dedicacao_exclusiva") is not None
            else operation.get("contrato_dedicacao_exclusiva")
        ),
    }
    if not contract["valor_global"] or not contract["vigencia_inicio"] or not contract["vigencia_fim"]:
        return None
    return contract


def _capacidade_do_contrato(
    operation: dict[str, Any], snapshots: dict[str, Any], statuses: dict[str, str], params: dict[str, Any]
) -> dict[str, Any] | None:
    contract = _contrato_cedido(operation, snapshots, statuses)
    if not contract:
        return None
    from app.services.capacidade_contrato_service import calcular_capacidade_contrato

    try:
        result = calcular_capacidade_contrato(
            contract["valor_global"],
            contract["vigencia_inicio"],
            contract["vigencia_fim"],
            contract["dedicacao_exclusiva"],
            params,
        )
    except ValueError:
        return None
    base = _as_float(
        operation.get("valor_enquadrado_pre_capacidade")
        if operation.get("valor_enquadrado_pre_capacidade") not in (None, "")
        else operation.get("valor_enquadrado")
    )
    capacity = _as_float(result["capacidade"])
    framed = floor(min(base, capacity) / 1000) * 1000
    return {
        **result,
        "base": base,
        "valor_enquadrado": framed,
        "reduziu": framed < base,
        "reenquadrado": framed < _as_float(operation.get("valor_enquadrado")),
    }


def _flags_salto_escala(snapshots: dict[str, Any], params: dict[str, Any]) -> list[str]:
    pncp = snapshots.get("contratos_pncp") or {}
    recursos = snapshots.get("recursos_recebidos") or {}
    contratado = _as_float(pncp.get("faturamento_contratado_12m"))
    recebido = _as_float(recursos.get("faturamento_verificado_12m"))
    threshold = _as_float(params.get("alerta_salto_escala")) or 1.5
    if contratado <= 0:
        return []
    if recebido <= 0:
        return ["salto_escala:sem_historico"]
    ratio = contratado / recebido
    return [f"salto_escala:{ratio:.1f}"] if ratio > threshold else []


def _avaliar_com_dados(
    operation: dict[str, Any], snapshots: dict[str, Any], statuses: dict[str, str], params: dict[str, Any]
) -> list[str]:
    motivos: list[str] = []
    motivos.extend(gates_deterministicos(snapshots))

    cadastro = snapshots.get("brasil_api") or {}
    situacao = (
        cadastro.get("situacao_cadastral")
        or cadastro.get("descricao_situacao_cadastral")
        or cadastro.get("situacao")
    )
    if not situacao:
        motivos.append("situacao_cadastral_nao_verificada")

    for component in SANCTION_COMPONENTS:
        if statuses.get(component) != "completed":
            motivos.append(f"{UNAVAILABLE_SOURCE_PREFIX}{component}")

    capacidade = _capacidade_do_contrato(operation, snapshots, statuses, params)
    if not capacidade:
        motivos.append("indisponibilidade_fonte:contrato_cedido")
    elif capacidade["valor_enquadrado"] < _as_float(params.get("ticket_minimo")):
        motivos.append(f"capacidade_insuficiente:{capacidade['capacidade']:.0f}")

    if (
        statuses.get("contratos_comprasnet") != "completed"
        or _contract_verification_unavailable(snapshots)
    ):
        motivos.append(f"{UNAVAILABLE_SOURCE_PREFIX}contratos_comprasnet")
    elif not _contract_found(snapshots):
        motivos.append("contrato_comprasnet_nao_encontrado")

    prazo_minimo = _as_int(params.get("prazo_minimo_dias")) or 60
    prazo = _prazo_dias(operation, snapshots)
    if prazo < prazo_minimo:
        motivos.append(f"prazo_vincendo_insuficiente:{prazo}d")

    hist_min = _as_int(params.get("funil_hist_min_meses")) or 6
    orgaos_min = _as_int(params.get("funil_orgaos_min")) or 2
    hist_meses, n_orgaos = _received_history(snapshots)
    if hist_meses < hist_min:
        motivos.append(f"historico_recebimentos_insuficiente:{hist_meses}m")
    if n_orgaos < orgaos_min:
        motivos.append(f"orgaos_pagadores_insuficientes:{n_orgaos}")

    return motivos


def avaliar_qualificacao_funil(operation_id: str) -> tuple[bool, list[str]]:
    operation = _load_operation(operation_id)
    if not operation:
        return False, ["operacao_nao_encontrada"]
    snapshots, statuses = _load_snapshots(operation_id)
    motivos = _avaliar_com_dados(
        operation, snapshots, statuses, get_eligibility_config()
    )

    return not motivos, motivos


def atualizar_estagio_pos_fase2(cotacao_id: str, operation_id: str) -> tuple[str, list[str]]:
    operation = _load_operation(operation_id)
    snapshots, statuses = _load_snapshots(operation_id)
    params = get_eligibility_config()
    capacidade = _capacidade_do_contrato(operation, snapshots, statuses, params)
    flags = _flags_salto_escala(snapshots, params)
    previous_flags = [
        flag for flag in (operation.get("flags_funil") or [])
        if not str(flag).startswith("salto_escala:")
    ]
    operation_updates: dict[str, Any] = {"flags_funil": [*previous_flags, *flags]}
    if capacidade:
        valor_anterior = operation.get("valor_enquadrado")
        operation_updates.update({
            "capacidade_contrato": capacidade["capacidade"],
            "capacidade_memoria": capacidade["memoria"],
        })
        if capacidade["reduziu"]:
            # A memória usa sempre a base original; só escrevemos/auditamos o
            # valor quando ele realmente muda nesta reavaliação.
            if capacidade["reenquadrado"]:
                operation_updates["valor_enquadrado"] = capacidade["valor_enquadrado"]
            if operation.get("valor_enquadrado_pre_capacidade") in (None, ""):
                operation_updates["valor_enquadrado_pre_capacidade"] = capacidade["base"]
        supabase.table("operations").update(operation_updates).eq("id", operation_id).execute()
        operation = {**operation, **operation_updates}
        if capacidade["reenquadrado"]:
            supabase.table("cotacoes_broadfactor").update({
                "valor_enquadrado": capacidade["valor_enquadrado"],
            }).eq("cotacao_id", cotacao_id).execute()
            from app.services.audit_service import AuditService

            AuditService().log(
                operation_id=operation_id,
                action="operation_status_changed",
                actor_type="system",
                previous_value={"valor_enquadrado": valor_anterior},
                new_value={"valor_enquadrado": capacidade["valor_enquadrado"]},
                payload={"contexto": "reavaliacao_capacidade", "memoria": capacidade["memoria"]},
            )
    else:
        supabase.table("operations").update(operation_updates).eq("id", operation_id).execute()

    motivos = _avaliar_com_dados(operation, snapshots, statuses, params)
    qualificada = not motivos
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
