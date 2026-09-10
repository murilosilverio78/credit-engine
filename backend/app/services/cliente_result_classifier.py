"""Classificacao semantica de observacoes de componentes de cliente."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import structlog


logger = structlog.get_logger()


@dataclass(frozen=True)
class Classificacao:
    result_state: str
    degradado: bool
    degradacao_motivo: str | None
    fonte: str | None
    error_message: str | None
    fingerprint: Any


def calcular_payload_hash(fingerprint: Any) -> str | None:
    """Calcula hash deterministico da parte comparavel de um resultado."""
    if fingerprint is None:
        return None
    payload = json.dumps(
        fingerprint,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pagination_truncated(result: dict[str, Any]) -> bool:
    pagination = result.get("_pagination")
    return isinstance(pagination, dict) and bool(pagination.get("atingiu_cap"))


def _classification(
    *,
    result_state: str = "OK",
    degradation_reasons: list[str] | None = None,
    source: str | None = None,
    error_message: str | None = None,
    fingerprint: Any = None,
) -> Classificacao:
    reasons = degradation_reasons or []
    return Classificacao(
        result_state=result_state,
        degradado=bool(reasons),
        degradacao_motivo=";".join(reasons) if reasons else None,
        fonte=source,
        error_message=error_message,
        fingerprint=fingerprint,
    )


def classificar(
    component: str,
    result: Any,
    *,
    cotacao_id: str | None,
) -> Classificacao:
    """Classifica um retorno sem alterar o resultado operacional do componente."""
    if not isinstance(result, dict):
        return _classification(
            result_state="ERROR",
            error_message="resultado nao e dict",
        )

    if component == "brasil_api":
        error = None
        state = "OK"
        if not result.get("cnpj") and not result.get("razao_social"):
            state = "ERROR"
            error = "resposta sem identificacao"
        return _classification(
            result_state=state,
            source="BRASIL_API",
            error_message=error,
            fingerprint=result,
        )

    if component == "pessoa_juridica":
        is_empty_body = result.get("erro") == "sem_registro"
        return _classification(
            result_state="ERROR" if is_empty_body else "OK",
            source="PORTAL_TRANSPARENCIA",
            error_message="corpo vazio da fonte" if is_empty_body else None,
            fingerprint=result,
        )

    if component == "contratos":
        reasons = ["paginacao_truncada"] if _pagination_truncated(result) else []
        return _classification(
            result_state="EMPTY" if result.get("total_contratos") == 0 else "OK",
            degradation_reasons=reasons,
            source="PORTAL_TRANSPARENCIA",
            fingerprint=result.get("contratos_detalhe"),
        )

    if component == "recursos_recebidos":
        source = result.get("fonte_primaria")
        reasons = []
        if cotacao_id and source == "PORTAL_TRANSPARENCIA":
            reasons.append("fallback_broadfactor")
        if _pagination_truncated(result):
            reasons.append("paginacao_truncada")
        return _classification(
            result_state="EMPTY" if result.get("total_registros") == 0 else "OK",
            degradation_reasons=reasons,
            source=source,
            fingerprint=result.get("recursos_detalhe"),
        )

    if component in {"ceis", "cnep", "cepim"}:
        return _classification(
            result_state="EMPTY" if result.get("total_registros") == 0 else "OK",
            source="PORTAL_TRANSPARENCIA",
            fingerprint=result.get("registros"),
        )

    if component == "acordos_leniencia":
        return _classification(
            result_state="EMPTY" if result.get("total_acordos") == 0 else "OK",
            source="PORTAL_TRANSPARENCIA",
            fingerprint=result.get("acordos"),
        )

    if component == "web_research":
        flags = result.get("flags_reputacao")
        explicit_failure = (
            isinstance(flags, list) and "reputacao_parse_falhou" in flags
        )
        return _classification(
            result_state="ERROR" if explicit_failure else "OK",
            source="WEB_RESEARCH",
            error_message=(
                "falha explicita no parsing da pesquisa reputacional"
                if explicit_failure
                else None
            ),
            fingerprint=None,
        )

    logger.warning("client.classifier_default", component=component)
    return _classification(fingerprint=result)
