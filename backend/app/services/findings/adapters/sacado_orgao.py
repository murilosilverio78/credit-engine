"""Sources: score_engine.score_relacionamento (908), _ajuste_pd_volatilidade
(426), _cobertura_exposicao (405), and contratos_comprasnet._performance (630)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Escopo


def _date(value: Any):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def emit_sacado_orgao(snapshots: dict[str, Any], *, fingerprint: str, operation: dict[str, Any] | None = None):
    result = []
    contracts = snapshots.get("contratos")
    receipts = snapshots.get("recursos_recebidos")
    if not isinstance(contracts, dict):
        result.extend([
            unverified("contratos_ativos_qtd", Escopo.CEDENTE, component="contratos", path="contratos_ativos", fingerprint=fingerprint),
            unverified("contratos_total_qtd", Escopo.CEDENTE, component="contratos", path="total_contratos", fingerprint=fingerprint),
            unverified("orgaos_distintos_qtd", Escopo.SACADO, component="contratos", path="orgaos_contratantes", fingerprint=fingerprint),
            unverified("maturidade_max_anos", Escopo.CONTRATO, component="contratos", path="contratos_detalhe", fingerprint=fingerprint),
        ])
    else:
        result.extend([
            finding("contratos_ativos_qtd", Escopo.CEDENTE, int(contracts.get("contratos_ativos") or 0), component="contratos", path="contratos_ativos", fingerprint=fingerprint),
            finding("contratos_total_qtd", Escopo.CEDENTE, int(contracts.get("total_contratos") or 0), component="contratos", path="total_contratos", fingerprint=fingerprint),
        ])
        orgaos = {str(org) for org in (contracts.get("orgaos_contratantes") or []) if org}
        result.append(finding("orgaos_distintos_qtd", Escopo.SACADO, len(orgaos), component="contratos", path="orgaos_contratantes", fingerprint=fingerprint))
        durations = []
        for contract in contracts.get("contratos_detalhe") or []:
            if not isinstance(contract, dict):
                continue
            start, end = _date(contract.get("data_inicio") or contract.get("inicio_vigencia")), _date(contract.get("data_fim") or contract.get("fim_vigencia"))
            if start and end:
                durations.append(round((end - start).days / 365.25, 3))
        if durations:
            result.append(finding("maturidade_max_anos", Escopo.CONTRATO, max(durations), component="contratos", path="contratos_detalhe", fingerprint=fingerprint))
        else:
            result.append(unverified("maturidade_max_anos", Escopo.CONTRATO, component="contratos", path="contratos_detalhe", fingerprint=fingerprint))
    if not isinstance(receipts, dict):
        for code, scope, path in (("hhi_recebimentos", Escopo.SACADO, "concentracao.hhi"), ("meses_com_recebimento", Escopo.CEDENTE, "meses_com_recebimento"), ("volatilidade_cv", Escopo.CEDENTE, "volatilidade.cv"), ("anos_completos_receita", Escopo.CEDENTE, "volatilidade.anos_completos")):
            result.append(unverified(code, scope, component="recursos_recebidos", path=path, fingerprint=fingerprint))
    else:
        concentration = receipts.get("concentracao") or {}
        volatility = receipts.get("volatilidade") or {}
        for code, scope, value, path in (
            ("hhi_recebimentos", Escopo.SACADO, concentration.get("hhi"), "concentracao.hhi"),
            ("meses_com_recebimento", Escopo.CEDENTE, receipts.get("meses_com_recebimento"), "meses_com_recebimento"),
            ("volatilidade_cv", Escopo.CEDENTE, volatility.get("cv"), "volatilidade.cv"),
            ("anos_completos_receita", Escopo.CEDENTE, volatility.get("anos_completos"), "volatilidade.anos_completos"),
        ):
            result.append(finding(code, scope, value, component="recursos_recebidos", path=path, fingerprint=fingerprint) if value is not None else unverified(code, scope, component="recursos_recebidos", path=path, fingerprint=fingerprint))
        if operation and operation.get("valor_enquadrado") is not None and receipts.get("faturamento_verificado_12m"):
            result.append(finding("cobertura_exposicao", Escopo.OPERACAO, round(float(operation["valor_enquadrado"]) / float(receipts["faturamento_verificado_12m"]), 4), component="recursos_recebidos", path="faturamento_verificado_12m", fingerprint=fingerprint))
        else:
            result.append(unverified("cobertura_exposicao", Escopo.OPERACAO, component="recursos_recebidos", path="faturamento_verificado_12m", fingerprint=fingerprint))
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    performance = comprasnet.get("performance_contratual") if isinstance(comprasnet, dict) else None
    if isinstance(performance, dict):
        result.append(finding("glosa_historica", Escopo.CONTRATO, {"glosa_total": performance.get("glosa_total"), "taxa_glosa": performance.get("taxa_glosa"), "faturado_total": performance.get("faturado_total")}, component="contratos_comprasnet", path="performance_contratual", fingerprint=fingerprint))
    else:
        result.append(unverified("glosa_historica", Escopo.CONTRATO, component="contratos_comprasnet", path="performance_contratual", fingerprint=fingerprint))
    return result
