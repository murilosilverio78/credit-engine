"""
Pricing engine determinístico para taxa sugerida de antecipação.

Waterfall traduzido do Simulador_AntecipaGOV.xlsm.
"""

CDI_AM = 0.0114
PCT_SUBORDINADO = 0.20
PCT_MEZZANINO = 0.20
PCT_SENIOR = 0.60
SPREAD_SENIOR_AA = 0.05
SPREAD_MEZ_AA = 0.10

PD_BASE = 0.016
LGD_BASE = 0.70

RATING_MATRIX = {
    "A": {"pd_mult": 0.6, "lgd_mult": 0.70, "bond_cobertura": 0.50, "bond_premio_aa": 0.010},
    "B": {"pd_mult": 1.0, "lgd_mult": 0.80, "bond_cobertura": 0.70, "bond_premio_aa": 0.020},
    "C": {"pd_mult": 2.0, "lgd_mult": 0.95, "bond_cobertura": 0.85, "bond_premio_aa": 0.035},
    "D": {"pd_mult": 5.0, "lgd_mult": 1.10, "bond_cobertura": 1.00, "bond_premio_aa": 0.050},
    "E": None,
}

TAXA_ADM_AA = 0.01
BANCARIZACAO = 0.035
CUSTO_ORIG_ANALISE = 0.005
CUSTO_PLATAFORMA_PERF = 0.005
SUB_AM = 0.025  # custo de capital do tranche subordinado (team) = margem alvo


def _fee_serpro(valor: float) -> float:
    if valor <= 300_000:
        return 0.0042
    if valor <= 1_000_000:
        return 0.0034
    if valor <= 10_000_000:
        return 0.0027
    return 0.0022


def compute_taxa(rating: str, valor: float, prazo_meses: int) -> dict:
    """
    Calcula taxa a.m. e breakdown financeiro por rating, valor e prazo.

    Nota: o simulador Excel usa Solver para determinar a taxa via Goal Seek
    (IRR do caixa residual = margem alvo). Esta função usa soma direta dos
    componentes, resultando em taxa ~0.3-0.5pp menor que o simulador para
    prazos de 6 meses. Para prazos mais longos a diferença é menor.
    """
    rating = (rating or "").upper()
    if rating not in RATING_MATRIX or RATING_MATRIX[rating] is None:
        raise ValueError("Rating recusado ou inválido para precificação")

    valor = float(valor or 0)
    prazo_meses = max(int(prazo_meses or 1), 1)
    matrix = RATING_MATRIX[rating]

    cdi_aa = (1 + CDI_AM) ** 12 - 1
    custo_senior_aa = cdi_aa + SPREAD_SENIOR_AA
    custo_mez_aa = cdi_aa + SPREAD_MEZ_AA
    custo_senior_am = (1 + custo_senior_aa) ** (1 / 12) - 1
    custo_mez_am = (1 + custo_mez_aa) ** (1 / 12) - 1
    funding_ponderado_am = (
        custo_senior_am * PCT_SENIOR
        + custo_mez_am * PCT_MEZZANINO
        + SUB_AM * PCT_SUBORDINADO
    )

    pd = PD_BASE * matrix["pd_mult"]
    lgd = LGD_BASE * matrix["lgd_mult"]
    el = pd * lgd
    el_mensal = el / prazo_meses

    bond_premio_am = (1 + matrix["bond_premio_aa"]) ** (1 / 12) - 1
    custo_bond_am = bond_premio_am * matrix["bond_cobertura"]

    taxa_adm_am = (1 + TAXA_ADM_AA) ** (1 / 12) - 1
    bancarizacao_am = BANCARIZACAO / prazo_meses
    orig_am = CUSTO_ORIG_ANALISE / prazo_meses
    serpro_am = _fee_serpro(valor) / prazo_meses

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
        "sub_am": SUB_AM,
        "pd": pd,
        "lgd": lgd,
        "el": el,
        "detalhes": {
            "custo_funding_rs": valor * funding_ponderado_am * prazo_meses,
            "custo_risco_rs": valor * el,
            "custo_bond_rs": valor * custo_bond_am * prazo_meses,
            "custo_operacional_rs": valor * custo_operacional_am * prazo_meses,
            "custo_sub_rs": valor * SUB_AM * PCT_SUBORDINADO * prazo_meses,
            "total_receita_rs": total_receita_rs,
        },
    }
