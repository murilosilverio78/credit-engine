"""Sources: score_engine.score_relacionamento (908), _ajuste_pd_volatilidade
(426), _cobertura_exposicao (405), and contratos_comprasnet._performance (630)."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any

from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Escopo


def _date(value: Any):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _score_float(value)
    return int(number) if number is not None else None


def _number(value: Any) -> float:
    return _score_float(value) or 0.0


def _score_float(value: Any) -> float | None:
    """Use score-engine's tolerant conversion without duplicating its rules."""
    from app.workers.tasks.score_engine import _as_float

    return _as_float(value)


def _annual_series_fact(receipts: dict[str, Any]) -> dict[str, Any]:
    volatility = receipts.get("volatilidade") or {}
    volatility = volatility if isinstance(volatility, dict) else {}
    series = receipts.get("serie_anual") or receipts.get("valor_por_ano") or {}
    series = series if isinstance(series, dict) else {}
    years = sorted({year for key in series for year in [_integer(key)] if year is not None})
    return {
        "anos_informados": _integer(volatility.get("anos_completos")) if isinstance(volatility, dict) else None,
        "serie_qtd_chaves": len(series),
        "anos_na_serie": years,
    }


def emit_sacado_orgao(
    snapshots: dict[str, Any], *, fingerprint: str,
    operation: dict[str, Any] | None = None, database=None,
):
    result = []
    # The score helper mutates only its supplied snapshot copy.  Reusing it
    # preserves the exact eligibility, de-duplication and date semantics.
    relationship_snapshots = deepcopy(snapshots)
    operation = operation or {}
    operation_id = operation.get("id")
    cnpj = operation.get("cnpj")
    if database is not None and operation_id and cnpj:
        from app.workers.tasks.score_engine import _add_verified_comprasnet_contract_for_score
        _add_verified_comprasnet_contract_for_score(
            str(operation_id), str(cnpj), relationship_snapshots, database,
        )
    contracts = relationship_snapshots.get("contratos")
    receipts = snapshots.get("recursos_recebidos")
    if not isinstance(contracts, dict):
        result.extend([
            unverified("contratos_ativos_qtd", Escopo.CEDENTE, component="contratos", path="contratos_ativos", fingerprint=fingerprint),
            unverified("contratos_total_qtd", Escopo.CEDENTE, component="contratos", path="total_contratos", fingerprint=fingerprint),
            unverified("orgaos_distintos_qtd", Escopo.SACADO, component="contratos", path="orgaos_contratantes", fingerprint=fingerprint),
            unverified("maturidade_max_anos", Escopo.CONTRATO, component="contratos", path="contratos_detalhe", fingerprint=fingerprint),
        ])
    else:
        from app.workers.tasks.score_engine import _active_contracts, _contract_duration_years
        result.extend([
            finding("contratos_ativos_qtd", Escopo.CEDENTE, _integer(contracts.get("contratos_ativos")) or 0, component="contratos", path="contratos_ativos", fingerprint=fingerprint),
            finding("contratos_total_qtd", Escopo.CEDENTE, _integer(contracts.get("total_contratos")) or 0, component="contratos", path="total_contratos", fingerprint=fingerprint),
        ])
        declared_orgaos = contracts.get("orgaos_contratantes")
        if isinstance(declared_orgaos, list):
            orgaos = {str(org) for org in declared_orgaos if org}
        else:
            orgaos = {
                str(contract.get("orgao") or contract.get("orgao_nome") or contract.get("contratante"))
                for contract in _active_contracts(contracts)
                if contract.get("orgao") or contract.get("orgao_nome") or contract.get("contratante")
            }
        result.append(finding("orgaos_distintos_qtd", Escopo.SACADO, len(orgaos), component="contratos", path="orgaos_contratantes", fingerprint=fingerprint))
        active_contracts = _active_contracts(contracts)
        ativos = _integer(contracts.get("contratos_ativos")) or len(active_contracts) or 0
        total = _integer(contracts.get("total_contratos")) or len(contracts.get("contratos_detalhe") or []) or ativos
        result[0] = finding("contratos_ativos_qtd", Escopo.CEDENTE, ativos, component="contratos", path="contratos_ativos", fingerprint=fingerprint)
        result[1] = finding("contratos_total_qtd", Escopo.CEDENTE, total, component="contratos", path="total_contratos", fingerprint=fingerprint)
        durations = []
        for contract in active_contracts:
            if not isinstance(contract, dict):
                continue
            duration = _contract_duration_years(contract)
            if duration is not None:
                durations.append(duration)
        if durations:
            result.append(finding("maturidade_max_anos", Escopo.CONTRATO, max(durations), component="contratos", path="contratos_detalhe", fingerprint=fingerprint))
        else:
            result.append(unverified("maturidade_max_anos", Escopo.CONTRATO, component="contratos", path="contratos_detalhe", fingerprint=fingerprint))
        result.append(finding(
            "contratos_comprasnet_incluidos_qtd", Escopo.CONTRATO,
            _integer(contracts.get("contratos_comprasnet_incluidos")) or 0,
            component="contratos", path="contratos_comprasnet_incluidos",
            fingerprint=fingerprint,
        ))
        result.append(finding(
            "contratos_valor_total_ativo_rs", Escopo.CONTRATO,
            _number(contracts.get("valor_total_ativo")), component="contratos",
            path="valor_total_ativo", fingerprint=fingerprint,
        ))
    if not isinstance(receipts, dict):
        for code, scope, path in (("hhi_recebimentos", Escopo.SACADO, "concentracao.hhi"), ("meses_com_recebimento", Escopo.CEDENTE, "meses_com_recebimento"), ("volatilidade_cv", Escopo.CEDENTE, "volatilidade.cv"), ("anos_completos_receita", Escopo.CEDENTE, "volatilidade.anos_completos"), ("receita_serie_anual", Escopo.CEDENTE, "volatilidade.anos_completos,serie_anual,valor_por_ano")):
            result.append(unverified(code, scope, component="recursos_recebidos", path=path, fingerprint=fingerprint))
    else:
        concentration = receipts.get("concentracao") or {}
        concentration = concentration if isinstance(concentration, dict) else {}
        volatility = receipts.get("volatilidade") or {}
        volatility = volatility if isinstance(volatility, dict) else {}
        for code, scope, value, path in (
            ("hhi_recebimentos", Escopo.SACADO, concentration.get("hhi"), "concentracao.hhi"),
            ("meses_com_recebimento", Escopo.CEDENTE, receipts.get("meses_com_recebimento"), "meses_com_recebimento"),
            ("volatilidade_cv", Escopo.CEDENTE, volatility.get("cv"), "volatilidade.cv"),
            ("anos_completos_receita", Escopo.CEDENTE, volatility.get("anos_completos"), "volatilidade.anos_completos"),
        ):
            result.append(finding(code, scope, value, component="recursos_recebidos", path=path, fingerprint=fingerprint) if value is not None else unverified(code, scope, component="recursos_recebidos", path=path, fingerprint=fingerprint))
        result.append(finding(
            "receita_serie_anual", Escopo.CEDENTE, _annual_series_fact(receipts),
            component="recursos_recebidos", path="volatilidade.anos_completos,serie_anual,valor_por_ano",
            fingerprint=fingerprint,
        ))
        framed = _score_float(operation.get("valor_enquadrado")) if operation else None
        revenue = _score_float(receipts.get("faturamento_verificado_12m"))
        if framed is not None and revenue:
            result.append(finding("cobertura_exposicao", Escopo.OPERACAO, round(framed / revenue, 4), component="recursos_recebidos", path="faturamento_verificado_12m", fingerprint=fingerprint))
        else:
            result.append(unverified("cobertura_exposicao", Escopo.OPERACAO, component="recursos_recebidos", path="faturamento_verificado_12m", fingerprint=fingerprint))
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    performance = comprasnet.get("performance_contratual") if isinstance(comprasnet, dict) else None
    if isinstance(performance, dict):
        result.append(finding("glosa_historica", Escopo.CONTRATO, {"glosa_total": performance.get("glosa_total"), "taxa_glosa": performance.get("taxa_glosa"), "faturado_total": performance.get("faturado_total")}, component="contratos_comprasnet", path="performance_contratual", fingerprint=fingerprint))
    else:
        result.append(unverified("glosa_historica", Escopo.CONTRATO, component="contratos_comprasnet", path="performance_contratual", fingerprint=fingerprint))
    return result
