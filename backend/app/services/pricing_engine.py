"""
Pricing engine deterministico para taxa sugerida de antecipacao.

Waterfall traduzido do Simulador_AntecipaGOV.xlsm.
Os parametros financeiros sao carregados de pricing_parameters e
pricing_rating_matrix com cache curto em memoria.
"""

from app.services.pricing_params_service import get_pricing_config


def _fee_serpro(valor: float, params: dict) -> float:
    if valor <= 300_000:
        return params["serpro_ate_300k"]
    if valor <= 1_000_000:
        return params["serpro_300k_1m"]
    if valor <= 10_000_000:
        return params["serpro_1m_10m"]
    return params["serpro_acima_10m"]


def compute_taxa(rating: str, valor: float, prazo_meses: int) -> dict:
    """
    Calcula taxa a.m. e breakdown financeiro por rating, valor e prazo.

    Nota: o simulador Excel usa Solver para determinar a taxa via Goal Seek
    (IRR do caixa residual = margem alvo). Esta funcao usa soma direta dos
    componentes, resultando em taxa ~0.3-0.5pp menor que o simulador para
    prazos de 6 meses. Para prazos mais longos a diferenca e menor.
    """
    rating = (rating or "").upper()
    params, matrix = get_pricing_config()

    if rating not in matrix:
        raise ValueError("Rating inválido para precificação")
    if matrix[rating].get("recusa"):
        raise ValueError("Rating recusado para precificação")

    valor = float(valor or 0)
    prazo_meses = max(int(prazo_meses or 1), 1)
    m = matrix[rating]

    cdi_am = params["cdi_am"]
    pct_subordinado = params["pct_subordinado"]
    pct_mezzanino = params["pct_mezzanino"]
    pct_senior = 1.0 - pct_subordinado - pct_mezzanino
    spread_senior = params["spread_senior_aa"]
    spread_mez = params["spread_mez_aa"]
    sub_am = params["margem_alvo_sub_am"]

    cdi_aa = (1 + cdi_am) ** 12 - 1
    custo_senior_am = (1 + cdi_aa + spread_senior) ** (1 / 12) - 1
    custo_mez_am = (1 + cdi_aa + spread_mez) ** (1 / 12) - 1
    funding_ponderado_am = (
        custo_senior_am * pct_senior
        + custo_mez_am * pct_mezzanino
        + sub_am * pct_subordinado
    )

    pd = params["pd_performada"] * m["pd_mult"]
    lgd = params["lgd_estimada"] * m["lgd_mult"]
    el = pd * lgd
    el_mensal = el / prazo_meses

    bond_premio_am = (1 + m["bond_premio_aa"]) ** (1 / 12) - 1
    custo_bond_am = bond_premio_am * m["bond_cobertura"]

    taxa_adm_am = (1 + params["taxa_adm_aa"]) ** (1 / 12) - 1
    bancarizacao_am = params["bancarizacao"] / prazo_meses
    orig_am = params["custo_orig_analise"] / prazo_meses
    serpro_am = _fee_serpro(valor, params) / prazo_meses

    taxa_am = (
        funding_ponderado_am
        + el_mensal
        + custo_bond_am
        + taxa_adm_am
        + bancarizacao_am
        + orig_am
        + serpro_am
    )

    custo_operacional_am = taxa_adm_am + bancarizacao_am + orig_am + serpro_am
    total_receita_rs = valor * taxa_am * prazo_meses

    return {
        "taxa_sugerida_am": taxa_am,
        "taxa_sugerida_am_pct": taxa_am * 100,
        "funding_ponderado_am": funding_ponderado_am,
        "el_mensal": el_mensal,
        "custo_bond_am": custo_bond_am,
        "taxa_adm_am": taxa_adm_am,
        "bancarizacao_am": bancarizacao_am,
        "orig_am": orig_am,
        "serpro_am": serpro_am,
        "sub_am": sub_am,
        "pd": pd,
        "lgd": lgd,
        "el": el,
        "detalhes": {
            "custo_funding_rs": valor * funding_ponderado_am * prazo_meses,
            "custo_risco_rs": valor * el,
            "custo_bond_rs": valor * custo_bond_am * prazo_meses,
            "custo_operacional_rs": valor * custo_operacional_am * prazo_meses,
            "custo_sub_rs": valor * sub_am * pct_subordinado * prazo_meses,
            "total_receita_rs": total_receita_rs,
        },
    }
