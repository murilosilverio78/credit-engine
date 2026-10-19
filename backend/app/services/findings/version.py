"""Version of the deterministic finding-emission rules.

Version 20 adiciona escopo de UG, conciliação 1:N e antecipações concorrentes.
"""

EMITTER_VERSION = "21"

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
