"""Version of the deterministic finding-emission rules.

Version 34 inclui as certidoes estadual e municipal da Broadfactor.
"""

EMITTER_VERSION = "34"

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
