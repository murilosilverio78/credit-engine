"""Version of the deterministic finding-emission rules.

Version 13 reconhece resultado de balancete ainda não incorporado ao PL.
"""

EMITTER_VERSION = "13"

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
}
