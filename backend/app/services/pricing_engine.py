"""
Pricing engine deterministico para taxa sugerida de antecipacao.

Replica o Simulador_AntecipaGOV.xlsm por fluxo de caixa mensal e solver de
bissecao. Os parametros financeiros sao carregados de pricing_parameters e
pricing_rating_matrix com cache curto em memoria.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FlowMetrics:
    custo_funding_rs: float = 0.0
    custo_risco_rs: float = 0.0
    custo_adm_rs: float = 0.0
    custo_performance_rs: float = 0.0
    custo_sub_rs: float = 0.0
    total_pmt_rs: float = 0.0


def _fee_serpro(valor: float, params: dict) -> float:
    if valor <= 300_000:
        return params["serpro_ate_300k"]
    if valor <= 1_000_000:
        return params["serpro_300k_1m"]
    if valor <= 10_000_000:
        return params["serpro_1m_10m"]
    return params["serpro_acima_10m"]


def _monthly_funding_cost(cdi_am: float, spread_aa: float) -> float:
    cdi_aa = (1 + cdi_am) ** 12 - 1
    return (1 + cdi_aa + spread_aa) ** (1 / 12) - 1


def _pmt(rate: float, nper: int, pv: float) -> float:
    if rate == 0:
        return pv / nper
    return pv * rate / (1 - (1 + rate) ** (-nper))


def _get_pricing_config() -> tuple[dict, dict]:
    from app.services.pricing_params_service import get_pricing_config

    return get_pricing_config()


def _pricing_inputs(rating: str, valor: float, prazo_meses: int) -> tuple[dict, dict, dict]:
    params, matrix = _get_pricing_config()
    if not params or not matrix:
        raise ValueError("Configuracao de precificacao indisponivel")
    if rating not in matrix:
        raise ValueError("Rating invalido para precificacao")
    if matrix[rating].get("recusa"):
        raise ValueError("Rating recusado para precificacao")
    if valor <= 0:
        raise ValueError("Valor deve ser positivo para precificacao")
    if prazo_meses <= 0:
        raise ValueError("Prazo deve ser positivo para precificacao")
    return params, matrix, matrix[rating]


def _derive(params: dict, m: dict, valor: float, prazo_meses: int) -> dict:
    cdi_am = params["cdi_am"]
    pct_sub = params["pct_subordinado"]
    pct_mezz = params["pct_mezzanino"]
    pct_sen = 1.0 - pct_mezz - pct_sub

    custo_sen_am = _monthly_funding_cost(cdi_am, params["spread_senior_aa"])
    custo_mezz_am = _monthly_funding_cost(cdi_am, params["spread_mez_aa"])
    margem_sub_am = params["margem_alvo_sub_am"]

    pd = params["pd_performada"] * m["pd_mult"]
    lgd = params["lgd_estimada"] * m["lgd_mult"]
    el = pd * lgd
    el_mensal = el / prazo_meses

    bond_premio_aa = m["bond_premio_aa"] or 0.0
    seguro_rs = m["bond_cobertura"] * valor * bond_premio_aa * (prazo_meses / 12)
    carteira_inicial = valor + seguro_rs
    serpro_flat = _fee_serpro(valor, params)

    return {
        "pct_sen": pct_sen,
        "pct_mezz": pct_mezz,
        "pct_sub": pct_sub,
        "custo_sen_am": custo_sen_am,
        "custo_mezz_am": custo_mezz_am,
        "margem_sub_am": margem_sub_am,
        "funding_ponderado_am": (
            custo_sen_am * pct_sen
            + custo_mezz_am * pct_mezz
            + margem_sub_am * pct_sub
        ),
        "pd": pd,
        "lgd": lgd,
        "el": el,
        "el_mensal": el_mensal,
        "seguro_rs": seguro_rs,
        "carteira_inicial": carteira_inicial,
        "serpro_flat": serpro_flat,
        "taxa_adm_am": (1 + params["taxa_adm_aa"]) ** (1 / 12) - 1,
        "bancarizacao_flat": params["bancarizacao"],
        "orig_flat": params["custo_orig_analise"],
        "performance": params["custo_plataforma_perf"],
    }


def _build_flow(
    taxa: float,
    valor: float,
    prazo_meses: int,
    params: dict,
    m: dict,
    collect_metrics: bool = False,
) -> tuple[list[float], FlowMetrics]:
    d = _derive(params, m, valor, prazo_meses)

    carteira_inicial = d["carteira_inicial"]
    sen_inicial = d["pct_sen"] * carteira_inicial
    mezz_inicial = d["pct_mezz"] * carteira_inicial
    sub_inicial = d["pct_sub"] * carteira_inicial

    custo_flat_rs = valor * (d["bancarizacao_flat"] + d["orig_flat"] + d["serpro_flat"])
    flows = [-custo_flat_rs]
    metrics = FlowMetrics()

    carteira = carteira_inicial
    sen = sen_inicial
    mezz = mezz_inicial
    sub = sub_inicial
    pmt = _pmt(taxa, prazo_meses, carteira_inicial)

    for _ in range(1, prazo_meses + 1):
        el_mes_rs = carteira * d["el_mensal"]
        senior_payment = sen * d["custo_sen_am"] + sen_inicial / prazo_meses
        mezz_payment = mezz * d["custo_mezz_am"] + mezz_inicial / prazo_meses
        sub_payment = sub * d["margem_sub_am"] + sub_inicial / prazo_meses
        taxa_adm_rs = d["taxa_adm_am"] * carteira
        performance_rs = (pmt - el_mes_rs) * d["performance"]

        flows.append(
            pmt
            - senior_payment
            - mezz_payment
            - sub_payment
            - taxa_adm_rs
            - performance_rs
            - el_mes_rs
        )

        if collect_metrics:
            metrics.custo_funding_rs += (
                sen * d["custo_sen_am"] + mezz * d["custo_mezz_am"]
            )
            metrics.custo_risco_rs += el_mes_rs
            metrics.custo_adm_rs += taxa_adm_rs
            metrics.custo_performance_rs += performance_rs
            metrics.custo_sub_rs += sub * d["margem_sub_am"]
            metrics.total_pmt_rs += pmt

        carteira = carteira * (1 + taxa) - pmt
        sen = sen * (1 + d["custo_sen_am"]) - senior_payment
        mezz = mezz * (1 + d["custo_mezz_am"]) - mezz_payment
        sub = sub * (1 + d["margem_sub_am"]) - sub_payment

    if collect_metrics:
        metrics.custo_adm_rs += custo_flat_rs

    return flows, metrics


def _solve_taxa(valor: float, prazo_meses: int, params: dict, m: dict) -> float:
    lo, hi = 0.0001, 0.30
    lo_sum = sum(_build_flow(lo, valor, prazo_meses, params, m)[0])
    hi_sum = sum(_build_flow(hi, valor, prazo_meses, params, m)[0])
    if lo_sum >= 0:
        return lo
    if hi_sum <= 0:
        raise ValueError("Nao foi possivel convergir taxa de precificacao")

    for _ in range(300):
        mid = (lo + hi) / 2
        flows, _ = _build_flow(mid, valor, prazo_meses, params, m)
        if sum(flows) > 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2


def compute_margem_subordinado(
    taxa_am: float,
    valor: float,
    prazo_meses: int,
    rating: str,
) -> float:
    """
    Calcula a margem mensal resultante do subordinado para uma taxa fixa.

    Reusa o waterfall existente sem rodar o solver de taxa. O fluxo residual
    positivo/negativo depois da margem alvo do subordinado e convertido em
    margem mensal incremental sobre a base subordinada media do fluxo.
    """
    rating = (rating or "").upper()
    taxa_am = float(taxa_am or 0)
    valor = float(valor or 0)
    prazo_meses = max(int(prazo_meses or 1), 1)
    params, _, m = _pricing_inputs(rating, valor, prazo_meses)
    derived = _derive(params, m, valor, prazo_meses)
    flows, metrics = _build_flow(
        taxa_am,
        valor,
        prazo_meses,
        params,
        m,
        collect_metrics=True,
    )

    margem_alvo = derived["margem_sub_am"]
    if margem_alvo <= 0:
        return 0.0

    saldo_subordinado_total = metrics.custo_sub_rs / margem_alvo
    if saldo_subordinado_total <= 0:
        return 0.0

    return (metrics.custo_sub_rs + sum(flows)) / saldo_subordinado_total


def compute_taxa(rating: str, valor: float, prazo_meses: int) -> dict:
    """
    Calcula taxa a.m. por fluxo de caixa e Goal Seek via bissecao.

    A taxa encontrada zera a soma do fluxo residual mensal, com a margem alvo do
    subordinado ja embutida no waterfall. Rating E permanece recusa.
    """
    rating = (rating or "").upper()
    valor = float(valor or 0)
    prazo_meses = max(int(prazo_meses or 1), 1)
    params, _, m = _pricing_inputs(rating, valor, prazo_meses)
    derived = _derive(params, m, valor, prazo_meses)

    taxa_am = _solve_taxa(valor, prazo_meses, params, m)
    flows, metrics = _build_flow(
        taxa_am,
        valor,
        prazo_meses,
        params,
        m,
        collect_metrics=True,
    )

    custo_operacional_rs = metrics.custo_adm_rs + metrics.custo_performance_rs
    total_receita_rs = metrics.total_pmt_rs - derived["carteira_inicial"]
    custo_bond_equiv_am = derived["seguro_rs"] / valor / prazo_meses
    bancarizacao_am = derived["bancarizacao_flat"] / prazo_meses
    orig_am = derived["orig_flat"] / prazo_meses
    serpro_am = derived["serpro_flat"] / prazo_meses

    return {
        "taxa_sugerida_am": taxa_am,
        "taxa_sugerida_am_pct": taxa_am * 100,
        "funding_ponderado_am": derived["funding_ponderado_am"],
        "el_mensal": derived["el_mensal"],
        "custo_bond_am": custo_bond_equiv_am,
        "taxa_adm_am": derived["taxa_adm_am"],
        "bancarizacao_am": bancarizacao_am,
        "orig_am": orig_am,
        "serpro_am": serpro_am,
        "sub_am": derived["margem_sub_am"],
        "pd": derived["pd"],
        "lgd": derived["lgd"],
        "el": derived["el"],
        "fluxo_caixa": flows,
        "detalhes": {
            "metodologia": "fluxo_caixa_bissecao",
            "prazo_meses": prazo_meses,
            "carteira_inicial_rs": derived["carteira_inicial"],
            "seguro_rs": derived["seguro_rs"],
            "custo_funding_rs": metrics.custo_funding_rs,
            "custo_risco_rs": metrics.custo_risco_rs,
            "custo_bond_rs": derived["seguro_rs"],
            "custo_operacional_rs": custo_operacional_rs,
            "custo_flat_rs": valor
            * (
                derived["bancarizacao_flat"]
                + derived["orig_flat"]
                + derived["serpro_flat"]
            ),
            "custo_adm_rs": metrics.custo_adm_rs,
            "custo_performance_rs": metrics.custo_performance_rs,
            "custo_sub_rs": metrics.custo_sub_rs,
            "total_receita_rs": total_receita_rs,
            "residual_fluxo_rs": sum(flows),
        },
    }
