"""Pure adapters from persisted snapshots to candidate findings."""

from .cadastro_regularidade import emit_cadastro_regularidade
from .documentos import emit_documentos
from .porte import emit_porte
from .reputacional import emit_reputacional
from .sacado_orgao import emit_sacado_orgao

ADAPTERS = {
    "cadastro_regularidade": emit_cadastro_regularidade,
    "sacado_orgao": emit_sacado_orgao,
    "documentos": emit_documentos,
    "reputacional": emit_reputacional,
    "porte": emit_porte,
}
