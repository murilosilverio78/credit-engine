"""Version of the deterministic finding-emission rules.

Version 35 includes the corrected Comprasnet commitment and payment matching
rules, forcing deterministic re-emission instead of deduplicating old runs.
"""

EMITTER_VERSION = "35"

# Fields outside component snapshots that each adapter actually reads.
HASH_EXTRAS = {
    "cadastro_regularidade": (
        "tipos_documento",
        "documentos_operacao",
        "certidoes_broadfactor",
    ),
    "sacado_orgao": ("valor_enquadrado",),
    "documentos": (),
    "reputacional": (),
    "porte": ("overrides",),
    "financeiro": (
        "valor_enquadrado",
        "contrato_operacao",
        "cotacao_data",
        "parametros_financeiros",
    ),
    "fluxo_caixa": ("contrato_operacao",),
}
