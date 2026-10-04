"""Version of the deterministic finding-emission rules.

Version 5 emits the potential Porte/Operacionalidade level, rather than the
post-balance-penalty effective level. Re-emit v4 runs to apply this correction.
"""

EMITTER_VERSION = "5"

# Fields outside component snapshots that each adapter actually reads.
HASH_EXTRAS = {
    "cadastro_regularidade": ("tipos_documento", "documentos_operacao"),
    "sacado_orgao": ("valor_enquadrado",),
    "documentos": (),
    "reputacional": (),
    "porte": ("overrides",),
}
