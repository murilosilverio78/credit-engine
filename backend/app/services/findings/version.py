"""Version of the deterministic finding-emission rules."""

EMITTER_VERSION = "3"

# Fields outside component snapshots that each adapter actually reads.
HASH_EXTRAS = {
    "cadastro_regularidade": ("tipos_documento",),
    "sacado_orgao": ("valor_enquadrado",),
    "documentos": (),
    "reputacional": (),
    "porte": ("overrides",),
}
