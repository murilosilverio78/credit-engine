"""Version of the deterministic finding-emission rules.

Version 27 registra a degradação de achados de pagamentos quando o Portal foi lido parcialmente.
"""

EMITTER_VERSION = "27"

# Fields outside component snapshots that each adapter actually reads.
HASH_EXTRAS = {
    "cadastro_regularidade": ("tipos_documento", "documentos_operacao"),
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
