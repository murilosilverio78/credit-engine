"""Operation-scoped financial findings from the client financial snapshot."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Confianca, Escopo, Estado


def _confidence(value: Any, fallback: Confianca = Confianca.BAIXA) -> Confianca:
    value = (value or "").upper()
    return Confianca(value) if value in Confianca.__members__ else fallback


def _evidence(
    payload: dict[str, Any],
    family: str | None = None,
    field: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    doc = next(
        (
            item
            for item in payload.get("documentos", [])
            if not family or item.get("familia") == family
        ),
        {},
    )
    return [
        {
            "componente": "documentos_financeiros",
            "sha256": doc.get("sha256"),
            "familia": family or doc.get("familia"),
            "pagina": (field or {}).get("pagina"),
            "trecho": (field or {}).get("trecho"),
        }
    ]


def _emit(
    codigo: str,
    escopo: Escopo,
    value: Any,
    payload: dict[str, Any],
    *,
    fingerprint: str,
    state: Estado = Estado.CONFIRMADO,
    confidence: Confianca = Confianca.MEDIA,
    family: str | None = None,
    field: dict[str, Any] | None = None,
    entity: str | None = None,
    pendencia: dict | None = None,
):
    candidate = finding(
        codigo,
        escopo,
        value,
        component="documentos_financeiros",
        path=codigo,
        fingerprint=fingerprint,
        state=state,
        confidence=confidence,
        entity=entity,
        pendencia=pendencia,
    )
    candidate.evidencia = _evidence(payload, family, field)
    return candidate


def _num(value: Any) -> float | None:
    if isinstance(value, dict):
        if value.get("ausente_no_documento"):
            return None
        value = value.get("valor")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _contract_key(value: Any) -> tuple[int, int] | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 5:
        return int(digits[:-4] or 0), int(digits[-4:])
    match = re.search(r"(\d+)\s*(?:/|de)\s*(20\d{2})", str(value or ""), re.I)
    return (int(match.group(1)), int(match.group(2))) if match else None


def emit_financeiro(
    snapshots: dict[str, Any], *, fingerprint: str, operation: dict[str, Any]
):
    payload = snapshots.get("documentos_financeiros")
    if not isinstance(payload, dict):
        return [
            unverified(
                "documento_financeiro_ausente",
                Escopo.CEDENTE,
                component="documentos_financeiros",
                path="snapshot",
                fingerprint=fingerprint,
            )
        ]
    output = []
    documents = payload.get("documentos") or []
    families = {
        row.get("familia") for row in documents if row.get("status") == "EXTRAIDO"
    }
    missing = []
    if not {"SPED_ECD", "BALANCO_DRE", "BALANCETE"}.intersection(families):
        missing.append("demonstracao_anual")
    if "FATURAMENTO" not in families:
        missing.append("faturamento")
    if not {"SCR", "ENDIVIDAMENTO_DECLARADO"}.intersection(families):
        missing.append("endividamento")
    if "IRPF" not in families:
        missing.append("irpf_avalista")
    output.append(
        _emit(
            "documento_financeiro_ausente",
            Escopo.CEDENTE,
            {"familias": missing},
            payload,
            fingerprint=fingerprint,
            state=Estado.NAO_VERIFICADO if missing else Estado.NEGATIVO_CONFIRMADO,
            pendencia={"motivo": "familias_sem_extracao", "familias": missing}
            if missing
            else None,
        )
    )
    checks = {item.get("codigo"): item for item in payload.get("verificacoes", [])}
    for code, finding_code in (("balanco_fecha", "balanco_inconsistente"),):
        item = checks.get(code, {})
        state = (
            Estado.CONFIRMADO
            if item.get("resultado") == "FALHA"
            else Estado.NEGATIVO_CONFIRMADO
            if item.get("resultado") == "OK"
            else Estado.NAO_VERIFICADO
        )
        output.append(
            _emit(
                finding_code,
                Escopo.CEDENTE,
                item.get("resultado") == "FALHA"
                if state != Estado.NAO_VERIFICADO
                else None,
                payload,
                fingerprint=fingerprint,
                state=state,
            )
        )
    stale = [
        key
        for key in ("balanco_atual", "faturamento_atual")
        if checks.get(key, {}).get("resultado") == "FALHA"
    ]
    output.append(
        _emit(
            "documento_financeiro_desatualizado",
            Escopo.CEDENTE,
            {"verificacoes": stale} if stale else {},
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO if stale else Estado.NEGATIVO_CONFIRMADO,
        )
    )
    indicators = payload.get("indicadores") or {}
    for code in (
        "liquidez_corrente",
        "alavancagem_declarada",
        "servico_divida_sobre_faturamento",
        "margem_liquida",
    ):
        item = indicators.get(code) or {}
        value = _num(item.get("valor"))
        state = Estado.CONFIRMADO if value is not None else Estado.NAO_VERIFICADO
        output.append(
            _emit(
                code,
                Escopo.CEDENTE,
                value,
                payload,
                fingerprint=fingerprint,
                state=state,
                pendencia={"motivo": item.get("motivo")}
                if state == Estado.NAO_VERIFICADO
                else None,
            )
        )
    revenue = payload.get("faturamento_12m") or {}
    revenue_value = _num(revenue.get("soma_calculada"))
    output.append(
        _emit(
            "receita_declarada_12m",
            Escopo.CEDENTE,
            revenue_value,
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO
            if revenue_value is not None
            else Estado.NAO_VERIFICADO,
        )
    )
    for divergence in payload.get("divergencias") or []:
        output.append(
            _emit(
                "divergencia_documental",
                Escopo.CEDENTE,
                divergence,
                payload,
                fingerprint=fingerprint,
            )
        )
    current = (payload.get("demonstracoes") or [{}])[0]
    pl = _num(current.get("patrimonio_liquido"))
    output.append(
        _emit(
            "patrimonio_liquido_negativo",
            Escopo.CEDENTE,
            pl,
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO
            if pl is not None and pl < 0
            else Estado.NEGATIVO_CONFIRMADO
            if pl is not None
            else Estado.NAO_VERIFICADO,
            family=current.get("fonte"),
            field=current.get("patrimonio_liquido"),
        )
    )
    key = _contract_key(operation.get("contrato_id"))
    same, other = [], []
    for debt in (payload.get("endividamento") or {}).get("fontes", []):
        for creditor in debt.get("dados", {}).get("credores", []):
            if creditor.get("cessao_recebiveis"):
                (
                    same
                    if key
                    and _contract_key(creditor.get("contrato_cedido_referencia")) == key
                    else other
                ).append(creditor)
    output.append(
        _emit(
            "cessao_previa_mesmo_contrato",
            Escopo.CONTRATO,
            {"cessoes": same},
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO if same else Estado.NEGATIVO_CONFIRMADO,
            confidence=Confianca.MEDIA,
        )
    )
    output.append(
        _emit(
            "cessao_recebiveis_outros_contratos",
            Escopo.CEDENTE,
            {"cessoes": other},
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO if other else Estado.NEGATIVO_CONFIRMADO,
            confidence=Confianca.MEDIA,
        )
    )
    recursos = snapshots.get("recursos_recebidos") or {}
    pncp = snapshots.get("contratos_pncp") or {}
    received, contracted = (
        _num(recursos.get("faturamento_verificado_12m")),
        _num(pncp.get("faturamento_contratado_12m")),
    )
    output.append(
        _emit(
            "incoerencia_receita_publica",
            Escopo.CEDENTE,
            {"receita": revenue_value, "recebido": received},
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFLITO
            if revenue_value is not None
            and received is not None
            and revenue_value < received
            else Estado.NEGATIVO_CONFIRMADO
            if revenue_value is not None and received is not None
            else Estado.NAO_VERIFICADO,
        )
    )
    output.append(
        _emit(
            "dependencia_publica",
            Escopo.CEDENTE,
            received / revenue_value
            if received is not None and revenue_value
            else None,
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO
            if received is not None and revenue_value
            else Estado.NAO_VERIFICADO,
        )
    )
    output.append(
        _emit(
            "salto_escala_real",
            Escopo.CEDENTE,
            contracted / revenue_value
            if contracted is not None and revenue_value
            else None,
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO
            if contracted is not None and revenue_value
            else Estado.NAO_VERIFICADO,
        )
    )
    brasil = snapshots.get("brasil_api") or {}
    activity = str(
        brasil.get("atividade_principal") or brasil.get("descricao_atividade") or ""
    ).lower()
    exceptions = ("construcao", "obras", "vigilancia", "limpeza", "conservacao")
    applies = (
        brasil.get("opcao_simples") is True
        and operation.get("contrato_dedicacao_exclusiva") is True
    )
    simple_state = (
        Estado.AMBIGUO
        if applies and any(word in activity for word in exceptions)
        else Estado.CONFIRMADO
        if applies
        else Estado.NEGATIVO_CONFIRMADO
        if brasil
        else Estado.NAO_VERIFICADO
    )
    output.append(
        _emit(
            "simples_com_cessao_mao_obra",
            Escopo.CEDENTE,
            bool(applies),
            payload,
            fingerprint=fingerprint,
            state=simple_state,
            confidence=Confianca.MEDIA,
        )
    )
    avalistas = payload.get("avalistas") or []
    operation_value = _num(operation.get("valor_enquadrado"))
    coverage = sum(item.get("patrimonio_liquido_declarado") or 0 for item in avalistas)
    output.append(
        _emit(
            "aval_cobertura",
            Escopo.OPERACAO,
            coverage / operation_value if operation_value else None,
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO
            if operation_value and avalistas
            else Estado.NAO_VERIFICADO,
        )
    )
    non_verifiable = [
        {"cpf_final": item.get("cpf_final"), "bens": item.get("bens")}
        for item in avalistas
    ]
    output.append(
        _emit(
            "aval_patrimonio_nao_verificavel",
            Escopo.OPERACAO,
            non_verifiable,
            payload,
            fingerprint=fingerprint,
            state=Estado.CONFIRMADO if avalistas else Estado.NAO_VERIFICADO,
        )
    )
    socios = (payload.get("societario") or {}).get("socios") or []
    irpf_names = {str(item.get("nome") or "").strip().lower() for item in avalistas}
    for socio in socios:
        name = str(socio.get("nome") or "").strip()
        if socio.get("administrador") and name.lower() not in irpf_names:
            output.append(
                _emit(
                    "avalista_sem_irpf",
                    Escopo.OPERACAO,
                    {"nome": name},
                    payload,
                    fingerprint=fingerprint,
                    state=Estado.CONFIRMADO,
                    entity=name,
                )
            )
    if not any(socio.get("administrador") for socio in socios):
        output.append(
            _emit(
                "avalista_sem_irpf",
                Escopo.OPERACAO,
                None,
                payload,
                fingerprint=fingerprint,
                state=Estado.NAO_VERIFICADO,
                pendencia={"motivo": "societario_ausente"},
            )
        )
    return output
